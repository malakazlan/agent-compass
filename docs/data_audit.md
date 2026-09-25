# Data audit: SWE-agent trajectory datasets with outcome labels

Audit date: 2026-09-25. Method: `HfApi().dataset_info(files_metadata=True)` for file lists/sizes, README + card
metadata for licenses, parquet footers (pyarrow over `HfFileSystem`) for schemas and row-group sizes, first
2-3 rows of one shard per dataset (row-group range reads; no full downloads, ~600 MB fetched in total), and
column-only reads of `instance_id`/`repo`/label columns for overlap and balance statistics. All scripts and raw
outputs are in `.scratch/` (gitignored): `hf_meta.py`, `hf_sample.py`, `hf_rg_sample.py`, `hf_ids.py`,
`cf_ids.py`, `.scratch/meta/*.json|md`, `.scratch/samples/*`, `.scratch/ids/*`.

SWE-bench Verified reference set: `princeton-nlp/SWE-bench_Verified` test split, 500 instances, 12 repos:
astropy/astropy, django/django, matplotlib/matplotlib, mwaskom/seaborn, pallets/flask, psf/requests,
pydata/xarray, pylint-dev/pylint, pytest-dev/pytest, scikit-learn/scikit-learn, sphinx-doc/sphinx, sympy/sympy.
"Verified overlap" below means either exact instance-id overlap or repo overlap with this set.

## 1. Summary table

| # | HF id | License (card / README) | Gated | # traj | Size | Scaffold(s) | Policy model(s) | Outcome field | Verified overlap |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `nvidia/Open-SWE-Traces` | cc-by-4.0 / cc-by-4.0 | no | 511,668 (v1.0 151,219 + v1.1 253,182 + v1.2 107,267); 368,217 with a known label | 42.6 GB, 212 parquet | OpenHands v0.53, SWE-agent, mini-swe-agent | MiniMax-M2.5, Qwen3.5-122B, DeepSeek-V4-Flash, Qwen3.6-27B, Qwen3.8-27B | `resolved` int {1,0,-1} | 0 Verified ids / 0 Verified repos in sampled shards (tasks from SWE-rebench-V2 / Scale-SWE; note getmoto/moto and pandas-dev/pandas also appear in SWE-Gym) |
| 2 | `nebius/SWE-agent-trajectories` | cc-by-4.0 / cc-by-4.0 (+ Llama 3.1 output notice) | no | 80,036 | 1.11 GB, 12 parquet | SWE-agent (Nebius fork) | `swe-agent-llama-70b` 74,792 / `-8b` 4,053 / `-405b` 1,191 (Nebius Llama-3.1 fine-tunes) | `target` bool (13,389 T / 66,647 F) | 0 exact-id overlap (3,591 tasks, 1,276 repos, SWE-bench-extra + SWE-bench dev); repo-level overlap not computed (id-derived repo list not persisted; SWE-bench dev repos are marshmallow/pvlib/pydicom/pyvista/sqlfluff/astroid, none Verified) - re-check in the M1 converter |
| 3 | `SWE-bench/SWE-smith-trajectories` | mit / mit | no | 76,002 (tool 24,100 / xml 26,076 / ticks 25,826) | 4.22 GB, 32 parquet | SWE-agent (3 tool-call formats) | claude-3-7-sonnet (+ 3.5-sonnet, gpt-4o per Nebius table) | `resolved` bool | synthetic SWE-smith tasks (ids `owner__repo.<commit>.<bugtype>__<hash>`); 0 Verified ids; sampled repos (django-money, apispec, marshmallow, gpxpy, environs, word_cloud...) are not Verified repos |
| 4 | `nebius/SWE-rebench-openhands-trajectories` | cc-by-4.0 / cc-by-4.0 | no | 67,074 | 2.08 GB, 1 parquet | OpenHands v0.54.0 | Qwen3-Coder-480B-A35B-Instruct | `resolved` int {1,0} (+ `gen_tests_correct`, `pred_passes_gen_tests`) | 0 Verified ids and 0 Verified repos (6,306 tasks, 1,823 repos, 10.6 traj/task) |
| 5 | `togethercomputer/CoderForge-Preview` | **none in card metadata, none in README text** (only per-row repo `license`) | no | 258,134 (SWE_Rebench 77,169 / SWE_Smith 148,001 / R2E_Gym 32,964) + `filtered_reward1` 155,144 (subset) | 72.8 GB total (22.8 GB `trajectories` config; rest is a pre-tokenized copy) | single OpenHands-style scaffold | not stated on card (unknown) | `reward` float {1.0, 0.0, rare -1.0} | 0 Verified ids in 24 sampled shards across all splits (tasks: SWE-rebench, SWE-smith, R2E-Gym; R2E repos include pandas/numpy/aiohttp/pillow, none in Verified) |
| 6 | `ByteDance-Seed/Multi-SWE-bench_trajs` | cc0-1.0 / cc0-1.0 | no | ~319 leaderboard submissions x up to 500-1,600 instances (not stated) | 4.73 GB, 319 zip | MSWE-agent, MagentLess, MopenHands, Agentless, OpenHands, SWE-agent, RepoRepair | many (GPT-4o, o1, o3-mini, Claude-3.5/3.7, DeepSeek-V3/R1, Qwen2.5-72B, Doubao, Gemini-2.5, Llama-4...) | **none inside the zips**; must be joined to leaderboard results | **yes**: the `python/*` zips are exactly the 500 SWE-bench Verified instances |
| 7 | AgentLens-Bench | n/a (not released) | n/a | 1,815 (paper) | n/a | 8 model backends (paper) | n/a | quality tier / waste / divergence (paper) | **yes by construction** (47 SWE-bench Verified tasks) |
| E1 | `SWE-Gym/OpenHands-Sampled-Trajectories` | **missing** / missing | no | 6,055 (491 resolved) | 0.30 GB, 3 parquet | OpenHands | gpt-4o-2024-08-06, claude-3-5-sonnet-20241022 (`run_id`) | `resolved` bool + `test_result.report` | 0 Verified ids; 2,438 tasks over 11 repos (getmoto/moto, pandas, mypy, dask, ...), none in Verified; 2.5 runs/task |
| E2 | `R2E-Gym/R2EGym-Verifier-Trajectories` | **missing** / missing | no | 5,750 | 0.11 GB, 1 parquet | R2E agent (OpenHands-like function calls), wrapped in judge prompt | Claude-Sonnet-3.5-v2 (per Nebius table) | `rewards` float | 0 Verified ids (2,203 distinct docker images = tasks; rewards 1.0: 2,705 / 0.0: 3,045, 47% positive) |
| E3 | `Kwai-Klear/SWE-smith-mini_swe_agent_plus-trajectories-66k` | mit / mit | no | 65,994 (all successful) | 1.58 GB, 47 parquet | mini-swe-agent-plus | not stated | **none** (success-only) | synthetic SWE-smith tasks (column read hit network errors; card says 123 repos) |
| E4 | `nvidia/SWE-Hero-openhands-trajectories` | cc-by-4.0 / cc-by-4.0 | no | 34,269 (11,766 issues) | 2.40 GB, 14 parquet | OpenHands | Qwen3-Coder-480B-A35B-Instruct | **none** (execution-filtered SFT set, no label column) | 0 Verified ids/repos in 2 sampled shards (5,000 rows, all `dataset = R2E-Gym/R2E-Gym-Subset`, 3,179 tasks, 8 repos: pandas 2,050, numpy 1,192, aiohttp, tornado, scrapy, pyramid, datalad, coveragepy) |
| E5 | `togethercomputer/CoderForge-Preview-32B-SWE-Bench-Verified-Evaluation-trajectories` | **missing** / missing | no | 500 | 0.16 GB, 32 parquet | OpenHands-style (CoderForge 32B policy) | CoderForge-Preview-32B | `reward` float, `test_output`, `num_steps` | **yes, 100%**: one run per Verified instance (eval-only) |
| x | `SWE-Critix/rl_dataset`, `SWE-Critix/test_dataset` | other ("mixed-upstream-licenses") | no | 108,690 / 43,445 | 23.0 GB / 3.7 GB | judge prompts over CoderForge trajectories | n/a | `reward_model.ground_truth` / `ground_truth` "1"/"0" | derived from CoderForge (see 5) |
| x | `nvidia/SWE-Zero-openhands-trajectories`, `AlienKevin/SWE-ZERO-12M-trajectories` | cc-by-4.0 / apache-2.0 | no | 318,115 / 12,290,800 | 12.2 GB / 36 GB | OpenHands / mini-swe-agent v1 | Qwen3-Coder-480B / mini-coder-1.7b | **none** (execution-free, unverified) | n/a - excluded |
| x | `SWE-Factory/DeepSWE-Agent-Kimi-K2-Trajectories-2.8K` | mit | no | ~2.8k | 0.24 GB jsonl | R2E/DeepSWE agent | Kimi-K2 | **none** (`messages` only) | excluded |

