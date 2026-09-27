"""Score a kev benchmark run (rows.json) with agent-compass metrics.

kev.benchmark writes one row per (record, question) with `p` (probabilities over `keys`) and
`label` (index into keys). This joins those rows back to our records for prefix position and
task grouping, then reports AUROC / Brier / ECE / confident-error overall and per prefix
bucket with grouped bootstrap CIs, plus Fail-Fast style recall at fixed FPR, and compares
against the baseline result.json when given.

    uv run --python 3.12 --with numpy python scripts/eval_rows.py --rows runs/<run>/eval-test/rows.json \
        --records data/v0/test.jsonl --out runs/<run>/metrics_test.json [--baseline runs/baselines-swe_agent-v0/result.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.eval.metrics import auroc, binary_report, grouped_bootstrap, ordinal_report, recall_at_fpr, reliability_table  # noqa: E402

BUCKETS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01))


def load_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["rows"]


def load_meta(path: Path) -> dict[str, dict]:
    out = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                out[r["_meta"]["id"]] = r["_meta"]
    return out


def p_yes(row: dict) -> float:
    keys = [str(k).lower() for k in row["keys"]]
    p = row["p"]
    for i, k in enumerate(keys):
        if k in ("true", "yes", "1"):
            return float(p[i])
    return float(p[-1])


def score_binary(rows: list[dict], meta: dict[str, dict], n_boot: int) -> dict:
    y = np.asarray([int(r["label"]) for r in rows])
    # label is an index into keys; make sure index 1 means "yes"
    keys = [str(k).lower() for k in rows[0]["keys"]]
    yes_idx = next((i for i, k in enumerate(keys) if k in ("true", "yes", "1")), len(keys) - 1)
    y = (y == yes_idx).astype(int)
    p = np.asarray([p_yes(r) for r in rows])
    groups = np.asarray([r["group"] for r in rows])
    frac = np.asarray([meta[r["id"]]["prefix_frac"] for r in rows])
    ds = np.asarray([meta[r["id"]]["source"] for r in rows])

    rep = binary_report(y, p)
    pt, lo, hi = grouped_bootstrap(groups, lambda idx: auroc(y[idx], p[idx]), n_boot=n_boot)
    rep["auroc_ci95"] = [lo, hi]
    rep["by_prefix"] = {}
    for lo_f, hi_f in BUCKETS:
        m = (frac >= lo_f) & (frac < hi_f)
        if m.sum() >= 20 and len(set(y[m])) == 2:
            pt2, l2, h2 = grouped_bootstrap(groups[m], lambda idx, mm=m: auroc(y[mm][idx], p[mm][idx]), n_boot=max(200, n_boot // 2))
            rep["by_prefix"][f"{int(lo_f * 100)}-{int(min(hi_f, 1) * 100)}%"] = {"n": int(m.sum()), "auroc": pt2, "auroc_ci95": [l2, h2],
                                                                                 "brier": float(np.mean((p[m] - y[m]) ** 2))}
    rep["by_source"] = {}
    for s in sorted(set(ds.tolist())):
        m = ds == s
        if len(set(y[m])) == 2:
            rep["by_source"][s] = binary_report(y[m], p[m])
    rep["abort"] = {f"fpr_{int(f * 100)}": recall_at_fpr(1 - y, 1 - p, f) for f in (0.05, 0.10, 0.25)}
    rep["reliability"] = reliability_table(y, p, 15)
    return rep


def score_ordinal(rows: list[dict]) -> dict:
    y = [int(r["label"]) for r in rows]
    P = [r["p"] for r in rows]
    return ordinal_report(y, P)


def score_choice(rows: list[dict]) -> dict:
    y = np.asarray([int(r["label"]) for r in rows])
    P = np.asarray([r["p"] for r in rows], dtype=object)
    top1 = np.mean([int(np.argmax(p) == yi) for p, yi in zip(P, y)])
    return {"n": int(len(y)), "top1": float(top1), "k_mean": float(np.mean([len(p) for p in P]))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--records", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, default=None, help="runs/baselines-*/result.json to print side by side")
    ap.add_argument("--n-boot", type=int, default=500)
    args = ap.parse_args()

    rows = [r for r in load_rows(args.rows) if r.get("variant", "clean") == "clean"]
    meta = load_meta(args.records)
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["id"] in meta:
            by_q[r["question"]].append(r)

    result: dict = {"rows": str(args.rows), "records": str(args.records), "questions": {}}
    for qid, qrows in by_q.items():
        t = qrows[0]["type"]
        if t == "noul":
            result["questions"][qid] = score_binary(qrows, meta, args.n_boot)
        elif t == "score":
            result["questions"][qid] = score_ordinal(qrows)
        else:
            result["questions"][qid] = score_choice(qrows)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8", newline="\n")

    if "p_success" in result["questions"]:
        r = result["questions"]["p_success"]
        print(f"\np_success  n={r['n']}  AUROC {r['auroc']:.3f} [{r['auroc_ci95'][0]:.3f}, {r['auroc_ci95'][1]:.3f}]  Brier {r['brier']:.3f}  ECE {r['ece15']:.3f}  conf-err@0.9 {r['confident_error_0.9']:.3f}")
        print("  by prefix: " + "  ".join(f"{k} {v['auroc']:.3f}" for k, v in r["by_prefix"].items()))
        print("  by source: " + "  ".join(f"{k.split('/')[-1]} {v['auroc']:.3f}" for k, v in r["by_source"].items()))
        print("  abort recall @5/10/25% FPR: " + " / ".join(f"{r['abort'][k]['recall']:.3f}" for k in ("fpr_5", "fpr_10", "fpr_25")))
        if args.baseline and args.baseline.exists():
            b = json.loads(args.baseline.read_text(encoding="utf-8"))
            for ev, d in b["evals"].items():
                g = d["baselines"]["gbt"]
                print(f"  baseline gbt on {ev}: AUROC {g['auroc']:.3f} [{g['auroc_ci95'][0]:.3f}, {g['auroc_ci95'][1]:.3f}]  Brier {g['brier']:.3f}")
    for qid, r in result["questions"].items():
        if qid != "p_success":
            print(qid, json.dumps({k: v for k, v in r.items() if not isinstance(v, (list, dict))}))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
