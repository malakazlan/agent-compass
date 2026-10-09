---
license: apache-2.0
task_categories:
- text-classification
language:
- en
tags:
- agents
- value-model
- process-reward-model
- swe-bench
- trajectories
size_categories:
- 100K<n<1M
pretty_name: agent-compass training data
---

# agent-compass-data

Training, development and test records for [agent-compass](https://github.com/malakazlan/agent-compass), a calibrated value model for AI agents. Each record is one **agent state** (task plus trajectory prefix, rendered to text) with labels for up to six typed questions.

Sources: `nebius/SWE-agent-trajectories` (80,034 runs), `nebius/SWE-rebench-openhands-trajectories` (67,074 runs) and `AgentSuite/tau2-bench-trajectories` (8,340 runs). Every run has a measured outcome.

## Files

| path | questions | records | note |
|---|---|---|---|
| `train.jsonl`, `dev.jsonl`, `test.jsonl` | `p_success` | 49,987 / 9,999 / 64,124 | **v0**: the success-only release |
| `v1/train.jsonl` | all six | 27,000 | 12,000 trajectories per SWE scaffold x 2 prefixes, 3,000 tau2-bench states; 3,360 success/failure pairs linked by `_meta.pair_id` |
| `v1/dev.jsonl` | all six | 11,005 | 5,000 per SWE scaffold plus 1,000 tau2 |
| `v1/test.jsonl` | all six | 64,124 | held-out repositories, both SWE scaffolds (same states as v0 test) |
| `v1/test_tau2.jsonl` | five (no progress) | 3,832 | held-out tau2-bench tasks, 840 runs, 30 policy models |
| `stats.json`, `v1/stats.json` | | | counts, positive rates, token totals |

The v1 files carry the refined stuck rule (judge precision about 88%). The released v1 model was trained on an earlier version of that rule.

## Record format

```json
{
  "state": "<task> ... </task>\n<policy> ... </policy>\n<hist> ... </hist>\n<recent> ... </recent>",
  "questions": {
    "p_success": {"type": "noul", "instructions": "...", "label": false},
    "stuck":     {"type": "noul", "instructions": "...", "label": false},
    "progress":  {"type": "score", "instructions": "...", "criteria": ["regressed", "no change", "small progress", "big progress"], "label": 1},
    "steps_left":{"type": "score", "instructions": "...", "criteria": ["1 to 5 steps", "6 to 15 steps", "16 to 40 steps", "more than 40 steps"], "label": 2},
    "best_next": {"type": "choice", "instructions": "...", "criteria": {"option_1": "...", "option_2": "..."}, "label": "option_2"},
    "escalate":  {"type": "noul", "instructions": "...", "label": false}
  },
  "_meta": {"id", "traj_id", "task_id", "group_id", "source", "split", "domain", "scaffold", "policy_model",
            "policy_shown", "repo", "prefix_len", "n_steps", "prefix_frac", "outcome", "baseline", "advantage",
            "stuck_reasons", "progress_reasons", "best_next" (candidate-set tier and provenance), "state_tokens",
            "variant", "pair_id" (train only)}
}
```

Questions are omitted when their label is undefined (for example `steps_left` on failed runs, `best_next` when no candidate set exists, `progress` on tool-use states). The `questions` map is the System One request shape (`noul` / `choice` / `score`), so a record is also a valid scoring request.

## How states are built

- Task text, optional policy-model tag (dropped in 30% of training records), older steps compressed to one line each, the last 8 steps in full with truncated observations. Budget 4,096 tokens (Qwen3.5 tokenizer).
- Agent thoughts removed. Padded test output collapsed, harness status lines and outcome-revealing submit messages stripped.
- Prefixes sampled at several points of each run; records whose last visible action is the final submit are excluded.

## Labels

| question | source |
|---|---|
| `p_success` | measured outcome of the run (hidden tests for SWE, task checks for tau2). `_meta.baseline` is the leave-one-out pass rate of the task, `advantage` the outcome minus baseline |
| `stuck` | rule: repeated near-identical commands, repeated error signature, edit-revert cycles; navigation commands excluded; validated against a judge model |
| `progress` | rule over the last step: test counts, error counts, new files, patch size, mapped to 4 levels |
| `best_next` | candidate set from other runs of the same task with a similar prefix (tier `branching`) or hard negatives from failed runs at a similar step (tier `hard_negative`); positive = action whose continuation succeeded more often |
| `steps_left` | remaining steps of successful runs, binned |
| `escalate` | failed run on a task whose leave-one-out pass rate is at most half the policy's overall pass rate |

Rule definitions and the judge validation are in `docs/label_qa_findings.md` of the repository.

## Splits

By repository hash (SWE: owner/repo; tau2: domain and task slug), 80/10/10, with SWE-bench Verified repositories forced into the held-out test split. No task or repository appears in more than one split.

## License

Apache-2.0 for the processing and labels. The underlying trajectories keep the licenses of their sources: the two Nebius datasets are CC-BY-4.0; `AgentSuite/tau2-bench-trajectories` states no license on its card (tau2-bench itself is MIT).
