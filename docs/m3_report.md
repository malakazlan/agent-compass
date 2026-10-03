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

`scripts/offline_best_of_n.py` on the 20k scored test records: for each group with at least two
scored runs, pick the run with the highest p_success at its latest scored prefix (<= 90% of the
run, so the final patch and submission are never seen). Compared with picking at random (the
group's mean pass rate), the shortest run, "a run from the strongest policy model present", and an
oracle. CIs are task-level bootstraps of the lift.

**A first version of this analysis grouped runs by task only and reported +7.4 pp overall and
+11.5 pp on SWE-agent. That number was inflated**: 123 of the 767 test tasks exist in both datasets
(the same GitHub issue attempted by the Llama policies under SWE-agent and by Qwen3-Coder-480B
under OpenHands), and 158 tasks mix policies. On those, "pick the strongest policy's run" scores
0.624 and the model 0.620: the model was recognising the stronger agent, not a better run. The
honest, deployment-realistic setting is selecting among N runs of ONE agent on one task:

| within-policy groups (task x policy, >= 2 runs) | groups | runs/group | random pick | shortest | **model pick** | oracle | lift [95% CI] |
|---|---|---|---|---|---|---|---|
| Qwen3-Coder-480B (OpenHands) | 580 | 9.1 | 0.525 | 0.526 | **0.545** | 0.640 | +2.0 pp [0.2, 3.9] |
| Llama-3.1-70B fine-tune (SWE-agent) | 308 | 17.9 | 0.122 | 0.091 | **0.153** | 0.273 | +3.0 pp [0.7, 5.3] |
| Llama-3.1-8B fine-tune (SWE-agent) | 52 | 6.4 | 0.090 | 0.096 | 0.096 | 0.212 | +0.6 pp [-3.4, 5.1] |

Best-of-N within policy (random subsets of N runs, 200 draws), model pick / random pick:
Qwen 480B: N=2 0.533 / 0.525, N=4 0.540 / 0.525, N=8 0.528 / 0.514.
Llama 70B: N=2 0.134 / 0.122, N=4 0.143 / 0.126, N=8 0.171 / 0.152.

Reading: the v0 model gives a real but modest selection gain when the policy is fixed, +2 to +3
points absolute (a 25% relative gain for the weak 70B agent), significant at 95% for both main
policies. The large cross-policy number is not a model achievement and is not claimed. The
"shortest run" heuristic is at or below random.

**The ranking quality itself does not come from the policy cue.** AUROC within a single policy on
the same test rows: Qwen 480B 0.722 [0.689, 0.763] (n=9,738), Llama-70B 0.703 [0.649, 0.755]
(n=9,577); records whose state hid the policy name score 0.779 versus 0.773 when shown. The gap
between a 0.70 AUROC across states and a +2 to +3 pp selection lift within a task is expected:
within one task the runs share most of their difficulty, so the model has to separate runs on
finer evidence than it needs across tasks. This is the first target for M4 (pairwise loss on
same-task pairs is designed for exactly this), and the online M5 experiment remains to be done.

## Pod cost of the whole session (M0 + M3)

About 5 h 10 min of one H100 including environment build, kev reproduction (M0), the OOM detour
before the fused kernels were installed, three throughput probes, the smoke run, training,
evaluation and the adapter upload. Scripts now encode everything learned; a repeat of M3 alone
would take about 3 h.
