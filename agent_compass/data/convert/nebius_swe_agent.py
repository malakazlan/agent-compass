"""Converter for `nebius/SWE-agent-trajectories` (CC-BY-4.0).

Row schema (parquet): instance_id, model_name, target (bool), trajectory (list of
{role: system|user|ai, text, system_prompt, mask, cutoff_date}), exit_status,
generated_patch, eval_logs.

Message pattern is always `system, user, (ai, user)*, ai?`. The first user message
wraps the GitHub issue in an "ISSUE: ... INSTRUCTIONS:" template. Each `ai` message is
free-text reasoning followed by one fenced block holding the command (SWE-agent
convention). `eval_logs` holds the harness output and is never copied.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_compass.data.schema import Step, Trajectory, repo_key


class EmptyTrajectory(ValueError):
    """The agent never acted (only system + task messages). Nothing to learn from; skipped."""

SOURCE = "nebius/SWE-agent-trajectories"
SCAFFOLD = "swe-agent"
TRAJ_PREFIX = "nebius-swe-agent"

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_ISSUE_HEAD = re.compile(r"^.*?\bISSUE:\s*\n", re.S)
_ISSUE_TAIL = re.compile(r"\n\s*INSTRUCTIONS:.*$", re.S)


def extract_issue(first_user_text: str) -> str:
    """The issue text without the SWE-agent wrapper sentences."""
    s = first_user_text
    s = _ISSUE_HEAD.sub("", s, count=1) if "ISSUE:" in s else s
    s = _ISSUE_TAIL.sub("", s, count=1)
    return s.strip()


def split_thought_command(ai_text: str) -> tuple[str | None, str, int]:
    """(thought, command, number_of_fenced_blocks). The command is the last fenced block."""
    blocks = list(_FENCE.finditer(ai_text))
    if not blocks:
        return (ai_text.strip() or None), "", 0
    last = blocks[-1]
    command = last.group(1).strip()
    thought = ai_text[: last.start()].strip()
    return (thought or None), command, len(blocks)


def convert_row(row: dict[str, Any], *, row_index: int, source: str = SOURCE, shard: str = "") -> Trajectory:
    msgs = row["trajectory"]
    if len(msgs) >= 2 and msgs[0]["role"] == "system" and msgs[1]["role"] == "user" and len(msgs) < 3:
        raise EmptyTrajectory(f"{row.get('instance_id')}: no agent steps")
    if len(msgs) < 3 or msgs[0]["role"] != "system" or msgs[1]["role"] != "user":
        raise ValueError(f"{row.get('instance_id')}: unexpected message prefix {[m['role'] for m in msgs[:3]]}")
    task = extract_issue(msgs[1]["text"] or "")

    steps: list[Step] = []
    i = 2
    while i < len(msgs):
        m = msgs[i]
        if m["role"] != "ai":
            raise ValueError(f"{row.get('instance_id')}: expected ai at message {i}, got {m['role']}")
        thought, command, n_blocks = split_thought_command(m["text"] or "")
        observation = ""
        if i + 1 < len(msgs):
            nxt = msgs[i + 1]
            if nxt["role"] != "user":
                raise ValueError(f"{row.get('instance_id')}: expected user at message {i + 1}, got {nxt['role']}")
            observation = nxt["text"] or ""
        if not command:
            # No fenced block: the agent sent a bare message. Keep it as a thought so the
            # thoughts-removed view drops it.
            steps.append(Step(action="message", observation=observation, thought=thought, extra={"n_code_blocks": 0}))
        else:
            steps.append(Step(action=command, observation=observation, thought=thought, extra={"n_code_blocks": n_blocks}))
        i += 2

    instance_id = row["instance_id"]
    traj_id = f"{TRAJ_PREFIX}/{shard}/{row_index}" if shard else f"{TRAJ_PREFIX}/{row_index}"
    return Trajectory(
        traj_id=traj_id,
        task_id=instance_id,
        domain="swe",
        scaffold=SCAFFOLD,
        policy_model=row["model_name"],
        repo_or_site=repo_key(instance_id),
        task=task,
        steps=steps,
        outcome=bool(row["target"]),
        meta={
            "source": source,
            "exit_status": row.get("exit_status"),
            "has_patch": bool((row.get("generated_patch") or "").strip()),
            "n_messages": len(msgs),
        },
    )


def convert_parquet(path: str | Path, *, source: str = SOURCE, skipped: dict[str, int] | None = None) -> Iterator[Trajectory]:
    """Stream one parquet shard row-group by row-group; `eval_logs` is never read.
    Rows without agent steps are skipped and counted in `skipped["empty"]`."""
    import pyarrow.parquet as pq

    path = Path(path)
    shard = path.stem
    pf = pq.ParquetFile(path)
    cols = ["instance_id", "model_name", "target", "trajectory", "exit_status", "generated_patch"]
    base = 0
    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=cols)
        for j, row in enumerate(table.to_pylist()):
            try:
                yield convert_row(row, row_index=base + j, source=source, shard=shard)
            except EmptyTrajectory:
                if skipped is not None:
                    skipped["empty"] = skipped.get("empty", 0) + 1
        base += table.num_rows
