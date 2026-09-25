# Data audit — secondary domains (web, tool-use, general assistants, terminal, math PRMs)

Date: 2026-09-25. Scope: candidate trajectory datasets for the **non-SWE** domains of agent-compass
(Milestone 6) plus math process-reward data as an auxiliary signal. SWE datasets are audited separately.

Method: `HfApi().dataset_info(files_metadata=True)`, dataset cards, `load_dataset(streaming=True)` for 2–3 rows,
the datasets-server `/size` and `/filter` endpoints for row counts, and a handful of small file downloads
(≈150 MB total). Raw probe dumps (features + rows) are in `.scratch/probes/<domain>__<repo>.txt`.
Network to HF reset frequently; every number below was either printed from data or copied from the dataset card
(marked "card"). Anything I could not verify is marked "unverified".

Requirement for domains (a)–(d): multi-step trajectories (actions + observations) **with an outcome label per
trajectory**, ideally with failures and with multiple runs per task (needed for `best_next` action-value labels).

---

## 0. Summary and recommendation

| Domain | Best datasets (verified) | Trajectories w/ outcome | Failures? | Multi-run per task? | Verdict |
|---|---|---|---|---|---|
| (b) Tool-use (tau/tau2-bench) | `AgentSuite/tau2-bench-trajectories`, `AgentSuite/tau-bench-trajectories`, `fuvty/tau-bench-synthetic`, `snorkelai/Tau2-Bench-Airline-With-Code-Agents`, `KermitCO/qwen3.5-9B-tau2bench-retail-traces` | ~8.3k + ~5k + 1.5k + 0.5k + 0.5k | yes (tau2 gpt-4o-mini: 77% fail) | yes: 30 policy models per task; fuvty up to 8 trials/task | **Recommended M6 domain** |
| (d) Terminal (Terminal-Bench 2.x) | `yoonholee/terminalbench-trajectories` (52k), `mercor/ApexAgentsRecipe-TBench2_1-EvalTraces`, `openguardrails/...`, `Lottolabs/...`, Terminal Wrench (GitHub) | 52k trials (34k with steps) + ~1k + 178 + ~160 | yes (60% fail) | yes: ~5 trials per (task, agent), 109 agent/model combos | Largest, cleanest set; but overlaps SWE (CLI coding) — use as held-out-domain eval + optional training |
| (a) Web | `McGill-NLP/agent-reward-bench` (1.3k, human labels incl. **looping**), `apurvaga/go-browse-wa` (164k steps ≈ 30k trajectories, traj_reward), `OpenHandsCommunity/eval-output-webarena` (unverified, ~2.4k) | ~1.3k + ~30k + ~2.4k | yes (go-browse 65% fail) | ARB: 1–6 runs/task (different models) | Good **eval** set (human `stuck` labels); training-scale data thin; own generation needs WebArena Docker hosting |
| (c) General assistant (GAIA) | `DCAgent2/gaia_127_*-traces` (~140 model runs × ~380 trials), `Intelligent-Internet/ii-agent_gaia-benchmark_validation` (165) | tens of thousands (undocumented, no license) + 165 | yes | yes (3 episodes/task/model, 140 models) | Usable for eval; provenance/licensing unclear; tasks are GAIA validation (contamination risk) |
| (e) Math PRM | PRM800K (MIT), Math-Shepherd, ProcessBench, PRMBench | 800k step labels / 445k solutions | n/a | n/a | Optional auxiliary signal only (≤10% mixture ablation); not a substitute for agent data |

**Recommendation for Milestone 6: tool-use agents on tau-bench / tau2-bench.**
Reasons: (1) outcome labels are programmatic (DB-state match + NL assertions), not LLM-judged; (2) the same 278/165
tasks are run by 30 policy models, so held-out-policy splits and cross-policy `best_next` candidate sets are
free; (3) three task families (airline / retail / telecom) give clean held-out-task-family splits; (4) the
message format (system policy, user, assistant `tool_calls`, `tool` results) maps directly to our
action/observation schema with thoughts optional; (5) total size is ~400 MB; (6) generating more data is cheap
and fully offline-capable (tau2-bench is pip-installable; the user simulator can be an open model on the H100).
Terminal-Bench trajectories are the largest and best-documented dataset found, and I would still convert them,
but the domain is close enough to SWE that it is a weaker "generality" claim; it is an excellent held-out-domain
eval. Web agents are the most *different* domain but the labeled data is small and self-generation requires
hosting the WebArena Docker environments (7 sites, ~200 GB of images) or a ServiceNow instance for WorkArena.

**Math PRM data:** include only as an ablation. Math-Shepherd's MC step labels are literally "does a prefix still
lead to a correct answer", i.e. our `p_success`-from-prefix target; PRM800K's −1/0/+1 ratings map to a 3-level
`progress`. But there are no observations/tools, the text distribution is far from agent logs, and R2E-Gym-style
"confident language" shortcuts are exactly what math CoT rewards. Expect little transfer; worth one run.

---

## (a) Web agents

### a1. `McGill-NLP/agent-reward-bench` (AgentRewardBench)
- URL: https://huggingface.co/datasets/McGill-NLP/agent-reward-bench — paper arXiv:2504.08942
- License: **none stated** on card (custom "Terms of Use"; derived from WebArena/VWA/WorkArena/AssistantBench). Not gated.
- Size: 48,143 files, 38.4 GB total, dominated by `screenshots/` (27,299 PNG) and `judgments/` (19,530 LLM-judge JSONs).
  Trajectories: `cleaned/` = **1,302 JSON files** (WebArena 398, VisualWebArena 300, WorkArena 472, AssistantBench 132).
  Some cleaned JSONs are 100–400 MB because they embed the full `axtree_obj` per step.
