#!/usr/bin/env bash
# M4 v1: multi-head agent-compass-2b, warm-started from v0, trained with agent_compass.train.multihead.
#
# Prerequisites on the pod (network volume): $WORK/kev venv with fused kernels (m0_reproduce_kev.sh),
# $WORK/kev/runs/m3-v0-2b (v0 checkpoint) or the Hub id, v1 data in $DATA_REPO (make_v1_split.py output).
#
#   export HF_TOKEN=...  WORK=/workspace  DATA_REPO=azlanmalikai/agent-compass-data
#   bash m4_train_v1.sh smoke     # ~10 min: 600 records, measures s/record with six branches
#   bash m4_train_v1.sh full      # size with TRAIN_RECORDS from the smoke rate (target ~3 h)
#
# Outputs: $WORK/kev/runs/$RUN/{adapter, head.pt, training_*.json, eval-dev/, eval-test/, metrics_*.json}

set -euo pipefail
MODE="${1:-smoke}"
WORK="${WORK:-/workspace}"
DATA_REPO="${DATA_REPO:-azlanmalikai/agent-compass-data}"
BASE="${BASE:-Qwen/Qwen3.5-2B-Base}"
INIT_FROM="${INIT_FROM:-runs/m3-v0-2b}"
RUN="${RUN:-m4-v1-2b}"
TRAIN_RECORDS="${TRAIN_RECORDS:-0}"
EPOCHS="${EPOCHS:-1}"
LR="${LR:-5e-5}"
BATCH="${BATCH:-2}"
ACCUM="${ACCUM:-4}"
MAX_STATE="${MAX_STATE:-4352}"
PAIR_WEIGHT="${PAIR_WEIGHT:-0.25}"
ORDINAL="${ORDINAL:-1}"
WEIGHTS="${WEIGHTS:-}"
DEV_LIMIT="${DEV_LIMIT:-5000}"
TEST_LIMIT="${TEST_LIMIT:-20000}"
export HF_HOME="$WORK/hf" UV_NO_SYNC=1 UV_LINK_MODE=copy PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

AC="$WORK/agent-compass"
[ -d "$AC" ] || git clone https://github.com/malakazlan/agent-compass.git "$AC"
git -C "$AC" pull --quiet
export PYTHONPATH="$AC"
cd "$WORK/kev"

echo "== data (v1 files from $DATA_REPO, subfolder v1/)"
mkdir -p "$WORK/data/v1"
uv run hf download "$DATA_REPO" --repo-type dataset --include "v1/*" --local-dir "$WORK/data" >/dev/null
ls -la "$WORK/data/v1"
TRAIN="$WORK/data/v1/train.jsonl"; DEV="$WORK/data/v1/dev.jsonl"; TEST="$WORK/data/v1/test.jsonl"
if [ "$MODE" = "smoke" ]; then
  RUN="$RUN-smoke"; MAXREC="--max_records 600"; DEV_LIMIT=500; TEST_LIMIT=500
else
  MAXREC=""; [ "$TRAIN_RECORDS" != "0" ] && MAXREC="--max_records $TRAIN_RECORDS"
fi

echo "== train ($MODE) $BASE from $INIT_FROM  lr=$LR epochs=$EPOCHS batch=$BATCH x accum=$ACCUM pair_weight=$PAIR_WEIGHT ordinal=$ORDINAL"
rm -rf "runs/$RUN"
START=$(date +%s)
uv run python -m agent_compass.train.multihead --data "$TRAIN" --out "runs/$RUN" --base "$BASE" --init_from "$INIT_FROM" \
  --epochs "$EPOCHS" --lr "$LR" --batch "$BATCH" --accum "$ACCUM" --max_state "$MAX_STATE" --shared_prefix 1 \
  --pair_weight "$PAIR_WEIGHT" --ordinal "$ORDINAL" ${WEIGHTS:+--weights "$WEIGHTS"} $MAXREC 2>&1 | tee "runs/$RUN.train.log" | tail -25
echo "train wall: $(( $(date +%s) - START ))s"

echo "== evaluate dev/test (all questions), then agent-compass metrics"
BENCH="$AC/scripts/pod/kev_benchmark_long.py"
KEV_DTYPE=bf16 uv run python "$BENCH" --run "runs/$RUN" --data "$DEV"  --device cuda --max_state "$MAX_STATE" --limit "$DEV_LIMIT"  --out "runs/$RUN/eval-dev"  2>&1 | tail -8
KEV_DTYPE=bf16 uv run python "$BENCH" --run "runs/$RUN" --data "$TEST" --device cuda --max_state "$MAX_STATE" --limit "$TEST_LIMIT" --out "runs/$RUN/eval-test" 2>&1 | tail -8
cd "$AC"
uv run --python 3.12 --with numpy python scripts/eval_rows.py --rows "$WORK/kev/runs/$RUN/eval-dev/rows.json"  --records "$DEV"  --out "$WORK/kev/runs/$RUN/metrics_dev.json"
uv run --python 3.12 --with numpy python scripts/eval_rows.py --rows "$WORK/kev/runs/$RUN/eval-test/rows.json" --records "$TEST" --out "$WORK/kev/runs/$RUN/metrics_test.json" \
  --baseline runs/baselines-swe_agent-v0/result.json
echo "== done: $WORK/kev/runs/$RUN"
