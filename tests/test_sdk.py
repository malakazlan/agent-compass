import math

import pytest

from agent_compass.data.schema import Step, Trajectory
from agent_compass.sdk import Answers, Compass, Guardian, TrajectoryTracker, build_questions
from agent_compass.sdk.calibration import DEFAULT_CALIBRATION, temper
from agent_compass.sdk.client import FunctionBackend, answers_to_probs
from agent_compass.sdk.integrations.generic import CompassMonitor
from agent_compass.sdk.questions import QUESTIONS, option_keys


def fake_scorer(values: dict[str, list[float]]):
    calls: list[dict] = []

    def fn(state, questions):
        calls.append({"state": state, "questions": questions})
        out = {}
        for qid, q in questions.items():
            if qid in values:
                out[qid] = values[qid]
            else:
                k = len(option_keys(qid, q))
                out[qid] = [1.0 / k] * k
        return out

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def test_temper_identity_and_flattening():
    p = [0.1, 0.9]
    assert temper(p, 1.0) == p
    hot = temper(p, 3.0)
    assert 0.5 < hot[1] < 0.9 and math.isclose(sum(hot), 1.0)


def test_build_questions_matches_training_definitions():
    q = build_questions(("p_success", "best_next"), candidates=["ls", "pytest -x"])
    assert q["p_success"] == QUESTIONS["p_success"]
    assert list(q["best_next"]["criteria"]) == ["option_1", "option_2"]
    with pytest.raises(ValueError):
        build_questions(("best_next",))
    with pytest.raises(ValueError):
        build_questions(("nonsense",))


def test_answers_to_probs_inverts_server_shape():
    qs = build_questions(("p_success", "progress", "best_next"), candidates=["a", "b", "c"])
    answers = {"p_success": {"type": "noul", "noul": 0.3},
               "progress": {"type": "score", "probabilities": {"0": 0.1, "1": 0.2, "2": 0.3, "3": 0.4}},
               "best_next": {"type": "choice", "probabilities": {"option_1": 0.2, "option_2": 0.5, "option_3": 0.3}}}
    p = answers_to_probs(answers, qs)
    assert p["p_success"] == [0.7, 0.3]
    assert p["progress"] == [0.1, 0.2, 0.3, 0.4]
    assert p["best_next"] == [0.2, 0.5, 0.3]


def test_compass_routes_and_calibrates():
    v0 = FunctionBackend(fake_scorer({"p_success": [0.2, 0.8]}))
    v1 = FunctionBackend(fake_scorer({"p_success": [0.5, 0.5], "stuck": [0.9, 0.1]}))
    compass = Compass({"v0": v0, "v1": v1})
    a = compass.score("<task>x</task>", ("p_success", "stuck"))
    assert a["p_success"].adapter == "v0" and a["stuck"].adapter == "v1"
    assert a["p_success"].raw == [0.2, 0.8]
    expected = temper([0.2, 0.8], DEFAULT_CALIBRATION.temperature("v0", "p_success"))[1]
    assert math.isclose(a.p_success, expected)
    assert len(v0.fn.calls) == 1 and len(v1.fn.calls) == 1
    assert set(v0.fn.calls[0]["questions"]) == {"p_success"}


def test_single_backend_takes_everything_and_drops_best_next_by_default():
    only = FunctionBackend(fake_scorer({}))
    compass = Compass(only)
    a = compass.score("<task>x</task>")
    assert set(a.questions) == {"p_success", "stuck", "progress", "escalate", "steps_left"}
    assert all(q.adapter == "v1" for q in a.questions.values())
    with pytest.raises(ValueError):
        compass.score("<task>x</task>", ("best_next",))


def test_compass_renders_trajectories_and_picks():
    backend = FunctionBackend(fake_scorer({"best_next": [0.1, 0.7, 0.2]}))
    compass = Compass(backend)
    traj = Trajectory(traj_id="t", task_id="task", domain="swe", scaffold="custom", policy_model="gpt-x", repo_or_site="o/r",
                      task="Fix the bug", steps=[Step(action="ls", observation="a.py b.py")], outcome=False)
    idx, answers = compass.pick(traj, ["cat a.py", "pytest", "rm -rf /"])
    assert idx == 1
    state = backend.fn.calls[0]["state"]
    assert "<task>" in state and "Fix the bug" in state and "<policy>gpt-x</policy>" in state and "$ ls" in state
    assert answers.best_next == 1 and answers.policy == "gpt-x"


def test_tracker_builds_valid_trajectory():
    t = TrajectoryTracker(task="do it", policy_model="unknown")
    with pytest.raises(ValueError):
        t.trajectory()
    t.add("pytest", "1 failed")
    t.add("edit", "ok", thought="hmm")
    traj = t.trajectory()
    assert len(traj.steps) == 2 and traj.steps[1].thought == "hmm" and traj.policy_model == "unknown"


def make_answers(p_success=None, stuck=None, escalate=None) -> Answers:
    from agent_compass.sdk.client import ScoredQuestion

    qs = {}
    for qid, p in (("p_success", p_success), ("stuck", stuck), ("escalate", escalate)):
        if p is not None:
            qs[qid] = ScoredQuestion(qid=qid, type="noul", keys=["false", "true"], raw=[1 - p, p], calibrated=[1 - p, p], adapter="v1")
    return Answers(questions=qs, state="")


def test_guardian_rules():
    g = Guardian(budget="fpr_5", min_steps=2, patience=2)
    assert g.abort_threshold == 0.12
    assert g.decide(make_answers(0.01), step=1).action == "continue"  # before min_steps
    assert g.decide(make_answers(0.01), step=2).action == "continue"  # streak 1
    assert g.decide(make_answers(0.01), step=3).action == "abort"  # streak 2
    g.reset()
    assert g.decide(make_answers(0.5, stuck=0.9), step=5).action == "escalate"
    assert g.decide(make_answers(0.5, escalate=0.95), step=5).action == "escalate"
    assert g.decide(make_answers(0.5), step=5).action == "continue"
    per_policy = Guardian(policy="swe-agent-llama-70b")
    assert per_policy.abort_threshold == 0.06
    assert Guardian(policy="never-seen").abort_threshold == 0.12


def test_monitor_end_to_end_with_fake_backend():
    # raw p_success 1e-4 becomes about 0.045 after the v1 temperature (3.02), under the 0.12 pooled abort threshold
    backend = FunctionBackend(fake_scorer({"p_success": [0.9999, 0.0001], "stuck": [0.9, 0.1], "escalate": [0.9, 0.1]}))
    monitor = CompassMonitor("fix it", Compass(backend), guardian=Guardian(min_steps=1, patience=2), policy_model="unknown")
    assert monitor.on_step("pytest", "fail").action == "continue"
    assert monitor.on_step("pytest", "fail").action == "abort"
    assert len(monitor.log) == 2 and monitor.log[-1]["decision"] == "abort"
    assert monitor.pick(["a", "b"]) in (0, 1)
