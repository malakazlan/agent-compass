import random

from agent_compass.data.candidates import build_candidate_set, prefix_similarity
from agent_compass.data.prefixes import prefix_fraction, sample_prefix_lengths
from agent_compass.data.schema import Step, Trajectory


def test_sample_prefix_lengths_fixed_fractions_plus_random():
    lens = sample_prefix_lengths(40, n_random=0)
    assert lens == [10, 20, 30, 36]
    lens = sample_prefix_lengths(40, n_random=3, rng=random.Random(1))
    assert all(1 <= L <= 39 for L in lens) and lens == sorted(set(lens))
    assert sample_prefix_lengths(1, n_random=2) == [1]
    assert sample_prefix_lengths(2, n_random=0) == [1]
    assert sample_prefix_lengths(2, n_random=0, include_full=True) == [1, 2]
    assert sample_prefix_lengths(0) == []
    assert prefix_fraction(10, 40) == 0.25


def run(traj_id, actions, outcome, task_id="o__r-1"):
    return Trajectory(
        traj_id=traj_id, task_id=task_id, domain="swe", scaffold="s", policy_model="p", repo_or_site="o/r", task="fix",
        steps=[Step(action=a, observation="ok") for a in actions], outcome=outcome, meta={},
    )


A = run("A", ["ls", "open a.py", "edit 1:2 x", "pytest", "submit"], True)
B = run("B", ["ls", "open a.py", "edit 9:9 wrong", "pytest", "edit 9:9 wrong2", "submit"], False)
C = run("C", ["ls", "open b.py", "cat README", "grep foo", "submit"], False)
D = run("D", ["find . -name x", "open c.py", "edit 5:5 y", "pytest", "submit"], True)


def test_prefix_similarity():
    assert prefix_similarity(A, B, 2) == 1.0
    assert 0 < prefix_similarity(A, C, 2) < 1.0
    assert prefix_similarity(A, D, 1) == 0.0


def test_successful_run_positive_is_own_next_action():
    cs = build_candidate_set(A, 2, [A, B, C, D], k=4, rng=random.Random(0))
    assert cs is not None
    assert cs.options[cs.correct].action == "edit 1:2 x" and cs.options[cs.correct].outcome is True
    negs = [c.action for i, c in enumerate(cs.options) if i != cs.correct]
    assert negs and all(c.outcome is False for i, c in enumerate(cs.options) if i != cs.correct)
    assert "edit 1:2 x" not in negs
    assert cs.tier == "branching"  # B shares A's first two actions
    assert 2 <= len(cs.options) <= 4


def test_failed_run_gets_positive_from_similar_success():
    cs = build_candidate_set(B, 2, [A, B, C, D], k=4, rng=random.Random(0))
    assert cs is not None
    pos = cs.options[cs.correct]
    assert pos.from_traj == "A" and pos.outcome is True  # A is the most similar successful run
    assert any(c.action == "edit 9:9 wrong" and c.from_traj == "B" for c in cs.options)  # own bad action is a negative


def test_ambiguous_or_thin_sets_are_skipped():
    # positive action also taken by a failing run at the same step -> ambiguous
    E = run("E", ["ls", "open a.py", "edit 1:2 x", "pytest", "submit"], False)
    assert build_candidate_set(A, 2, [A, E], k=4) is None
    # no successful run to supply a positive for a failed trajectory
    assert build_candidate_set(C, 1, [B, C], k=4) is None
    # no next action at the end
    assert build_candidate_set(A, len(A.steps), [A, B], k=4) is None


def test_finish_is_not_contrasted_with_non_finish():
    F = run("F", ["ls", "open a.py", "edit 1:2 x", "pytest", "open z.py"], False)
    # at prefix 4, A's next action is "submit"; F's nearby actions are non-finish -> no set
    assert build_candidate_set(A, 4, [A, F], k=4, step_tolerance=0) is None


def test_hard_negative_tier_when_prefixes_differ():
    G = run("G", ["find . -name q", "sed -i s/a/b/ q.py", "python q.py", "git diff", "submit"], False)
    cs = build_candidate_set(A, 2, [A, G], k=4, rng=random.Random(0))
    assert cs is not None and cs.tier == "hard_negative"
