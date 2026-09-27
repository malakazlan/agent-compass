"""Heuristic and gradient-boosted baselines for p_success on the example records.

Baselines:
  step_count          score = -prefix_len (longer runs fail more)
  error_count         score = -recent_error_lines
  repetition          score = -(recent_repeat_actions + 3*stuck_rule)
  progress_rule       score = progress label of the last step
  gbt (AgentStop-style)  sklearn HistGradientBoostingClassifier on all hand features, fit on train

Heuristics have no parameters except a sign, which is fixed by definition (never fit on
eval data). Every metric is reported overall and per prefix-fraction bucket with a
grouped bootstrap CI (groups = task). Results go to runs/<run_id>/result.json and a
markdown table is printed.

    uv run --python 3.12 --with pydantic --with numpy --with scikit-learn python scripts/eval_baselines.py \
        --train data/examples/swe_agent.train.jsonl --eval data/examples/swe_agent.dev.jsonl --run-id baselines-swe_agent-dev
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.baselines.features import FEATURE_NAMES, feature_matrix  # noqa: E402
from agent_compass.eval.metrics import auroc, binary_report, grouped_bootstrap, recall_at_fpr  # noqa: E402

BUCKETS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01))


def load(path: Path, max_records: int | None) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
            if max_records and len(out) >= max_records:
                break
    return out


def git_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def heuristic_scores(X: np.ndarray) -> dict[str, np.ndarray]:
    col = {k: i for i, k in enumerate(FEATURE_NAMES)}
    return {
        "step_count": -X[:, col["prefix_len"]],
        "error_count": -X[:, col["recent_error_lines"]],
        "repetition": -(X[:, col["recent_repeat_actions"]] + 3 * X[:, col["stuck_rule"]]),
        "progress_rule": X[:, col["progress_rule"]],
    }


def to_prob(score: np.ndarray, y_train: np.ndarray | None = None, score_train: np.ndarray | None = None) -> np.ndarray:
    """Heuristics only rank; for Brier/ECE we map score -> probability with a logistic fit on TRAIN scores."""
    from sklearn.linear_model import LogisticRegression

    if y_train is None or len(set(y_train.tolist())) < 2:
        return 1 / (1 + np.exp(-score))
    lr = LogisticRegression().fit(score_train.reshape(-1, 1), y_train)
    return lr.predict_proba(score.reshape(-1, 1))[:, 1]


def evaluate(name: str, y: np.ndarray, p: np.ndarray, groups: np.ndarray, frac: np.ndarray, n_boot: int) -> dict:
    rep = binary_report(y, p)
    point, lo, hi = grouped_bootstrap(groups, lambda idx: auroc(y[idx], p[idx]), n_boot=n_boot)
    rep["auroc_ci95"] = [lo, hi]
    rep["by_prefix"] = {}
    for lo_f, hi_f in BUCKETS:
        m = (frac >= lo_f) & (frac < hi_f)
        if m.sum() >= 20 and len(set(y[m])) == 2:
            pt, l2, h2 = grouped_bootstrap(groups[m], lambda idx, mm=m: auroc(y[mm][idx], p[mm][idx]), n_boot=max(200, n_boot // 2))
            rep["by_prefix"][f"{int(lo_f * 100)}-{int(min(hi_f, 1.0) * 100)}%"] = {"n": int(m.sum()), "auroc": pt, "auroc_ci95": [l2, h2], "brier": float(np.mean((p[m] - y[m]) ** 2))}
    # Fail-Fast style: failure score = 1 - p_success; flag failures at fixed FPR
    rep["abort"] = {f"fpr_{int(f * 100)}": recall_at_fpr(1 - y, 1 - p, f) for f in (0.05, 0.10, 0.25)}
    rep["name"] = name
    return rep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--eval", type=Path, required=True, nargs="+")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--max-records", type=int, default=None)
    ap.add_argument("--n-boot", type=int, default=500)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    t0 = time.time()

    train = load(args.train, args.max_records)
    Xtr = np.asarray(feature_matrix(train))
    ytr = np.asarray([int(r["questions"]["p_success"]["label"]) for r in train])
    print(f"train: {len(train)} records, positive rate {ytr.mean():.3f}", flush=True)

    from sklearn.ensemble import HistGradientBoostingClassifier

    gbt = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, random_state=args.seed).fit(Xtr, ytr)
    htr = heuristic_scores(Xtr)

    results = {"run_id": args.run_id, "git": git_hash(), "created": datetime.now(timezone.utc).isoformat(), "train": str(args.train),
               "n_train": len(train), "features": list(FEATURE_NAMES), "evals": {}}
    for ev in args.eval:
        recs = load(ev, args.max_records)
        X = np.asarray(feature_matrix(recs))
        y = np.asarray([int(r["questions"]["p_success"]["label"]) for r in recs])
        groups = np.asarray([r["_meta"]["group_id"] for r in recs])
        frac = np.asarray([r["_meta"]["prefix_frac"] for r in recs])
        print(f"eval {ev.name}: {len(recs)} records, positive rate {y.mean():.3f}", flush=True)
        out = {}
        hs = heuristic_scores(X)
        for name, s in hs.items():
            out[name] = evaluate(name, y, to_prob(s, ytr, htr[name]), groups, frac, args.n_boot)
        out["gbt"] = evaluate("gbt", y, gbt.predict_proba(X)[:, 1], groups, frac, args.n_boot)
        out["prior"] = evaluate("prior", y, np.full(len(y), ytr.mean()), groups, frac, 50)
        results["evals"][ev.name] = {"n": len(recs), "positive_rate": float(y.mean()), "baselines": out}

        print(f"\n### {ev.name}  (n={len(recs)}, success rate {y.mean():.3f})\n")
        print("| baseline | AUROC [95% CI] | Brier | ECE | conf-err@0.9 | AUROC 0-25% | 25-50% | 50-75% | 75-100% | recall@5%FPR | recall@25%FPR |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for name, r in out.items():
            bp = r["by_prefix"]
            cells = [f"{bp[k]['auroc']:.3f}" if k in bp else "-" for k in ("0-25%", "25-50%", "50-75%", "75-100%")]
            print(f"| {name} | {r['auroc']:.3f} [{r['auroc_ci95'][0]:.3f}, {r['auroc_ci95'][1]:.3f}] | {r['brier']:.3f} | {r['ece15']:.3f} | {r['confident_error_0.9']:.3f} | "
                  + " | ".join(cells) + f" | {r['abort']['fpr_5']['recall']:.3f} | {r['abort']['fpr_25']['recall']:.3f} |")

    results["seconds"] = round(time.time() - t0, 1)
    run_dir = ROOT / "runs" / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "result.json").write_text(json.dumps(results, indent=1, default=float) + "\n", encoding="utf-8", newline="\n")
    (run_dir / "config.json").write_text(json.dumps(vars(args), indent=1, default=str) + "\n", encoding="utf-8", newline="\n")
    print(f"\nwrote {run_dir / 'result.json'}")


if __name__ == "__main__":
    main()
