# agent-compass: plan

Written 2026-09-25 after the study phase. Sources: `docs/kev_study.md`, `docs/related_work.md`,
`docs/data_audit.md`, `docs/data_audit_secondary.md`. Every number below is quoted from those docs
and carries its citation there; nothing here has been measured by us yet.

## 1. What we are building

A small decoder model (Qwen base + LoRA) that reads an agent's task and a compressed trajectory
prefix and, in one prefill pass with no generation, returns calibrated probabilities for six typed
questions: `p_success`, `progress`, `stuck`, `best_next`, `escalate`, `steps_left`. The request and
response shape is the TypeSafe System One contract (`noul` / `choice` / `score`), so the model is a
drop-in for any client that already speaks that API. First domain: software-engineering agents on
SWE-bench-style tasks. Deliverables: two model tiers, a benchmark, an SDK with agent hooks, a report.

The competitive gap is concrete. Jev (closed), kev, Laya and meraGPT Decider are general
typed-decision models trained on synthetic or teacher-labelled questions. None is trained on real
agent trajectories with ground-truth outcomes, none ranks candidate actions from branching runs, and
none reports a measured lift inside a running agent. Nebius, SWE-Gym, R2E-Gym and DeepSWE have
trajectory-level critics, but they are 14B to 70B, need a generated YES/NO token, and released no
calibration numbers. Fail-Fast has the right size (0.6B) and the right task (early abort) but
released no code, weights or data.

## 2. What the study changed in the brief

| CLAUDE.md said | Study found | Change |
|---|---|---|
| kev is MPS-tested, CUDA untested | Every released kev checkpoint was trained on H100/H200 via Modal in bf16; CUDA graphs, fused Qwen3.5 kernels and CUDA-only tests exist | M0 shrinks to "pipeline runs on RunPod and reproduces one small kev run" |
| M0 gate: kev-0.5b reproduces its README numbers | README has no 0.5b numbers; the card's command omits `--base`; augmentation is re-seeded per epoch | M0 gate becomes the `q35-08b` run (Qwen3.5-0.8B, decision-v7, about 20 min on one H100, transfer 0.643 at seed 2) within seed noise of plus or minus 1 pp |
| Backbone about 1.5B and about 4B | No Qwen3.5 1.5B exists. Bases on HF today: Qwen3.5-0.8B/2B/4B/9B-Base (hybrid Gated-DeltaNet), Qwen3-0.6B/1.7B/4B-Base (pure attention) | Fast tier Qwen3.5-2B-Base, quality tier Qwen3.5-4B-Base, Qwen3-1.7B-Base as the pure-attention ablation |
| kev's packed block-causal mask is the shared-state mechanism | The packed mask is only valid on pure-attention bases; on Qwen3.5 hybrids kev runs one causal row per question with a shared-prefix cache (`--shared_prefix 1`) | Both paths kept behind one flag; the backbone ablation decides |
| History budget 8k default, 16k tested | kev trains on states of at most 384 tokens (7.5k max ever tried), and each question branch has a fixed 640-token budget | Long context is new work, not a port. v0 uses a 4k budget like Fail-Fast; 8k and 16k are M4 ablations after a memory probe |
| Ordinal head, per-head temperature, pairwise loss | kev has only a pointer head; noul is a 2-option pointer, score is a plain softmax; one scalar temperature; CE only (every extra loss was a recorded negative) | Add these as new code with the pointer-for-everything setup as the ablation control |
| Five SWE datasets | CoderForge-Preview has no licence on card or README; Multi-SWE-bench python is exactly SWE-bench Verified; AgentLens-Bench is unreleased (URL 404 on 2026-09-25) | CoderForge needs an owner decision; Multi-SWE python and AgentLens are excluded from training |
| Fail-Fast: reproduce their setup | Their monitor, data and weights are unreleased; data was 11 seeds of Qwen3.6-27B on SWE-bench Verified | Reproduce the recipe on our data, not their numbers |
| Metrics: AUROC, ECE, Brier, confident-error | Fail-Fast reports recall / precision / fired / saved at a fixed false-positive rate with a 20-step floor and none of ours | Report both families so the two lines of work are comparable |

## 3. Design decisions

1. **Codebase.** Start from kev (Apache-2.0, fork at `malakazlan/kev`). Keep its encode, mask,
   rows, checkpoint, metrics, benchmark and serving skeleton. Replace the readout, losses, data
   builders and context limits. Package name `agent_compass`.
2. **Backbones.** Qwen3.5-2B-Base (fast) and Qwen3.5-4B-Base (quality), LoRA r=16 alpha=32 on
   attention, MLP and DeltaNet projections, bf16. kev's own leaderboard shows Qwen3.5 beating Qwen3
   by about 7 pp on locked transfer at 4B and 9B, and hybrid layers are linear in sequence length,
   which matters at 8k to 16k. Qwen3-1.7B-Base is the pure-attention ablation.