Notes on ids: "CoderForge-Preview (via SWE-Critix)" resolves to `togethercomputer/CoderForge-Preview`; the
`SWE-Critix/*` datasets are prompt-formatted derivatives of it. "AgentLens-Bench" has **no HF release** as of
2026-09-25 (see section 2.7).

## 2. Per-dataset notes

### 2.1 `nvidia/Open-SWE-Traces`

- License: `cc-by-4.0` in card metadata; README: "governed by CC BY 4.0; Additional Information: MIT, Apache 2.0,
  BSD-2, BSD-3" (repo licenses); "ready for commercial/non-commercial use". Not gated. Downloads: see meta json.
- Layout: 3 configs, split = scaffold, data files partitioned `data/<scaffold>/<model>/<task source>/*.parquet`:
  - `v1.0`: `openhands` (minimax_m25, qwen35_122b), `sweagent` (minimax_m25, qwen35_122b) - 151,219 traj
  - `v1.1`: `openhands` (qwen36_27b, deepseek_v4_flash), `sweagent` (qwen36_27b), `minisweagent` (qwen36_27b) - 253,182
  - `v1.2`: `minisweagent` (qwen38_27b) - 107,267
  Task sources: `swe-rebench-v2/` (nebius/SWE-rebench-V2) and `scale-swe/` (AweAI-Team/Scale-SWE). Shard sizes 33-307 MB,
  one row group per shard (2,500 rows) - a "sample" costs a whole shard, but `instance_id/repo/resolved` columns are < 0.1 MB.
- Schema (parquet footer + README): `instance_id str`, `repo str (owner/repo)`, `license str`, `language str`,
  `trajectory_id str`, `messages list<struct{role, content, reasoning_content, tool_calls list<struct{function{arguments str, name}, id, type}>}>`,
  `tools list<str>`, `resolved int64` (1/0/-1 unknown), `metadata struct{category, teacher_model{name, enable_thinking, reasoning_effort},
  reference_patch{patch,num_modified_files,num_modified_lines}, model_patch{...}}`, `hf_dataset_name str`.
  - task text: `messages[1].content` (user message, `<pr_description>` block); repo: `repo`; instance: `instance_id`;
    steps: `messages` (assistant = `content` + `tool_calls`, tool = observation prefixed `OBSERVATION:`); outcome: `resolved`;
    policy model: `metadata.teacher_model.name` (also encoded in path); scaffold: only in the file path / split name;
    test results: none (patch in `metadata.model_patch`).
- Outcome balance (from `data_distribution.md`, summing categories): v1.0 resolved 48,293 / unresolved 67,590 / unknown 35,336;
  v1.1 resolved 64,382 / unresolved 91,357 / unknown 97,443 (**all v1.1 OpenHands rows are unknown**); v1.2 resolved 49,925 /
  unresolved 46,670 / unknown 10,672. Total labelled: 162,600 resolved vs 205,617 unresolved (44% positive).
- Tasks: v1.0 ~19-20k PRs per scaffold/model (9 languages: Python 34k, Go 33k, TS 27k, JS 22k, Rust 17k, Java 9.5k, PHP 7.5k, C/C++ <1k traj);
  v1.1/v1.2 Python only (30-41k PRs). Column-read stats, 1 OpenHands/Qwen3.5 shard (2,500 rows, swe-rebench-v2 source): 2,500 distinct instance ids (1 trajectory per
  task within a shard; the multi-run structure comes from the same PR being run under several scaffold/model groups), 1,203
  repos, top: vaskoz/dailycodingproblem-go 51, swc-project/swc 35, getmoto/moto 29, eslint/eslint 21, pandas-dev/pandas 19;
  resolved 0: 1,319 / -1: 591 / 1: 590 (24% positive, 24% unknown); languages go 666, python 523, typescript 391, rust 327,
  javascript 319, java 149, php 112, c 11, cpp 2; repo licenses MIT/Apache-2.0/BSD; **0 Verified ids, 0 Verified repos**.
  1 SWE-agent/MiniMax-M2.5 shard (2,500 rows): 2,500 distinct ids, 1,160 repos (top getmoto/moto 54, swc 29,
  obsidian-linter 24, eslint 23, pandas 21), resolved 1: 965 / 0: 1,012 / -1: 523 (**39% positive**, 21% unknown), languages
  python 619, typescript 512, go 423, javascript 409, rust 278, java 137, php 113; 0 Verified ids / repos.
