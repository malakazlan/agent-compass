from collections import Counter

from agent_compass.data.schema import Step, Trajectory
from agent_compass.data.splits import (
    VERIFIED_HOLDOUT,
    assign_split,
    build_split_table,
    split_for,
    verified_ids,
    verified_repos,
)


def traj(repo, task_id="x__y-1", **over):
    base = dict(
        traj_id=f"t/{repo}/{task_id}", task_id=task_id, domain="swe", scaffold="s", policy_model="p",
        repo_or_site=repo, task="do it", steps=[Step(action="ls", observation="")], outcome=True, meta={},
    )
    base.update(over)
    return Trajectory(**base)


def test_assign_split_is_deterministic_and_balanced():
    repos = [f"owner{i}/repo{i}" for i in range(20000)]
    a = [assign_split(r) for r in repos]
    b = [assign_split(r) for r in repos]
    assert a == b
    c = Counter(a)
    assert set(c) == {"train", "dev", "test"}
    assert abs(c["train"] / len(repos) - 0.80) < 0.02
    assert abs(c["dev"] / len(repos) - 0.10) < 0.02
    assert abs(c["test"] / len(repos) - 0.10) < 0.02


def test_assign_split_ignores_case_and_is_seeded():
    assert assign_split("Django/Django") == assign_split("django/django")
    # a different seed produces a different assignment for at least some repos
    repos = [f"o/r{i}" for i in range(200)]
    assert [assign_split(r) for r in repos] != [assign_split(r, seed="other") for r in repos]


def test_verified_resources_loaded():
    assert len(verified_ids()) == 500
    assert "django/django" in verified_repos()
    assert len(verified_repos()) == 12


def test_verified_repo_and_id_go_to_holdout():
    assert split_for(traj("django/django", task_id="django__django-11099")) == VERIFIED_HOLDOUT
    assert split_for(traj("django/django", task_id="django__django-99999999")) == VERIFIED_HOLDOUT  # repo alone is enough
    assert split_for(traj("someone/else", task_id="astropy__astropy-12907")) == VERIFIED_HOLDOUT  # id alone is enough
    assert split_for(traj("someone/else", task_id="someone__else-1")) in {"train", "dev", "test"}


def test_split_table_is_shared_across_datasets():
    ts = [traj("a/b", meta={"source": "ds1"}), traj("a/b", task_id="a__b-2", meta={"source": "ds2"}), traj("c/d")]
    table = build_split_table(ts)
    assert set(table) == {"a/b", "c/d"}
    assert table["a/b"] == assign_split("a/b")