3. **Input packing.** kev's layout with existing Qwen tokens: state, then one branch per question,
   branch positions restart after the state. State text is built by our converter: task, compressed
   older steps one line each, last N steps in full, optional `policy:` field (30% dropout to
   "unknown"). Default view removes thoughts. Evidence: R2E-Gym's verifier over-attends to thoughts
   and loses 5 pp of Best@26 without them, and Nebius reports value hacking in regression critics.
4. **Heads.** Pointer head for `best_next`. Binary readout on the decide token for the three noul
   questions. CORAL-style ordinal head for `progress` and `steps_left`. Per-question temperature
   fitted on the dev split at the same prefix-point distribution we serve. Conformal thresholds for
   `escalate`.
5. **Losses.** Cross-entropy or BCE per question, masked when the label is missing. Pairwise
   Bradley-Terry loss on success-versus-failure prefixes matched by step-fraction decile, as in
   Fail-Fast (lambda 0.25). Permutation KL for option order. TD-style consistency between
   consecutive prefixes is an M4 ablation.
6. **Labels.** `p_success` from the final outcome plus a per-task baseline and advantage;
   `best_next` from multiple runs of the same task sharing a similar prefix, hard negatives from
   failed runs otherwise; `stuck` rule-based, validated on 200 samples; `progress` from observation
   signals; `escalate` derived; `steps_left` from successful runs only.
7. **Data for M1.** `nebius/SWE-rebench-openhands-trajectories` (67k, OpenHands, 48% resolved,
   about 11 runs per task) and `nebius/SWE-agent-trajectories` (80k, SWE-agent, 17% resolved, 22
   runs per task). Both CC-BY-4.0, real issues, separate eval-log columns, zero overlap with
   SWE-bench Verified ids. They give the held-out-scaffold and held-out-policy splits immediately.
   Scale-up with `nvidia/Open-SWE-Traces` (368k labelled) and `SWE-bench/SWE-smith-trajectories`.
8. **Splits.** One global repo-hash table shared by every dataset (sha1 of `owner/repo`, 80/10/10).
   The 12 SWE-bench Verified repos and any Verified instance id go to a holdout bucket never used
   for training or tuning. Duplicate trajectories across datasets are removed by content hash.
9. **Baselines.** Step count, error count, repetition heuristics; zero-shot small instruct model
   logprobs; AgentStop-style gradient-boosted trees; a hidden-state linear probe; the Fail-Fast
   recipe on Qwen3-0.6B; a large-model judge on a small sample.
10. **Evaluation.** AUROC, accuracy, ECE, Brier and confident-error at 25/50/75/90% prefixes and
    per step; recall / precision / fired / saved at 5, 10 and 25% false-positive rate with a 20-step
    floor; top-1 and NDCG for `best_next`; latency p50/p95. Also the `agent_trace_observability`
    workflow of `LocalLLaMA/typed-decisions` as a public generality check next to Jev and Laya.
11. **Serving.** kev's `/v1/systemone` server with request batching. kev reaches 18 ms for six
    questions on 253-token inputs on an H100 with CUDA graphs; our states are 15 to 60 times longer
    and above the graph bank limit, so expect the eager path. Prefix caching across steps of one
    trajectory is the main latency lever.

## 4. Risks

1. **Small critics.** Nebius found 7B and 8B critics underperform 70B; kev-0.8B trails kev-4B by
   about 17 pp on new sources. The 4B tier is the primary result; the 2B tier is reported with
   deltas. Mitigation: pairwise and TD losses, thoughts removed, policy token.
2. **Long context memory and quality.** Untested in kev beyond 7.5k state tokens. Mitigation: memory
   probe first, 4k budget for v0, `--row_budget` and length sorting, shared-prefix path on hybrids.
3. **Training budget.** 147k trajectories times several prefixes at 4k tokens is far more than 1 to 2
   H100-hours. The smoke test measures tokens per second and the v0 run is sized to the budget from
   that, by subsampling prefixes, not by shortening the budget.
4. **Best-next coverage.** Branching data exists (28% of SWE-rebench tasks have mixed outcomes) but
   "similar prefix" matching across runs is untested. Coverage is documented in the label QA report;
   hard negatives fill the gaps.
5. **Label noise in `stuck` and `progress`.** Rule-based labels are validated on samples before
   training; AgentLens would have been the natural test set but is unreleased.
6. **Environment.** kev needs Linux and Python 3.12 or 3.13, torch 2.8, transformers 5.17, and
   flash-linear-attention 0.5.2 plus triton for the hybrid path. The RunPod image must match. This
   Windows box only does data prep and static work.
