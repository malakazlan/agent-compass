import json

import pytest

from agent_compass.data.schema import Step, Trajectory, read_jsonl, repo_key, write_jsonl


def make_traj(**over):
    base = dict(
        traj_id="nebius-swe-agent/0",
        task_id="django__django-11099",
        domain="swe",
        scaffold="swe-agent",
        policy_model="swe-agent-llama-70b",
        repo_or_site="django/django",
        task="Fix the bug in UsernameValidator.",
        steps=[
            Step(action="ls", observation="README.md setup.py", thought="Look around."),
            Step(action="cat setup.py", observation="...", thought=None),
        ],
        outcome=False,
        meta={"source": "nebius/SWE-agent-trajectories"},
    )
    base.update(over)
    return Trajectory(**base)


def test_roundtrip_jsonl(tmp_path):
    p = tmp_path / "t.jsonl"
    t = make_traj()
    write_jsonl([t], p)
    back = list(read_jsonl(p))
    assert back == [t]
    line = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert set(line) == {"traj_id", "task_id", "domain", "scaffold", "policy_model", "repo_or_site", "task", "steps", "outcome", "meta"}
    assert line["steps"][1]["thought"] is None


def test_rejects_empty_task_and_steps():
    with pytest.raises(ValueError):
        make_traj(task="  ")
    with pytest.raises(ValueError):
        make_traj(steps=[])


def test_outcome_must_be_bool():
    with pytest.raises(ValueError):
        make_traj(outcome=1)


def test_without_thoughts_view():
    t = make_traj()
    v = t.without_thoughts()
    assert all(s.thought is None for s in v.steps)
    assert v.steps[0].action == "ls"
    assert t.steps[0].thought == "Look around."


def test_repo_key_normalises():
    assert repo_key("Django/Django") == "django/django"
    assert repo_key("https://github.com/psf/requests.git") == "psf/requests"
    assert repo_key("psf__requests-1234") == "psf/requests"
    assert repo_key("marshmallow-code__marshmallow.abc123.func_pm__xyz") == "marshmallow-code/marshmallow"
    with pytest.raises(ValueError):
        repo_key("nonsense")
