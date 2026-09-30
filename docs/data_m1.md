# M1 data: measured on the full conversion (2026-09-30)

Produced by `scripts/convert.py` (unified schema, repo-hash splits, dedupe) and `scripts/build_examples.py` (kev-shaped records). Numbers below are read from the `*.stats.json` files those scripts write.

## Trajectories

| dataset | trajectories | dropped dup | skipped | success rate | tasks | runs/task | mixed-outcome tasks | repos | steps mean / median / max | steps success vs failure | thoughts on steps |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `nebius/SWE-rebench-openhands-trajectories` | 67,074 | 0 | - | 47.9% | 6,306 | 10.64 | 1,741 (27.6%) | 1,823 | 64.4 / 61.0 / 100 | 59.1 vs 69.2 | 71.7% |
| `nebius/SWE-agent-trajectories` | 80,034 | 1 | {'empty': 1} | 16.7% | 3,591 | 22.29 | 807 (22.5%) | 1,276 | 26.4 / 17.0 / 408 | 15.2 vs 28.7 | 100.0% |

### Policy models and exit status

- **openhands** policies: Qwen3-Coder-480B-A35B-Instruct (67,074)
  exit status: submit (60,739), RuntimeError: Agent reached maximum iteration. Current iteration: 100, max iteration: 100 (6,003), AgentStuckInLoopError: Agent got stuck in a loop (252), Timeout: litellm.Timeout: APITimeoutError - Request timed out. Error_str: Request timed out. (36), RuntimeError: There was an unexpected error while running the agent: ServiceUnavailableError. You can refresh the page or ask the agent to try again. (29), unknown (10), AttributeError: 'str' object has no attribute 'get' (5)
- **swe_agent** policies: swe-agent-llama-70b (74,791), swe-agent-llama-8b (4,052), swe-agent-llama-405b (1,191)
  exit status: submitted (51,086), submitted (exit_context) (21,026), exit_context (3,567), early_exit (3,176), submitted_no_patch (1,066), submitted (exit_format) (80), exit_format (20), submitted (exit_cost) (10)

## Splits (unit = repository, one global hash table)

| dataset | train | dev | test | verified_holdout | Verified instance hits |
|---|---|---|---|---|---|
| openhands | 52,066 | 8,738 | 6,270 | 0 | 0 |
| swe_agent | 58,455 | 14,352 | 7,227 | 0 | 0 |

Frozen table `data/splits/repo_split.json`: 2,251 repositories. Rule: sha1('agent-compass-v1:' + owner/repo) % 100 -> train < 80, dev < 90, else test; SWE-bench Verified repos and instance ids -> verified_holdout. Repos per split: dev 218, test 223, train 1,810.

Dev and test shares differ from 10/10 by trajectory count because a few very large repositories dominate; the split is by repository, so this is expected and must not be rebalanced by hand.

## Example records (kev-shaped, one per trajectory prefix)

### openhands

- input `data\unified\openhands.jsonl`, variant `clean`, budget 4096 tokens, tokenizer `.scratch\tok\qwen3.5-2b\tokenizer.json`
- trajectories 27,008 (tasks 6,306), records: train 59,199, test 30,894, dev 43,116
- state tokens p50 / p90 / max: [3632, 4079, 4092]
- questions present: p_success 133,209, stuck 133,209, progress 133,209, escalate 132,832, steps_left 63,893, best_next 29,469
- best_next tiers: branching 12,823, hard_negative 16,646
- build time 11605.9 s

Label histograms:

- p_success: False 69,316 (52.0%), True 63,893 (48.0%)
- stuck: False 130,878 (98.3%), True 2,331 (1.7%)
- progress: 0 2,343 (1.8%), 1 88,243 (66.2%), 2 39,787 (29.9%), 3 2,836 (2.1%)
- escalate: False 74,549 (56.1%), True 58,283 (43.9%)
- steps_left: 0 6,981 (10.9%), 1 17,017 (26.6%), 2 27,432 (42.9%), 3 12,463 (19.5%)
- best_next: set 29,469 (100.0%)

### swe_agent

- input `data\unified\swe_agent.jsonl`, variant `clean`, budget 4096 tokens, tokenizer `.scratch\tok\qwen3.5-2b\tokenizer.json`
- trajectories 36,579 (tasks 3,591), records: train 69,364, dev 67,234, test 33,242
- state tokens p50 / p90 / max: [2030, 3699, 4094]
- questions present: p_success 169,840, stuck 169,840, progress 169,840, escalate 169,125, best_next 25,487, steps_left 27,945
- best_next tiers: branching 9,736, hard_negative 15,751
- build time 8917.2 s

Label histograms:

- p_success: False 141,895 (83.5%), True 27,945 (16.5%)
- stuck: False 132,036 (77.7%), True 37,804 (22.3%)
- progress: 0 23,173 (13.6%), 1 82,616 (48.6%), 2 63,815 (37.6%), 3 236 (0.1%)
- escalate: False 60,062 (35.5%), True 109,063 (64.5%)
- best_next: set 25,487 (100.0%)
- steps_left: 0 15,731 (56.3%), 1 10,249 (36.7%), 2 1,795 (6.4%), 3 170 (0.6%)

