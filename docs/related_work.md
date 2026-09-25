# Related work for agent-compass

Compiled 2026-09-25 from fetched primary sources (arXiv, Hugging Face, vendor blogs/docs, GitHub). Every fact below was read in a fetched source; anything that could not be confirmed is marked **unconfirmed**. Numbers are quoted as reported by the source, not re-derived.

Project context: agent-compass is a small (1.5B-4B, Qwen-based, LoRA) calibrated value model that, in one forward pass with no generation, answers typed questions about an agent mid-task: `p_success`, `progress` (0-3), `stuck`, `best_next` (rank K candidate actions), `escalate`, `steps_left`. First domain: SWE-bench-style trajectories.

---

## 1. Fail-Fast, Restart-Smart (closest baseline; reproduce)

- **Title:** Fail-Fast, Restart-Smart: Early Failure Prediction and Restart for SWE Agentic Tasks
- **Authors / orgs:** Chenyu Wang, Yunbo Lyu, Junda He, Zhou Yang, Chenxing Zhong, Yaniv Harel, David Lo -- Singapore Management University, University of Alberta, Nanjing University of Science and Technology, Tel Aviv University
- **Date:** arXiv v1 4 Aug 2026 (cs.SE, cs.AI)
- **URL:** https://arxiv.org/abs/2608.03222 (HTML: https://arxiv.org/html/2608.03222)
- **Code/weights/data:** no URLs given in the paper for the monitor itself. Only the AgentStop baseline's code is referenced (github.com/brave-experiments/AgentStop). **Monitor weights, code and trajectories: not released as far as the paper states (unconfirmed otherwise).**

### Monitor (FailFast) -- everything needed to reproduce

| Item | Reported setup |
|---|---|
| Backbone | Qwen3-0.6B, frozen; LoRA r=16, alpha=32 on attention and MLP projections; LoRA dropout 0.05 |
| Heads | three linear heads kept in fp32: value head `s` (ultimate resolution), F2P head `a_f`, P2P head `a_p` |
| Input | `[ISSUE]` = task specification; `[WINDOW]` = the recent **8 steps** of thought + action + observation, plus a persistent pin of the latest patch-producing step. Each step shows "the agent's thought, its command (code edits receive a larger character budget; read-only commands are clipped to one line), a classified error tag, a self-test flag, a patch? flag, and the head/tail-truncated observation." Error-tag taxonomy and exact truncation lengths are not enumerated in the text. |
| Context cap | 4,096 tokens, enforced as a character budget at 3.5 chars/token |
| Includes agent thoughts? | **Yes** (thought text is part of every window step) |
| Labels | `y_final in {0,1}` terminal resolution. Auxiliary: F2P and P2P pass rates of an intermediate patch obtained by "replaying the prefix's bash commands in a fresh instance environment" and scoring with the official evaluator. Bradley-Terry preference pairs: successful vs failed prefixes from mixed instances, "matched within each step-fraction decile bucket". |
| Loss | `L = BCE(s, y_final) + lambda_f * BCE(a_f, g_f2p) + lambda_p * BCE(a_p, g_p2p) + lambda_r * [-log sigmoid(s_pos - s_neg)]`; deployed lambda_f=0.5, lambda_r=0.25, lambda_p=0 (P2P head gave no benefit) |
| Data | Policy Qwen3.6-27B-FP8 under mini-swe-agent on SWE-bench Verified, seeds 0-10 -> 11 runs x 500 instances. Instance-grouped split: 350 train / 50 val / 100 test (approx. 3,850 / 550 / 1,100 trajectories). Prefixes at every step up to T=100 (max steps). Trained "entirely out-of-fold; only calibration and threshold selection are fitted on evaluation-fold predictions". |
| Optimisation | peak LR 1e-4, cosine over 1,500 optimizer steps, weight decay 0.01; "2 GPUs in under 6 h with PyTorch 2.6" |
| Calibration | Platt scaling of the raw value logit on validation. A "meta score" `p_fail = sigmoid(w^T [v, g_f, phi] + b)` (logistic regression over calibrated value `v`, F2P estimate `g_f`, step fraction `phi`) is what the abort rule consumes. |
| Abort rule | Per-step after a minimum-step floor (phi_min = 20 steps; grid over floors {0.20..0.50}); score thresholds 0.30-0.96; either cumulative-vote **count-M** (M in [1,12]) or **sustained-K** consecutive votes (K in [1,8]). Operating points chosen by "budget-constrained grid search, maximizing recall subject to the target false-positive rate" on validation only. |
| Metrics | **Recall** = % of true failing runs flagged; **Precision** = % of true failures among aborted runs; **Fired** = % of trajectories aborted; **Saved** = fraction of total agent tokens reclaimed relative to running everything to completion; **FPR budget** = max fraction of successful runs wrongly terminated. **No AUROC / AUPRC / Brier / ECE are reported.** |

