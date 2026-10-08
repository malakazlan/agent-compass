"""Offline early-abort simulation on recorded trajectories.

Each test trajectory has several scored prefixes (25/50/75/90% plus one random). Walking the
checkpoints in order, the run is aborted at the first checkpoint whose calibrated p_success is
below a threshold. Thresholds are chosen on DEV at fixed false-positive rates (share of successful
runs aborted), then applied to TEST, Fail-Fast style. Cost is measured in steps (the fraction of
each run's steps that would not have been executed), since per-step token counts are not in the
records. Also reports the net resolve rate when aborted runs count as failures.

    uv run --python 3.12 --with numpy --with scipy python scripts/offline_early_abort.py \
        --dev-rows runs/m3-v0-2b/eval-dev/rows.json --dev-records data/v0/dev.jsonl \
        --test-rows runs/m3-v0-2b/eval-test/rows.json --test-records data/v0/test.jsonl \
        --out runs/m3-v0-2b/early_abort_test.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def load(rows_path: Path, records_path: Path):
    meta = {}
    with records_path.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            meta[r["_meta"]["id"]] = r["_meta"]
    rows = json.load(open(rows_path, encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows["rows"]
    keys = [str(k).lower() for k in rows[0]["keys"]]
    yes = keys.index("true") if "true" in keys else 1
    traj: dict[str, dict] = {}
    for r in rows:
        m = meta.get(r["id"])
        if not m:
            continue
        t = traj.setdefault(m["traj_id"], {"outcome": bool(m["outcome"]), "n_steps": m["n_steps"], "policy": m["policy_model"],
                                           "source": m["source"].split("/")[-1], "checkpoints": []})
        t["checkpoints"].append((m["prefix_len"], float(r["p"][yes])))
    for t in traj.values():
        t["checkpoints"].sort()
    return traj


def calibrate(p: np.ndarray, T: float) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return 1 / (1 + np.exp(-np.log(p / (1 - p)) / T))


def simulate(traj: dict, thr: float, T: float, min_frac: float):
    """Returns per-run (aborted, abort_step) given threshold on calibrated p_success, after min_frac of the run."""
    out = {}
    for tid, t in traj.items():
        aborted, step = False, None
        for L, p in t["checkpoints"]:
            if L / t["n_steps"] < min_frac:
                continue
            if calibrate(np.array([p]), T)[0] < thr:
                aborted, step = True, L
                break
        out[tid] = (aborted, step)
    return out


def metrics(traj: dict, sim: dict) -> dict:
    n = len(traj)
    succ = [tid for tid, t in traj.items() if t["outcome"]]
    fail = [tid for tid, t in traj.items() if not t["outcome"]]
    fp = sum(sim[tid][0] for tid in succ)
    tp = sum(sim[tid][0] for tid in fail)
    fired = sum(a for a, _ in sim.values())
    steps_total = sum(t["n_steps"] for t in traj.values())
    steps_saved = sum((t["n_steps"] - sim[tid][1]) for tid, t in traj.items() if sim[tid][0])
    return {
        "runs": n, "success_runs": len(succ), "fail_runs": len(fail),
        "fpr": fp / max(1, len(succ)), "recall": tp / max(1, len(fail)), "precision": tp / max(1, fired), "fired": fired / n,
        "steps_saved_fraction": steps_saved / max(1, steps_total),
        "resolve_rate_no_abort": len(succ) / n, "resolve_rate_with_abort": (len(succ) - fp) / n,
    }


def pick_threshold(traj: dict, fpr_target: float, T: float, min_frac: float) -> float:
    """Largest threshold on dev whose FPR is <= target (abort the most failures allowed by the budget)."""
    best = 0.0
    for thr in np.linspace(0.02, 0.98, 97):
        m = metrics(traj, simulate(traj, thr, T, min_frac))
        if m["fpr"] <= fpr_target:
            best = thr
    return float(best)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-rows", type=Path, required=True)
    ap.add_argument("--dev-records", type=Path, required=True)
    ap.add_argument("--test-rows", type=Path, required=True)
    ap.add_argument("--test-records", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--temperature", type=float, default=None, help="default: read runs/.../calibration.json next to --out")
    ap.add_argument("--min-frac", type=float, default=0.2, help="no abort before this fraction of the run (Fail-Fast uses a 20-step floor)")
    a = ap.parse_args()
    T = a.temperature
    if T is None:
        cal = a.out.parent / "calibration.json"
        T = json.loads(cal.read_text())["temperature_fitted_on_dev"] if cal.exists() else 1.0
    dev = load(a.dev_rows, a.dev_records)
    test = load(a.test_rows, a.test_records)
    print(f"dev runs {len(dev)}  test runs {len(test)}  temperature {T:.3f}  min_frac {a.min_frac}")

    result = {"temperature": T, "min_frac": a.min_frac, "dev_runs": len(dev), "test_runs": len(test), "operating_points": {}}
    print("\n| FPR budget | threshold (dev) | test FPR | recall | precision | fired | steps saved | resolve no-abort -> with abort |")
    print("|---|---|---|---|---|---|---|---|")
    for fpr in (0.05, 0.10, 0.25):
        thr = pick_threshold(dev, fpr, T, a.min_frac)
        m = metrics(test, simulate(test, thr, T, a.min_frac))
        by_pol = {}
        for pol in sorted({t["policy"] for t in test.values()}):
            sub = {k: v for k, v in test.items() if v["policy"] == pol}
            if len(sub) >= 50:
                by_pol[pol] = metrics(sub, simulate(sub, thr, T, a.min_frac))
        result["operating_points"][f"fpr_{int(fpr * 100)}"] = {"threshold": thr, "test": m, "by_policy": by_pol}
        print(f"| {int(fpr * 100)}% | {thr:.2f} | {m['fpr']:.3f} | {m['recall']:.3f} | {m['precision']:.3f} | {m['fired']:.3f} | {m['steps_saved_fraction']:.3f} | {m['resolve_rate_no_abort']:.3f} -> {m['resolve_rate_with_abort']:.3f} |")
        for pol, pm in by_pol.items():
            print(f"|   {pol[:28]} | | {pm['fpr']:.3f} | {pm['recall']:.3f} | {pm['precision']:.3f} | {pm['fired']:.3f} | {pm['steps_saved_fraction']:.3f} | {pm['resolve_rate_no_abort']:.3f} -> {pm['resolve_rate_with_abort']:.3f} |")
    # Deployment protocol: one threshold per policy model, chosen on that policy's dev runs.
    print("\nPer-policy thresholds (chosen on each policy's own dev runs):")
    print("\n| policy | FPR budget | threshold | test FPR | recall | precision | fired | steps saved | resolve no-abort -> with abort |")
    print("|---|---|---|---|---|---|---|---|---|")
    result["per_policy_thresholds"] = {}
    for pol in sorted({t["policy"] for t in test.values()}):
        dsub = {k: v for k, v in dev.items() if v["policy"] == pol}
        tsub = {k: v for k, v in test.items() if v["policy"] == pol}
        if len(dsub) < 100 or len(tsub) < 100:
            continue
        result["per_policy_thresholds"][pol] = {"dev_runs": len(dsub), "test_runs": len(tsub)}
        for fpr in (0.05, 0.10, 0.25):
            thr = pick_threshold(dsub, fpr, T, a.min_frac)
            m = metrics(tsub, simulate(tsub, thr, T, a.min_frac))
            result["per_policy_thresholds"][pol][f"fpr_{int(fpr * 100)}"] = {"threshold": thr, "test": m}
            print(f"| {pol[:28]} | {int(fpr * 100)}% | {thr:.2f} | {m['fpr']:.3f} | {m['recall']:.3f} | {m['precision']:.3f} | {m['fired']:.3f} | {m['steps_saved_fraction']:.3f} | {m['resolve_rate_no_abort']:.3f} -> {m['resolve_rate_with_abort']:.3f} |")
    a.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
