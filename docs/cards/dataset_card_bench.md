---
license: apache-2.0
task_categories:
- text-classification
language:
- en
tags:
- agents
- value-model
- benchmark
- calibration
- swe-bench
size_categories:
- 10K<n<100K
pretty_name: AgentCompass-Bench
---

# AgentCompass-Bench

A benchmark for **value models of AI agents**: models that look at an agent mid-task and predict, without running the agent further, whether it will succeed, whether it is stuck, which next action is best, whether to escalate, and how far it is from done.

Companion to [agent-compass](https://github.com/malakazlan/agent-compass). Every state here comes from a repository or task that no agent-compass model saw in training.

## Files

| file | domain | records | runs | questions |
|---|---|---|---|---|
| `test.jsonl` | software engineering, SWE-agent and OpenHands scaffolds, held-out repositories incl. SWE-bench Verified | 64,124 | 13,486 | p_success, stuck, progress, steps_left (successes), best_next (where candidates exist), escalate |
| `test_tau2.jsonl` | tool-use customer-service agents, tau2-bench, held-out tasks, 30 policy models | 3,832 | 840 | p_success, stuck, steps_left, best_next, escalate |

Record format is the agent-compass record (`state` text, `questions` map in System One shape, `_meta` with trajectory, task, policy, prefix position and outcome). See `azlanmalikai/agent-compass-data` for the full field description.

## Metrics

Reported by `scripts/eval_rows.py` in the repository, with task-grouped bootstrap confidence intervals:

- `p_success`: AUROC overall and by prefix quarter, Brier, ECE (15 bins), confident-error rate at p >= 0.9, abort recall at 5 / 10 / 25% false-positive rate.
- `stuck`, `escalate`: AUROC, accuracy, ECE.
- `progress`, `steps_left`: accuracy, within-one accuracy, MAE, ranked probability score.
- `best_next`: top-1 accuracy against the chance rate 1/K.
- Offline agent simulations (`scripts/offline_best_of_n.py`, `scripts/offline_early_abort.py`): within-policy best-of-N resolve-rate lift, and steps saved versus resolve-rate loss under a false-abort budget.

Recommended fixed subsets for cost: the first 20,000 records of `test.jsonl` (the files are shuffled) for every question, all of `test_tau2.jsonl`.

## Reference results

| model | p_success AUROC (SWE) | p_success AUROC (tau2) | stuck | best_next top-1 |
|---|---|---|---|---|
| step-count heuristic | 0.614 (SWE-agent) | | | |
| hand-feature GBT | 0.667 / 0.619 by scaffold | | | |
| zero-shot Qwen3.5-2B instruct | 0.679 (5k) | | | |
| agent-compass-2b v0 | **0.775** [0.746, 0.800] | | | |
| agent-compass-2b v1 | 0.761 [0.735, 0.791] | **0.780** [0.726, 0.832] | 0.973 | 0.483 (chance 0.255); tau2 0.633 |

Full tables in `docs/m3_report.md` and `docs/m4_report.md` of the repository.

## Rules

- Do not train on these repositories or tasks. The split table is `agent_compass/data/resources/repo_split.json`.
- Report AUROC with confidence intervals, and calibration (ECE, confident-error) alongside discrimination.
- For best-of-N claims, group by policy model; selecting the stronger LLM is not a value-model skill.

## License

Apache-2.0 for the processing and labels. Trajectories keep their source licenses: the two Nebius datasets are CC-BY-4.0; `AgentSuite/tau2-bench-trajectories` states no license on its card (tau2-bench itself is MIT).
