#!/usr/bin/env bash
# M3 v0: train agent-compass-2b (p_success only) with kev's trainer on our records, on one H100.
#
# Data: the v0 files (data/v0/{train,dev,test}.jsonl, made by scripts/make_v0_split.py) uploaded to
# the private HF dataset repo $DATA_REPO. States are <= 4096 tokens, thoughts removed.
#
# Usage on the pod (after scripts/pod/m0_reproduce_kev.sh has set up $WORK/kev):
#   export HF_TOKEN=...  WORK=/workspace  DATA_REPO=azlanmalikai/agent-compass-data
#   bash m3_train_v0.sh smoke      # ~10 min: 2,000 records, measures tokens/s, checks memory
#   bash m3_train_v0.sh full       # the run; size is set from the smoke throughput (see TRAIN_RECORDS)
#
# Outputs under $WORK/kev/runs/m3-v0-2b/: checkpoint, provenance.json, eval-{dev,test}/rows.json,
# metrics_{dev,test}.json. Copy metrics + provenance back to agent-compass/runs/m3-v0-2b/.

set -euo pipefail
MODE="${1:-smoke}"
WORK="${WORK:-/workspace}"
DATA_REPO="${DATA_REPO:-azlanmalikai/agent-compass-data}"
BASE="${BASE:-Qwen/Qwen3.5-2B-Base}"
RUN="${RUN:-m3-v0-2b}"
TRAIN_RECORDS="${TRAIN_RECORDS:-0}"      # 0 = all of train.jsonl; set after the smoke run so training fits ~2 h
EPOCHS="${EPOCHS:-1}"
LR="${LR:-1e-4}"                          # kev used 1e-4 for 0.8B and 5e-5 for 4B; 2B sits between, start at 1e-4
BATCH="${BATCH:-2}"
ACCUM="${ACCUM:-4}"                       # effective batch 8, as in kev's recipes
MAX_STATE="${MAX_STATE:-4352}"            # our states are <= 4096 tokens; kev drops records that do not fit
export HF_HOME="$WORK/hf"
export UV_NO_SYNC=1 UV_LINK_MODE=copy     # keep the fused-kernel installs from m0_reproduce_kev.sh (uv run would re-sync them away)
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
# Measured on one H100 (2026-10-02), Qwen3.5-2B, 4k states: batch 2 x accum 4 with gradient checkpointing = 0.50 s/record,
# without checkpointing = 0.33 s/record at 51 GB peak; batch 4 or 8 without checkpointing OOMs on the longest records.

cd "$WORK/kev"
AC="$WORK/agent-compass"
[ -d "$AC" ] || git clone https://github.com/malakazlan/agent-compass.git "$AC"
git -C "$AC" pull --quiet

echo "== data"
mkdir -p "$WORK/data/v0"
uv run hf download "$DATA_REPO" --repo-type dataset --local-dir "$WORK/data/v0" >/dev/null
ls -la "$WORK/data/v0"
TRAIN="$WORK/data/v0/train.jsonl"
if [ "$MODE" = "smoke" ]; then
  head -n 2000 "$WORK/data/v0/train.jsonl" > "$WORK/data/v0/train.smoke.jsonl"
  head -n 1000 "$WORK/data/v0/dev.jsonl" > "$WORK/data/v0/dev.smoke.jsonl"
  TRAIN="$WORK/data/v0/train.smoke.jsonl"; DEV="$WORK/data/v0/dev.smoke.jsonl"; TEST="$DEV"; RUN="$RUN-smoke"
else
  if [ "$TRAIN_RECORDS" != "0" ]; then
    head -n "$TRAIN_RECORDS" "$WORK/data/v0/train.jsonl" > "$WORK/data/v0/train.cut.jsonl"
    TRAIN="$WORK/data/v0/train.cut.jsonl"
  fi
  DEV="$WORK/data/v0/dev.jsonl"
  # Full test is 64k records / 169M tokens (~1 h of forward passes at 2B). v0 scores the first
  # TEST_RECORDS (the file is shuffled, so this is a fair mix of both datasets); set 0 for all.
  TEST_RECORDS="${TEST_RECORDS:-20000}"
  if [ "$TEST_RECORDS" != "0" ]; then
    head -n "$TEST_RECORDS" "$WORK/data/v0/test.jsonl" > "$WORK/data/v0/test.cut.jsonl"
    TEST="$WORK/data/v0/test.cut.jsonl"
  else
    TEST="$WORK/data/v0/test.jsonl"
  fi
fi
echo "train: $(wc -l < "$TRAIN") records"

echo "== train ($MODE) $BASE  lr=$LR epochs=$EPOCHS batch=$BATCH x accum=$ACCUM max_state=$MAX_STATE"
rm -rf "runs/$RUN"   # kev.train refuses to overwrite an existing run directory
START=$(date +%s)
uv run python -m kev.train --data "$TRAIN" --base "$BASE" \
  --epochs "$EPOCHS" --lr "$LR" --batch "$BATCH" --accum "$ACCUM" --dtype bf16 --device cuda \
  --max_state "$MAX_STATE" --shared_prefix 1 --length_sort 1 --checkpointing "${CHECKPOINTING:-0}" \
  --p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0 \
  --out "runs/$RUN" 2>&1 | tee "runs/$RUN.train.log" | tail -30
WALL=$(( $(date +%s) - START ))
echo "train wall: ${WALL}s"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv

echo "== evaluate (kev.benchmark with a long-state context -> rows.json), then agent-compass metrics"
# kev.benchmark --data admits only 384-token states; the wrapper runs the same evaluation with our context.
# KEV_DTYPE=bf16: kev's bf16 scoring path (probabilities within ~0.01 of fp32, several times faster).
DEV_LIMIT="${DEV_LIMIT:-0}"; TEST_LIMIT="${TEST_LIMIT:-0}"
BENCH="$AC/scripts/pod/kev_benchmark_long.py"
KEV_DTYPE="${KEV_DTYPE:-bf16}" uv run python "$BENCH" --run "runs/$RUN" --data "$DEV"  --device cuda --max_state "$MAX_STATE" --limit "$DEV_LIMIT"  --out "runs/$RUN/eval-dev"  2>&1 | tail -12
KEV_DTYPE="${KEV_DTYPE:-bf16}" uv run python "$BENCH" --run "runs/$RUN" --data "$TEST" --device cuda --max_state "$MAX_STATE" --limit "$TEST_LIMIT" --out "runs/$RUN/eval-test" 2>&1 | tail -12
cd "$AC"
uv run --python 3.12 --with numpy python scripts/eval_rows.py --rows "$WORK/kev/runs/$RUN/eval-dev/rows.json"  --records "$DEV"  --out "$WORK/kev/runs/$RUN/metrics_dev.json"
uv run --python 3.12 --with numpy python scripts/eval_rows.py --rows "$WORK/kev/runs/$RUN/eval-test/rows.json" --records "$TEST" --out "$WORK/kev/runs/$RUN/metrics_test.json" \
  --baseline runs/baselines-swe_agent-v0/result.json
echo "== done: $WORK/kev/runs/$RUN  (train ${WALL}s)"
