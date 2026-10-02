"""kev.benchmark for long-state records.

`kev.benchmark --data` admits records only up to kev's default training context (384 state
tokens), so our 4k-token states are rejected. This wrapper is the same evaluation
(`kev.benchmark.evaluate_records`, same rows.json / report.json) with the predictor built on
`training_context(max_state)`, plus an optional record limit and timing.

Run inside kev's project (its venv has kev installed):
    cd /workspace/kev && UV_NO_SYNC=1 KEV_DTYPE=bf16 uv run python /workspace/agent-compass/scripts/pod/kev_benchmark_long.py \
        --run runs/<run> --data /workspace/data/v0/dev.jsonl --out runs/<run>/eval-dev --max_state 4352 [--limit 2000]

KEV_DTYPE=bf16 scores in bf16 (kev: probabilities within ~0.01 of the fp32 path, 2-4x faster);
unset it for the exact fp32 path.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from kev.benchmark import evaluate_records
from kev.checkpoint import LoadOptions
from kev.data import load_records
from kev.device import default_device
from kev.model import training_context
from kev.predictors import LocalPredictor
from kev.suite import write_json


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", choices=["cpu", "mps", "cuda"], default=default_device())
    ap.add_argument("--max_state", type=int, default=4352)
    ap.add_argument("--limit", type=int, default=0, help="score only the first N records (0 = all)")
    a = ap.parse_args()

    records = load_records(a.data)
    if a.limit:
        records = records[: a.limit]
    context = training_context(a.max_state)
    opts = LoadOptions.from_env()
    print(f"records {len(records)}  context {context}  dtype {opts.dtype or 'fp32'}", flush=True)

    t0 = time.time()
    predictor = LocalPredictor(a.run, a.device, opts, context=context)
    load_s = time.time() - t0
    t1 = time.time()
    report, rows = evaluate_records(records, predictor, a.out, skip_overlong=True)
    eval_s = time.time() - t1
    cov = report["coverage"]
    summary = {
        "records": len(records),
        "evaluated_records": cov.get("evaluated_records"),
        "rejected_records": cov.get("rejected_records"),
        "load_seconds": round(load_s, 1),
        "eval_seconds": round(eval_s, 1),
        "seconds_per_record": round(eval_s / max(1, cov.get("evaluated_records") or 1), 3),
        "max_state": a.max_state,
        "dtype": opts.dtype or "fp32",
        "clean": report.get("clean"),
    }
    report.update(long_context_eval=summary, data=a.data, run=a.run)
    write_json(Path(a.out) / "report.json", report)
    print(json.dumps({k: v for k, v in summary.items() if k != "clean"}, indent=1))
    print("clean:", json.dumps(report.get("clean"))[:400])


if __name__ == "__main__":
    main()