### Reported numbers

Table 1, monitor trained on Qwen3.6-27B, applied without retraining (5% FPR unless noted):

| Policy (vanilla resolve) | Recall | Precision | Fired | Saved |
|---|---|---|---|---|
| Qwen3.6-27B (66.6%) | 30.5% | 76.1% | 13.4% | 20.4% |
| Qwen3.5-9B (48.2%) | 30.5% | 76.1% | -- | -- (as extracted; identical to the 27B row, which is suspicious -- **verify against the PDF before quoting**) |
| Gemma4-31B (62.2%) | 27.2% | 82.1% | -- | -- |
| Gemini 3 Flash (Table 2) | 22.7% | 69.8% | 10.6% | 16.0% |
| Qwen3.6-27B at 25% FPR | 68.3% | 58.8% | 38.8% | 49.0% |

Baselines on Qwen3.6-27B at 5% FPR: AgentStop (gradient-boosted tree over token log-probs, token counts, step repetitions; reproduced from released code on the same data, extended to per-step decisions) 21.0% recall / 68.6% precision / 10.2% fired / 12.5% saved; Duration (step-count only) 19.8% / 67.3% / 9.8% / 11.4%. SWE-PRM (prompt-intervention PRM with Claude-Sonnet-4) *reduced* resolve rate by 3.2 pp.

Cross-policy token savings 14.6%-20.4% at 5% FPR; net energy/carbon reduction 14.5%-20.3% after monitor overhead; monitor overhead 0.1%-0.6% of policy compute. Ablations: the `[WINDOW]` is the most critical input (dropping it cut recall by 13.2 pp at one setting); window k=8 chosen, k=16 gave no dependable gain; F2P auxiliary head helped, P2P did not.

### RestartSmart
On abort at step t_f, replay edits, "locate the first edit at or after t_f and extend the cut until g consecutive steps pass without any further edits" (g=5), and offer the resulting diff to a fresh run as an **overlay** ("offered, not forced"; starts off; framed as "an unverified lead"; commands `overlay diff / on / off`). Results on Qwen3.6-27B: 10% FPR -> +3.2 pp resolve at 30.3% token overhead; 25% FPR -> 66.6% to 71.8% (+5.2 pp) at 43.8% overhead; cold restart at 25% FPR only +0.2 pp. Across policies at 10% FPR: +3.0-4.0 pp at 20.5%-36.9% overhead. Table 12 uses policy-specific monitors for restarts.

**Reproduction checklist for agent-compass:** Qwen3-0.6B + LoRA r=16/alpha=32, 8-step window with thoughts, 4k-token cap, BCE + BT pairwise (lambda=0.25) + F2P aux (lambda=0.5; requires replay infra -- optional in our first pass), Platt scaling, evaluate recall/precision/fired/saved at 5/10/25% FPR with a 20-step floor and count-M / sustained-K aggregation. Their data (mini-swe-agent x Qwen3.6-27B x 11 seeds on SWE-bench Verified) is not published; we must regenerate or substitute.

---

## 2. Nebius critic-guided search

### 2a. Blog: Leveraging training and search for better software engineering agents
- **Authors:** Golubev, Polezhaev, Zainullina, Trofimova, Badertdinov, Anapolskiy, Litvintseva, Karasik, Fisin, Skvortsov, Nekrashevich, Shevtsov, Abramov, Yangel (Nebius)
- **Date:** 15 Nov 2024. URL: https://nebius.com/blog/posts/training-and-search-for-software-engineering-agents
- Critic: LLaMA 3.1 70B Base, predicts **both value and action-value** with discounted reward-to-go targets, L2 loss with equal weighting, discount 0.85 (blog) "preventing delayed submission incentives"; rewards = terminal (0 or 20 on success) + per-step (0/1) from GPT-4o prompts flagging clearly poor actions; 3 epochs, batch 128. **Policy conditioning:** critic receives an LLM identifier string (e.g. `swe-agent-llama-3.1-70b-instruct`). Training data: 8.3k positive + 12.7k negative trajectories (21k), from SWE-bench dev + 3.3k issues from 1.3k permissive repos; after dedup / 30 shortest per problem, 2.7k used for the generator. **1-step lookahead:** 4 candidates/step, T=0.9, critic picks the max. Results: open-weight system 40.6% on full SWE-bench Verified; on Verified-50 Qwen-2.5-72B-Instruct ~15% -> fine-tuned ~35% -> +lookahead ~52%; +trajectory selection over 10 runs 48%. No critic accuracy/calibration metrics; no released critic weights.

