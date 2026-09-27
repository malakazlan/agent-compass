# Label QA findings (M2)

Written 2026-09-28 from `docs/label_qa_swe_agent.md` and `docs/label_qa_openhands.md` (dev splits,
60k and 40k records) and the example-build statistics. Rule definitions live in
`agent_compass/data/labels.py`; candidate-set construction in `agent_compass/data/candidates.py`.

## Distributions (dev)

| label | SWE-agent (weak 2024 Llama policies) | OpenHands (Qwen3-Coder-480B) |
|---|---|---|
| p_success = True | 14.8% | 44.0% |
| stuck = True | 24.1% | 1.9% |
| progress 0 / 1 / 2 / 3 | 14.7 / 50.0 / 35.2 / 0.1% | 1.6 / 68.0 / 28.5 / 1.9% |
| escalate = True | 64.6% | 46.7% |
| steps_left bins 0 / 1 / 2 / 3 (successful runs) | 55.7 / 37.5 / 6.3 / 0.5% | 9.6 / 26.4 / 42.0 / 22.1% |
| best_next candidate set present | 13.7% of records | 24.8% of records |
| best_next tier = branching | 46% of sets | 47% of sets |

Success rate is flat across prefix positions (SWE-agent 14.6 to 14.9%, OpenHands 43.4 to 45.7%), as
it must be: the label is the final outcome, and prefix position carries no information by itself.

## What the samples showed

**stuck.** 25 random stuck = True samples per dataset were read. Rules that fired: same action
repeated 3+ times in the last 6 steps, same error signature 3+ times, and failed edits at the same
location 3+ times. All sampled positives were genuine loops (reproduce script failing with the
same exception, `edit` producing the same syntax error, `scroll_down` spam, opening a file that
does not exist repeatedly). One rule was wrong in the first version: "edit cycle" fired on any
three edits within six steps, which flagged ordinary editing. It now requires the edits to be at
the same location (line range bucketed to tens for SWE-agent `edit A:B`, path for
`str_replace_editor`) and to have failed. That change moved 23% of stuck labels on SWE-agent
(31.6% to 24.1% positive) and 50% on OpenHands (3.8% to 1.9%).

**progress.** Level 0 comes from failed edits (56%), a repeated action with the same error
(43%), or a test run with more failures. Level 2 comes from an applied edit or a newly explored
location. Level 3 requires a test run flipping to passing or a finish right after one, which is
rare on SWE-agent because those agents almost never run the test suite (0.7% of steps contain a
pytest summary) and is 1.9% on OpenHands. The ordinal head will need class weights for level 3.
Level 1 ("no signal") is the majority on both datasets; on OpenHands 68% of steps are reading or
re-reading, which matches the dataset's 60-step median.

**escalate.** First definition (failed run and task baseline <= 0.25) was true on 73% and 45% of
records: close to "the run failed". Second definition (failed run and leave-one-out task baseline
<= 0.5 x policy success rate) is true on 64.6% and 46.7%. Still 76% and 83% of failed states are
flagged, because most tasks are uniform across runs: only 22.5% (SWE-agent) and 27.6%
(OpenHands) of tasks have mixed outcomes, so a failed run usually belongs to a task where
the other runs also failed. Conclusion: as a trajectory-derived training label, escalate is
largely redundant with p_success on these datasets. It stays in the record because it is
free, but the operational escalation signal will come from the calibrated p_success plus
its confidence and a conformal threshold (plan section 5.5), and the M4 ablation must show
whether the separate head adds anything beyond p_success.

**best_next.** Candidate sets exist for 14% (SWE-agent) and 25% (OpenHands) of records; roughly
half of them are "branching" (the positive and a negative come from runs whose first L action
families overlap by at least 50%). The rest are hard negatives from the same task at a similar
step. Coverage is bounded by the share of mixed-outcome tasks. Sets are skipped when the
positive action also appears among failing runs or when only "submit vs something" would remain.

**Leakage checks.** No SWE-bench Verified instance in either dataset; harness output lives in
separate columns and never enters the state; agent-authored "all tests pass" text is present in
observations and is treated as untrusted (thoughts removed by default; the
`fake_confidence` variant injects it deliberately for the robustness set).

## Open items for M3 and M4

- Validate stuck and progress precision on 200 samples with a judge model (needs API budget).
- Class weights for progress = 3 and steps_left bin 3 (SWE-agent).
- Decide from the M4 ablation whether the escalate head is kept or replaced by a threshold on p_success.
