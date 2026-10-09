# M4 report: agent-compass-2b v1 (six heads)

Trained 2026-10-09 on one RunPod H100 (pod session 2). Script `scripts/pod/m4_train_v1.sh full`;
trainer `agent_compass.train.multihead`; result files in `runs/m4-v1-2b/`.

## Setup

| item | value |
|---|---|
| base and adapter | Qwen/Qwen3.5-2B-Base, LoRA r=16 (17.9M params), **warm-started from v0** (adapter and pointer head) |
| questions | p_success, stuck, escalate (noul); progress, steps_left (score, cumulative-link loss + 0.5 CE); best_next (choice, K<=4) |
| losses | per-question weights 1.0 / 0.5 / 0.25 / 0.5 / 0.5 / 0.5; pairwise Bradley-Terry on p_success for same-task success/failure prefix pairs, weight 0.25 |
| data | v1: 27,000 train records (12k trajectories x 2 prefixes from SWE-agent and OpenHands, plus 3,000 tau2-bench tool-use records), 3,360 pairs; dev 11,005 (5k eval); SWE test 64,124 (20k eval); tau2 test 3,832 |
| optimisation | 1 epoch, lr 5e-5 OneCycle, batch 1 x accumulation 8, bf16, no checkpointing, 2,955 optimizer steps |
| throughput | 0.28 s/record with six branches (shared state), peak about 72 GB |
| label caveat | the stuck training labels use the rule as of the run start (judge precision 58%); test scoring for stuck uses the refined rule (`docs/label_qa_findings.md`) |

## Results, held-out repositories (SWE test, 20,000 records; same records as v0)

Per-question temperatures fitted on 5,000 dev records (`runs/m4-v1-2b/calibration.json`).

| question | metric | v1 raw | v1 calibrated (T) | reference |
|---|---|---|---|---|
| p_success | AUROC [95% CI] | 0.761 [0.735, 0.791] | same | v0 0.775; GBT 0.667 (SWE-agent) / 0.619 (OpenHands); zero-shot Qwen3.5-2B 0.679 on a 5k subset |
| p_success | by agent: SWE-agent / OpenHands | 0.717 / 0.678 | | v0 0.707 / 0.722 |
| p_success | ECE / confident-error at 0.9 | 0.121 / 0.081 | 0.069 / 0.000 (T 3.02) | v0 calibrated 0.048 / 0.007 |
| stuck (old-rule labels) | AUROC / accuracy / ECE | 0.973 / 0.961 / 0.008 | (T 1.04) | positive rate 11.0% |
| stuck (**refined** labels) | AUROC / accuracy / ECE | 0.973 / 0.950 / 0.049 | | positive rate 5.8%; trained on the old rule, so it over-flags |
| progress (4 levels) | accuracy / within-one / MAE | 0.937 / 0.990 / 0.106 | unchanged (T 1.01) | rule labels; same against refined labels |
| steps_left (4 bins, successes) | accuracy / within-one / MAE | 0.711 / 0.981 / 0.374 | unchanged (T 0.97) | n = 7,198 |
| best_next (K about 4) | top-1 | 0.483 | NLL 1.180 -> 1.174 (T 1.19) | chance 0.255; n = 3,815 sets |
| escalate | AUROC / ECE | 0.670 / 0.228 | ECE 0.073 (T 3.13) | label nearly redundant with p_success (`docs/label_qa_findings.md`) |

Dev (5,000 records incl. tau2): p_success AUROC 0.675 pooled; tau2 0.774, SWE-agent 0.703, OpenHands 0.561
(the OpenHands dev subsample has been noisy for v0 as well: 0.651 dev versus 0.722 test).

**Second domain, tau2-bench tool-use agents** (test_tau2: 3,832 records, 840 runs of 30 policy models on
held-out tasks; only 3,000 tau2 records were in training). Raw, no calibration:

| question | tau2 test |
|---|---|
| p_success AUROC [95% CI] | **0.780** [0.726, 0.832], by prefix 0.770 / 0.779 / 0.762 / 0.798; ECE 0.017; abort recall 36.6% at 5% FPR |
| stuck AUROC | 0.993 (positive rate 2.2%) |
| escalate AUROC | 0.902 |
| best_next top-1 (K about 4) | 0.633 (chance 0.25) |
| steps_left accuracy / within-one | 0.773 / 0.994 |

The model transfers to a second domain with a small slice of in-domain training data: p_success on
tool-use runs is as good as on coding runs, and best_next is markedly easier there (30 policies
attempt each task, so candidate sets contrast many agents at the same turn). Progress is not scored
on tau2 (its rule is coding-specific and was masked).

## Reading

- **The five new heads work.** stuck is near-perfect against its rule labels (AUROC 0.97) and still
  0.97 against the refined rule it was not trained on; progress and steps_left are within one level
  99% and 98% of the time; best_next picks the successful run's action 48% of the time among about
  four candidates (chance 26%).
- **p_success did not improve and slipped on the strong agent.** Pooled 0.761 versus v0's 0.775
  (overlapping CIs); SWE-agent up 1 point, OpenHands down 4.4 points. Three differences from v0 are
  candidates: six branches sharing the backbone (multi-task interference), 2 prefixes per trajectory
  from 12k trajectories instead of 5 from 4.5k, and 3k tool-use records in the mix. Separating them is
  the first ablation to run on cached features; it was not affordable in this session.
- **Calibration**: v1's p_success logits are sharper (T = 3.0 versus 1.6 for v0); after scaling, ECE 0.069
  and no confident errors.
- **Recommendation for release**: v0 for p_success (and abort decisions), v1 for stuck, progress,
  steps_left and best_next. Both adapters share the base and the server can load both; the cost is a
  second forward pass only when both are asked.

## Offline simulations with v1 p_success

Within-policy best-of-N (same protocol as `docs/m3_report.md`):

| agent | random pick | v1 pick | lift [95% CI] | v0 lift |
|---|---|---|---|---|
| Llama-70B, SWE-agent (306 groups) | 0.123 | **0.173** | +5.0 pp [2.6, 7.7] | +3.0 pp |
| Qwen3-Coder-480B, OpenHands (579 groups) | 0.531 | 0.547 | +1.6 pp [-0.0, 3.3] | +2.0 pp |

The pairwise loss did what it was meant to on the weak agent (selection lift up from 3 to 5 points);
on the strong agent it is unchanged within noise.

Early abort with per-agent thresholds: **v1 is worse than v0.** At a 5% false-positive budget it
catches 2.7% of Llama-70B failures and 2.2% of Qwen failures (v0: 17.8% and 12.8%). Its ranking
quality (AUROC) is comparable, but the low-probability tail that early abort relies on is less
separated. v0 stays the abort model.

## Pod cost

Session 2 (zero-shot baseline, M4 smoke, M4 full with evaluation, tau2 evaluation, uploads):
about 4 h 40 min of one H100. The 20k-record test evaluation took 75 minutes because six branches
cost 2x per record; next time, evaluate the extra heads on a 5k subset and only p_success on 20k.
