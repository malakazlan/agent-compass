# M3 report: agent-compass-2b v0 (p_success only)

Trained 2026-10-02 on one RunPod H100. Script `scripts/pod/m3_train_v0.sh full`; result files in
`runs/m3-v0-2b/` (training config and metrics, kev evaluation reports, our metrics, calibration);
adapter at `azlanmalikai/agent-compass-2b` (private until release).

## Setup

| item | value |
|---|---|
| base | Qwen/Qwen3.5-2B-Base (hybrid Gated-DeltaNet), LoRA r=16 alpha=32 on attention, MLP and DeltaNet projections, 17.9M trainable params |
| trainer | kev.train with `--data`, kev pointer head (noul as 2-option pointer), cross-entropy only, `--shared_prefix 1`, `--max_state 4352` |
| data | v0 files: 21,000 of the 49,987 train records (first 21k of the shuffled file; 32.5% positive), states <= 4,096 tokens, thoughts removed, policy name shown with 30% dropout |
| optimisation | 1 epoch, lr 1e-4 OneCycle, batch 2 x accumulation 4, bf16, no gradient checkpointing |
| cost | training 7,814 s (2 h 10 min) at 0.37 s/record, peak about 60 GB; dev eval 5k records 9.5 min, test eval 20k records 35 min (bf16 scoring, 0.11 s/record) |
| evaluation | dev: first 5,000 of the 9,999 dev records; test: first 20,000 of the 64,124 held-out-repository test records; both files are shuffled mixes of the two datasets |
| calibration | one scalar temperature fitted on the 5k dev records by NLL: T = 1.595, applied to test |

## Results, held-out repositories (test, 20,000 records, 35.5% positive)

| metric | v0 raw | v0 calibrated | GBT baseline |
|---|---|---|---|
| AUROC pooled [95% CI, task bootstrap] | 0.775 [0.746, 0.800] | 0.775 | |
| AUROC, SWE-agent records (n=10,4xx) | 0.707 | 0.707 | **0.667** [0.640, 0.698] |
| AUROC, OpenHands records | 0.722 | 0.722 | **0.619** [0.593, 0.649] |
| Brier | 0.194 | 0.184 | 0.146 (SWE-agent) / 0.239 (OpenHands) |
| ECE (15 bins) | 0.107 | **0.048** | 0.020 / 0.045 |
| confident-error rate at p >= 0.9 | 0.040 | **0.007** | 0.018 / 0.000 |
| abort recall at 5% / 10% / 25% FPR | 0.301 / 0.423 / 0.637 | same (threshold-free) | 0.21 / - / 0.49 (SWE-agent); 0.15 / - / 0.42 (OpenHands) |

AUROC by prefix position, test, versus GBT in parentheses:

| records | 0-25% | 25-50% | 50-75% | 75-100% |
|---|---|---|---|---|
| SWE-agent | 0.666 (0.620) | 0.691 (0.638) | 0.708 (0.673) | 0.734 (0.707) |
| OpenHands | 0.712 (0.564) | 0.715 (0.610) | 0.705 (0.609) | 0.739 (0.652) |

Dev (5,000 records, same repositories as train): AUROC 0.758 [0.728, 0.785]; SWE-agent 0.754, OpenHands 0.651.

## Reading

- **Gate versus heuristics: passed.** The model beats the strongest non-LM baseline at every prefix
  position on both datasets. The margin is largest on the strong OpenHands policy (+10 points
  overall, +15 at the earliest quarter), where loop and error counts carry no signal and only
  reading the content helps.
- **Early states are informative.** At 0 to 25% of the run the model is already at 0.67 to 0.71,
  which is the regime early-abort and escalation decisions need.
- **Calibration after one temperature is usable**: ECE 0.048 and 0.7% confident errors. Per
  dataset: ECE 0.063 (SWE-agent) and 0.045 (OpenHands). Brier is worse than the GBT's on SWE-agent
  because the GBT is fitted on that dataset's 17% base rate while the model sees a 32.5% mix;
  AUROC, which is base-rate free, is the fair comparison here.
- **Dev versus test on OpenHands is reversed** (0.651 dev, 0.722 test). The dev subsample has 5,000
  records from about 1,100 trajectories; the test set is 4x larger. Treat the test number as the
  estimate and the dev number as noisy.
- **Not yet compared**: zero-shot instruct-model logprobs and a large-model judge (plan 6.3). Both
  need either a served model or API spend; they are the remaining M3 baselines.
- **What was left on the table**: 29k train records unused (time budget), 1 epoch, no pairwise
  loss, no multi-head. These are the M4 knobs.

## Offline best-of-N trajectory selection (zero cost, CPU)

`scripts/offline_best_of_n.py` on the 20k scored test records: for each task with at least two
scored runs, pick the run with the highest p_success at its latest scored prefix (<= 90% of the
run, so the final patch and submission are never seen). Resolve rate of the picked run versus
picking at random (the task's mean pass rate), picking the shortest run, and an oracle. CIs are
task-level bootstraps of the lift (model pick minus random pick).

| test tasks with >= 2 runs | tasks | runs/task | random pick | shortest run | **model pick** | oracle | lift [95% CI] |
|---|---|---|---|---|---|---|---|
| all | 765 | 14.6 | 0.354 | 0.307 | **0.427** | 0.518 | +7.4 pp [5.5, 9.0] |
| SWE-agent (weak policies) | 264 | 21.5 | 0.162 | 0.072 | **0.277** | 0.367 | +11.5 pp [8.2, 14.9] |
| OpenHands (strong policy) | 501 | 11.0 | 0.455 | 0.431 | **0.507** | 0.597 | +5.2 pp [3.1, 7.1] |
| mixed-outcome tasks only | 248 | 23.0 | 0.495 | 0.351 | **0.722** | 1.000 | +22.7 pp [17.5, 27.7] |

Best-of-N curve (random subsets of N runs per task, 200 draws), model pick versus random pick:

| N | all | SWE-agent | OpenHands |
|---|---|---|---|
| 1 | 0.354 / 0.354 | 0.162 / 0.162 | 0.456 / 0.456 |
| 2 | 0.381 / 0.354 | 0.203 / 0.162 | 0.475 / 0.454 |
| 4 | 0.412 / 0.358 | 0.250 / 0.168 | 0.495 / 0.456 |
| 8 | 0.429 / 0.343 | 0.332 / 0.198 | 0.496 / 0.441 |

Reading: on the weak SWE-agent policies, picking among 8 runs with the model takes the resolve rate
from 16% to 33%, close to doubling it; on the strong OpenHands policy the gain is +5 points. The
"shortest run" heuristic is worse than random on both. Where selection can matter at all (tasks
with both passing and failing runs) the model recovers 72% of what an oracle would. This is an
offline, held-out-repository estimate of the M5 trajectory-selection experiment; the online version
with freshly sampled runs is still to be done.

## Pod cost of the whole session (M0 + M3)

About 5 h 10 min of one H100 including environment build, kev reproduction (M0), the OOM detour
before the fused kernels were installed, three throughput probes, the smoke run, training,
evaluation and the adapter upload. Scripts now encode everything learned; a repeat of M3 alone
would take about 3 h.