### 2b. Paper: Guided Search Strategies in Non-Serializable Environments with Applications to Software Engineering Agents
- **Authors:** Zainullina, Golubev, Trofimova, Polezhaev, Badertdinov, Litvintseva, Karasik, Fisin, Skvortsov, Nekrashevich, Shevtsov, Yangel. ICML 2025. arXiv 19 May 2025. URL: https://arxiv.org/abs/2505.13652
- Critic: LLaMA3.1-70B (sensitivity: Qwen2.5-72B, LLaMA3.1-8B, Qwen2.5-7B -- small models underperform); unembedding replaced by a scalar linear layer; "a special token to the end of every agent turn" whose scalar is the action-value; **full trajectory** input, seq len 32,768; "policy identifiers into the critic's system prompt". Targets: TD(lambda), lambda=0.7 best; discounted return-to-go, higher gamma better; sparse terminal reward {0,1}; L2 loss. AdamW, LR 2e-6, 459 steps, batch 128, 4 epochs. Data: 80,000 trajectories from 6,500 issue-PR pairs over 2,500 Python repos + SWE-bench dev, multiple policy versions annotated.
- Search: lookahead K=8 optimal (saturates), T=0.9; trajectory selection N=5/10; final submission K=8, N=15.
- SWE-bench Verified (Qwen-72B FT policy): baseline 16.2% -> lookahead(K=4) 26.8% -> traj-sel N=5 27.2% / N=10 31.27% -> combined N=5 36.5% / N=10 41.7% -> final 40.8%. GPT-4o: 22.0% -> 27.2% (lookahead) -> 34.0% (sel N=5) -> 40.0% (combined N=5). No AUC/accuracy for the critic. Weights not released.

### 2c. Blog: Reasoning critics enable better parallel search for software engineering agents
- Yangel, Polezhaev; 1 May 2025. URL: https://nebius.com/blog/posts/reasoning-critics-parallel-search-for-agents
- DeepSeek-R1-Distill-Qwen-32B RL-fine-tuned to output CoT + GOOD/BAD; success probability = mean over 10-20 samples (saturates ~20). Trained on SWE-bench Extra trajectories. Findings vs Q-regression critic: regression critics degrade with more parallel samples, show "value hacking" in lookahead (predicted Q grows without outcome gains), and are more vulnerable OOD; precision-prioritising CoT critics are better OOD on other scaffolds/policies. Evaluated on Verified-50; no absolute resolve rates. No weights released.

### 2d. Datasets
- **nebius/SWE-agent-trajectories** -- 80,036 SWE-agent trajectories; fields `instance_id, model_name, target (bool resolved), trajectory, exit_status, generated_patch, eval_logs`; tasks from nebius/SWE-bench-extra + SWE-bench dev; patches evaluated by PR tests; CC-BY-4.0 (plus Llama 3.1 licence for outputs); 1.11 GB. https://huggingface.co/datasets/nebius/SWE-agent-trajectories
- **nebius/SWE-rebench-openhands-trajectories** -- 67,074 OpenHands v0.54.0 trajectories from Qwen3-Coder-480B-A35B-Instruct; 32,161 resolved; avg 64.3 turns, max 100; 1,823 repos; fields include `resolved` (int), `gen_tests_correct`, `pred_passes_gen_test`; CC-BY-4.0; 2.08 GB. https://huggingface.co/datasets/nebius/SWE-rebench-openhands-trajectories
- **Critic weights on HF: none found** (search returned only datasets).

---

## 3. SWE-Gym (verifier / ORM)

