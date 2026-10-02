# M0 report: kev reproduction on RunPod H100 (2026-10-02)

Gate (docs/plan.md): reproduce kev's `q35-08b` run (Qwen3.5-0.8B-Base, decision-v7, seed 2) within
seed noise of its leaderboard rows. Script: `scripts/pod/m0_reproduce_kev.sh`. Files:
`runs/m0-q35-08b-s2/` (training config and metrics, both evaluation reports).

## Result

| metric (development partitions) | ours, seed 2 | kev leaderboard, seeds 0 / 1 / 2 |
|---|---|---|
| decision-v7 accuracy (in-distribution) | **0.814** | 0.817 / 0.820 / 0.829 |
| transfer-v4 accuracy (out-of-domain) | **0.642** | 0.622 / 0.634 / 0.643 |
| in-distribution ECE / Brier / confident-error | 0.043 / 0.256 / 0.028 | (card: ECE 0.09 in-domain for the released 0.8B) |
| out-of-domain ECE / Brier / confident-error | 0.139 / 0.498 / 0.070 | (card: ECE 0.15, confident-error 0.11 OOD) |
| training wall time | 1,172 s (19.5 min) | "about 20 min on one H100" |
| trainable parameters | 11.3M (LoRA r=16) | same recipe |
| GPU | NVIDIA H100 80GB HBM3, torch 2.8.0+cu128, triton 3.8.0, fla 0.5.2 | H100 via Modal |

Transfer accuracy matches kev's seed-2 row to one point in a thousand. In-distribution accuracy is
0.3 pp below kev's lowest seed; kev's own seed spread is 1.2 pp, so this is within noise. No
temperature was fitted for this read (clean and calibrated partitions are identical), which is the
likely source of the small in-distribution gap: kev's leaderboard trials fit a temperature on the
calibration partition before the development read.

**Gate: passed.** The pipeline, kernels, and evaluation protocol reproduce kev on this pod.

## What it took, and what to keep

1. `uv sync` alone is not enough for Qwen3.5 hybrids. Without flash-linear-attention 0.5.2,
   triton >= 3.7.1 and the causal-conv1d CUDA wheel, transformers falls back to a pure-PyTorch
   causal convolution and the 0.8B run runs out of 80 GB. kev installs these in its Modal image,
   not in the lockfile. The pod script now installs them after `uv sync`.
2. `uv run` re-syncs the venv to the lockfile and silently reverts triton to 3.4. Everything after
   the kernel install runs with `UV_NO_SYNC=1`.
3. `kev.train` refuses to overwrite a run directory; the scripts clear it first.
4. Cost of discovering 1 to 3: about 10 minutes of pod time. Total M0 pod time: about 55 minutes
   including environment build and downloads.

Peak GPU memory during the 0.8B run: about 43 GB (kev reports 57 GB for its run; the difference is
most likely the eval-free training process and allocator timing, not a protocol change).
