# Pod runbook: M0 and M3 v0 on one RunPod H100

Everything below is scripted. The pod is up only while these steps run; total budget about
3 GPU-hours. Data preparation is finished on the CPU box and uploaded to the private HF dataset
repo `azlanmalikai/agent-compass-data` (files `train.jsonl`, `dev.jsonl`, `test.jsonl`, `stats.json`).

## Pod

- Template: RunPod PyTorch image with CUDA 12.x drivers (torch 2.8 + cu128 wheels are installed by `uv sync` inside kev; the image only needs the driver).
- GPU: 1x H100 80GB. Network volume mounted at `/workspace` (persists the HF cache, kev checkout, runs).
- Disk: 60 GB is enough (bases 2B/4B, data 1.4 GB, checkpoints).

## Steps

```bash
export HF_TOKEN=<write token of azlanmalikai>      # read is enough for bases and the data repo
export WORK=/workspace
git clone https://github.com/malakazlan/agent-compass.git $WORK/agent-compass

# 1. M0: environment + kev reproduction (0.8B, ~20 min train + smoke)      -> ~35 min total
bash $WORK/agent-compass/scripts/pod/m0_reproduce_kev.sh 2
#    pass if dev/transfer within +-1 pp of 0.829 / 0.643 (seed 2); see script header

# 2. M3 smoke: 2,000 records, measures tokens/s and memory                  -> ~10 min
bash $WORK/agent-compass/scripts/pod/m3_train_v0.sh smoke
#    read "train wall" and the records count; tokens/s = 2000 x ~2.8k tokens / wall

# 3. M3 full run. Size it from the smoke so training stays near 2 h:
#    TRAIN_RECORDS = 7200 s x tokens/s / 2800 tokens (cap 50002 = all)
TRAIN_RECORDS=<n> bash $WORK/agent-compass/scripts/pod/m3_train_v0.sh full     -> ~2 h + ~25 min eval
```

## What comes back

Copy into the repo and commit (results only, never weights):

```bash
mkdir -p $WORK/agent-compass/runs/m3-v0-2b
cp $WORK/kev/runs/m3-v0-2b/{provenance.json,metrics_dev.json,metrics_test.json} $WORK/agent-compass/runs/m3-v0-2b/
cp $WORK/kev/runs/m3-v0-2b.train.log $WORK/agent-compass/runs/m3-v0-2b/train.log
cp $WORK/kev/runs/m0-q35-08b-s2/{provenance.json,result.json} $WORK/agent-compass/runs/m0-q35-08b-s2/ 2>/dev/null || true
```

Weights: `uv run python -m kev.publish` (kev's publisher) or `hf upload azlanmalikai/agent-compass-2b $WORK/kev/runs/m3-v0-2b/checkpoint` once the numbers are reviewed.

## Pass criteria for M3 v0

Held-out-repository test, p_success, compared to `docs/baselines_v0.md`:

| metric | must beat |
|---|---|
| AUROC, SWE-agent records | 0.667 (GBT) |
| AUROC, OpenHands records | 0.619 (GBT) |
| AUROC at every prefix bucket | the GBT bucket value |
| ECE (15 bins) after kev's fitted temperature | report; target < 0.05 |

If the model does not beat GBT on OpenHands, the next lever is more train records (the full 50k
took a cut for time) or the 4B base, not more epochs.

## Knobs (env vars of m3_train_v0.sh)

`BASE` (default Qwen/Qwen3.5-2B-Base), `LR` (1e-4), `EPOCHS` (1), `BATCH` (2), `ACCUM` (4),
`MAX_STATE` (4352), `TRAIN_RECORDS` (0 = all), `TEST_RECORDS` (20000; 0 = all 64k, ~1 h).