- **Title:** Training Software Engineering Agents and Verifiers with SWE-Gym. Pan, Wang, Neubig, Jaitly, Ji, Suhr, Zhang. arXiv v1 30 Dec 2024, v2 6 Jun 2025. https://arxiv.org/abs/2412.21139
- Environment: 2,438 Python instances from 11 repos; dataset `SWE-Gym/SWE-Gym` licence **MIT** (HF card).
- Verifier: 32B Qwen2.5-Coder-Instruct fine-tuned as an ORM. Input (OpenHands): full interleaved trajectory `[o1,a1,...,on,an]` incl. problem statement; MoatlessTools variant: task + context + patch. Output: logits of `<YES>`/`<NO>`, `r = exp(l_y)/(exp(l_y)+exp(l_n))`. Data (OpenHands): 2,636 trajectories -- 1,318 successful (443 off-policy GPT-4o/Claude + 875 on-policy) balanced with 1,318 failed, <=32k tokens.
- Best-of-N on SWE-bench Verified (OpenHands 32B): pass@1 20.6%; pass@8 37.8% vs best@8 29.8%; pass@16 42.8% vs best@16 32.0%. MoatlessTools 32B: 19.7% -> 26.3% at N=8.
- **Weights released:** `SWE-Gym/OpenHands-32B-Verifier`, `SWE-Gym/MoatlessTools-32B-Verifier`, `SWE-Gym/MoatlessTools-7B-Verifier` (model cards are empty -- licence **unconfirmed**). Trajectory datasets: `SWE-Gym/OpenHands-Verifier-Trajectories`, `SWE-Gym/OpenHands-Sampled-Trajectories`, `SWE-Gym/MoatlessTools-Sampled-Trajectories`, `SWE-Gym/MoatlessTools-Agent-Verifier-Train-Data`, `SWE-Gym/OpenHands-SFT-Trajectories`.

---

## 4. R2E-Gym (hybrid verifiers; thought over-reliance)

- **Title:** R2E-Gym: Procedural Environments and Hybrid Verifiers for Scaling Open-Weights SWE Agents. Jain, Singh, Shetty, Zheng, Sen, Stoica. arXiv 9 Apr 2025; COLM 2025. https://arxiv.org/abs/2504.07164 ; https://github.com/R2E-Gym/R2E-Gym
- Execution-free verifier: Qwen2.5-Coder-14B, 5,700 balanced trajectories (code-editing trajectories + on-policy samples from their 32B agent); input = task + **full trajectory (thoughts, actions, observations)** + patch; YES/NO token probabilities.
- **Key finding:** removing agent thoughts drops Best@26 from 42.8% to 37.6%; verifier accuracy 71.82% with full trajectory vs 68.01% patch-only; attention analysis shows the verifier "disproportionately attend[s] to agent thoughts", i.e. it uses sentiment as a correctness proxy. Execution-free verifiers "often rely on stylistic features".
- Hybrid: `s_H = Top_n(s_EF) + s_EB`. SWE-bench Verified: execution-based 43.7% (Best@26), execution-free 42.8%, hybrid 49.4% (Best@16) / **51.0% (Best@26)**.
- HF: `R2E-Gym/R2EGym-Verifier` (card empty; licence unconfirmed), datasets `R2E-Gym/R2EGym-Verifier-Trajectories`, `R2E-Gym/R2EGym-VerifierTrajectories-PatchOnly`, `R2E-Gym/R2E-Gym-V1`, `R2E-Gym/R2E-Gym-Subset`, `R2E-Gym/R2E-Gym-Lite`.

---

## 5. SWE-Critix and CoderForge-Preview

### SWE-Critix
- https://github.com/SWE-Critix/SWE-Critix ; HF org https://huggingface.co/SWE-Critix. Paper "coming soon" (no arXiv id yet); evaluation results "coming soon".
- Verifier takes issue + agent implementation process, emits CoT then YES/NO. Base **Qwen3-30B-A3B-Thinking-2507**. Pipeline: teacher writes comparative rationales for paired pass/fail trajectories -> SFT on 70K samples -> RL with unit-test truth as reward (+1/-1).
- Released: `SWE-Critix/SWE-Critix-Qwen3-30B-A3B-SFT-Epoch3`, `SWE-Critix/SWE-Critix-Qwen3-30B-A3B-RL-Step-3396`; datasets `SWE-Critix/alpaca_style_sft_dataset` (10.4k), `SWE-Critix/rl_dataset` (109k), `SWE-Critix/test_dataset` (43.4k). Licence Apache-2.0 (README).

