# agent-compass: a calibrated value model for AI agents (technical report, draft 1)

Azlan Malik. Draft of 2026-10-10. Every number below is copied from a results file in `runs/` or a
milestone report in `docs/`; nothing is estimated.

## Abstract

Agents that run for tens of steps need a cheap way to know whether they are on course. We train a
2B-parameter value model that reads an agent's task and trajectory prefix and answers six typed
questions in one forward pass without generating text: probability of eventual success, whether the
agent is stuck, how much the last step moved the task forward, which of K candidate next actions is
best, whether to escalate, and how many steps remain. Training uses 147k software-engineering agent
runs with measured outcomes (two scaffolds, four policy models) plus 8k tool-use runs. On held-out
repositories the success head reaches AUROC 0.775 against 0.667 and 0.619 for a hand-feature baseline
and 0.679 for a zero-shot instruct model of the same size, with ECE 0.048 after one temperature. The
five other heads work from the same pass (stuck AUROC 0.97, ordinal heads within one level 98 to 99%
of the time, best-next top-1 0.48 at chance 0.26). On a tool-use domain with 3k in-domain training
states the success head reaches AUROC 0.780. In offline simulations the model lifts best-of-N run
selection by 5 points for a weak agent and lets early abort save 12% of steps for a 0.4 point resolve
rate loss. We release the models, data, benchmark, SDK and server.

## 1. Problem

A value model for agents estimates, from the state alone, quantities an orchestrator needs: will this
run succeed, is it worth continuing, which action to take next, should a stronger model take over.
Existing options are heuristics (step and error counts), generative critics of 14B to 70B parameters
that answer one question by emitting a token and publish no calibration, and small failure monitors
without released weights. We want one small model, several questions, calibrated probabilities, and
evidence that it changes agent outcomes.

## 2. Data

Sources: `nebius/SWE-agent-trajectories` (80,034 runs, SWE-agent scaffold, Llama 3.1 fine-tunes of
8B, 70B and 405B, 16.7% success), `nebius/SWE-rebench-openhands-trajectories` (67,074 runs, OpenHands,
Qwen3-Coder-480B, 47.9% success), and `AgentSuite/tau2-bench-trajectories` (8,340 runs, 556 tasks, 30
policy models) for the tool-use domain. All runs carry a measured outcome (hidden tests or task checks).

Every source is converted to one trajectory schema (task, steps as action, observation and optional
thought, outcome, policy, repository). Splits are by repository hash (80/10/10), with every SWE-bench
Verified repository forced into the test split; no task or repository appears in two splits
(`docs/prelaunch_checks_v0.md`).

**States.** A state is the task, an optional policy tag, older steps compressed to one line each,
and the last eight steps with truncated observations, under a 4,096-token budget. Thoughts are
removed: verifiers that read them learn to trust confident language. Harness status lines and
outcome-revealing submit messages are stripped; a robustness view injects fake confident thoughts
into failing runs. Prefixes are sampled at 25/50/75/90% of each run plus one random position; a
prefix whose last action is the final submit is excluded.

**Labels.** `p_success` is the run's outcome, with the task's leave-one-out pass rate stored as a
baseline. `stuck` and `progress` are rules over observations (repeated near-identical commands,
repeated error signatures, failed edits at the same location; test and error counts moving, new
locations explored). A judge model on 200 states found the first stuck rule at 58% precision; the
refined rule (navigation excluded, run commands need the same error twice, listing lines ignored in
error signatures) is estimated at about 88% (`docs/label_qa_findings.md`). `best_next` candidate sets
come from other runs of the same task with a similar prefix (branching tier) or from failed runs at
a similar step (hard-negative tier); the positive is the action whose continuation succeeded more
often. `steps_left` bins the remaining length of successful runs. `escalate` marks a failed run on
a task whose leave-one-out pass rate is at most half the policy's rate; it is nearly redundant with
`p_success` on these data and is the weakest label.

**Files.** v0: 49,987 / 9,999 / 64,124 success-only records. v1: 27,000 training records (12,000
trajectories per SWE scaffold, two prefixes each, plus 3,000 tau2 states; 3,360 success/failure
pairs within task and policy), 11,005 dev, the same 64,124 SWE test records, and 3,832 tau2 test
records. Published at `azlanmalikai/agent-compass-data`; the test files form AgentCompass-Bench.

## 3. Model

The backbone is Qwen3.5-2B-Base with LoRA rank 16 on attention, MLP and DeltaNet projections (17.9M
trainable parameters), run in bf16. The state is encoded once; each question is appended as its own
branch that attends to the state but not to other branches (shared-prefix rows on this hybrid
backbone; a block-causal mask on pure-attention backbones). A pointer readout scores each question's
options against its decide token, so yes/no, ordinal and K-way questions share one head and one
pass. The encoder, pointer readout, checkpoint format and serving runtime come from kev (Apache-2.0);
the state construction, labels, losses, calibration and evaluation are new.

Losses: cross-entropy for yes/no and choice questions; for `progress` and `steps_left` a
cumulative-link (CORAL-style) loss on the K-way distribution plus 0.5 cross-entropy; a pairwise
Bradley-Terry term on `p_success` for same-task success/failure pairs at similar prefix fractions
(weight 0.25); per-question weights 1.0 / 0.5 / 0.5 / 0.5 / 0.5 / 0.25 for p_success / stuck /
progress / steps_left / best_next / escalate. Calibration is one temperature per question fitted by
NLL on the dev split.

Two trained adapters:

| | v0 | v1 |
|---|---|---|
| questions | p_success | all six |
| data | 21,000 v0 records | 27,000 v1 records, warm start from v0 |
| optimisation | 1 epoch, lr 1e-4, batch 2 x 4 | 1 epoch, lr 5e-5, batch 1 x 8 |
| H100 time | 2 h 10 min | 2 h 07 min |

