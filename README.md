# agent-compass

Keeps agents on course: a small calibrated model that scores progress, flags loops, and picks the best next move in about 100 ms.

agent-compass is a value model for AI agents. It reads an agent's task and trajectory so far and, in one forward pass with no text generation, returns calibrated probabilities for six questions:

| Question | Type | Meaning |
|---|---|---|
| `p_success` | yes/no | Will this run end in success from here? |
| `progress` | score 0-3 | Did the last step regress, do nothing, make small progress, or big progress? |
| `stuck` | yes/no | Is the agent looping or repeating failures? |
| `best_next` | choice | Which of K candidate next actions is most promising? |
| `escalate` | yes/no | Should this be handed to a stronger model or a human now? |
| `steps_left` | score | Expected remaining steps: 1-5, 6-15, 16-40, 40+ |

The request and response shape follows the System One contract (`noul`, `choice`, `score`), so it is a drop-in for clients that already speak that API.

## Status

Study phase complete, no model trained yet. See `docs/plan.md` for the plan and milestones, `docs/related_work.md` for the literature, `docs/data_audit.md` for the datasets, and `docs/kev_study.md` for the codebase we start from.

Planned artifacts:

- Models: `azlanmalikai/agent-compass-2b` (fast tier), `azlanmalikai/agent-compass-4b` (quality tier)
- Benchmark: AgentCompass-Bench (`azlanmalikai/agent-compass-bench`)
- Package: `agent-compass` on PyPI

## License

Apache-2.0. Started from [kev](https://github.com/jaredpalmer/kev) by Jared Palmer (Apache-2.0); see `NOTICE`.