### CoderForge-Preview
- Together AI blog, 25 Feb 2026 (Ariyak, Zhang, Wang et al.). https://www.together.ai/blog/coderforge-preview ; https://huggingface.co/datasets/togethercomputer/CoderForge-Preview
- 258k test-verified trajectories (155k pass | 103k fail), 51K tasks, 1,655 repos; sources R2E-Gym (4,216 tasks / 9 repos), SWE-Smith (37,221 / 124), SWE-Rebench (9,764 / 1,577). Policy Qwen3-Coder-480B, scaffold OpenHands v0.52.1 (bash, file edit, log, finish tools). HF card: 826,556 rows / 413k trajectories; splits SWE_Rebench 77.2k, SWE_Smith 148k, R2E_Gym 33k, filtered_reward1 155k; fields `trajectory_id, finish_reason, image, messages, reward (-1..1), tools, license, tool_calls`. Licence: per-trajectory repo licences ("Multiple: Apache-2.0, MIT and 7 others"). Qwen3-32B SFT: 23.0% -> 59.4% pass@1 on SWE-bench Verified. Note: single scaffold only.

---

## 6. DeepSWE test-time scaling

- **Title:** DeepSWE: Training a Fully Open-sourced, State-of-the-Art Coding Agent by Scaling RL. Luo, Jain, Singh, ... Sen, Stoica (Agentica / Together AI). 2 Jul 2025. https://www.together.ai/blog/deepswe
- Selection: execution-based verifier (LLM generates tests; pick trajectory passing most) + execution-free `agentica-org/DeepSWE-Verifier` (Qwen3-14B + LoRA, 2 epochs SFT on `r2e-edits/deepswe-swebv-eval-n16-verifier-v1`, 8,000 examples, context 76,800, LR 1e-5; model-card licence "other"). Input = patches + problem context.
- SWE-bench Verified: Pass@1 42.2%; execution-free Best@8 47.0%; hybrid Best@8 57.9%; **hybrid Best@16 59.0%**. Policy: `agentica-org/DeepSWE-Preview` (Qwen3-32B, RL only).

---

## 7. AgentLens / AgentLens-Bench

- **Title:** AgentLens: Revealing The Lucky Pass Problem in SWE-Agent Evaluation. Sahoo, Mittal, Li, Ma, Steenhoek, Lin, Hu (Microsoft). arXiv v1 13 May 2026, v3 2 Jun 2026. https://arxiv.org/abs/2605.12925
- 2,614 trajectories analysed; **AgentLens-Bench = 1,815 trajectories over 47 SWE-bench Verified tasks** (tasks with >=2 passing solutions), OpenHands scaffold, 8 backends (GPT-4.1, GPT-4o, GPT-5.2-Codex, GPT-5.3-Codex, Claude Sonnet 4.5, Opus 4.5, Opus 4.6, Gemini 2.5 Pro).
- Annotations: quality score 0-100; tiers Ideal >=70 / Solid 47-69 / Lucky <47; intent labels (Exploration/Implementation/Verification/Orchestration) via context-sensitive rule-based labeler (kappa=0.933 on 7 annotators); waste signals (regression loops, blind retries, redundant steps, unnecessary exploration, cycles); divergence localisation vs a Prefix Tree Acceptor built from passing runs; 40-column feature vectors. Deterministic pipeline, no LLM calls.
- Lucky Pass: 122/1,136 passing (10.7%); Ideal 229 (20.2%), Solid 785 (69.1%). Categories: Minimal & Unverified 19, Brute-Force Convergence 42, Incomplete Implementation 41, Excessive Exploration 5, Divergent-but-Valid 15. Lucky rate by backend 0.5% (Opus 4.5) to 23.2% (GPT-4.1); rankings shift up to 5 positions. Quality score vs pass/fail AUROC 0.766.
- Release: paper and HF paper page point to https://github.com/microsoft/code-agent-state-trajectories, but that URL returned 404 on 2026-09-25 -- **dataset availability and licence unconfirmed**. No HF dataset id found.

---

## 8. Math PRMs

### PRM800K
- "Let's Verify Step by Step", Lightman et al. (OpenAI), arXiv 2305.20050. https://github.com/openai/prm800k
- 800K step-level human labels on model-generated MATH solutions; per-step rating +1 / -1 / 0 (neutral); two phases (phase 2 uses the best PRM to choose solutions; labelling stops after first error); JSONL; **MIT licence**. HF mirror id: unconfirmed (source is GitHub).

