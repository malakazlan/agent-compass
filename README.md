# agent-compass

**Keeps agents on course.** A small, calibrated value model that watches an AI agent mid-task and answers, in one forward pass with no text generation, whether the run will succeed, whether the agent is stuck, which candidate next action is best, whether to escalate, and how far from done it is.

- Models: [`azlanmalikai/agent-compass-2b`](https://huggingface.co/azlanmalikai/agent-compass-2b) (fast tier, LoRA on Qwen3.5-2B-Base; two adapters, v0 and v1)
- Data and benchmark: [`azlanmalikai/agent-compass-data`](https://huggingface.co/datasets/azlanmalikai/agent-compass-data), [`azlanmalikai/agent-compass-bench`](https://huggingface.co/datasets/azlanmalikai/agent-compass-bench)
- Reports: [`docs/m3_report.md`](docs/m3_report.md) (v0), [`docs/m4_report.md`](docs/m4_report.md) (v1, six heads, second domain), [`docs/related_work.md`](docs/related_work.md)

## Why

Agent frameworks today decide "keep going, retry, or stop" with step counters and timeouts, or by asking a large model to reflect in free text. Both are blind or expensive. Trajectory critics from the SWE-agent literature (Nebius, SWE-Gym, R2E-Gym, DeepSWE) are 14B to 70B parameters, answer one question by generating a YES/NO token, and ship no calibration numbers. Failure monitors such as Fail-Fast are small and fast but single-purpose and unreleased.

agent-compass fills that gap: one 2B model, six typed questions, calibrated probabilities, about 100 ms per 4k-token state for one question and about 220 ms for all six (batched bf16 evaluation on an H100), trained on real agent trajectories with ground-truth outcomes, and evaluated on held-out repositories and a second domain.

## The six questions

| id | type | question the model answers |
|---|---|---|
| `p_success` | yes/no probability | Will this run end in success from the current state? |
| `stuck` | yes/no probability | Is the agent looping: repeating actions, hitting the same error, cycling edits? |
| `progress` | ordered score, 4 levels | Did the last step regress, do nothing, make small progress, or make big progress? |
| `best_next` | choice over K candidates | Which candidate next action is most likely to lead to success? |
| `escalate` | yes/no probability | Should this task be handed to a stronger model or a human now? |
| `steps_left` | ordered score, 4 bins | Expected remaining steps: 1 to 5, 6 to 15, 16 to 40, more than 40 |

Requests and answers use the typed System One shape (`noul`, `choice`, `score`), so any client that already speaks that contract can call agent-compass unchanged.

## How it works

**State.** The agent's task and its trajectory so far are rendered into one text state under a fixed token budget (4,096 by default). Older steps are compressed to one line each (command, exit status, first error line); the last eight steps are kept in full with truncated observations. Agent "thoughts" are removed by default: verifiers that read them learn to trust confident language rather than evidence. An optional `<policy>` tag names the policy model (dropped 30% of the time in training, so the model also works blind).

**One pass, many questions.** The state is encoded once. Each question is appended as its own branch that can attend to the state but not to the other branches (shared-prefix rows on hybrid backbones, a block-causal mask on pure-attention backbones). A pointer readout scores each question's options against its decide token, so yes/no, ordinal and K-way choice questions all come out of the same head and the same forward pass.

**Backbone.** Qwen3.5-2B-Base with LoRA rank 16 on attention, MLP and DeltaNet projections (17.9M trainable parameters), bf16. A 4B quality tier is planned.

**Losses.** Cross-entropy for yes/no and choice questions; a cumulative-link (CORAL-style) ordinal loss for `progress` and `steps_left` so predictions respect level order; a pairwise Bradley-Terry ranking term on `p_success` that forces a successful prefix to outscore a failed prefix of the same task at a similar point; per-question loss weights; per-question temperature scaling fitted on a dev split for calibration.

**Labels from outcomes, not opinions.** `p_success` is the run's measured outcome. `stuck` and `progress` come from rules over the observations (repeated commands, repeated error signatures, edit-revert cycles, test counts moving), validated against a judge model on a labelled sample. `best_next` candidate sets are built from other runs of the same task that share a similar prefix; the positive is the action whose continuation succeeded more often. `steps_left` comes from the remaining length of successful runs. `escalate` marks states where the run failed and the task is hard for that policy.

**No leakage, no shortcuts.** Splits are by repository hash, never by row; SWE-bench Verified repositories are held out entirely. Harness status lines, final submit messages and test summaries that reveal the outcome are stripped from states. A robustness view injects fake confident thoughts into failing runs.

## Results

All numbers are on held-out repositories or held-out tasks that never appeared in training. Confidence intervals are 95% bootstrap over tasks. Full tables, baselines and protocols: `docs/m3_report.md`, `docs/m4_report.md`.

**Success prediction** (`p_success`), SWE test set, 20,000 states from 147k SWE-agent and OpenHands trajectories:

| model | AUROC | SWE-agent | OpenHands | calibrated ECE |
|---|---|---|---|---|
| hand-feature gradient-boosted baseline | | 0.667 | 0.619 | |
| zero-shot Qwen3.5-2B instruct, yes/no logprobs | 0.679 (5k subset) | | | 0.275 |
| **agent-compass-2b v0** | **0.775** [0.746, 0.800] | 0.707 | 0.722 | 0.048 |
| agent-compass-2b v1 (six heads) | 0.761 [0.735, 0.791] | 0.717 | 0.678 | 0.069 |

The model is already informative in the first quarter of a run (AUROC 0.67 to 0.71), which is the regime where abort and escalation decisions have value.

**The other heads** (v1, same test set): stuck AUROC 0.973; progress within one level 99.0%; steps_left within one bin 98.1%; best_next top-1 0.483 against a chance rate of 0.255 (about four candidates); escalate AUROC 0.670.

**Second domain, tool-use agents** (tau2-bench, 3,832 states from 840 runs of 30 policy models on held-out tasks, with only 3,000 tool-use states in training): p_success AUROC 0.780 [0.726, 0.832] with ECE 0.017; stuck 0.993; escalate 0.902; best_next top-1 0.633; steps_left within one bin 99.4%.

**Does it help an agent?** Offline simulations on the test runs, within one policy model so the value model cannot simply pick the stronger LLM:

| use | effect |
|---|---|
| best-of-N run selection, Llama-70B SWE-agent | resolve rate 12.3% random pick to 17.3% with v1 (+5.0 pp [2.6, 7.7]) |
| best-of-N run selection, Qwen3-Coder-480B OpenHands | 53.1% to 54.7% (+1.6 pp [-0.0, 3.3]) |
| early abort, Llama-70B, 5% false-abort budget (v0) | 12% of steps saved for a 0.4 pp resolve-rate loss |

v0 remains the recommended head for `p_success` and abort decisions; v1 for `stuck`, `progress`, `steps_left` and `best_next`. Both adapters share the base model.

## Using the model

The released adapters are in the checkpoint layout of the [kev](https://github.com/jaredpalmer/kev) runtime, which provides batched serving, CUDA graphs and the System One endpoint. Until the `agent-compass` SDK and server wrapper land (next milestone), scoring runs through that runtime:

```bash
# in a kev checkout with its environment (fused Qwen3.5 kernels required, see docs/pod_runbook.md)
huggingface-cli download azlanmalikai/agent-compass-2b --local-dir runs/agent-compass-2b
UV_NO_SYNC=1 KEV_DTYPE=bf16 uv run python <agent-compass>/scripts/pod/kev_benchmark_long.py \
    --run runs/agent-compass-2b --data states.jsonl --out out/ --max_state 4352
```

`states.jsonl` holds one record per state in the format of the dataset (`state` text plus a `questions` map); `agent_compass.data.state.build_state` renders a state from a trajectory in the unified schema (`agent_compass/data/schema.py`). The default server rejects states longer than its training context, so the context override shown above is required for 4k-token states.

## Repository layout

| path | contents |
|---|---|
| `agent_compass/data/` | unified trajectory schema, dataset converters (SWE-agent, OpenHands, tau2-bench), repository-hash splits, state builder, rule labels, candidate-set builder, prefix sampler, record writer |
| `agent_compass/train/` | multi-head training loop, ordinal and pairwise losses, per-question weights |
| `agent_compass/eval/` | AUROC, Brier, ECE, confident-error, recall at FPR, ordinal metrics, grouped bootstrap |
| `agent_compass/baselines/` | hand-feature baseline |
| `scripts/` | end-to-end pipeline: download, convert, build examples, make splits, label QA, baselines, calibration, offline best-of-N and early-abort simulations; `scripts/pod/` GPU scripts |
| `docs/` | plan, related work, data audits, label QA, design notes, milestone reports |
| `runs/` | configs, metrics and reports of every run cited above (weights and raw rows excluded) |
| `tests/` | unit tests for the data and training modules |

## Roadmap and future work

Done: data pipeline for three trajectory sources (155k runs), rule labels with judge validation, v0 success model, v1 six-head model, calibration, offline agent simulations, second-domain evaluation.

Next, in order:

1. **Release tooling**: `agent-compass` Python SDK (`score(state, questions)`), FastAPI server with batching, agent hooks for mini-swe-agent, OpenHands and a generic callback.
2. **Retrain on corrected labels**: v1 was trained before the stuck rule was refined (judge precision 58% to about 88%). The corrected data is published under `v1/` in the data repo.
3. **Ablations** at 0.6B scale and on cached features: thoughts on/off, history budget, pairwise loss, single versus multi-head.
4. **Quality tier**: the same recipe on Qwen3.5-4B-Base.
5. **Live agent evaluation**: best-of-N action selection and early abort inside mini-swe-agent on SWE-bench Verified, with resolve-rate and token-cost curves.
6. **Better escalate labels** from judge annotations; the current rule label is nearly redundant with `p_success`.

Known limitations: trained on two SWE scaffolds and one tool-use benchmark, so other scaffolds and domains are out of distribution; `escalate` is weak on SWE data; v1 trails v0 on `p_success` for the strong OpenHands agent; latency figures are from batched evaluation, not a served endpoint.

## Development

```bash
uv sync --group dev
uv run pytest
```

Data preparation runs on a CPU box and streams every file; training and evaluation scripts target one 80 GB GPU (`docs/pod_runbook.md`).

## Acknowledgements and license

agent-compass started from [kev](https://github.com/jaredpalmer/kev) by Jared Palmer (Apache-2.0), whose encoder, pointer readout, checkpoint format and serving runtime it builds on; see `NOTICE`. Training data comes from `nebius/SWE-agent-trajectories`, `nebius/SWE-rebench-openhands-trajectories` and `AgentSuite/tau2-bench-trajectories`; the base model is Qwen3.5-2B-Base.

Code and weights: Apache-2.0.