- Policy models: GenericAgent (AgentLab/BrowserGym) × {gpt-4o-2024-11-20, claude-3.7-sonnet, Llama-3.3-70B, Qwen2.5-VL-72B}.
- Labels (`data/annotations.csv`, 1,408 expert annotation rows; a few trajectories double-annotated):
  - `trajectory_success`: Successful 395 / Unsuccessful 1,012 / Unsure 1 (28% success)
  - `trajectory_looping`: Yes 711 / No 697 — **a human "stuck" label**, directly usable to validate our `stuck` head
  - `trajectory_side_effect`: Yes 91 / No 1,316
  - `trajectory_optimality`: 1 Complete Failure 435, 2 Suboptimal 505, 3 Somewhat Optimal 202, 4 Completely Optimal 265
  - Per benchmark success: webarena 37.1% (n=501), visualwebarena 30.3% (300), workarena 22.7% (475), assistantbench 7.6% (132)
- Multiple runs per task: 451 unique (benchmark, task_id); trajectories per task: 1×100, 2×100, 4×150, 5×98, 6×3 (different policy models on the same task).
- Schema of one cleaned trajectory (`webarena.400.json`): top keys `benchmark, agent, model, valid, experiment, goal, seed, model_args, flags, summary_info{n_steps, cum_reward, terminated, truncated, err_msg, stats.*}, package_version, steps[]`.
  Step keys: `num, reasoning, action, screenshot_path, url, open_pages_urls, focused_element, last_action_error, stats, axtree, axtree_obj, chat_messages, bounding_boxes, extra_element_properties, axtree_pruned`.
  → task = `goal`; action = `action` (BrowserGym DSL, e.g. `goto('https://www.reddit.com/')`); observation = `axtree_pruned` (1–2k chars) or `axtree`; thought = `reasoning`; error = `last_action_error`; outcome = annotation CSV (human) or `summary_info.cum_reward` (benchmark evaluator; note WA evaluator is noisy, that is the paper's point).
- Sample (annotations rows + first step, abbreviated):
```
annotator_name,benchmark,task_id,model_name,trajectory_success,trajectory_side_effect,trajectory_optimality,trajectory_looping
A,webarena,webarena.177,GenericAgent-Qwen_Qwen2.5-VL-72B-Instruct,Unsuccessful,No,2. Suboptimal,No
A,webarena,webarena.155,GenericAgent-Qwen_Qwen2.5-VL-72B-Instruct,Unsuccessful,No,3. Somewhat Optimal,Yes
F,workarena,workarena.servicenow.dashboard-retrieve-catalog-and-max-order-apple-watch-l2,GenericAgent-gpt-4o-2024-11-20,Unsuccessful,No,1. Complete Failure,Yes
--- cleaned/webarena/GenericAgent-anthropic_claude-3.7-sonnet/.../webarena.400.json
goal: Change my reddit bio to "Pro Python Developer with 20 years of Experience"
model_args: {"model_name": "anthropic/claude-3.7-sonnet", "max_total_tokens": 16384, "temperature": 0, "vision_support": true}
summary_info: {"n_steps": 1, "cum_reward": 0, "terminated": true, "truncated": false, "err_msg": null, ...}
steps[0].num = 0
steps[0].reasoning = <action>\ngoto('https://www.reddit.com/')\n</action>
steps[0].action = goto('https://www.reddit.com/')
steps[0].url = https://wa-forum-xl-1.mcgill-nlp.org/
steps[0].last_action_error = ""
steps[0].axtree_pruned =
  [24] navigation ''
  [27] navigation ''
      [30] link 'Home'
          StaticText 'Postmill'
      [40] list ''
          [41] listitem ''
              [42] link 'Forums'
          [43] listitem ''
              [44] link 'Wiki'
      [45] Section ''
          [54] searchbox 'Search query'
      [55] list ''
          [56] listitem ''
              [57] link 'Notifications (0)'
  [113] main '' ...
steps[0].stats = {"n_retry_llm": 1, "input_tokens": 2760, "output_tokens": 125, "cost": 0.010155}
steps[0].chat_messages = [{"role":"system","content":"You are an agent trying to solve a web task based on the content of the page and user instructions..."}, {"role":"user","content":[{"type":"text","text":"# Instructions\nReview the current state of the page ..."}]}]
```
- Assessment: the best **labeled** web set; small (1.3k) but has human success + looping + optimality labels and 4 policies per task. Screenshots can be skipped (download only `cleaned/` + `data/`, but note some cleaned files are huge because of `axtree_obj`; strip it on ingest). Intended as a judge benchmark: use for eval / calibration, and if used for training, hold out whole benchmarks.

### a2. `apurvaga/go-browse-wa` (Go-Browse, WebArena) — step-level with trajectory reward
- URL: https://huggingface.co/datasets/apurvaga/go-browse-wa (raw variant with screenshots: `apurvaga/go-browse-wa-raw`, 194 parquet files, 28.7 GB, 196,462 rows). Paper: Go-Browse, arXiv:2506.03533.
- License: none stated. Not gated. No README.
- Size: 848 MB (card), **164,533 step rows**; features printed:
  `step_idx:int64, step_data:{prompt:[{role,content}], completion:[{role,content}]}, traj_reward:float64, next_step_idx:int64, traj_length:int64 (1–20), step_number:int64`.
- Each row is one step: `prompt` = system instructions + `# Goal` + current page accessibility tree (+ history); `completion` = a JSON `{"thought": ..., "action": ...}` in the BrowserGym DSL. Rows chain via `next_step_idx` (−1 ends a trajectory). `traj_reward` is the WebArena evaluator reward (0/1) for the whole trajectory.
- Verified on shard `train-00000-of-00009.parquet` (40.6 MB, 18,282 steps): **3,366 trajectories, reward 1.0 = 1,190 / 0.0 = 2,176 (35% success)**; 5.4 steps per trajectory on average; `traj_length` mode 10 (866) then 2 (668), 1 (433). Step-level reward split 5,152 / 13,130. Extrapolated: ≈30k trajectories over 164,533 steps (9 shards; other shards not checked).
- Policy model: trajectories were collected by the Go-Browse exploration procedure (paper; GPT-4o-class explorer plus trained Qwen policies); the policy is not stored per row — treat as "unknown".
- Sample (row 0):
```
step_idx=0  step_number=0  traj_length=5  next_step_idx=1  traj_reward=1.0
prompt[system]: # Instructions
  You are a UI Assistant, your goal is to help the user perform tasks using a web browser.
  Review the instructions from the user, the current state of the page and all other information to find the best possible next action to accomplish your goal. Your answer will be interpreted and executed by a program...
prompt[user]: # Goal
  Find driving directions from Central Park, New York to Times Square, New York using the Car (OSRM) option.

  # Current page Accessibility Tree
  RootWebArea 'OpenStreetMap', focused, url='http://ec2-3-148-123-246.us-east-2.compute.amazonaws.com:3000/#map=7/42.896/-75.108'
      [44] banner ''
          [45] heading 'OpenStreetMap logo OpenStreetMap'
              [46] link 'OpenStreetMap logo OpenStreetMap', url='http://ec2-3-148-123-246...'
          [51] navigation ''
              [95] link 'Edit', url='http://ec2-3-148-123-246...:3000/edit#map=7/42.896/-75.108'
              [96] button ''
              [102] link 'History', ...
completion[assistant]: {"thought": "Looking at the current page, I can see I'm on OpenStreetMap. To find directions, I need to click on the 'Find directions between two points' link which has a directions icon. I can see this element with ID 149 in the accessibility tree.", "action": "click('149')"}
```
- Assessment: the only large web set with per-trajectory reward and verified failures (65%). Reconstructing trajectories = follow the `next_step_idx` chain. Prompts already contain the observation, so ingestion is straightforward; thoughts are separable (`thought` key) for the thoughts-removed view. Caveat: all on WebArena's 5 self-hosted sites and Go-Browse's own auto-generated tasks (not the 812 official ones) → split by site; policy unknown.

### a3. `OpenHandsCommunity/eval-output-webarena` — OpenHands BrowsingAgent on WebArena (unverified)
- URL: https://huggingface.co/datasets/OpenHandsCommunity/eval-output-webarena. No README, no license. Not gated.
- Files: `BrowsingAgent/{gpt-4o-2024-05-13, claude-3-5-sonnet-20240620, gpt-3.5-turbo-0125}_maxiter_15_N_v1.0/output.jsonl` — 11.8 / 10.5 / 9.5 GB, plus `metadata.json`.
- Format: OpenHands eval output (one JSON per task: `instance_id, instruction, history, metrics, test_result{reward}`), 812 WebArena tasks × 3 models ≈ 2.4k trajectories with reward. Could not stream (connection resets); schema inferred from the OpenHands eval harness, **unverified**.
- Assessment: probably the largest full-observation WebArena run set with rewards, but 32 GB and undocumented. Worth a targeted download of one file later if we pursue web.

### a4. `webarena-x/webarena-infinity-trajectories` — successes only
- MIT. 2,329 trajectories (Gemini 2.5 Flash 978, Kimi K2.5 643, Qwen2.5-VL-Plus 708) on 13 auto-generated web apps; `history.json` (browser-use / model-specific formats) + `result.json{passed, verifier_message, steps}` + screenshots; 3.0 GB, 28k files. Card: "Successful browser-agent trajectories" → **no failures**. Demo-only; not useful for value labels except as positives.

### a5. `xlangai/AgentTrek` — synthetic successful demonstrations
- License unspecified. 52,594 dialogue turns (`messages` list, BrowserGym-style prompt with axtree + action), 1.8 GB single JSON. Tutorial-guided replays filtered for success → **no failure labels**. Demo-only.

### a6. `Hongliang1997/OpenWebVoyager-IL-Trajectories-GPT-4o` (and `WebVoyager-Trajectories-GPT-4V`)
- Apache-2.0, 6.1 GB of zips (per-task folders with accessibility trees + screenshots). Imitation-learning phase = GPT-4o trajectories auto-judged successful → **no failures**. Not loadable via `datasets` (mixed JSON).

### a7. `mlfoundations-utg-dev/mind2web-utg-eval-trajectories`
- No license. 360 browser-use v0.12 trajectories (90 Mind2Web tasks × {Haiku 4.5, Gemini 2.5 Flash} × 2 conditions), Gemini-judged SUCCESS/PARTIAL/FAILURE (success ≈ 36–42%). 7.2 GB, mostly screenshots; `trajectories/<task>/<cond>/history.json` + `results/eval_results_*.json`. LLM-judged outcome, small. Eval-only candidate.

### a8. `agentlabtraces/agentlabtraces` — BrowserGym ecosystem paper traces
- 207 GB as 5 split tar parts, no schema, no license. Impractical; skip.

### a9. Demonstration-only / not trajectories (checked, not useful for outcome labels)
`osunlp/Mind2Web` (CC-BY-4.0 human demos), `osunlp/Multimodal-Mind2Web`, `McGill-NLP/weblinx-browsergym` (CC-BY-NC-SA), `iMeanAI/Mind2Web-Live`, `osunlp/Online-Mind2Web` (tasks, gated), `ServiceNow/WorkArena-Instances` (task instances, manual gating), `AmineHA/Webarena-Verified-Submissions` (empty repo), `microsoft/webgym_tasks` (tasks only).

### a10. Generating our own web trajectories (what it would take)
- WebArena/VisualWebArena: host 5–7 site Docker images (GitLab, Reddit/Postmill, Shopping, Shopping-Admin, Map, Wikipedia; ~200 GB, needs a beefy box; official AMIs exist) + BrowserGym/AgentLab with an open policy model served by vLLM. Evaluator gives programmatic reward per task (812 tasks). Reset between episodes is slow (minutes) — the main cost.
- WorkArena: needs a free ServiceNow developer instance (rate-limited, ToS constraints).
- WebArena-Infinity (13 auto-generated apps, MIT) is lighter to host and comes with verifiers; worth considering if we want web at all.
- Realistic budget: a week of engineering plus a dedicated CPU server; not needed for M6 if we pick tool-use.

---

## (b) Tool-use agents

### b1. `AgentSuite/tau2-bench-trajectories` — **primary candidate**
- URL: https://huggingface.co/datasets/AgentSuite/tau2-bench-trajectories. License: **none stated** (tau2-bench itself is MIT; ask/confirm before release). Not gated.
- Size: 32 files, 290 MB; one `{model}.jsonl` per policy model, **30 models** (DeepSeek-R1/V3/V3.2, Kimi-K2, Qwen3-235B variants, Qwen3-Coder-480B, claude-4-opus/sonnet, claude-4.5-sonnet (thinking on/off), gemini-2.5-flash/pro, gpt-4.1 family, gpt-4o family, gpt-5, gpt-5-nano, gpt-oss-20b/120b, o3-high, o4-mini-high) × **278 tasks** (telecom 114, retail 114, airline 50) ≈ **8,340 trajectories**. User simulator: gpt-4o-2024-08-06.
- Fields: `model_path, user_model_path, benchmark_name, task_name (domain), sampling_params, user_sampling_params, messages[], eval_result{score, db_match}, meta{id, is_correct, finish_reason, duration_seconds, agent_cost, reward_details{db_reward, reward_breakdown, nl_assertions}, task_description, task_instructions, task_id}`.
- Outcome balance (gpt-4o-mini file, n=278): score 1.0 = 65, 0.0 = 213 (23% pass). Finish reasons include `user_stop` and `max_steps`. Messages per trajectory: min 9 / median 33 / max 202.
- Runs per task: exactly 1 per model (meta.id unique within a file) → 30 runs per task across policies. Good for held-out-policy splits and cross-policy `best_next` candidates at matched turn index.
- Mapping: task = system policy + `meta.task_instructions.reason_for_call` (user scenario is *hidden* from the agent — the value model should only see the agent-visible transcript); steps = assistant turns (text or `tool_calls`) and `tool` results / user replies as observations; thoughts = reasoning content where present (thinking-on variants); outcome = `eval_result.score`.
- Sample (gpt-4o-mini, telecom task, score 0.0, finish_reason max_steps):
```
model_path=deepseek-ai/DeepSeek-R1-0528  user_model_path=openai/gpt-4o-20240806  benchmark_name=tau2-bench  task_name=telecom
eval_result={"score": 1.0, "db_match": true}
meta.id="telecom_[mobile_data_issue]airplane_mode_on|data_mode_off[PERSONA:None]"  meta.is_correct=true  meta.finish_reason="user_stop"
messages[0] system: "<main_policy>\n# Telecom Agent Policy\n\nThe current time is 2025-02-25 12:08:00 EST.\n\nAs a telecom agent, you can help users with technical support, overdue bill payment, line suspension, and plan options. ..."
--- (gpt-4o-mini file, row 3, score 0.0, finish_reason max_steps, 101 assistant / 97 tool / 3 user turns)
[assistant] "Hi! How can I help you today?"
[user]      "Hi there! I'm having trouble with my mobile data. It either stops working or is very slow. I'm currently abroad in France..."
[assistant] "I can help you with that! First, I need to check a few things... Could you please provide me with the phone number associated with your account?"
[user]      "Sure, my phone number is 555-123-2002."
[assistant] tool_calls=[{"name": "get_customer_by_phone", "arguments": {"phone_number": "555-123-2002"}}]
[tool]      {"customer_id": "C1001", "full_name": "John Smith", "date_of_birth": "1985-06-15", "email": "john.smith@example.com", "phone_number": "555-123-2002", "address": {...}, "account_status": "Active", ...}
[assistant] tool_calls=[{"name": "get_data_usage", "arguments": {"customer_id": "C1001", "line_id": "L1001"}}]
[tool]      {"line_id": "L1001", "data_used_gb": "3.2", "data_limit_gb": "5.0", "data_refueling_gb": "0.0", "cycle_end_date": "2025-02-28"}
...  (agent loops on diagnostics until max_steps → natural `stuck` positives)
meta.reward_details={"db_reward": null, "reward_breakdown": null, "nl_assertions": null}
meta.task_instructions={"domain": "telecom", "reason_for_call": "You mobile data is not working properly...", ...}
```

### b2. `AgentSuite/tau-bench-trajectories` — same layout, tau-bench v1
- 30 models × 165 tasks (retail 115, airline 50) ≈ 4,950 trajectories; 112 MB; no license stated. gpt-4o-mini: 72/165 pass (44%). Messages per trajectory min 6 / median 28 / max 62. `tool_calls` use the OpenAI `function{name, arguments}` form here (tau2 file uses flat `name/arguments`) — normalise on ingest.
- Also `AgentSuite/BFCL_V4-trajectories` (30 models × 5,865 items, 1.45 GB) — mostly single-turn function-calling items; `datasets` fails to parse (schema drift in `meta.error`). Only the BFCL multi-turn subset would be relevant; low priority.

### b3. `fuvty/tau-bench-synthetic` — multi-trial synthetic tau-bench tasks
- Apache-2.0. 16.5 MB. Configs: `tasks` (280 synthetic retail/airline tasks, GT-first construction, 183/280 passed env validation), `traj-GLM5` (**1,464 GLM-5 trajectories with reasoning, up to 8 trials per task, reward 0/1**; pass 795/1,000 retail, 405/464 airline), `sft-GLM5` (4,270 per-turn rows from passing trajectories).
- Value: the only tool-use set with **same-policy repeated trials per task** (needed for per-task pass-rate baselines / advantage and `best_next` from branching). Pass rate is high (80–87%), so failures are ~250.

### b4. `snorkelai/Tau2-Bench-Airline-With-Code-Agents`
- Apache-2.0, 500 rows, 4.8 MB parquet. Fields: `task_id, model, version (original|code-generation), user_scenario{...}, reward, db_diff, messages/trace ...` (see probe file). Models include Claude Sonnet 4.5 and GPT-5. Card gives db_diff breakdown (e.g. 33.6% "DB not updated at all but updates were required" for code agents). Small but two scaffolds on the same 50 airline tasks.

### b5. `KermitCO/qwen3.5-9B-tau2bench-retail-traces`
- License "other". 546 deduped Qwen3.5-9B retail traces (114-task pool, with/without memory injection) with `canonical_reward` (0/1, DB + NL-assertion), plus blind LLM-judge process-quality scores (0–5) and `termination_reason`. Messages include full tau2 turn objects. Useful as a small-open-policy slice and as a process-quality sanity check.

### b6. `changdae/tau2-uq-artifacts`
- MIT, 1.7 GB: tau2 trajectories for Kimi-K2.5 and gpt-4.1/Kimi pairs across airline/retail/telecom plus **token-level logprobs** (2.2M rows). Trajectory JSONs failed to stream (one file empty). Interesting later for a "policy-entropy" baseline; not needed now.

### b7. `inclusionAI/AReaL-tau2-data`
- Apache-2.0, 970 MB. `tau2_sft_train.jsonl` = 33,531 **turn-level** samples (messages + `answer` with `thinking` + `metadata{correct, reward, source_dialog_id, turn_index}`) from synthetic SEA-generated dialogs; `tau2_rl_train.jsonl` = 1,982 task specs + DB snapshots (for rollouts). Sample metadata shows `correct: 1, reward: 1.0`; unclear whether negatives exist (unverified). Synthetic user; treat as supplementary.

### b8. `Salesforce/APIGen-MT-5k`
- CC-BY-NC-4.0 (non-commercial — avoid for release weights), 5,000 verified **successful** multi-turn trajectories (retail/airline, ShareGPT format). No failures → demos only.

### b9. Others seen, not useful
`Toprak1yu/agent-tool-use-trajectories` (10k synthetic ChatML, no outcome), `Maurus/ToolBench`/`Yhyu13/ToolBench_toolllama_G123_dfs` (ToolBench DFS trees, LLM-judged "pass" per query, noisy RapidAPI observations; old), `bespokelabs/bfcl-v3-*` and `AgentSuite/BFCL-trajectories` (mostly single-turn).

### b10. Generating our own tool-use data
`pip install tau2-bench`; agent model = any OpenAI-compatible endpoint (vLLM on H100), user simulator = another LLM (an open 30B model works; official runs use gpt-4o — API cost if we replicate exactly). Rewards are deterministic Python checks; ~1–5 min per episode; 278 tasks × N trials is cheap. This is the easiest domain to scale on-policy with small open models (which is exactly what we need for `best_next` branching and for policy-held-out tests).

---

## (c) General assistants (GAIA-style)

### c1. `DCAgent2/gaia_127_*-traces` family (DCAgent / DCAgent2 / DCAgent3 orgs)
- 142 repos matching `gaia_127` (e.g. `DCAgent2/gaia_127_Kimi_K2_5_20260430_052932-traces`, `..._GLM_5_...`, `..._Qwen3_32B_...`, `..._SWE_agent_LM_32B_...`, many `DCAgent3/gaia_127_a3_rl_*` RL checkpoints). No README, no license, no card.
- Verified one: 379 rows, 62.7 MB parquet. Features: `conversations[{role,content}], agent (terminus-2), model, model_provider, date, task (GAIA task uuid), episode, run_id, trial_name, result (string: "1.0"/"0.0"/"AgentTimeoutError"/"ContextLengthExceededError"), verifier_output`.
  127 unique tasks, **3 episodes per task** (125 tasks ×3, 2 ×2). Result: 1.0 = 219, 0.0 = 112, AgentTimeoutError = 47, ContextLengthExceededError = 1 (58% pass for Kimi-K2.5). Turns per trajectory median 48, max 506.
- The agent is Terminus-2 (terminal harness) with a `web_search` CLI tool inside a container; observations are terminal output. Verifier = exact-match on GAIA answer.
- Sample (failed episode, task 50f58759-…):
```
[assistant] Analysis: I'm starting fresh in a Linux environment. I need to solve a complex question about counting Twitter/X post citations on English Wikipedia pages for each day of August in the last June 2023 versions...
            Plan: ... 1. Search for information about how to access Wikipedia page history ...
[user]      New Terminal Output:
            root@a0637333:/app# web_search "Wikipedia API get page revision June 2023 specific date"
            Traceback (most recent call last): ... requests.exceptions.HTTPError: 432 Client Error:  for url: https://api.tavily.com/search
[assistant] Analysis: The web_search tool is not working (getting a 432 error). I need to use bash and python3 tools ... access Wikipedia's API directly
            <tool_call>{"name": "bash_command", "arguments": {"keystrokes": "python3 -c \"import requests; print('requests available')\"\n"}}</tool_call>
[user]      New Terminal Output: requests available
[assistant] Analysis: Good, Python's requests library is available... Plan: create a Python script that loops through all 31 days of August ...
[user]      New Terminal Output: root@a0637333:/app# cat > solve.py << 'EOF' ... 
verifier_output: === STARTING GAIA TEST EXECUTION ===  Agent answer: '2'  Expected answer: '3'  ✗ Test failed - answer is incorrect
```
- Assessment: large (potentially ~50k trajectories across models), multi-episode, programmatic outcome, but undocumented, unlicensed, and the tool failure above (Tavily 432) shows infra noise in labels. Tasks are GAIA validation questions → any model trained on these must not be evaluated on GAIA. Usable as an eval/transfer set with a caveat; not as a headline domain.

### c2. `Intelligent-Internet/ii-agent_gaia-benchmark_validation`
- No license/README. 165 rows (GAIA validation), 473 MB parquet. Features: `task_id, Question, Level, file_name, Annotator Metadata{Steps, Tools,...}, trace (JSON string of tool_call/tool_result events: sequential_thinking, tavily_web_search, ...; up to ~900k chars), prediction, Final answer, judge (bool)`. One run per task (Claude via Vertex). Good small eval set with judged outcome; no multi-run.

### c3. `smolagents/gaia-traces` and `shamikbose89/gaia_traces` — **no outcome label**
- 1,204 smolagents CodeAgent traces (gpt-4o 465, Llama-4-Scout 390, Qwen2.5-Coder-32B 349); `model_id, system_prompt, messages[]` (+ `question, gaia_id, answer` in shamikbose89's copy, but `gaia_id` is populated for only 71 rows). Thought/Code/Observation format is clean, but success must be recomputed by matching `final_answer(...)` to GAIA ground truth; my substring heuristic scored only 4% so a proper GAIA scorer is needed. 4.5 MB. Secondary.

### c4. `PatronusAI/TRAIL`
- MIT but "must not be used for training systems intended to automate human evaluation"; gated (auto). 148 OpenTelemetry traces (118 GAIA via Open Deep Research, 30 SWE-bench) with 841 span-level error annotations (reasoning / execution / planning categories, impact levels). Eval-only; useful as a qualitative `progress`/error taxonomy reference.

### c5. Others
`sammshen/gaia-sonnet-traces` (112 raw HTTP proxy sessions, MIT, no outcome), `timchen0618/browsecomp-plus-trajectories` (15,249 rows, 1.2 GB, no card; unverified), `smolagents/post-train-bench-traces` (non-GAIA benchmarks), `meta-agents-research-environments/gaia2` (CC-BY-4.0 **environment + tasks**, not trajectories — a viable place to generate our own general-assistant data with programmatic checks), `gaia-benchmark/GAIA` (gated tasks).

### c6. Generating our own
Open Deep Research / smolagents on GAIA validation with an open policy, scored by the GAIA exact-match scorer; needs a search API (paid) or the Gaia2/ARE simulated apps (offline, free, CC-BY-4.0) — the latter is the credible route if this domain is chosen.

---

## (d) Terminal agents

### d1. `yoonholee/terminalbench-trajectories` — **best single dataset found in any secondary domain**
- URL: https://huggingface.co/datasets/yoonholee/terminalbench-trajectories. Apache-2.0. Not gated. 221 MB (2 parquet shards). Scraped from public Terminal-Bench 2.0 leaderboard submissions.
- Card stats: **52,104 trials**, 89 tasks, 109 agent/model combos, 26 scaffolds, 49 models, overall pass 39.6%, median 21 / mean 47 steps, **34,462 trials have step traces** (others only metadata), typically 5 trials per (task, agent).
- Features (printed): `task_name, agent, model, reward:int64, duration_seconds, input_tokens, output_tokens, cache_tokens, cost_cents, trial_name, trial_id, started_at, ended_at, steps:string (JSON)`.
  Step object: `{src: user|agent|system, msg: <agent text/reasoning>, tools: [{fn, cmd}] | null, obs: <tool output truncated to 5,000 chars> | null}`.
- Verified on shard `train-00001-of-00002.parquet` (106.6 MB, 26,052 trials, all with steps): **reward 0 = 16,460 / 1 = 9,592 (36.8% pass)**; 45 tasks, 26 scaffolds, 49 models; trials per (task, agent, model): 5 for 4,112 groups, 10 for 475, 1 for 136. (The first rows of shard 0 have `steps=null` — Factory Droid submissions without traces.)
- Sample (failing trial; steps 0–8 of 54):
```
task=make-doom-for-mips  agent=terminus-2  model=Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8@together_ai  reward=0  n_steps=54
--- step 0 src=system
  msg: 'You are an AI assistant tasked with solving command-line tasks in a Linux environment. You will be given a task description and the output from previously executed commands... Format your response as JSON with ...'
--- step 1 src=user
  msg: "I have provided /app/doomgeneric/, the source code to doom. I've also wrote a special doomgeneric_img.c that I want you to use which will write each drawn frame to /tmp/frame.bmp. I've finally provided vm.js that will expect a file called doomgeneric_mips and will run it. Please ..."
--- step 2 src=agent
  msg: "Analysis: I'm starting with a root shell in the /app directory. The task requires me to build a MIPS ELF binary called 'doomgeneric_mips' ..."
  tools: [{"fn": "bash_command", "cmd": "ls -la\n"}]
  obs: 'New Terminal Output:\nroot@9a667cce:/app# ls -la\ntotal 4164\n-rw-r--r-- 1 root root 4196020 Sep 13 17:55 doom.wad\ndrwxr-xr-x 1 root root 25 Sep 13 17:55 doomgeneric ...'
--- step 3 src=agent
  msg: 'Analysis: I can see the /app directory contains: 1) doom.wad, 2) doomgeneric (source), 3) vm.js. Now I need to explore the doomgeneric directory ...'
  tools: [{"fn": "bash_command", "cmd": "cd doomgeneric\n"}]
  obs: 'New Terminal Output:\n\nroot@9a667cce:/app# cd doomgeneric\nroot@9a667cce:/app/doomgeneric#'
--- step 4 src=agent
  msg: "Analysis: I've changed into the doomgeneric directory. Now I need to see what files are in this directory ... Plan: I'll list the contents ..."
  tools: [{"fn": "bash_command", "cmd": "ls -la\n"}]
  obs: 'New Terminal Output:\n\nroot@9a667cce:/app/doomgeneric# ls -la\ntotal 40\ndrwxr-xr-x 8 root root 4096 Sep 13 17:55 .git ...'
--- step 5 src=agent
  msg: "Analysis: I see the doomgeneric directory structure. There's another doomgeneric subdirectory which likely contains the actual source ..."
  tools: [{"fn": "bash_command", "cmd": "cd doomgeneric\n"}]
  obs: 'New Terminal Output:\n\nroot@9a667cce:/app/doomgeneric# cd doomgeneric\nroot@9a667cce:/app/doomgeneric/doomgeneric#'
--- step 6 src=agent
  msg: "Analysis: I'm now in the /app/doomgeneric/doomgeneric directory. I need to list the contents ... locate the special doomgeneric_img.c file."
  tools: [{"fn": "bash_command", "cmd": "ls -la\n"}]
  obs: 'New Terminal Output:\n\n... total 2368\n-rw-r--r-- 1 root root 1659 Sep 13 17:55 Makefile ...'
--- step 7 src=agent
  msg: "Analysis: I've found the doomgeneric_img.c file ... There's also a Makefile which ..."
  tools: [{"fn": "bash_command", "cmd": "cat Makefile\n"}]
  obs: "New Terminal Output:\n\n... CC=clang  # gcc or g++\nCFLAGS+ ..."
--- step 8 src=agent
  msg: "Analysis: I've examined the Makefile ... To build for MIPS and use the special doomgeneric_img.c, I need to modify the ..."
  tools: [{"fn": "bash_command", "cmd": "grep -n \"frame.bmp\" doomgeneric_img.c\n"}]
  obs: 'New Terminal Output:\n\n167:    printf("DoomGeneric initialized. Frames will be saved to /tmp/frame.bmp\\n");\n175:    writeBMPFile("/tmp/frame.bmp", ...'
```
- Mapping: task = first `user` step msg (instruction); action = `tools[].cmd`; observation = `obs`; thought = `msg`; outcome = `reward`; policy = `model`, scaffold = `agent`. Held-out-scaffold and held-out-policy splits are natural. Note: 89 tasks only → split by task for held-out tasks; heavy per-task repetition means per-task baseline pass rates are well estimated (good for `advantage`).

### d2. `mercor/ApexAgentsRecipe-TBench2_1-EvalTraces`
- MIT, 317 MB, 1,074 files. 4 configs (Qwen3.5-397B and Qwen3.6-35B, before/after RL) × 89 tasks × **k=3 rollouts** = 1,068 per-trajectory JSONs (`<set>/tasks/<task>/traj_<id>.json`), failures and errors included; card table gives pass@1 ≈ 50.6% for the base 397B. Could not open a file on Windows (path > 260 chars; enable long paths or download with a short `local_dir`). Format unverified beyond the layout.

### d3. `openguardrails/terminal-bench-2.1-deepseek-v4-flash-trajectories`
- MIT, 73 MB, 1,137 files. DeepSeek-V4-Flash on all 89 TB-2.1 tasks with two scaffolds (terminus-2: 53/89, dsh: 61/89); per task: `trajectory.json` (ATIF format incl. `reasoning_content`) or `dsh-session.jsonl`, `result.json` (harbor reward), `verifier/test-stdout.txt`, `instruction.txt`. Clean, paired-scaffold data; small.

### d4. `Lottolabs/terminal-bench-2.1-qwen3.8-27b-traces`
- MIT, 578 MB. Qwen3.8-27B-GPTQ-4bit (local) on 89 tasks at thinking=xhigh (62/89) plus fallback reruns (medium/low/off) of failures: `traces/<task>/{instruction.txt, transcript.md, result.json, verifier.txt, agent/omp-*/omp.jsonl}`. A small-open-model slice with many failures; per-task result records.

### d5. `harithoppil/terminal-bench-2-trajectories`
- Apache-2.0, 72 MB. 3,723 rows (`all`), 5 models (Claude-Opus-4.6 2,213 trials 69% pass; Gemini-3.1-Pro 445 75%; GLM-5 445 52%; Kimi-k2.5 442 43%; Claude-Opus-4.5 178 55%). Fields `task_name, model, agent, prompt, response, reward(0/1), elapsed_seconds, is_ml_related`. `response` is a flattened text dump of the trajectory (from trajectory.json or stdout) — less structured than d1; subset of the same leaderboard data.

### d6. `nvidia/Nemotron-RL-Agentic-Terminal-Pivot-v1`
- CC-BY-4.0, 1.37 GB jsonl, 31,111 rows. Each row = one decision point (prompt = instruction + terminal history; `expected_answer` = teacher GLM-5.1 Terminus-2 action JSON) from **successful** trajectories on 630 ATCB tasks (≤5 successes per task). No failures, no per-trajectory outcome variation → not usable for value labels; possible source of "hard positives". Stream failed (network) — schema from card.

### d7. `m-a-p/TerminalTraj`
- License unspecified; 164 MB; 20,000 rows (card/paper: 50,733 verified trajectories over 32k Docker images). Features `query, messages[{role,content}]` in Terminus JSON-action format. Only verified-successful trajectories → demos only.

### d8. Terminal Wrench (GitHub `few-sh/terminal-wrench`, Apache-2.0)
- 331 reward-hackable environments, 6,289 trajectories (3,632 hack, 1,216 legitimate, 1,441 no-reward, 2,352 baselines) from Claude Opus 4.6 / Gemini 3.1 Pro / GPT-5.4, JSON. Not on HF. Valuable for a **robustness/anti-shortcut** test: a value model should not score "output-spoofing" hacks as progress.

### d9. Others
Dozens of `DCAgent*/terminal_bench_2_*` and `laion/terminal_bench_2_*` repos (same `conversations + result + verifier_output` layout as c1; various open checkpoints on TB2) — undocumented, unlicensed, but a large pool of small-open-model failures if needed. `harborframework/terminal-bench-2.0/2.1/3.0` = tasks (Apache-2.0) for generating our own runs with Harbor + vLLM (Docker on the CPU server; the same infra as SWE-bench).

---

## (e) Math process-reward data

| Dataset | HF id | License | Size | Label scheme |
|---|---|---|---|---|
| PRM800K | `tasksource/PRM800K` (mirror of openai/prm800k; also `trl-lib/prm800k`, `HuggingFaceH4/prm800k-trl-dedup`) | MIT | 477 MB (phase2_train 456 MB); trl-lib flattened: 41,177 solutions / 2.2 MB | human per-step rating **−1 / 0 / +1** (wrong / neutral-unhelpful / correct-helpful); multiple candidate completions per step with a `chosen_completion`; `finish_reason` found_error / solution / give_up |
| Math-Shepherd | `peiyi9979/Math-Shepherd` (`trl-lib/math_shepherd` flattened) | not stated | 793 MB; 444,655 solutions (422k train / 22k test) | automatic MC label per step: `+` if some sampled continuation from that step reaches the correct answer, else `−` (the `ки` token marks label positions) |
| ProcessBench | `Qwen/ProcessBench` | Apache-2.0 | 8 MB; 3,400 items (gsm8k 400, math 1,000, olympiadbench 1,000, omnimath 1,000) | `label` = index of the **first erroneous step** (−1 = all correct), `final_answer_correct` bool; gsm8k: 193 all-correct, first error at step 0/1/2/3 = 37/61/50/31 |
| PRMBench | `hitsmy/PRMBench_Preview` | Apache-2.0 | 20 MB; 6,216 items | `original_process` vs `modified_process`, `error_steps` indices, `classification` ∈ {redundency, circular, step_contradiction, domain_inconsistency, confidence, counterfactual, missing_condition, deception, multi_solutions} (~750 each) |

Verified numbers: PRM800K phase2_test (2,762 solutions, 458 problems, median 6 steps): ratings over all candidate completions +1: 18,174, −1: 6,080, 0: 2,002, null: 966; finish_reason found_error 2,077 / solution 587 / give_up 98. Math-Shepherd test: step labels True 67,134 / False 71,797; 36% of solutions have all steps `+`.

Samples:
```
# PRM800K (tasksource/PRM800K, phase2)
question: {"problem": "How many seconds are in 7.8 minutes?", "ground_truth_answer": "468"}
label.steps[0].completions: [{"text": "7.8 minutes is the same as 7 minutes and 0.8 minutes.", "rating": 1}]
label.steps[1].completions: [{"text": "Right, and since there are 60 seconds in a minute, then there are 60 * 7 = 420 seconds in 7 minutes.", "rating": 1}]
label.steps[4].completions: [{"text": "Right. Let's check our work. 7.8 minutes is the same as 7 minutes and 0.8 minutes.", "rating": 0}, {"text": "Exactly.\n\n# Answer\n\n468", "rating": 1}, ...]  chosen_completion: 1
label.finish_reason: solution
# trl-lib/prm800k flattened: prompt="How many seconds are in 7.8 minutes?"  completions=[5 steps]  labels=[true, true, true, true, false]

# Math-Shepherd (peiyi9979)
input: "Janet pays $40/hour for 3 hours per week of clarinet lessons and $28/hour for 5 hours a week of piano lessons. How much more does she spend on piano lessons than clarinet lessons in a year? Step 1: Janet spends 3 hours + 5 hours = <<3+5=8>>8 hours per week on music lessons. ки\nStep 2: She spends 40 * 3 = 120 on clarinet lessons per week. ки\n... Step 5: She spends 260 * 52 = 13520 on music lessons in a year. The answer is: 13520 ки"
label: "... Step 1: ... +\nStep 2: ... +\nStep 3: ... +\nStep 4: ... +\nStep 5: ... -"      task: GSM8K

# ProcessBench
{"id": "gsm8k-0", "generator": "Qwen2-7B-Instruct", "problem": "Sue lives in a fun neighborhood...", "steps": ["To find out how many more pink plastic flamingos...", "On Saturday, they take back one third...", ...], "final_answer_correct": false, "label": 1}

# PRMBench
original_process[4]: "This simplifies to $2p=0.58$. So $p=0.29$."   modified_process[4]: "This simplifies to $2p=0.58$. So $p=0.58$."
modified_steps: [5, 6]  error_steps: [5, 6]  classification: confidence
reason: "Steps 5 and 6 contain confident hallucinations... presented with unwarranted confidence"
```

How step labels map to our questions:
- `progress` (0 regressed / 1 no change / 2 small / 3 big): PRM800K −1 → 0, 0 → 1, +1 → 2 (never 3; math steps are uniform). Math-Shepherd `−` → 0, `+` → 2. ProcessBench/PRMBench give only the first-error index → steps before it 2, the error step 0, later steps masked.
- `p_success`: Math-Shepherd's per-step `+` is exactly "P(correct completion from this prefix) > 0" under a fixed sampler — the same quantity as our prefix-value target, so it is the most compatible auxiliary. PRM800K's human labels are a stricter, process-quality notion.
- `stuck`, `best_next`, `escalate`, `steps_left`: no analogue (PRM800K has multiple candidate completions per step with ratings, which could seed a pointer-head `best_next` over K math steps, but that is far from agent actions).
- Caveats: no observations/tool outputs; labels reward confident fluent math prose, which is the R2E-Gym shortcut we are trying to avoid; PRMBench's `confidence`/`deception` categories could be reused as a robustness probe. Recommendation: one ablation run with ≤10% math-PRM mixture on the `progress`/`p_success` heads; drop if it does not help held-out agent metrics.

---

## Open items / things to confirm before use
- Licenses not stated on card: `AgentSuite/*`, `McGill-NLP/agent-reward-bench` (terms of use only), `apurvaga/go-browse-wa`, `DCAgent*/gaia_127_*`, `m-a-p/TerminalTraj`, `peiyi9979/Math-Shepherd`, `Intelligent-Internet/ii-agent_gaia-benchmark_validation`. Contact authors or restrict to eval / research-only before publishing weights trained on them.
- Non-commercial: `Salesforce/APIGen-MT-5k` (CC-BY-NC-4.0), `McGill-NLP/weblinx-browsergym` (CC-BY-NC-SA-4.0) — exclude from release training.
- Unverified schemas (network failures): `OpenHandsCommunity/eval-output-webarena`, `mercor/ApexAgentsRecipe-TBench2_1-EvalTraces` per-trajectory JSON, `nvidia/Nemotron-RL-Agentic-Terminal-Pivot-v1` rows, `inclusionAI/AReaL-tau2-data` negatives, `timchen0618/browsecomp-plus-trajectories`.
- Datasets-server `/filter` indices for go-browse-wa and terminalbench-trajectories were still building; the balance numbers above come from one downloaded shard each (may not be representative of all shards).