### Math-Shepherd
- "Math-Shepherd: Verify and Reinforce LLMs Step-by-step without Human Annotations", Wang, Li, Shao, Xu, Dai, Li, Chen, Wu, Sui; arXiv 2312.08935 (v3 19 Feb 2024). https://arxiv.org/abs/2312.08935 ; HF `peiyi9979/Math-Shepherd` (444,655 rows; fields `input`, `label`, `task`; step marker token, labels `+`/`-`). **Licence: not stated on HF card or paper -- unconfirmed.**
- Labelling: completer (LLemma-7B trained on MetaMath) samples N=8 continuations from each step; **hard estimation** = 1 if any continuation reaches the correct answer; **soft estimation** = fraction that do. ~170k GSM8K + ~270k MATH solutions from 15 samples/problem (LLaMA2-7B/13B).
- Transfer to agent steps: the Monte-Carlo-rollout label is exactly our `p_success`-at-prefix label when multiple continuations of a shared prefix exist (our `best_next` candidate-set construction); PRM800K's 3-way {+1, 0, -1} rating maps to our ordinal `progress` head. Use as auxiliary step-level signal only (different domain).

---

## 9. Typed-decision-model space

### TypeSafe Jev / System One API (compatibility target)
- Blog "Introducing System One Models & Jev", Diogo Almeida, 25 Sep 2026: https://typesafe.ai/blog/introducing-system-one-models-and-jev . Jev "gives up string generation", trained with RLCD (Reinforcement Learning for Calibrated Decisions), latency 70-500 ms, $0.042/MTok input, output free; claims parity with LLMs on System One tasks at ~two orders of magnitude faster. Third-party coverage dates the launch 15 Sep 2026 (unconfirmed).
- Docs: https://docs.typesafe.ai/concepts/system-one and API reference https://docs.typesafe.ai/api
- **HTTP contract:** `POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer`. Request: `state` (string | object | array), `model` (e.g. `jev-latest`), `questions` (map id -> question). Question: `type` in {`noul`,`choice`,`score`}, `instructions` (string/object/array), `criteria` (noul: optional `{true,false}` descriptions; choice: required map option->description, <=255; score: required ordered array of 2-10 level descriptions). Response: `model`, `answers` (id -> answer), `usage {input_tokens, output_tokens}`. Answers: noul -> `{type, noul: p_yes}`; choice -> `{type, choice, probabilities, confidence}`; score -> `{type, score (probability-weighted), legend, probabilities, confidence}`. Errors 401/422/429/529. Text-only input.

### kev (jaredpalmer/kev)
- https://github.com/jaredpalmer/kev -- open System One-compatible models: Qwen3.5/3.8 base + rank-16 LoRA (attention, MLP, DeltaNet projections), block mask ("a token [reads] the state and its own question, but not other questions or future tokens"), pointer head scoring options vs decide token, per-checkpoint fitted temperature. Apache-2.0.
- Published accuracies (new sources dev/test; trained sources dev/test): Kev-0.8B 0.648/0.697; 0.827/0.838 -- Kev-4B 0.817/0.838; 0.873/0.865 -- Kev-9B 0.822/0.852; 0.872/0.874 -- Kev-27B 0.848/0.896; 0.866/0.870. Jev 0.857 on new sources; Kev-9B MMLU 0.74 vs Jev 0.90. Brier 0.236-0.481. Kev-4B: six questions in 18.1 ms on H100.
- Note: README lists tested hardware including L4/L40S/H100/H200/B200 and Apple M5 (MLX), so the CLAUDE.md note "CUDA untested" appears outdated; still port carefully.

### Laya (convaiinnovations/laya)
- https://huggingface.co/convaiinnovations/laya -- non-autoregressive decision model: ModernBERT-large backbone + 2-layer decision head, 421M params (multilingual mmBERT-base variant 322M); noul/choice/score primitives; RLCD-style proper-scoring-rule training; typed-decisions fine-tuned 0.766; ECE 0.081 after temperature scaling; ~33 ms/question on T4; Apache-2.0.

### LocalLLaMA/typed-decisions
- https://huggingface.co/datasets/LocalLLaMA/typed-decisions -- synthetic, Apache-2.0, parquet; configs `agent_trace_observability`, `customer_service`, `invoice_processing`, `security_incidents` (400 rows each; 300 train / 100 test) + `all` (1.6k). No leaderboard on the card.
- **agent_trace_observability** questions (5 per case; 2,000 decisions per 400 cases): `outcome` choice {failure, harmful, partial, success}; `action` choice {continue, human_review, observe, stop}; `needs_review` noul; `risk` score 0-3; `urgency` score 0-3. State = agent config/autonomy (checkpointed/unsupervised/dry_run), templated task (billing reconciliation, schema migration, cert rotation, ...), factors, constraint violations; labels carry probability distributions and `label_agreement`.