- Thoughts: separate `reasoning_content` field on assistant messages (empty for non-thinking models, e.g. Qwen3.5 non-thinking);
  free-text commentary also appears in `content` next to `tool_calls`. Thoughts-removed view = drop `reasoning_content` and
  `content` on assistant turns, keep `tool_calls`.
- Harness leakage: no test output in messages; last observation is the SWE-agent "please review your changes" prompt and the
  final assistant turn is the `submit` call. `metadata.model_patch` is separate.
- Known issues (card): trajectories with git-hacking behaviour were removed 08/26 (v1.0 207k -> 151k); "Unknown" outcomes
  are frequent (28% overall); the same PR appears under several scaffolds/models (PR counts not unique across columns).

### 2.2 `nebius/SWE-agent-trajectories`

- License: `cc-by-4.0` (card + README); README adds: respect per-repo licenses (listed in `nebius/SWE-bench-extra`) and the
  Llama 3.1 license applies to model outputs. Not gated.
- 80,036 rows, one split `train`, 12 parquet (1.11 GB download; 5.6 GB in memory). Row groups of 1,000 rows (12 MB each).
- Schema (`features`): `instance_id str`, `model_name str`, `target bool`, `trajectory list<struct{cutoff_date str, mask bool,
  role str (system|user|ai), system_prompt str, text str}>`, `exit_status str`, `generated_patch str`, `eval_logs str`.
  - task: `trajectory[1].text` ("We're currently solving the following issue ... ISSUE: ..."); steps: alternating `ai` (thought +
    ```command``` block in one `text`) and `user` (observation, with SWE-agent "(Open file: ...) (Current directory: ...)" footer);
    outcome: `target`; policy: `model_name` (e.g. `swe-agent-llama-70b`); scaffold: implicit (SWE-agent-style, Nebius fork);
    test results: `eval_logs` (separate column, not in the trajectory).
- Balance (card): 13,389 resolved vs 66,647 not (16.7% positive). Exit status: submits 94.6% (resolved) vs 57.6% (unresolved),
  `exit_context` 30% of failures. Avg steps 31 (resolved) vs 58 (unresolved) - step count alone is a strong heuristic baseline.
