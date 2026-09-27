# Baselines v0: p_success without a language model

Measured 2026-09-28 by `scripts/eval_baselines.py` on the example records (4k-token states,
thoughts removed). Results and configs: `runs/baselines-swe_agent-v0/`, `runs/baselines-openhands-v0/`.
Bootstrap CIs resample tasks (groups), 300 draws. These are the numbers the M3 model must beat.

Baselines:

- `step_count`: score = -prefix_len (longer runs fail more). No fitting.
- `error_count`: score = -(error-ish lines in the recent window).
- `repetition`: score = -(repeated identical actions in the window + 3 x stuck rule).
- `progress_rule`: the rule-based progress level of the last step.
- `gbt`: gradient-boosted trees on 19 hand features computed from the state text and prefix
  length only (AgentStop-style), fit on the train split.
- `prior`: constant train success rate.

Heuristic scores are mapped to probabilities with a logistic fit on train so Brier and ECE are
comparable; AUROC does not depend on that mapping. "recall@FPR" follows Fail-Fast: flag failures
with a failure score above the threshold that mislabels at most 5% or 25% of successful runs.

## SWE-agent (weak 2024 Llama policies, 15 to 19% success)

Dev (same repos as train, n=67,234):

| baseline | AUROC [95% CI] | Brier | ECE | AUROC 0-25% / 25-50% / 50-75% / 75-100% | recall@5%FPR | recall@25%FPR |
|---|---|---|---|---|---|---|
| step_count | 0.689 [0.669, 0.704] | 0.122 | 0.018 | 0.702 / 0.723 / 0.736 / 0.748 | 0.264 | 0.530 |
| error_count | 0.590 [0.558, 0.619] | 0.126 | 0.012 | 0.622 / 0.596 / 0.582 / 0.595 | 0.151 | 0.360 |
| repetition | 0.645 [0.628, 0.660] | 0.124 | 0.007 | 0.602 / 0.641 / 0.675 / 0.673 | 0.183 | 0.428 |
| progress_rule | 0.577 [0.567, 0.588] | 0.127 | 0.015 | 0.573 / 0.582 / 0.615 / 0.553 | 0.000 | 0.165 |
| **gbt** | **0.735 [0.710, 0.757]** | 0.117 | 0.008 | 0.646 / 0.720 / 0.750 / 0.788 | 0.324 | 0.601 |
| prior | 0.500 | 0.129 | 0.014 | - | 0 | 0 |

Test (held-out repositories, n=33,242):

| baseline | AUROC [95% CI] | Brier | ECE | AUROC 0-25% / 25-50% / 50-75% / 75-100% | recall@5%FPR | recall@25%FPR |
|---|---|---|---|---|---|---|
| step_count | 0.614 [0.591, 0.643] | 0.151 | 0.022 | 0.622 / 0.632 / 0.633 / 0.657 | 0.164 | 0.405 |
| error_count | 0.565 [0.535, 0.594] | 0.153 | 0.024 | 0.583 / 0.584 / 0.553 / 0.568 | 0.117 | 0.327 |
| repetition | 0.593 [0.574, 0.615] | 0.152 | 0.024 | 0.565 / 0.595 / 0.600 / 0.616 | 0.131 | 0.364 |
| progress_rule | 0.548 [0.534, 0.564] | 0.154 | 0.025 | 0.547 / 0.562 / 0.566 / 0.527 | 0.000 | 0.132 |
| **gbt** | **0.667 [0.640, 0.698]** | 0.146 | 0.020 | 0.620 / 0.638 / 0.673 / 0.707 | 0.210 | 0.489 |
| prior | 0.500 | 0.155 | 0.026 | - | 0 | 0 |

## OpenHands (Qwen3-Coder-480B, 44 to 53% success)

Dev (n=43,116):

| baseline | AUROC [95% CI] | Brier | ECE | AUROC 0-25% / 25-50% / 50-75% / 75-100% | recall@5%FPR | recall@25%FPR |
|---|---|---|---|---|---|---|
| step_count | 0.576 [0.559, 0.589] | 0.243 | 0.034 | 0.548 / 0.605 / 0.634 / 0.657 | 0.118 | 0.377 |
| error_count | 0.510 [0.496, 0.524] | 0.248 | 0.043 | 0.501 / 0.498 / 0.504 / 0.524 | 0.055 | 0.239 |
| repetition | 0.509 [0.502, 0.516] | 0.248 | 0.042 | 0.501 / 0.502 / 0.491 / 0.528 | 0.049 | 0.101 |
| progress_rule | 0.508 [0.503, 0.513] | 0.248 | 0.042 | 0.510 / 0.515 / 0.509 / 0.503 | 0.017 | 0.017 |
| **gbt** | **0.629 [0.605, 0.651]** | 0.234 | 0.026 | 0.567 / 0.621 / 0.630 / 0.658 | 0.147 | 0.422 |
| prior | 0.500 | 0.248 | 0.042 | - | 0 | 0 |

Test (held-out repositories, n=30,894):

| baseline | AUROC [95% CI] | Brier | ECE | AUROC 0-25% / 25-50% / 50-75% / 75-100% | recall@5%FPR | recall@25%FPR |
|---|---|---|---|---|---|---|
| step_count | 0.571 [0.554, 0.589] | 0.247 | 0.051 | 0.562 / 0.603 / 0.621 / 0.646 | 0.120 | 0.361 |
| error_count | 0.513 [0.494, 0.534] | 0.251 | 0.048 | 0.518 / 0.502 / 0.502 / 0.524 | 0.050 | 0.244 |
| repetition | 0.498 [0.489, 0.506] | 0.251 | 0.048 | 0.504 / 0.504 / 0.481 / 0.503 | 0.034 | 0.081 |
| progress_rule | 0.506 [0.498, 0.511] | 0.251 | 0.049 | 0.509 / 0.508 / 0.508 / 0.501 | 0.018 | 0.018 |
| **gbt** | **0.619 [0.593, 0.649]** | 0.239 | 0.045 | 0.564 / 0.610 / 0.609 / 0.652 | 0.145 | 0.417 |
| prior | 0.500 | 0.251 | 0.049 | - | 0 | 0 |

## Reading

- The bar for M3 on held-out repositories: **AUROC 0.667 on SWE-agent, 0.619 on OpenHands**, from
  the gradient-boosted hand features. Plain step count is the strongest single heuristic.
- Surface heuristics collapse on the strong policy: on OpenHands, error count, repetition and
  the progress rule are at chance (0.50 to 0.51). Whether a capable agent will succeed is not
  visible from loop or error counts; that is the case for a model that reads the content.
- Every baseline improves with prefix position (later states are easier), so per-position
  AUROC must be reported for the model as well.
- Dev-to-test drops (0.735 to 0.667, 0.629 to 0.619) show the repository shift the split was
  built to measure. Held-out-repository test is the headline number.
