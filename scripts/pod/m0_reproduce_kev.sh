#!/usr/bin/env bash
# M0 gate: reproduce kev's q35-08b run (Qwen3.5-0.8B-Base on decision-v7) on a RunPod H100.
#
# Pass criterion (docs/plan.md): dev accuracy and transfer-v4 dev accuracy within +-1 pp of
# kev's leaderboard rows for q35-08b (dev 0.817-0.829, transfer 0.622-0.643 over seeds 0-2).
#
# Usage on a fresh pod (Ubuntu image with CUDA 12.x drivers, one H100 80GB):
#   export HF_TOKEN=...            # read access is enough for the public bases and suites
#   export WORK=/workspace         # a network volume so downloads and runs survive the pod
#   bash m0_reproduce_kev.sh [SEED]
#
# Everything lands under $WORK/kev/runs/m0-q35-08b-s<SEED>/ ; copy result.json + provenance.json
# back into agent-compass/runs/ afterwards (scripts/pod/pull_results.sh).

set -euo pipefail
SEED="${1:-2}"
WORK="${WORK:-/workspace}"
KEV_REPO="${KEV_REPO:-https://github.com/malakazlan/kev.git}"
KEV_REF="${KEV_REF:-main}"

echo "== system"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
python3 --version || true

echo "== uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version

echo "== kev checkout"
mkdir -p "$WORK"
cd "$WORK"
if [ ! -d kev ]; then
  git clone "$KEV_REPO" kev
fi
cd kev
git fetch --all --tags --quiet
git checkout --quiet "$KEV_REF"
git rev-parse HEAD

echo "== python env (Python 3.13 per kev's .python-version; torch cu128 wheels)"
export UV_LINK_MODE=copy
uv sync --quiet
# Fused Gated-DeltaNet kernels for the Qwen3.5 hybrid backbones, exactly as kev's Modal image installs them
# (modal_app.py). Without them transformers falls back to a pure-PyTorch causal conv that OOMs an 80 GB H100
# on the 0.8B run. `uv run` would re-sync the venv to uv.lock and revert triton to 3.4, so every later
# command runs with UV_NO_SYNC=1.
uv pip install -q "flash-linear-attention==0.5.2" "triton>=3.7.1"
uv pip install -q --no-deps "https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.7.0/causal_conv1d-1.7.0%2Bcu12torch2.8cxx11abiTRUE-cp313-cp313-linux_x86_64.whl"
export UV_NO_SYNC=1
uv run python -c "import torch, transformers, peft, fla, triton, causal_conv1d; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), torch.cuda.get_device_name(0)); print('transformers', transformers.__version__, 'peft', peft.__version__, 'fla', fla.__version__, 'triton', triton.__version__)"

echo "== HF cache on the volume"
export HF_HOME="$WORK/hf"
mkdir -p "$HF_HOME"
if [ -n "${HF_TOKEN:-}" ]; then uv run hf auth login --token "$HF_TOKEN" >/dev/null 2>&1 || true; fi

echo "== smoke (~1 min): pipeline runs end to end on this GPU"
uv run python -m kev.train --n_per_source 40 --accum 4 --device cuda --out "runs/m0-smoke" 2>&1 | tail -5

echo "== q35-08b reproduction, seed $SEED (~20 min on one H100)"
rm -rf "runs/m0-q35-08b-s$SEED"   # kev.train refuses to overwrite an existing run directory
START=$(date +%s)
uv run python -m kev.train --suite evals/v7/decision-v7 \
  --base Qwen/Qwen3.5-0.8B-Base --base_revision dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68 \
  --epochs 2 --lr 1e-4 --batch 8 --dtype bf16 --p_none_pair 0.25 --seed "$SEED" --device cuda \
  --out "runs/m0-q35-08b-s$SEED" 2>&1 | tail -20
echo "train wall: $(( $(date +%s) - START ))s"

echo "== in-distribution dev (decision-v7) and out-of-domain dev (transfer-v4)"
uv run python -m kev.benchmark --run "runs/m0-q35-08b-s$SEED" --suite evals/v7/decision-v7 --device cuda --out "runs/m0-q35-08b-s$SEED/eval-id" 2>&1 | tail -8
uv run python -m kev.benchmark --run "runs/m0-q35-08b-s$SEED" --suite evals/v4/transfer-v4 --device cuda --out "runs/m0-q35-08b-s$SEED/eval-ood" 2>&1 | tail -8

echo "== reference (kev leaderboard, runs/leaderboard.md rows q35-08b): dev 0.817 / 0.820 / 0.829, transfer 0.622 / 0.634 / 0.643 for seeds 0/1/2"
echo "== done: $WORK/kev/runs/m0-q35-08b-s$SEED"
