"""Per-question temperature scaling: fit one temperature per question id on dev rows, apply to test rows.

Works on kev rows.json (probabilities per option; logits when recorded). For every question id:
  - fit T minimising NLL on dev over the softmax of log(p)/T (or logits/T when present)
  - report test metrics raw and calibrated: noul -> AUROC / Brier / ECE / confident-error,
    score -> accuracy / MAE / RPS, choice -> top-1 / NLL
  - noul questions also get a conformal-style threshold table: for target false-positive rates
    5/10/25% on dev, the "yes" probability cut-off and its realised rate on test.

    uv run --python 3.12 --with numpy --with scipy python scripts/calibrate_per_question.py \
        --dev-rows runs/<run>/eval-dev/rows.json --dev-records data/v1/dev.jsonl \
        --test-rows runs/<run>/eval-test/rows.json --test-records data/v1/test.jsonl --out runs/<run>/calibration.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.eval.metrics import auroc, binary_report, ordinal_report, recall_at_fpr  # noqa: E402


def load(rows_path: Path, records_path: Path):
    ids = set()
    with records_path.open(encoding="utf-8") as f:
        for line in f:
            ids.add(json.loads(line)["_meta"]["id"])
    rows = json.load(open(rows_path, encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows["rows"]
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r["id"] in ids and r.get("variant", "clean") == "clean":
            by_q[r["question"]].append(r)
    return by_q


def logits_of(rows: list[dict]) -> np.ndarray:
    lg = rows[0].get("logits")
    if lg:
        if isinstance(lg, dict):
            return np.array([[r["logits"][k] for k in r["keys"]] for r in rows], dtype=float)
        return np.array([r["logits"] for r in rows], dtype=float)  # kev: list aligned with keys
    return np.log(np.clip(np.array([r["p"] for r in rows], dtype=float), 1e-9, 1.0))


def softmax(z: np.ndarray, T: float) -> np.ndarray:
    z = z / T
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def fit_T(z: np.ndarray, y: np.ndarray) -> float:
    def nll(T):
        p = np.clip(softmax(z, T)[np.arange(len(y)), y], 1e-9, 1)
        return -np.mean(np.log(p))
    return float(minimize_scalar(nll, bounds=(0.2, 10.0), method="bounded").x)


def report(rows: list[dict], p: np.ndarray, qtype: str, yes_idx: int) -> dict:
    y = np.array([int(r["label"]) for r in rows])
    if qtype == "noul":
        yb = (y == yes_idx).astype(int)
        return binary_report(yb, p[:, yes_idx])
    if qtype == "score":
        return ordinal_report(y, p)
    top1 = float(np.mean(p.argmax(1) == y))
    return {"n": int(len(y)), "top1": top1, "nll": float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1))))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev-rows", type=Path, required=True)
    ap.add_argument("--dev-records", type=Path, required=True)
    ap.add_argument("--test-rows", type=Path, required=True)
    ap.add_argument("--test-records", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    dev = load(a.dev_rows, a.dev_records)
    test = load(a.test_rows, a.test_records)
    result: dict = {}
    print(f"{'question':12s} {'type':6s} {'T':>5s}  test raw -> calibrated")
    for qid in sorted(dev):
        if qid not in test or len(dev[qid]) < 50:
            continue
        qtype = dev[qid][0]["type"]
        keys = [str(k).lower() for k in dev[qid][0]["keys"]]
        yes_idx = keys.index("true") if "true" in keys else len(keys) - 1
        zd, yd = logits_of(dev[qid]), np.array([int(r["label"]) for r in dev[qid]])
        T = fit_T(zd, yd)
        zt = logits_of(test[qid])
        raw, cal = report(test[qid], softmax(zt, 1.0), qtype, yes_idx), report(test[qid], softmax(zt, T), qtype, yes_idx)
        entry = {"type": qtype, "temperature": T, "n_dev": len(dev[qid]), "n_test": len(test[qid]), "test_raw": raw, "test_calibrated": cal}
        if qtype == "noul":
            # thresholds for flagging "yes" at a false-positive budget, chosen on dev, realised on test
            pd_ = softmax(zd, T)[:, yes_idx]
            ydb = (yd == yes_idx).astype(int)
            pt_ = softmax(zt, T)[:, yes_idx]
            ytb = (np.array([int(r["label"]) for r in test[qid]]) == yes_idx).astype(int)
            thr_table = {}
            for fpr in (0.05, 0.10, 0.25):
                op = recall_at_fpr(ydb, pd_, fpr)  # flag positives; threshold chosen on dev
                thr = op["threshold"]
                flagged = pt_ > thr
                thr_table[f"fpr_{int(fpr * 100)}"] = {"threshold": float(thr), "test_fpr": float(flagged[ytb == 0].mean()) if (ytb == 0).any() else None,
                                                     "test_recall": float(flagged[ytb == 1].mean()) if (ytb == 1).any() else None}
            entry["yes_thresholds"] = thr_table
            print(f"{qid:12s} {qtype:6s} {T:5.2f}  AUROC {raw['auroc']:.3f}  ECE {raw['ece15']:.3f} -> {cal['ece15']:.3f}  conf-err {raw['confident_error_0.9']:.3f} -> {cal['confident_error_0.9']:.3f}")
        elif qtype == "score":
            print(f"{qid:12s} {qtype:6s} {T:5.2f}  acc {raw['accuracy']:.3f}  within-one {raw['within_one']:.3f}  MAE {raw['mae']:.3f} -> {cal['mae']:.3f}  RPS {raw['rps']:.3f} -> {cal['rps']:.3f}")
        else:
            print(f"{qid:12s} {qtype:6s} {T:5.2f}  top1 {raw['top1']:.3f}  NLL {raw['nll']:.3f} -> {cal['nll']:.3f}")
        result[qid] = entry
    a.out.write_text(json.dumps(result, indent=1, default=float) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