## 4. Offline results (held-out repositories, 20,000 SWE test states)

| model | AUROC [95% CI] | SWE-agent | OpenHands | ECE (calibrated) | confident-error at 0.9 |
|---|---|---|---|---|---|
| step count | | 0.614 | 0.571 | | |
| hand-feature gradient boosting | | 0.667 [0.640, 0.698] | 0.619 [0.593, 0.649] | 0.020 / 0.045 | |
| zero-shot Qwen3.5-2B instruct (5k subset) | 0.679 | | | 0.275 raw | |
| v0 | **0.775** [0.746, 0.800] | 0.707 | 0.722 | 0.048 (T 1.60) | 0.007 |
| v1 | 0.761 [0.735, 0.791] | 0.717 | 0.678 | 0.069 (T 3.02) | 0.000 |

By prefix quarter (v0, SWE-agent / OpenHands): 0.666 / 0.712 in the first quarter, 0.734 / 0.739 in
the last; the gradient-boosting baseline is 0.620 / 0.564 and 0.707 / 0.652 at the same points.
The model is most useful early, where abort and escalation decisions have value, and its margin is
largest on the strong agent, whose runs carry no loop or error-count signal.

Other heads (v1): stuck AUROC 0.973 (0.973 also against the refined labels it was not trained on,
with over-flagging), progress accuracy 0.937 and within-one 0.990, steps_left accuracy 0.711 and
within-one 0.981 (successful runs), best_next top-1 0.483 at chance 0.255, escalate AUROC 0.670.

Second domain, tau2-bench (3,832 held-out states, 30 policies, 3,000 in-domain training states):
p_success AUROC 0.780 [0.726, 0.832], ECE 0.017; stuck 0.993; escalate 0.902; best_next top-1 0.633;
steps_left within-one 0.994.

Robustness and shortcuts: the policy tag does not drive the ranking (AUROC 0.779 with the tag
hidden versus 0.773 shown); confident phrases appear in failing runs' own test scripts as often as
in successful ones (`docs/prelaunch_checks_v0.md`). A first best-of-N analysis grouped runs by task
only and reported a 7.4-point lift; 123 test tasks exist in both datasets, and on those the model
mostly recognised the stronger agent. All selection results below are within one policy.

## 5. Does it help an agent? Offline simulations

**Best-of-N run selection** (pick among a task's runs by p_success at the latest prefix, never
seeing the final patch):

| agent | groups | random | v0 pick | v1 pick | v1 lift [95% CI] |
|---|---|---|---|---|---|
| Llama-70B fine-tune, SWE-agent | 306 to 308 | 0.122 | 0.153 | **0.173** | +5.0 pp [2.6, 7.7] |
| Qwen3-Coder-480B, OpenHands | 579 to 580 | 0.525 to 0.531 | 0.545 | 0.547 | +1.6 pp [-0.0, 3.3] |

The pairwise loss did what it was designed for on the weak agent (lift from 3 to 5 points); the
strong agent is unchanged within noise.

**Early abort** (per-agent thresholds chosen on dev at a false-abort budget, applied to test; abort
only after 20% of the run; v0):

| agent | budget | test FPR | failures caught | steps saved | resolve rate |
|---|---|---|---|---|---|
| Llama-70B | 5% | 0.021 | 17.8% | 12.0% | 0.190 to 0.186 |
| Llama-70B | 25% | 0.214 | 51.2% | 28.5% | 0.190 to 0.150 |
| Qwen3-Coder-480B | 5% | 0.035 | 12.8% | 3.8% | 0.529 to 0.510 |

Dev thresholds transfer to unseen repositories. The trade is attractive for the weak agent and not
yet for the strong one, where v0 catches too few failures early. v1 is worse than v0 for abort
(recall 2 to 3% at 5% FPR) despite similar AUROC: its low-probability tail is less separated, so v0
stays the abort model. Steps saved is a proxy for tokens; per-step token counts are not in the data.

## 6. Cost

Data preparation ran on a 16 GB laptop (streaming everything). GPU use was 9 h 50 min of one H100 in
two sessions, covering the kev reproduction, both trainings, all evaluations and a zero-shot baseline.
Scoring costs about 0.1 s per 4k-token state for one question and 0.22 s for six in batched bf16
evaluation on an H100; served latency is not yet measured.

## 7. Limitations and future work

- Two SWE scaffolds and one tool-use benchmark; other scaffolds, languages and domains are untested.
- v1's stuck head was trained on the earlier rule; a retrain on the refined labels is scripted
  (`docs/modal_runbook.md`) and pending compute.
- `escalate` needs a better label than a rule on outcomes; judge annotations are the plan.
- No live agent run yet. The offline simulations are conservative (abort only at scored checkpoints)
  but are not a substitute for best-of-N action selection and abort inside a running agent on
  SWE-bench Verified, which needs a served policy model and a Docker harness.
- Ablations (thoughts on/off, history budget, pairwise loss, single versus multi-head, backbone size)
  are not run; they are planned at 0.6B scale and on cached features.
- Latency figures come from batched evaluation, not a served endpoint.

## 8. Artifacts

Code `github.com/malakazlan/agent-compass`; adapters `azlanmalikai/agent-compass-2b` (v0 at the
root, v1 under `v1/`); data `azlanmalikai/agent-compass-data`; benchmark
`azlanmalikai/agent-compass-bench`. SDK (`agent_compass.sdk`), server (`agent_compass.server`),
mini-swe-agent and generic hooks, and the Modal training app are in the repository. Apache-2.0.
