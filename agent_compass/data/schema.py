"""Unified trajectory record.

Every source dataset is converted into this one shape before labels, prefixes or
packing are computed. Keep it small: the fields below are the only things the rest
of the pipeline is allowed to depend on. Source-specific extras go in `meta`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictBool, field_validator

DOMAINS = ("swe", "web", "tool", "terminal", "assistant", "math")


class Step(BaseModel):
    """One agent step: what it did, what it saw, and (optionally) what it said it was thinking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    observation: str
    thought: str | None = None
    extra: dict[str, Any] = {}


class Trajectory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    traj_id: str
    task_id: str
    domain: str
    scaffold: str
    policy_model: str
    repo_or_site: str
    task: str
    steps: list[Step]
    outcome: StrictBool
    meta: dict[str, Any] = {}

    @field_validator("task")
    @classmethod
    def _task_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("task must not be blank")
        return v

    @field_validator("steps")
    @classmethod
    def _steps_not_empty(cls, v: list[Step]) -> list[Step]:
        if not v:
            raise ValueError("steps must not be empty")
        return v

    @field_validator("domain")
    @classmethod
    def _domain_known(cls, v: str) -> str:
        if v not in DOMAINS:
            raise ValueError(f"domain must be one of {DOMAINS}, got {v!r}")
        return v

    def without_thoughts(self) -> "Trajectory":
        """The anti-shortcut view: actions and observations only."""
        steps = [s.model_copy(update={"thought": None}) for s in self.steps]
        return self.model_copy(update={"steps": steps})

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, line: str) -> "Trajectory":
        return cls.model_validate_json(line)


def write_jsonl(trajs: Iterable[Trajectory], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for t in trajs:
            f.write(t.to_json())
            f.write("\n")
            n += 1
    return n


def read_jsonl(path: str | Path) -> Iterator[Trajectory]:
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield Trajectory.from_json(line)


# --- repo keys -----------------------------------------------------------------

_GH_URL = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)
_OWNER_REPO = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")
# SWE-bench style: owner__repo-1234 ; SWE-smith style: owner__repo.<commit>.<bug>__<hash>
_DUNDER = re.compile(r"^([A-Za-z0-9_.-]+?)__([A-Za-z0-9_-]+?)(?:-\d+|\.[0-9a-f]{6,}.*|__.*)?$")


def repo_key(value: str) -> str:
    """Canonical `owner/repo`, lower-cased, from a URL, an `owner/repo` string, or an instance id.

    This key is the unit of the train/dev/test split, so every converter must go through it.
    """
    s = value.strip()
    m = _GH_URL.match(s) or _OWNER_REPO.match(s)
    if m:
        return f"{m.group(1)}/{m.group(2)}".lower()
    m = _DUNDER.match(s)
    if m:
        return f"{m.group(1)}/{m.group(2)}".lower()
    raise ValueError(f"cannot derive owner/repo from {value!r}")


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