- Tasks: `nebius/SWE-bench-extra` (real GitHub issues, 1,202 repos per Nebius comparison table) + the SWE-bench **dev** split
  (SWE-bench Verified is drawn from the test split, so no direct instance overlap is expected). Column-read stats over all 12
  shards (80,036 rows): **3,591 distinct instance ids** (~22 trajectories per task - multiple samples per task, useful for
  per-task baselines and `best_next`), 1,276 distinct repos derived from the id prefix, **0 SWE-bench Verified ids**;
  `model_name`: `swe-agent-llama-70b` 74,792, `swe-agent-llama-8b` 4,053, `swe-agent-llama-405b` 1,191 (the Nebius comparison
  table's "Qwen2.5-72B / Llama3-70B" refers to the bootstrapping models, the column holds their fine-tunes); `exit_status`:
  submitted 51,087, submitted (exit_context) 21,026, exit_context 3,568, early_exit 3,176, submitted_no_patch 1,066, other < 120.
- Thoughts: **inline** - the `ai` text is "reasoning ... ```\ncommand\n```"; the command must be parsed out of the last fenced
  block to build the thoughts-removed view (kev/SWE-agent convention).
- Harness leakage: `eval_logs` and `generated_patch` are separate columns; the trajectory ends at the submit action. Sample's
  `eval_logs` contains the full pytest run (never feed it to the model).
- Known issues: weak policy models (2024 Llama/Qwen fine-tunes) -> many degenerate loops; card notes "at least one correct
  file opened" in only 40% of failures. Good stuck-detection material, but distribution far from current agents.

### 2.3 `SWE-bench/SWE-smith-trajectories`

- License: `mit` (card + README). Not gated. README text is stale (says 5,017 trajectories used for SWE-agent-LM-32B); the
  actual card metadata lists 3 splits, `tool` 24,100 / `xml` 26,076 / `ticks` 25,826 = 76,002 rows, 4.22 GB (3.2 GB download).
  Splits are the three SWE-agent function-calling formats (native tool calls / XML / backticks), same task pool.
- Schema: `messages str` (JSON string!), `instance_id str`, `resolved bool`, `model str`, `traj_id str`, `patch str`.
  Parsed `messages`: list of {`role` system|user|assistant|tool, `content` (str or [{type:text,text}]), assistant extras
  `thought`, `action`, `tool_calls`, `message_type` (system_prompt|action|observation), `agent`, `cache_control`}.
  - task: `messages[1].content[0].text` (`<pr_description>`); steps: assistant `action` (already-parsed command string) +
    `thought`; tool `content[0].text` = "OBSERVATION:\n..."; outcome: `resolved`; policy: `model`
    (`claude-3-7-sonnet-20250219` in sample); scaffold: SWE-agent; instance ids look like
    `django-money__django-money.835c1ab8.func_pm_ctrl_shuffle__viqnyl9u` (repo.commit.bug-type__hash).
- Balance: not on the card. Nebius' comparison table (older snapshot, 49,897 traj) reports 21,513 successful (43%).
  Column-read stats from 3 of 8 `tool` shards (9,039 rows): resolved True 4,156 / False 4,883 (**46% positive**); 5,452 distinct
  instance ids (1.7 trajectories per task - little per-task branching); `model`: claude-3-7-sonnet-20250219 6,500,
  claude-3-5-sonnet-20241022 2,275, gpt-4o-2024-08-06 264; 0 SWE-bench Verified ids.
- Tasks: **synthetic** SWE-smith bugs (129 repos per Nebius table) injected into real repos; repos were chosen to be
  disjoint from SWE-bench, so no Verified overlap is expected; the 3-shard sample shows none (repo names such as django-money, apispec, marshmallow,
  python-pinyin, gpxpy, environs, word_cloud).
- Thoughts: **separate `thought` field** (duplicates the natural-language part of `content`); `action` is the clean command.
  Best-structured dataset for the thoughts-removed view.
- Harness leakage: none seen; last turn is the `submit` tool call, the preceding observation is a normal command result.
- Known issues: `patch` is empty for some resolved rows (sample row 0), and row 1's `patch` is for a different repo than its
  `instance_id` (flashtext vs apispec) - the `patch` column looks misaligned; do not trust it, derive patches from actions if needed.
  Synthetic bug descriptions are LM-written and can be formulaic.

### 2.4 `nebius/SWE-rebench-openhands-trajectories`

- License: `cc-by-4.0` (card + README). Not gated. 67,074 rows, one split, a single 2.08 GB parquet
  (`trajectories.parquet`, 17 row groups of 4,096 rows; the `trajectory` column is ~120 MB per row group, so any row sample
  costs ~130 MB - read id/label columns instead when possible).
- Schema (footer): `trajectory_id`, `instance_id`, `repo`, `trajectory list<struct{content, role, tool_calls list<struct{function{arguments str(JSON), name}, id, type}>, name, tool_call_id}>`,
  `tools`, `model_patch`, `exit_status`, `resolved int64`, `gen_tests_correct double`, `pred_passes_gen_tests double`.
  - task: `trajectory[1].content` (`<issue_description>`); steps: assistant `content` + `tool_calls` (OpenHands tools
    `execute_bash`, `str_replace_editor`, `think`, `finish`); tool messages carry the observation; outcome: `resolved`;
    policy: fixed (Qwen3-Coder-480B-A35B-Instruct, not a column); scaffold: OpenHands v0.54.0 (not a column);
    extra signals: `gen_tests_correct` and `pred_passes_gen_tests` (agent-written tests).
- Balance (card): 32,161 resolved / 67,074 (48%); 3,792 distinct resolved issues over 1,823 repos; avg 64 turns (max 100).
  Column-read stats over the full file (67,074 rows): resolved 1: 32,161 / 0: 34,913 (**48% positive**); **6,306 distinct tasks,
  10.6 trajectories per task, 28% of tasks have both successes and failures** (best source for per-task baselines /
  `best_next` among the CC-BY sets); 1,823 distinct repos, top: tobymao/sqlglot 2,678, python-pillow/Pillow 1,328,
  iterative/dvc 846, conan-io/conan 692, streamlink 661, pennylane 658, tox 606, dask 605; **0 Verified ids, 0 Verified repos**;
  `exit_status`: submit 60,739, max-iteration (100) 6,003, AgentStuckInLoopError 252, API timeouts/errors ~70, unknown 10.
- Thoughts: OpenHands `think` tool calls (arguments.thought) plus free-text assistant `content` before tool calls. Thoughts-removed
  view = drop `think` calls and assistant `content`, keep other `tool_calls`.
- Harness leakage: none; the sample's last observation is a pytest run the agent itself launched (self-verification), and the
  final assistant turn is a `finish` call or a plain message. Because agents run the repo tests themselves, "all tests pass"
  language from the agent is present and must be treated as untrusted (R2E-Gym caveat).
- Known issues: single policy model and scaffold (cannot do held-out-policy splits alone); 100-turn cap; `arguments` is a
  JSON string that must be deserialised.

### 2.5 `togethercomputer/CoderForge-Preview` (the "CoderForge-Preview" used by SWE-Critix)

- License: **missing**. No `license` key in the card metadata, no license section in the README (README covers results,
  limitations, next steps, citation). Only a per-row `license` column with the source repo's SPDX id. The SWE-Critix
  derivatives label it "mixed-upstream-licenses". -> Ask before using for a released model; usable for internal experiments only
  after the user decides.
- Not gated. 1,792 parquet files / 72.8 GB: config `trajectories` (22.8 GB download, 92.9 GB in memory) with splits
  `SWE_Rebench` 77,169, `SWE_Smith` 148,001, `R2E_Gym` 32,964 and `filtered_reward1` 155,144 (the reward=1 subset of the other
  three - do not concatenate); config `trajectories-tokenized_qwencoder` (same rows, `input_ids`/`labels`, 50 GB).
  Shards are 6-113 MB with one row group each.
- Schema: `trajectory_id str` (e.g. `0b01001001__spectree-64_run1`), `finish_reason str`, `image str` (docker image,
  e.g. `qingyangwu/sweb.eval.x86_64.0b01001001_1776_spectree-64`), `messages str` (JSON string), `reward double`, `tools str`,
  `license str`. **No `instance_id`, `repo`, or problem-statement columns**: derive task id from `trajectory_id`
  (strip `_runN`) or `image` (`_1776_` = `__`), repo from the id prefix, task text from `messages[1].content`.
  - steps: OpenAI-style {`role`, `content`, `tool_calls` (arguments already a dict), `name`, `tool_call_id`}; tools
    `execute_bash`, `str_replace_editor`, `think`, `finish`; outcome: `reward` (1.0 = tests pass); policy model: not stated
    anywhere (blog says a single scaffold/tool set; the eval set E5 names Qwen3-Coder-32B-SFT models); scaffold: OpenHands-style.
- Balance: card gives none. `filtered_reward1` = 155,144 of 258,134 (60% positive). Sampled column reads (6 shards per split, evenly spaced): `SWE_Rebench` 2,068 rows / 120 tasks, reward 1.0: 1,217, 0.0: 847,
  **-1.0: 4** (undocumented value - treat as unknown and drop), all `finish_reason = tool_calls`, licenses BSD-3/MIT/BSD-2/BSD/Apache,
  **7.8 runs per task, 33% of tasks have both successes and failures**, 0 Verified ids; shards are sorted by task id, so
  runs of one task sit in one shard. `SWE_Smith` (3,965 rows sampled): reward 1.0: 2,747 / 0.0: 1,213 / -1.0: 5, 4.0 runs per task, 17% mixed tasks; the `image`
  field alternates between `qingyangwu/swesmith.x86_64.<owner>_1776_<repo>.<commit>` and bare SWE-smith ids, so the task key
  must be parsed from both. `R2E_Gym` (883 rows): reward 1.0: 580 / 0.0: 294 / -1.0: 9, 7.7 runs per task, 45% mixed tasks;
  images are `qingyangwu/<repo>_final:<commit>` (R2E-Gym convention, repo = aiohttp, pandas, numpy, pillow (HPND) ...).
  `filtered_reward1` (4,156 rows): all reward 1.0 (confirmed subset). 0 SWE-bench Verified ids in any sampled shard.
  Overall sampled positive rate ~66%; the -1.0 rows (~0.3%) are undocumented.
- Multiple runs per task (`_run1.._runN`), which is exactly what the `best_next` / per-task-baseline labels need.
- Thoughts: `think` tool calls + assistant `content`. Harness leakage: none in messages (no test output column at all);
  sample failure trajectory ends with the agent calling `finish` after a stuck shell - a good "stuck" example.
- Known issues (card): single scaffold and fixed tool set; skewed to bug-fixing; no mid-trajectory user turns. Tasks are a
  union of SWE-rebench, SWE-smith and R2E-Gym so they overlap datasets 3, 4 and E2 at the task level.

### 2.6 `ByteDance-Seed/Multi-SWE-bench_trajs`

- License: `cc0-1.0` (card + README). Not gated. 319 zip files (4.73 GB) organised `<language>/<date>_<scaffold>_<model>.zip`;
  languages c, c++, go, java, javascript, python, rust, typescript (+ two stray folders `flash/`, `mini/`);
  scaffolds by file count: MopenHands 98, MagentLess 96, MSWE-agent 93, SWE-agent 12, Agentless 9, OpenHands 9, RepoRepair 1.
- Format (inspected `c/20250514_MopenHands_Doubao-1.5-thinking.zip` and `typescript/20250910_RepoRepair_...zip`): one file per
  instance. OpenHands zips hold the raw LLM-call log per instance (`messages`, `response`, `fncall_messages`, `fncall_response`,
  `cost`, ...) with OpenHands XML-style function calls inside `content`; SWE-agent zips hold `.traj` files; RepoRepair holds
  jsonl with `rag_files`, `buggy_files`, `repair_results`. Nothing is documented on the card.
- **Outcome label: absent from the zips.** Resolution must be joined from the Multi-SWE-bench leaderboard/report files per
  submission (instance-level resolved lists), which are not in this repo.
- **Verified overlap: yes.** `python/20250329_SWE-agent_DeepSeek-R1.zip` contains exactly 500 `.traj` files whose ids are the
  500 SWE-bench Verified instances (Multi-SWE-bench reuses SWE-bench Verified for Python). The non-Python splits are
  Multi-SWE-bench's own instances (e.g. `jqlang__jq-3161`, `facebook__zstd-3438`, `mui__material-ui-33880`).
- Assessment: heterogeneous, label-less, partly leaking. Useful only as a multilingual, multi-model *evaluation* source after
  joining leaderboard results and dropping `python/`. Not a training source for M1.

### 2.7 AgentLens-Bench

- Paper: "AgentLens: Revealing The Lucky Pass Problem in SWE-Agent Evaluation", arXiv 2605.12925 (Microsoft, May 2026).
  1,815 process-annotated trajectories over 47 SWE-bench Verified tasks (from 2,614 trajectories / 8 model backends / 60 tasks),
  with quality tiers (Lucky / Solid / Ideal), waste signals, divergence points, per-action intent labels (Exploration /
  Implementation / Verification / Orchestration) and one Prefix-Tree-Acceptor reference per task.
- Release status: abstract says "we plan to release the project repository soon"; **no HF dataset or GitHub release found**
  (HF search for "AgentLens" returns only the unrelated `ArkFelix7/agentlens-failure-corpus`; `gayatri-apte/swe-agentlens`
  on GitHub is an unrelated empty backend project). Re-check before M2/M6.
- By construction it is SWE-bench Verified -> eval-only, and it overlaps our online-eval instances; use only for process-label
  evaluation on tasks excluded from the online subset.

### 2.8 Extra candidates found on HF (2025-2026)

- E1 `SWE-Gym/OpenHands-Sampled-Trajectories` - 6,055 OpenHands runs (gpt-4o, claude-3.5-sonnet) on SWE-Gym (11 real repos:
  moto, pandas, mypy, dask, pydantic, dvc, ...), `resolved` bool plus full `test_result` struct (`report.resolved`,
  `test_output`, `git_patch`). Test output is a separate column, not in `messages`. Only 491 positives of 6,055 (8%); all 3 shards read: 2,438 distinct tasks (2.5 runs per task), 11 repos, 0 Verified ids. Messages are
  OpenAI tool-call format with `OBSERVATION:` tool messages. **No license on card or README.** Verifier variant
  (`SWE-Gym/OpenHands-Verifier-Trajectories`, 5,272 rows) is judge-prompt formatted (system judge prompt, user = interaction
  log, assistant = `<judgement>YES/NO</judgement>`) - a reference for the ORM baseline, not a trajectory source.
- E2 `R2E-Gym/R2EGym-Verifier-Trajectories` - 5,750 rows, `messages` (judge format), `docker_images`, `rewards` float.
  Trajectories are embedded as text inside the judge user prompt. **No license.** Useful as the R2E hybrid-verifier baseline. Full-file column read: 2,203 distinct docker images (tasks), rewards 1.0: 2,705 /
  0.0: 3,045 (47% positive), 0 Verified ids.
- E3 `Kwai-Klear/SWE-smith-mini_swe_agent_plus-trajectories-66k` - mit, 65,994 mini-swe-agent-plus runs on SWE-smith,
  `{instance_id, messages[{role, content}]}` only, THOUGHT + ```bash``` in assistant content, observations as
  `<returncode>/<output>`. **Success-only, no label** -> positives only (could pair with SWE-smith failures for hard negatives).
- E4 `nvidia/SWE-Hero-openhands-trajectories` - cc-by-4.0, 34,269 OpenHands/Qwen3-Coder-480B runs on SWE-Gym + R2E-Gym-Subset +
  SWE-rebench; schema has no outcome column (execution-verified SFT set, presumably success-filtered). Same format as
  SWE-rebench-openhands. Positives only. 2 of 14 shards read (5,000 rows): all from `R2E-Gym/R2E-Gym-Subset`, 3,179 tasks, 8 repos
  (pandas 2,050, numpy 1,192, aiohttp 420, tornado 364, scrapy 319, pyramid 272, datalad 253, coveragepy 130), 0 Verified overlap;
  files appear sorted by source dataset, so SWE-Gym/SWE-rebench rows sit in later shards.
- E5 `togethercomputer/CoderForge-Preview-32B-SWE-Bench-Verified-Evaluation-trajectories` - 500 rows, one CoderForge-32B run per
  SWE-bench Verified instance with `reward`, `num_steps`, `test_output`, `problem_statement`, `output_patch`, `messages` (JSON
  string) and the full SWE-bench record in `ds`. No license. 100% Verified -> never train on it; it is a ready-made
  in-distribution sanity check for the online-eval instances.
- Also seen but excluded: `nvidia/SWE-Zero-openhands-trajectories` (318k) and `AlienKevin/SWE-ZERO-12M-trajectories`
  (12.3M, apache-2.0) are execution-free (no tests run, no labels); `SWE-Factory/DeepSWE-Agent-Kimi-K2-Trajectories-2.8K`
  has messages only; `SWE-Critix/rl_dataset` (108,690, balanced 50/50) and `SWE-Critix/test_dataset` (43,445; 28,741 pos /
  14,704 neg, split by source parquet `SWE_Smith` / `SWE_Rebench` / `R2E_Gym`) are judge-prompt renderings of CoderForge
  trajectories (license "other"); they matter as the SWE-Critix benchmark definition, not as raw data.

## 3. Printed samples (task, first 2 steps, outcome; strings truncated)

#### Sample: nebius/SWE-agent-trajectories (instance AnalogJ__lexicon-336, model swe-agent-llama-70b)
```
TASK: We're currently solving the following issue within our repository. Here's the issue text:
ISSUE:
Memset provider: TypeError: string indices must be integers
Hi,

When using the Memset provider with the default table formatting I get this error:

```bash
$ lexicon memset create example.com TXT --name _acme-challenge.example.com --content BLAH --ttl 300
Traceback (most recent call last):
  File "/usr/local/bin/lexicon", line 11, in <module>
    sys.exit(main())
  File "/usr/local/lib/python2.7/dis ...
STEP 1 ACTION: To start solving this issue, we need to reproduce the error first. The issue provides a command to reproduce the error. Let's run it to see if we get the same error.

```
lexicon memset create example.com TXT --name _acme-challenge.example.com --content BLAH --ttl 300
```
STEP 1 OBS:    Traceback (most recent call last):
  File "/opt/conda/envs/lexicon/bin/lexicon", line 33, in <module>
    sys.exit(load_entry_point('dns-lexicon', 'console_scripts', 'lexicon')())
  File "/lexicon/lexicon/cli.py", line 102, in main
    results = client.execute()
  File "/lexicon/lexicon/client.py", line 64, in execute
    self.provider.authenticate ...
STEP 2 ACTION: The error we received is an HTTPError indicating that the request is forbidden. This suggests that the authentication is not working correctly. The traceback shows that the error occurs in the `authenticate` method of the `memset.py` file. We should start by examining the `authenticate` method in the `memset.py` file to understand how authenticatio ...
STEP 2 OBS:    Directory src not found

(Open file: n/a)
(Current directory: /lexicon)
bash-$
OUTCOME: target=False exit_status=submitted (exit_context) eval_logs(head)=Will execute following command for apply_patch:
```

cd //lexicon &&
source activate base &&
conda activate lexicon &&
g ...
```
#### Sample: SWE-bench/SWE-smith-trajectories tool split (instance django-money__django-money.835c1ab8.func_pm_ctrl_shuffle__viqnyl9u, model claude-3-7-sonnet-20250219)
```
TASK: <uploaded_files>
/testbed
</uploaded_files>
I've uploaded a python code repository in the directory /testbed. Consider the following PR description:

<pr_description>
# MoneyWidget decompress method breaks form validation with disabled fields

I've discovered an issue with the `MoneyWidget` class in the forms/widgets.py file. When a form field using this widget is disabled, validation fails unexpectedly.

## Steps to reproduce

1. Create a model with a MoneyField:
```python
class ModelWithVanill ...
STEP 1 ACTION: thought="I'll help you implement the necessary changes to fix the issue with the `MoneyWidget` class. Let's follow the steps you outlined.\n\n## Step 1: Find and read relevant code\n\nFirst, let's locate the `MoneyWidget` class in the forms/widgets.py file:" action='find /testbed -name "widgets.py" | grep -i forms'
STEP 1 OBS:    OBSERVATION:
/testbed/djmoney/forms/widgets.py
STEP 2 ACTION: thought="Now, let's examine the content of this file, focusing on the `MoneyWidget` class:" action='str_replace_editor view /testbed/djmoney/forms/widgets.py'
STEP 2 OBS:    OBSERVATION:
Here's the result of running `cat -n` on /testbed/djmoney/forms/widgets.py:
     1	from django.forms import MultiWidget, Select, TextInput
     2	
     3	from ..settings import CURRENCY_CHOICES
     4	
     5	
     6	__all__ = ("MoneyWidget",)
     7	
     8	
     9	class MoneyWidget(MultiWidget):
    10	    def __init__(
    11	       ...
OUTCOME: resolved=True last_msg={"role": "assistant", "content": "", "thought": "", "action": "submit", "agent": "main", "tool_calls": [{"index": 0, "function": {"arguments": "{}", "
```
#### Sample: nebius/SWE-rebench-openhands-trajectories (instance PlasmaFAIR__sdf-xarray-24)
```
TASK: <uploaded_files>
/workspace/PlasmaFAIR__sdf-xarray__unknown
</uploaded_files>

I've uploaded a python code repository in the directory PlasmaFAIR__sdf-xarray__unknown. Consider the following issue description:

<issue_description>
Forward slashes in variable names when writing to netcdf
```
ValueError: Forward slashes '/' are not allowed in variable and dimension names (got 'Electric Field/Ex'). Forward slashes are used as hierarchy-separators for HDF5-based files ('netcdf4'/'h5netcdf').
```

Ei ...
STEP 1 ACTION: content="I'll help you implement the necessary changes to handle forward slashes in variable names when writing to NetCDF. Let me start by following the phases you outlined.\n\n## Phase 1. READING: Understanding the Problem\n\n" tool_calls=[{"function": {"arguments": "{\"thought\": \"Let me analyze the issue description:\\n\\n1. The error message i ...
STEP 1 OBS:    Your thought has been logged.
STEP 2 ACTION: content="Let me first explore the repository structure to understand what we're working with.\n\n" tool_calls=[{"function": {"arguments": "{\"command\": \"view\", \"path\": \"/workspace\"}", "name": "str_replace_editor"}, "id": "chatcmpl-tool-c52500add0de4617861e7c4c9cb7f590", "type": "function"}]
STEP 2 OBS:    Here's the files and directories up to 2 levels deep in /workspace, excluding hidden items:
/workspace/
/workspace/PlasmaFAIR__sdf-xarray__unknown/
/workspace/PlasmaFAIR__sdf-xarray__unknown/CMakeLists.txt
/workspace/PlasmaFAIR__sdf-xarray__unknown/LICENCE
/workspace/PlasmaFAIR__sdf-xarray__unknown/README.md
/workspace/PlasmaFAIR__sdf-xarray__unkno ...
OUTCOME: resolved=0 exit_status=submit gen_tests_correct=0.0 pred_passes_gen_tests=1.0 last_role=assistant
```
#### Sample: nvidia/Open-SWE-Traces sweagent/qwen35_122b (instance open-telemetry__opentelemetry-go-contrib-6607, go)
```
TASK: <uploaded_files>
/testbed
</uploaded_files>
I've uploaded a Go code repository in the directory /testbed. Consider the following PR description:

<pr_description>
Generate New Client Metrics in `otelhttp`
The [`semconv.HTTPClient`](https://github.com/open-telemetry/opentelemetry-go-contrib/blob/13536ddd8c872685baba158a42a7b2ad37129b37/instrumentation/net/http/otelhttp/internal/semconv/env.go#L131-L138) already produces the existing `v1.20.0` client metrics (https://github.com/open-telemetry/open ...
STEP 1 ACTION: content="I'll start by exploring the repository structure and understanding the codebase to implement the necessary changes.\n\n" reasoning_content='' tool_calls=[{"function": {"arguments": "{\"command\": \"find /testbed -type f -name \\\"*.go\\\" | grep -E \\\"(env|httpconv)\\\" | head -20\"}", "name": "bash"}, "id": "chatcmpl-tool-a0e066c2b8050d1 ...
STEP 1 OBS:    OBSERVATION:
/testbed/instrumentation/net/http/otelhttp/internal/semconvutil/httpconv_test.go
/testbed/instrumentation/net/http/otelhttp/internal/semconvutil/httpconv.go
/testbed/instrumentation/net/http/otelhttp/internal/semconv/env_test.go
/testbed/instrumentation/net/http/otelhttp/internal/semconv/env.go
/testbed/instrumentation/net/http/otelhtt ...
STEP 2 ACTION: content='' tool_calls=[{"function": {"arguments": "{\"command\": \"view\", \"path\": \"/testbed/instrumentation/net/http/otelhttp/internal/semconv\"}", "name": "str_replace_editor"}, "id": "chatcmpl-tool-8db299d09d98b164",
STEP 2 OBS:    OBSERVATION:
Here's the files and directories up to 2 levels deep in /testbed/instrumentation/net/http/otelhttp/internal/semconv, excluding hidden items:
/testbed/instrumentation/net/http/otelhttp/internal/semconv
/testbed/instrumentation/net/http/otelhttp/internal/semconv/v1.20.0.go
/testbed/instrumentation/net/http/otelhttp/internal/semconv/env_t ...
OUTCOME: resolved=-1 (-1 = unknown) metadata.category=feature-request teacher_model={'name': 'Qwen3.5-122B-A10B', 'enable_thinking': False, 'reasoning_effort': None}
```
#### Sample: togethercomputer/CoderForge-Preview SWE_Rebench (trajectory_id 0b01001001__spectree-64_run1)
```
TASK: <uploaded_files>
/testbed
</uploaded_files>

I've uploaded a python code repository in the directory /testbed. Consider the following issue description:

<issue_description>
[BUG]description for query paramters can not show in swagger ui
Hi, when I add a description for a schema used in query, it can not show in swagger ui but can show in Redoc
```py
@HELLO.route('/', methods=['GET'])
@api.validate(query=HelloForm)
def hello():
    """
    hello 注释
    :return:
    """
   return 'ok'

class Hell ...
STEP 1 ACTION: content="I'll help you implement the necessary changes to fix the issue with query parameter descriptions not showing in Swagger UI. Let me start by following the phases you outlined.\n\n## Phase 1. READING: Understanding the Problem\n\n" tool_calls=[{"function": {"arguments": {"thought": "Let me analyze the issue description:\n\n1. The problem is  ...
STEP 1 OBS:    Your thought has been logged.
STEP 2 ACTION: content="### 1.1 Code Analysis\nThe code snippet shows:\n- A Flask route with query parameter validation using a Pydantic `HelloForm` model\n- The model uses Pydantic's `Field()` function to add descriptions and examples\n- Standard Python docstring comments for additional documentation\n\n### 1.2 Technical Details\n- **Method names**: `@HELLO.rout ...
STEP 2 OBS:    Here's the files and directories up to 2 levels deep in /testbed, excluding hidden items:
/testbed/
/testbed/LICENSE
/testbed/Makefile
/testbed/README.md
/testbed/docs/
/testbed/docs/Makefile
/testbed/docs/make.bat
/testbed/docs/source/
/testbed/examples/
/testbed/examples/falcon_demo.py
/testbed/examples/flask_demo.py
/testbed/examples/starlette_d ...
OUTCOME: reward=0.0 finish_reason=tool_calls image=qingyangwu/sweb.eval.x86_64.0b01001001_1776_spectree-64
```
#### Sample: SWE-Gym/OpenHands-Sampled-Trajectories (instance getmoto__moto-5321, run gpt-4o-2024-08-06_maxiter_30_N_v2.1-no-hint-train-t04-run_1)
```
TASK: <uploaded_files>
/workspace/getmoto__moto__3.1
</uploaded_files>
I've uploaded a python code repository in the directory getmoto__moto__3.1. Consider the following PR description:

<pr_description>
KeyError: 'clustername' (instead of `DBClusterNotFoundFault`) thrown when calling `describe_db_clusters` for a non-existent cluster
## Moto version:
 `3.1.16`

## Repro steps
```python
import boto3
from moto import mock_rds

@mock_rds
def test_bug():
    session = boto3.session.Session()
    rds = ses ...
STEP 1 ACTION: [{"function": {"arguments": "{\"command\":\"view\",\"path\":\"/workspace/getmoto__moto__3.1\"}", "name": "str_replace_editor"}, "id": "call_yvYHZVbXjUPsvihtKQVVOtI6", "index": null, "type": "function"}]
STEP 1 OBS:    OBSERVATION:
Here's the files and directories up to 2 levels deep in /workspace/getmoto__moto__3.1, excluding hidden items:
/workspace/getmoto__moto__3.1
STEP 2 ACTION: [{"function": {"arguments": "{\"command\":\"view\",\"path\":\"/workspace/getmoto__moto__3.1\"}", "name": "str_replace_editor"}, "id": "call_1pJhJa9YD2hFdbWDIUhuqEIV", "index": null, "type": "function"}]
STEP 2 OBS:    OBSERVATION:
Here's the files and directories up to 2 levels deep in /workspace/getmoto__moto__3.1, excluding hidden items:
/workspace/getmoto__moto__3.1
OUTCOME: resolved=False test_result.report={'empty_generation': True, 'error_eval': False, 'failed_apply_patch': False, 'resolved': False, 'test_timeout': False}
```
#### Sample: Kwai-Klear/SWE-smith-mini_swe_agent_plus-trajectories-66k (instance prettytable__prettytable.ca90b055.lm_rewrite__1qh5g9wu)
```
TASK: <pr_description>
Consider the following PR description:
Horizontal Rule Rendering Issue with Markdown and Random Styles

Description

It seems there's a problem with how horizontal rules are rendered in tables when using certain styles, specifically "MARKDOWN" and "RANDOM". The issue appears to affect the alignment and junction characters, leading to unexpected table formatting.

Steps to reproduce:

1. Create a table using the PrettyTable library.
2. Set the table style to "MARKDOWN" or "RANDOM ...
STEP 1 ACTION: THOUGHT: I need to start by understanding the codebase structure and finding the relevant files for the PrettyTable library. Let me first explore the directory structure to see what files are available.

```bash
ls -la
```
STEP 1 OBS:    <returncode>0</returncode>
<output>
total 80
drwxr-xr-x 1 root root    63 Sep 22 22:42 .
drwxr-xr-x 1 root root    43 Sep 22 22:42 ..
-rw-r--r-- 1 root root   221 Jul 13 14:14 .coveragerc
drwxr-xr-x 8 root root   163 Jul 13 14:15 .git
drwxr-xr-x 3 root root   133 Jul 13 14:14 .github
-rw-r--r-- 1 root root  1804 Sep 22 22:42 .gitignore
-rw-r--r-- 1 ...
STEP 2 ACTION: THOUGHT: I can see this is a Python project with a `src` directory. Let me explore the source code structure to understand how PrettyTable is organized.

```bash
find src -type f -name "*.py" | head -20
```
STEP 2 OBS:    <returncode>0</returncode>
<output>
src/prettytable/__init__.py
src/prettytable/_version.py
src/prettytable/colortable.py
src/prettytable/prettytable.py
</output>
OUTCOME: no outcome field (card: all 65,994 are successful runs)
```
#### Sample: togethercomputer/CoderForge-Preview-32B-SWE-Bench-Verified-Evaluation-trajectories (astropy__astropy-12907_run1)
```
TASK: Modeling's `separability_matrix` does not compute separability correctly for nested CompoundModels
Consider the following model:

```python
from astropy.modeling import models as m
from astropy.modeling.separable import separability_matrix

cm = m.Linear1D(10) & m.Linear1D(5)
```

It's separability matrix as you might expect is a diagonal:

```python
>>> separability_matrix(cm)
array([[ True, False],
       [False,  True]])
```

If I make the model more complex:
```python
>>> separability_matrix ...
STEP 1 ACTION: content="I'll help you implement the necessary changes to fix the issue with the `separability_matrix` for nested CompoundModels. Let me follow the phases systematically.\n\n## Phase 1. READING\n\n" tool_calls=[{'index': -1, 'function': {'arguments': '{"thought": "Let me analyze the issue description carefully:\\n\\n1. The problem is with the `sepa ...
STEP 1 OBS:    Your thought has been logged.
STEP 2 ACTION: content="### 1.1-1.3 Problem Analysis\n\nThe issue is with the `separability_matrix` function in `astropy.modeling.separable`. When dealing with nested CompoundModels, the separability matrix computation is incorrect. \n\n**Key technical details:**\n- **Method name**: `separability_matrix`\n- **Module**: `astropy.modeling.separable` \n- **Problem** ...
STEP 2 OBS:    Here's the files and directories up to 2 levels deep in /testbed, excluding hidden items:
/testbed/
/testbed/CHANGES.rst
/testbed/CITATION
/testbed/CODE_OF_CONDUCT.md
/testbed/CONTRIBUTING.md
/testbed/GOVERNANCE.md
/testbed/LICENSE.rst
/testbed/MANIFEST.in
/testbed/README.rst
/testbed/astropy/
/testbed/astropy.egg-info/
/testbed/astropy.egg-info/PK ...
OUTCOME: reward=1.0 num_steps=38 test_output(tail)='09de4b27f5875fe0d4ed41ce607 astropy/modeling/tests/test_separable.py\nUpdated 1 path from 4d9ea46e57\n'
```

## 4. Recommendation for Milestone 1

**Start with (a) `nebius/SWE-rebench-openhands-trajectories` and (b) `nebius/SWE-agent-trajectories`.**

Why these two:
1. Both have a clean, documented outcome label (`resolved` int / `target` bool), permissive CC-BY-4.0 on the card *and* in the
   README, real GitHub issues (not synthetic), and explicit `repo` (a) or repo-prefixed `instance_id` (b) for leakage-safe splits.
2. They cover the two scaffolds we evaluate online (OpenHands vs SWE-agent) with different policy strengths
   (Qwen3-Coder-480B, 48% resolved vs 2024 Llama/Qwen fine-tunes, 17% resolved), which gives the held-out-scaffold and
   held-out-policy splits immediately and a wide spread of "stuck" behaviour.
3. Small and fast to ingest (2.1 GB + 1.1 GB, 147k trajectories), with separate `eval_logs`/`model_patch` columns so no
   harness output has to be scrubbed from the steps; (a) also carries `gen_tests_correct`/`pred_passes_gen_tests` as extra
   auxiliary targets.
4. Scale-up path is clear: `nvidia/Open-SWE-Traces` (same SWE-rebench-V2 task family, +368k labelled rows, 5 policy models,
   3 scaffolds, 9 languages) for M3/M4 ablations, and `SWE-bench/SWE-smith-trajectories` for synthetic-task hard negatives and a
   Claude policy. `CoderForge-Preview` is the largest labelled pool (258k, multiple runs per task - ideal for `best_next`) but
   has **no stated license**; ask before using it beyond internal experiments.

Do not use: Multi-SWE-bench `python/` (is SWE-bench Verified), E5 and AgentLens-Bench for training (Verified), SWE-Zero /
SWE-ZERO-12M / DeepSWE-Kimi (no labels), Kwai-66k and SWE-Hero as sole sources (positives only).

### Splitting by repo without cross-dataset leakage

- Canonical repo key: `owner/repo` lower-cased. Sources: `repo` column (Open-SWE-Traces, SWE-rebench-openhands, SWE-Hero);
  `instance_id` prefix `owner__repo-<n>` (nebius SWE-agent, SWE-Gym, CoderForge `trajectory_id`/`image` with `_1776_` -> `__`);
  `owner__repo.<commit>.<bug>` for SWE-smith/Kwai. Also keep a `task_key` = normalised `instance_id`.
- One global assignment table `data/splits/repo_split.json` built once over the union of repos of *all* audited datasets:
  split = deterministic hash of the repo key (e.g. `sha1(repo) % 100` -> 80/10/10 train/dev/test), so a repo lands in the same
  split in every dataset, now and for datasets added later. Store the hash rule and the frozen table in git.
- Hard exclusions applied before hashing: the 12 SWE-bench Verified repos and any row whose `task_key` is in the Verified id list
  go to a `verified_holdout` bucket that is never used for training or tuning (only for the E5-style sanity check). Also drop
  rows with `resolved = -1`.
- Same-task, different-dataset duplicates: SWE-rebench(-V2) tasks appear in SWE-rebench-openhands, Open-SWE-Traces, CoderForge,
  SWE-Hero and SWE-Zero; SWE-smith tasks in SWE-smith-trajectories, Kwai-66k and CoderForge; SWE-Gym tasks in SWE-Gym, SWE-Hero.
  Repo-level hashing already keeps all of these on one side; additionally dedupe identical `(task_key, policy_model, scaffold)`
  trajectories by content hash when merging.
- Held-out-repo test set = the hashed test bucket; held-out-policy and held-out-scaffold sets are drawn *within* the train repos
  by filtering on `policy_model` / `scaffold` (so they measure model/scaffold shift, not repo shift). Dev set for temperature
  scaling is the hashed dev bucket only.
