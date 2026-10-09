---
license: apache-2.0
base_model: Qwen/Qwen3.5-2B-Base
library_name: peft
pipeline_tag: text-classification
language:
- en
tags:
- agents
- value-model
- process-reward-model
- calibration
- swe-bench
- lora
- base_model:adapter:Qwen/Qwen3.5-2B-Base
datasets:
- azlanmalikai/agent-compass-data
- nebius/SWE-agent-trajectories
- nebius/SWE-rebench-openhands-trajectories
- AgentSuite/tau2-bench-trajectories
---

# agent-compass-2b

A small, calibrated value model for AI agents. It reads an agent's task and trajectory so far and returns, in one forward pass with no text generation, probabilities for six typed questions: will the run succeed (`p_success`), is the agent stuck (`stuck`), did the last step make progress (`progress`, 4 levels), which of K candidate next actions is best (`best_next`), should the task be escalated (`escalate`), and how many steps remain (`steps_left`, 4 bins).

Code, data pipeline, reports and benchmark: [github.com/malakazlan/agent-compass](https://github.com/malakazlan/agent-compass).

## Two adapters in this repo

| path | name | questions | use it for |
|---|---|---|---|
| repo root | **v0** | `p_success` | success prediction, early abort, run selection |
| `v1/` | **v1** | all six | `stuck`, `progress`, `steps_left`, `best_next`, `escalate` |

Both are LoRA adapters (rank 16, 17.9M parameters, 67 MB) plus a 4 MB pointer-readout head on `Qwen/Qwen3.5-2B-Base`. v1 was warm-started from v0 and trained with an ordinal loss for the two score questions and a pairwise ranking loss on `p_success`. v0 is slightly better than v1 on `p_success` and much better for early abort, so the recommended deployment loads both and routes questions.

Each folder contains `adapter_model.safetensors`, `adapter_config.json`, `head.pt`, tokenizer files, `training_config.json`, `training_metrics.json`, and the evaluation metrics.

## Evaluation

Held-out repositories (the repository hash decides the split; SWE-bench Verified repositories are excluded from training). 20,000 test states from SWE-agent and OpenHands trajectories. 95% confidence intervals are bootstrap over tasks.

| question | metric | v0 | v1 | reference |
|---|---|---|---|---|
| p_success | AUROC | **0.775** [0.746, 0.800] | 0.761 [0.735, 0.791] | hand-feature GBT 0.667 / 0.619 per scaffold; zero-shot Qwen3.5-2B 0.679 |
| p_success | AUROC by scaffold, SWE-agent / OpenHands | 0.707 / 0.722 | 0.717 / 0.678 | |
| p_success | ECE after temperature scaling | 0.048 (T 1.60) | 0.069 (T 3.02) | raw 0.107 / 0.121 |
| p_success | abort recall at 5% false-positive rate | 0.301 | lower | |
| stuck | AUROC / accuracy | | 0.973 / 0.950 | rule labels validated with a judge model |
| progress | accuracy / within one level | | 0.937 / 0.990 | |
| steps_left | accuracy / within one bin | | 0.711 / 0.981 | successful runs only |
| best_next | top-1 | | 0.483 | chance 0.255 (about four candidates) |
| escalate | AUROC | | 0.670 | label nearly redundant with p_success |

Second domain, tool-use agents (tau2-bench, 3,832 held-out states, 30 policy models; 3,000 tool-use states in v1 training): v1 `p_success` AUROC 0.780 [0.726, 0.832], ECE 0.017; stuck 0.993; escalate 0.902; best_next top-1 0.633; steps_left within one bin 0.994.

Offline agent simulations within one policy model (so the model cannot gain by picking the stronger LLM): best-of-N run selection lifts the resolve rate of a Llama-70B SWE-agent from 12.3% to 17.3% with v1 (+5.0 pp [2.6, 7.7]) and of Qwen3-Coder-480B OpenHands from 53.1% to 54.7%; early abort with v0 at a 5% false-abort budget saves 12% of Llama-70B steps for a 0.4 pp resolve-rate loss.

Protocols, baselines, by-prefix tables and calibration curves: `docs/m3_report.md` and `docs/m4_report.md` in the repository.

## Input format

The model scores a text state plus a map of typed questions (System One shape: `noul`, `choice`, `score`). The state is rendered by `agent_compass.data.state.build_state`:

```
<task> task description </task>
<policy> policy model name or omitted </policy>
<hist> one line per older step: action -> short outcome </hist>
<recent> last 8 steps with truncated observations </recent>
```

Default budget 4,096 tokens, agent thoughts removed. Records in `azlanmalikai/agent-compass-data` show the exact format, including candidate options for `best_next`.

## How to run

The adapters use the checkpoint layout of the [kev](https://github.com/jaredpalmer/kev) runtime (Apache-2.0), which agent-compass builds on. Scoring a file of states:

```bash
huggingface-cli download azlanmalikai/agent-compass-2b --local-dir runs/agent-compass-2b
# inside a kev checkout with fused Qwen3.5 kernels installed (see docs/pod_runbook.md in the repo)
UV_NO_SYNC=1 KEV_DTYPE=bf16 uv run python scripts/pod/kev_benchmark_long.py \
    --run runs/agent-compass-2b --data states.jsonl --out out/ --max_state 4352
```

Use `--run runs/agent-compass-2b/v1` for the six-question adapter. The `agent-compass` SDK and server wrapper are the next release item. Throughput in batched bf16 evaluation on one H100: about 0.1 s per 4k-token state for one question, about 0.22 s for six.

## Training

| item | v0 | v1 |
|---|---|---|
| data | 21,000 states from SWE-agent and OpenHands runs (32.5% successful) | 27,000 states: 12k trajectories x 2 prefixes from both SWE scaffolds plus 3,000 tau2-bench states; 3,360 success/failure pairs |
| losses | cross-entropy | per-question weighted CE; cumulative-link ordinal loss (+0.5 CE) for progress and steps_left; pairwise Bradley-Terry on p_success, weight 0.25 |
| optimisation | 1 epoch, lr 1e-4 OneCycle, batch 2 x 4, bf16 | warm start from v0, 1 epoch, lr 5e-5, batch 1 x 8, bf16 |
| compute | 2 h 10 min on one H100 | 2 h 07 min on one H100 |

Labels: `p_success` is the run's measured outcome (hidden tests for SWE, task checks for tau2). `stuck` and `progress` are rule labels over observations, validated on a judged sample. `best_next` candidates come from other runs of the same task with a similar prefix; the positive is the action whose continuation succeeded more often. `steps_left` is the remaining length of successful runs. `escalate` marks failed runs on tasks that are hard for that policy.

## Limitations

- Trained on two SWE scaffolds (SWE-agent, OpenHands) and one tool-use benchmark. Other scaffolds, languages and domains are out of distribution.
- v1's `stuck` head was trained on an earlier version of the stuck rule (judge precision 58%); the refined rule (about 88%) is used in the published data and evaluation, and the head still scores 0.973 AUROC against it but over-flags. A retrain is planned.
- `escalate` is weak on SWE data (AUROC 0.67) because its rule label is almost the same as `p_success`.
- v1 trails v0 on `p_success` for the strong OpenHands agent and is not suitable for early abort.
- The model saw agent thoughts removed; feeding thoughts changes the input distribution.
- Latency figures come from batched evaluation, not a served endpoint.

## License and acknowledgements

Apache-2.0. Built on kev by Jared Palmer (Apache-2.0). Base model Qwen3.5-2B-Base. Training trajectories from Nebius (SWE-agent and SWE-rebench OpenHands) and AgentSuite (tau2-bench).
