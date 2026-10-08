# M4 design: one multi-head run on top of v0

Goal: the released model answers all six questions, trained in a single GPU run of 3 to 5 hours,
warm-started from the v0 adapter. Every design choice below is the cheapest option that keeps kev's
checkpoint format, benchmark and server unchanged, so nothing downstream has to be rewritten.

## Data (CPU, `scripts/make_v1_split.py`)

- Records: the existing example files (all six questions already attached), one record per prefix.
  Option `--prefixes-per-traj` to test the diversity lever (1 prefix from more trajectories versus 5
  from fewer) at equal record count.
- Pairs for the ranking loss: within (task, policy) groups, a successful and a failed prefix whose
  prefix fractions differ by at most 0.2 get a shared `_meta.pair_id` and are written adjacently, so
  a micro-batch of 2 holds the pair. Records without a partner are kept and simply have no pair term.
- Splits unchanged (repo-hash table). Dev and test keep every question for evaluation.

## Model and losses (`agent_compass/train/multihead.py`)

kev's `DecisionModel` is reused as is: one pointer head reads every question. The six questions are
six branches over one shared state (`--shared_prefix 1`). What changes is only the loss:

| question | type | loss |
|---|---|---|
| p_success, stuck, escalate | noul (2-option pointer) | cross-entropy, as kev |
| best_next | choice (K-option pointer) | cross-entropy, as kev |
| progress, steps_left | score (K ordered levels) | **cumulative-link (CORAL-style) loss on the pointer distribution**: for each threshold j, BCE(P(level > j), 1[y > j]); plus 0.5 x cross-entropy so the argmax stays sharp |
| p_success pairs | | **pairwise ranking**: softplus(-(d_success - d_fail)) on the yes/no logit difference d, weight 0.25 (Fail-Fast's lambda) |

Per-question weights: p_success 1.0, stuck 0.5, progress 0.5, steps_left 0.5, best_next 0.5,
escalate 0.25 (shown to be nearly redundant with p_success; kept cheap). Weights are flags.

Why no new head module: an ordinal head would change `head.pt`, serving, and the benchmark; an
ordinal *loss* on the existing K-way distribution gives the rank-consistent training signal with
none of that. The ablation "ordinal loss on/off" is then one flag.

## Training recipe

- Warm start from `azlanmalikai/agent-compass-2b` v0 (kev `--init_from` path: adapter + head loaded).
- 1 epoch, lr 5e-5 (half of v0's, since warm), batch 2 x accumulation 4, bf16, no checkpointing,
  `--max_state 4352`. Six branches per record cost about 1.5x one branch: expect about 0.5 s/record,
  so 20,000 records in about 3 hours. Smoke run of 1,000 records first, as before.
- Output in kev's layout (adapter, head.pt, tokenizer), so `kev_benchmark_long.py`, `eval_rows.py`
  and `kev.serve` work unchanged.

## Evaluation and calibration

- `eval_rows.py` already scores noul (AUROC, Brier, ECE), score (accuracy, MAE, RPS) and choice
  (top-1) questions from one rows.json.
- Per-question temperature fitted on dev (extend the calibration step from one scalar to one per
  question id); conformal threshold for escalate/abort from the dev quantiles.
- Gate: p_success must not regress versus v0 on the same 20k test records (AUROC 0.775 pooled,
  0.707 / 0.722 per dataset); stuck precision/recall against the rule labels; progress and
  steps_left within-one accuracy; best_next top-1 versus random (1/K) and versus "shortest option".
- Re-run the two offline simulations (best-of-N, early abort) with the new p_success: the pairwise
  loss is aimed exactly at within-task discrimination, so the within-policy lifts are the numbers
  to watch.

## Ablations without GPU training

- Loss and head ablations on cached features: run the frozen v1 backbone once over a 5k-record
  subset (forward only, bf16, about 15 GPU minutes), store the hidden states at each question's
  decide token and option tokens, then train pointer/ordinal variants on CPU in minutes. This gives
  "ordinal loss vs plain softmax" and "per-question temperature vs one" at full model scale.
- Backbone-level ablations (thoughts on/off, 4k vs 8k budget, 2B vs 4B) stay future work or run as
  0.6B pure-attention models on free Kaggle GPUs; reported as small-scale only.

## Order of work

1. `agent_compass/train/losses.py` with unit tests (CPU): ordinal loss, pairwise loss, weighting.
2. `scripts/make_v1_split.py` with pair construction and coverage stats (CPU).
3. `agent_compass/train/multihead.py`: training loop around kev's model; smoke-tested on the pod.
4. Per-question calibration in the eval path.
5. Pod session: zero-shot baseline (20 min), v1 smoke (10 min), v1 full (3 to 4 h), eval (40 min),
   feature cache (15 min). Then CPU: ablations, simulations, report.
