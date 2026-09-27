from agent_compass.data.labels import (
    RuleConfig,
    TaskStats,
    action_family,
    escalate_label,
    label_prefix,
    progress_label,
    steps_left_bin,
    stuck_label,
    task_stats,
    parse_test_counts,
)
from agent_compass.data.schema import Step, Trajectory


def S(action, observation="", thought=None):
    return Step(action=action, observation=observation, thought=thought)


def T(steps, outcome=True, task_id="o__r-1"):
    return Trajectory(
        traj_id=f"t/{task_id}/{id(steps)}", task_id=task_id, domain="swe", scaffold="s", policy_model="p",
        repo_or_site="o/r", task="fix", steps=steps, outcome=outcome, meta={},
    )


def test_steps_left_bins():
    assert [steps_left_bin(n) for n in (1, 5, 6, 15, 16, 40, 41, 500)] == [0, 0, 1, 1, 2, 2, 3, 3]


def test_parse_test_counts():
    assert parse_test_counts("== 3 passed, 2 failed in 1.2s ==") == (3, 2)
    assert parse_test_counts("5 passed in 0.1s") == (5, 0)
    assert parse_test_counts("1 failed, 1 error") == (0, 1)
    assert parse_test_counts("nothing here") == (None, None)


def test_action_family_ignores_numbers_and_edit_bodies():
    assert action_family("edit 141:143\nfoo\nend_of_edit") == action_family("edit 140:142\nbar\nend_of_edit")
    assert action_family("str_replace_editor str_replace /w/a.py old_str=\"x\"") == action_family("str_replace_editor str_replace /w/a.py old_str=\"y\"")
    assert action_family("open a.py") != action_family("open b.py")


def test_stuck_same_action():
    steps = [S("ls")] + [S("python run.py", "Traceback\nValueError: bad")] * 3
    r = stuck_label(steps)
    assert r.value is True
    assert any(rs.startswith("same action x3") for rs in r.reasons)
    assert any(rs.startswith("same error x3") for rs in r.reasons)


def test_stuck_edit_cycle_on_same_location():
    steps = [
        S("edit 10:12\nA\nend_of_edit", "Your proposed edit has introduced new syntax error(s)"),
        S("edit 10:12\nB\nend_of_edit", "Your proposed edit has introduced new syntax error(s)"),
        S("edit 11:12\nC\nend_of_edit", "Your proposed edit has introduced new syntax error(s)"),
    ]
    r = stuck_label(steps)
    assert r.value is True and any("edit cycle" in x for x in r.reasons)


def test_not_stuck_when_varied():
    steps = [S("ls", "a b"), S("open a.py", "[File: a.py]"), S("edit 1:2\nx\nend_of_edit", "File updated"), S("python -m pytest", "3 passed")]
    assert stuck_label(steps).value is False
    # repeated submit is not stuck (kev/SWE-agent may echo the final action)
    assert stuck_label([S("submit")] * 3).value is False


def test_stuck_window_only_looks_at_recent_steps():
    steps = [S("python x.py", "Error: boom")] * 3 + [S(f"open f{i}.py", f"[File: f{i}.py]") for i in range(6)]
    assert stuck_label(steps, RuleConfig(window=6)).value is False


def test_progress_tests():
    base = [S("open a.py", "[File: a.py]")]
    assert progress_label(base + [S("pytest", "2 passed, 3 failed")]).value == 2  # first run, failures present
    assert progress_label(base + [S("pytest", "5 passed")]).value == 3  # first passing run
    hist = base + [S("pytest", "2 passed, 3 failed")]
    assert progress_label(hist + [S("edit 1:1\nx\nend_of_edit", "File updated"), S("pytest", "5 passed")]).value == 3
    assert progress_label(hist + [S("edit 1:1\nx\nend_of_edit", "File updated"), S("pytest", "1 passed, 4 failed")]).value == 0
    assert progress_label(hist + [S("edit 1:1\nx\nend_of_edit", "File updated"), S("pytest", "3 passed, 2 failed")]).value == 2
    assert progress_label(hist + [S("pytest", "2 passed, 3 failed")]).value == 1


def test_progress_edits_and_exploration():
    assert progress_label([S("open a.py", "[File: a.py]")]).value == 2
    assert progress_label([S("open a.py", "[File: a.py]"), S("open a.py", "[File: a.py]")]).value == 1
    assert progress_label([S("open a.py", "[File: a.py]"), S("edit 1:2\nx\nend_of_edit", "File updated")]).value == 2
    assert progress_label([S("open a.py", "[File: a.py]"), S("edit 1:2\nx\nend_of_edit", "Your proposed edit has introduced new syntax error(s)")]).value == 0
    r = progress_label([S("python x.py", "Traceback\nValueError: bad"), S("python x.py", "Traceback\nValueError: bad")])
    assert r.value == 0 and "same error" in r.reasons[0]
    assert progress_label([S("python x.py", "Traceback\nValueError: bad"), S("python y.py", "Traceback\nTypeError: worse")]).value == 1


def test_progress_finish():
    assert progress_label([S("pytest", "5 passed"), S("submit")]).value == 3
    assert progress_label([S("pytest", "1 passed, 1 failed"), S("submit")]).value == 1
    assert progress_label([S("ls", "a"), S("finish")]).value == 1


def test_task_stats_and_escalate():
    runs = [T([S("ls")], outcome=o, task_id="x__y-1") for o in (True, False, False, False)]
    st = task_stats(runs)["x__y-1"]
    assert (st.n_runs, st.n_success, st.baseline) == (4, 1, 0.25)
    assert escalate_label(False, st) is True
    assert escalate_label(True, st) is False
    assert escalate_label(False, TaskStats(2, 0)) is None  # too few runs
    assert escalate_label(False, TaskStats(4, 2)) is False  # baseline 0.5 > 0.25


def test_label_prefix_end_to_end():
    steps = [S("ls", "a"), S("open a.py", "[File: a.py]"), S("pytest", "1 passed, 2 failed"), S("edit 1:1\nx\nend_of_edit", "File updated"),
             S("pytest", "3 passed"), S("submit")]
    t = T(steps, outcome=True)
    st = TaskStats(n_runs=5, n_success=1)
    L = label_prefix(t, 5, st)
    assert L.p_success is True and L.baseline == 0.2 and L.advantage == 0.8
    assert L.steps_left == 0  # 1 remaining step -> bin 0
    assert L.progress == 3 and L.stuck is False and L.escalate is False
    Lf = label_prefix(T(steps, outcome=False), 2, st)
    assert Lf.steps_left is None and Lf.escalate is True and Lf.advantage == -0.2
    assert label_prefix(t, 3, None).baseline is None
    d = L.as_dict()
    assert set(d) >= {"p_success", "steps_left", "stuck", "progress", "escalate", "baseline", "advantage"}