7. **Leakage across datasets.** SWE-rebench tasks recur in five datasets. The global repo hash keeps
   them on one side; the converter asserts zero Verified overlap on every build.

## 5. Open questions for the owner

1. **CoderForge-Preview** (258k trajectories, best branching data) has no stated licence. Use it
   for internal experiments only, ask Together for clarification, or skip it?
2. **Backbone:** agree with Qwen3.5-2B and 4B as the two tiers, with Qwen3-1.7B as the ablation?
3. **`best_next` budget:** how many candidates (K=4 or 8) and how many tokens per candidate? This
   sets the branch budget and the online best-of-N cost.
4. **Regenerating Fail-Fast-style data** (multi-seed mini-swe-agent runs on SWE-bench Verified with
   an open policy on the pod) costs GPU hours but gives the only clean apples-to-apples comparison.
   Do it in M5 or skip?
5. **Secondary-domain licences:** the tau-bench trajectory sets from AgentSuite carry no stated licence; the Apache-2.0 alternatives are smaller. Same question as CoderForge.
6. **Public repo licence:** Apache-2.0 is required for the code we take from kev. Confirm for the
   whole repo.

## 6. First 10 tasks

1. Repo skeleton: `agent_compass/` package, `configs/`, `scripts/`, `tests/`, `pyproject.toml`
   (Python 3.12, pinned torch/transformers/peft as in kev), CI smoke test, run-directory convention
   `runs/<run_id>/{config.yaml, provenance.json, result.json}`.
2. RunPod setup script: image, `uv sync`, HF login from a secret, dataset cache on the volume, one
   command that runs kev's smoke suite on the GPU.
3. **M0 gate:** reproduce kev `q35-08b` (Qwen3.5-0.8B, decision-v7) on RunPod; log dev and transfer
   accuracy with seed; pass if within plus or minus 1 pp of 0.643 transfer.
4. Unified schema (`agent_compass/data/schema.py`) and converters for the two nebius datasets,
   producing `traj_id, task_id, domain, scaffold, policy_model, repo, task, steps[], outcome, meta`.
   Assert zero SWE-bench Verified overlap. Write per-dataset stats to `docs/data_audit.md`.
5. Global repo-hash split table, Verified holdout bucket, cross-dataset dedupe, and split files under
   `data/splits/` with counts committed to docs.
6. State builder: thoughts-removed and thoughts-on views, history compression, leak stripping,
   `policy:` field with dropout, token-budget enforcement at 4k and 8k. Unit tests on fixed samples.
7. Labels: `p_success` with per-task baseline and advantage, `steps_left`, rule-based `stuck` and
   `progress`, derived `escalate`, `best_next` candidate sets from branching runs. Label QA report
   with 200-sample checks (M2 gate).
8. Prefix sampler and kev-shaped JSONL export; smoke-train the 0.8B model on 500 examples end to end
   on the pod (at most 5 minutes), measure tokens per second.
9. Baselines: heuristics, zero-shot logprobs, gradient-boosted trees, and the Fail-Fast recipe on
   Qwen3-0.6B, all evaluated on the same dev split with bootstrap CIs.
10. **M3 run:** `p_success`-only, Qwen3.5-2B, 4k budget, sized to 2 H100-hours from the measured
    throughput; calibration report with reliability diagrams; compare to every baseline at
    25/50/75/90% prefixes.

## 7. Milestones, revised

| # | Milestone | Gate |
|---|---|---|
| M0 | Pipeline on RunPod | kev `q35-08b` reproduced within seed noise |
| M1 | Data | two nebius datasets in the unified schema, global repo split, zero Verified overlap, audit doc |
| M2 | Labels and packing | six labels generated, QA report on samples, state builder tests green |
| M3 | v0 model | `p_success` 2B beats every baseline at all prefix points; calibration report |
| M4 | Multi-head and ablations | ablation table: thoughts, budget 4k/8k/16k, policy token, pairwise loss, backbone 1.7B/2B/4B, LoRA vs full, single vs multi-head; chosen default |
| M5 | Online proof | best-of-N and early-abort on a SWE-bench Verified subset, resolve-rate lift or token savings with CIs across seeds |
| M6 | Second domain | tool-use agents on tau-bench / tau2-bench (about 13k trajectories, 30 policies, programmatic 0/1 score, 30 runs per task); terminal-bench trajectories (52k trials, Apache-2.0) as held-out-domain eval; web agents via `McGill-NLP/agent-reward-bench` (1.3k trajectories with a human looping label) for `stuck` eval only. Math PRM data at most a 10% mixture in one ablation. Details in `docs/data_audit_secondary.md` |
| M7 | Release | AgentCompass-Bench, weights for both tiers, SDK, server, model cards, report |