### Luni/laya-jev-benchmark
- https://huggingface.co/datasets/Luni/laya-jev-benchmark -- phishing (2,000 emails) + typed-decisions (400 cases / 2,000 decisions) + 180k-item fine-tune set; code Apache-2.0, checkpoint CC-BY-NC-4.0. Reported: typed-decisions Laya fine-tuned 0.767 (16.4 ms) vs Jev 0.727 (710 ms); phishing Laya raw 0.505 / calibrated 0.611, Jev 0.626 (AUROC 0.689), Claude Haiku 4.5 0.813.

---

## 10. Other 2025-2026 value/PRM/critic work (max 6)

1. **AgentStop: Terminating Local AI Agents Early to Save Energy in Consumer Devices** -- Pham, Katevas, Shamsabadi, Haddadi (Brave), ACM CAIS '26, arXiv 1 May 2026. Gradient-boosted tree on token log-probs / token counts / step repetition predicts doomed runs; 15-20% wasted energy saved with <5% utility drop on web-QA and coding benchmarks. https://arxiv.org/abs/2605.15206 , code https://github.com/brave-experiments/AgentStop
2. **Doomed from the Start: Early Abort of LLM Agent Episodes via a Recall-Controlled Probe Cascade** -- Ruan et al., arXiv 7 Jul 2026. Linear probes on hidden activations + distribution-free calibrated per-round recall budgets; 60.2% (TextCraft) / 54.9% (WebShop) token reduction at 90% recall on Qwen2.5-7B, Llama-3.2-3B, Qwen3-1.7B. https://arxiv.org/abs/2607.06503
3. **SWE-Shepherd: Advancing PRMs for Reinforcing Code Agents** -- Dihan, Khan, arXiv 12 Apr 2026. Action-level reward dataset from SWE-bench trajectories; lightweight PRM scores intermediate actions for reward-guided action selection; claims better interaction efficiency on SWE-bench Verified (no numbers in abstract). https://arxiv.org/abs/2604.10493
4. **SWE-TRACE: Optimizing Long-Horizon SWE Agents Through Rubric Process Reward Models and Heuristic Test-Time Scaling** -- Han et al., arXiv 16 Apr 2026. Rubric-agent PRM gives dense step feedback in an RL pipeline and is reused at inference to prune action candidates per step instead of parallel sampling. https://arxiv.org/abs/2604.14820
5. **AgentPRM: Process Reward Models for LLM Agents via Step-Wise Promise and Progress** -- Xi et al., arXiv 11 Nov 2025, WWW 2026. Defines step reward as promise (proximity to goal) + progress; labels via TD estimation + GAE; ">8x more compute-efficient than baselines". https://arxiv.org/abs/2511.08325
6. **Web-Shepherd: Advancing PRMs for Reinforcing Web Agents** -- Chae et al., NeurIPS 2025 Spotlight, arXiv 21 May 2025. First web-navigation PRM; WebPRM Collection (40K step-level preference pairs + checklists), WebRewardBench; ~+30 pts over GPT-4o on WebRewardBench, +10.9 on WebArena-lite at 1/10 cost. HF: `LangAGI-Lab/WebShepherd_3B`, `LangAGI-Lab/WebShepherd_8B`, `LangAGI-Lab/WebPRMCollection_preference_pair` (9.46k), `LangAGI-Lab/WebRewardBench` (776). Licence unconfirmed. https://arxiv.org/abs/2505.15277

