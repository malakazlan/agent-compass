"""Leakage-safe splits.

The unit of the split is the repository, never the trajectory or the task. One global,
deterministic rule assigns every repository to train / dev / test, so a repository lands
in the same split in every dataset, now and for datasets added later.

Anything touching SWE-bench Verified (its 12 repositories or its 500 instance ids) goes
to `verified_holdout`, which is never used for training or tuning. Our online evaluation
runs on SWE-bench Verified, so this is what keeps M5 honest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

from agent_compass.data.schema import Trajectory, repo_key

SEED = "agent-compass-v1"
DEV_PCT = 10
TEST_PCT = 10
VERIFIED_HOLDOUT = "verified_holdout"
SPLITS = ("train", "dev", "test", VERIFIED_HOLDOUT)

_RESOURCE = Path(__file__).with_name("resources") / "swebench_verified.json"


@lru_cache(maxsize=1)
def _verified() -> dict:
    return json.loads(_RESOURCE.read_text(encoding="utf-8"))


def verified_ids() -> frozenset[str]:
    return frozenset(_verified()["instance_ids"])


def verified_repos() -> frozenset[str]:
    return frozenset(r.lower() for r in _verified()["repos"])


def assign_split(repo: str, *, seed: str = SEED, dev_pct: int = DEV_PCT, test_pct: int = TEST_PCT) -> str:
    """train / dev / test from a hash of the canonical repo key. Stable across runs and machines."""
    key = repo_key(repo) if "/" in repo else repo.lower()
    bucket = int(hashlib.sha1(f"{seed}:{key}".encode()).hexdigest(), 16) % 100
    if bucket < 100 - dev_pct - test_pct:
        return "train"
    if bucket < 100 - test_pct:
        return "dev"
    return "test"


def split_for(traj: Trajectory) -> str:
    repo = repo_key(traj.repo_or_site)
    if repo in verified_repos() or traj.task_id in verified_ids():
        return VERIFIED_HOLDOUT
    return assign_split(repo)


def build_split_table(trajs: Iterable[Trajectory]) -> dict[str, str]:
    """repo -> split for every repo seen. Freeze this to `data/splits/repo_split.json`."""
    table: dict[str, str] = {}
    for t in trajs:
        repo = repo_key(t.repo_or_site)
        if repo not in table:
            table[repo] = VERIFIED_HOLDOUT if repo in verified_repos() else assign_split(repo)
    return dict(sorted(table.items()))


def write_split_table(table: dict[str, str], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rule": f"sha1('{SEED}:' + owner/repo) % 100 -> train < {100 - DEV_PCT - TEST_PCT}, dev < {100 - TEST_PCT}, else test; "
        f"SWE-bench Verified repos and instance ids -> {VERIFIED_HOLDOUT}",
        "n_repos": len(table),
        "repos": table,
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
