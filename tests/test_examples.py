import json

from agent_compass.data.examples import FAKE_CONFIDENCE, BuildConfig, build_records
from agent_compass.data.labels import TaskStats
from agent_compass.data.schema import Step, Trajectory


def run(traj_id, actions, outcome, task_id="o__r-1"):
    return Trajectory(
        traj_id=traj_id, task_id=task_id, domain="swe", scaffold="swe-agent", policy_model="llama-70b", repo_or_site="o/r",
        task="Fix it", steps=[Step(action=a, observation=f"out of {a}", thought=f"thinking about {a}") for a in actions],
        outcome=outcome, meta={"source": "unit", "split": "train"},
    )


A = run("A", ["ls", "open a.py", "edit 1:2 x", "pytest", "submit"], True)
B = run("B", ["ls", "open a.py", "edit 9:9 wrong", "pytest", "edit 9:9 wrong2", "submit"], False)
STATS = TaskStats(n_runs=4, n_success=1)


def test_records_have_kev_shape_and_masking():
    recs = list(build_records(B, [A, B], STATS, BuildConfig(policy_dropout=0.0), policy_rate=0.9))
    assert recs and all(set(r) == {"state", "questions", "_meta"} for r in recs)
    r = recs[0]
    assert r["state"].startswith("<task>\nFix it\n</task>\n<policy>llama-70b</policy>")
    assert "thinking about" not in r["state"]  # clean variant has no thoughts
    q = r["questions"]
    assert q["p_success"]["type"] == "noul" and q["p_success"]["label"] is False
    assert "steps_left" not in q  # failed run -> masked
    assert q["escalate"]["label"] is True  # other runs pass 1/3 <= 0.5 * 0.9, and this run failed
    assert q["progress"]["type"] == "score" and len(q["progress"]["criteria"]) == 4 and 0 <= q["progress"]["label"] <= 3
    assert r["_meta"]["group_id"] == "o__r-1" and abs(r["_meta"]["advantage"] + 1 / 3) < 1e-9  # leave-one-out baseline
    assert "escalate" not in list(build_records(B, [A, B], STATS, BuildConfig()))[0]["questions"]  # no policy rate -> masked
    json.dumps(recs)  # serialisable


def test_success_run_has_steps_left_and_candidates():
    recs = list(build_records(A, [A, B], STATS, BuildConfig(policy_dropout=0.0), prefix_lens=[2]))
    q = recs[0]["questions"]
    assert q["steps_left"]["label"] == 0  # 3 remaining -> bin 0
    assert q["best_next"]["type"] == "choice"
    assert q["best_next"]["label"] in q["best_next"]["criteria"]
    assert q["best_next"]["criteria"][q["best_next"]["label"]] == "edit 1:2 x"
    assert recs[0]["_meta"]["best_next"]["tier"] == "branching"


def test_policy_dropout_and_variants():
    recs = list(build_records(A, [A], None, BuildConfig(policy_dropout=1.0), prefix_lens=[3]))
    assert "<policy>unknown</policy>" in recs[0]["state"] and recs[0]["_meta"]["policy_shown"] == "unknown"
    recs = list(build_records(A, [A], None, BuildConfig(variant="thoughts", policy_dropout=0.0), prefix_lens=[3]))
    assert "> thinking about edit 1:2 x" in recs[0]["state"]
    recs = list(build_records(B, [B], None, BuildConfig(variant="fake_confidence", policy_dropout=0.0), prefix_lens=[3]))
    assert FAKE_CONFIDENCE in recs[0]["state"]
    assert recs[0]["questions"]["p_success"]["label"] is False  # label unchanged: the model must not flip


def test_masked_when_no_stats_or_single_run():
    recs = list(build_records(A, [A], None, BuildConfig(), prefix_lens=[1, 2]))
    assert all("escalate" not in r["questions"] and "best_next" not in r["questions"] for r in recs)
    assert [r["_meta"]["prefix_len"] for r in recs] == [1, 2]