Also seen (not expanded): WebArbiter (ICLR 2026, 7B principle-guided reasoning PRM, https://arxiv.org/abs/2601.21872); "When Evidence is Sparse: Weakly Supervised Early Failure Alerting in Dialogs and LLM-Agent Trajectories" (https://arxiv.org/pdf/2606.05414).

---

## Implications for agent-compass

1. **Reproduce Fail-Fast exactly as baseline B0**: Qwen3-0.6B + LoRA r=16/alpha=32, 8-step window incl. thoughts, 4k cap, BCE + Bradley-Terry pairs matched by step-fraction decile (lambda_r=0.25), Platt scaling, 20-step floor, count-M / sustained-K aggregation. Report their metrics (recall/precision/fired/saved at 5/10/25% FPR) **in addition to** AUROC/Brier/ECE, which they do not report -- that is a gap we fill.
2. **Their data is not public**: we cannot reproduce their numbers on their trajectories. Reproduce the *recipe* on our unified data (nebius, CoderForge, SWE-smith) and, for one policy, regenerate multi-seed mini-swe-agent runs on SWE-bench Verified only if budget allows (ask first).
3. **Thoughts-removed training view is evidence-backed**: R2E-Gym shows verifiers over-attend to thoughts (Best@26 42.8 -> 37.6 without thoughts; 71.8% vs 68.0% accuracy) and Nebius reports value hacking in regression critics. Default = thoughts removed/truncated; keep a thoughts-on ablation; include the fake-confidence robustness set.
4. **Pairwise ranking loss** is used by Fail-Fast (BT pairs across instances, matched by step-fraction bucket). Keep our pairwise loss and match pairs by step-fraction decile, not only within-task.
5. **Auxiliary progress signal**: Fail-Fast's F2P head (intermediate-patch replay) helped; P2P did not. Our `progress` head should be driven by cheap observation signals first; a replay-based F2P label is a stretch goal requiring Docker replay on the CPU server.
6. **Policy conditioning**: Nebius conditions the critic on a policy-id string in the system prompt and trains across policy versions; Fail-Fast's monitor transfers zero-shot across four policies with modest recall loss. Keep the `<policy>` token with 30% "unknown" dropout and report both blind and policy-aware.
7. **Value vs action-value**: Nebius' 70B critic predicts Q at a special end-of-turn token from the full trajectory; K=8 lookahead candidates saturate; gamma near 1 and TD(lambda=0.7) beat plain MC. Our `best_next` pointer head should be trained with TD-style targets where continuation data exists and MC outcome otherwise; test K=4 and K=8 online.
8. **Small critics are the risk**: Nebius found 7B/8B critics underperform 70B; kev-0.8B trails kev-4B by ~17 points on new sources. Plan for the 4B quality tier as the primary deliverable and treat 1.5B as the fast tier with published deltas.
9. **Prefix points**: evaluate per-step curves plus 25/50/75/90% fractions, and a fixed 20-step floor to match the Fail-Fast operating regime; report tokens-saved at fixed FPR so numbers are comparable.
10. **Best-of-N baselines to include**: SWE-Gym ORM (32B, YES/NO logits; best@16 32.0 vs pass@16 42.8), R2E-Gym execution-free verifier (14B), DeepSWE-Verifier (14B LoRA, hybrid best@16 59.0). Weights exist for all three (licences unconfirmed) -- use as reference points for trajectory selection, noting they are 4-8x our size and need generation of a YES/NO token rather than one forward pass.
11. **Escalation**: prompt-based SWE-PRM intervention *hurt* resolve rate (-3.2 pp) in Fail-Fast; restart-with-overlay helped (+5.2 pp at 25% FPR). Our `escalate` online experiment should compare against cold restart and overlay restart, not only against no-intervention.
12. **AgentLens for eval only**: 1,815 process-annotated trajectories with quality tiers and waste signals are a natural test set for `stuck` (waste signals: regression loops, blind retries, cycles) and `progress` -- but the release URL is 404 today; re-check before M2 and do not plan on it.
13. **Data licences**: nebius datasets CC-BY-4.0; SWE-Gym MIT; CoderForge per-repo permissive; PRM800K MIT; Math-Shepherd unconfirmed; AgentLens unconfirmed. Record in `docs/data_audit.md`.
14. **API compatibility**: implement the System One request/response exactly (`state`, `model`, `questions{type, instructions, criteria}` -> `answers{type, noul | choice+probabilities+confidence | score+legend+probabilities+confidence}`, `usage`). Our six questions map cleanly: `p_success/stuck/escalate` -> noul, `best_next` -> choice (<=255 options), `progress/steps_left` -> score (4 levels each, within the 2-10 limit).
15. **Report on typed-decisions `agent_trace_observability`** (400 cases / 2,000 decisions; questions outcome/action/needs_review/risk/urgency) as a zero-shot generality check alongside AVM-Bench; published references: Laya-FT 0.767, Jev 0.727; kev numbers are on kev's own suites, not this set.
