"""Converter for `nebius/SWE-rebench-openhands-trajectories` (CC-BY-4.0).

Row schema (parquet): trajectory_id, instance_id, repo, trajectory (list of
{role: system|user|assistant|tool, content, tool_calls, name, tool_call_id}), tools,
model_patch, exit_status, resolved (int 0/1), gen_tests_correct, pred_passes_gen_tests.

Message pattern is `system, user, (assistant[1 tool_call], tool)*`. Tools seen:
execute_bash, str_replace_editor, think, task_tracker, finish. The first user message
wraps the issue in `<issue_description>` tags. Policy model and scaffold are fixed for the
whole dataset (dataset card): Qwen3-Coder-480B-A35B-Instruct under OpenHands v0.54.0.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_compass.data.convert.nebius_swe_agent import EmptyTrajectory
from agent_compass.data.schema import Step, Trajectory, repo_key

SOURCE = "nebius/SWE-rebench-openhands-trajectories"
SCAFFOLD = "openhands-0.54"
POLICY_MODEL = "Qwen3-Coder-480B-A35B-Instruct"
TRAJ_PREFIX = "nebius-openhands"

_ISSUE = re.compile(r"<issue_description>\s*(.*?)\s*</issue_description>", re.S)
# Tools whose payload is the agent talking, not acting. Their text goes to `thought`.
_TALK_TOOLS = {"think": "thought", "finish": "message"}


def extract_issue(first_user_content: str) -> str:
    m = _ISSUE.search(first_user_content or "")
    return (m.group(1) if m else (first_user_content or "")).strip()


def render_action(tool: str, args: dict[str, Any]) -> str:
    """One-line canonical action string. Raw tool name and args are kept in `Step.extra`."""
    if tool == "execute_bash":
        return str(args.get("command", "")).strip()
    if tool == "str_replace_editor":
        parts = ["str_replace_editor", str(args.get("command", "")), str(args.get("path", ""))]
        for k in ("view_range", "insert_line"):
            if k in args:
                parts.append(f"{k}={json.dumps(args[k])}")
        if "old_str" in args:
            parts.append(f"old_str={_short(args['old_str'])}")
        if "new_str" in args:
            parts.append(f"new_str={_short(args['new_str'])}")
        if "file_text" in args:
            parts.append(f"file_text={_short(args['file_text'])}")
        return " ".join(p for p in parts if p)
    if tool in _TALK_TOOLS:
        return tool
    if tool == "task_tracker":
        return f"task_tracker {args.get('command', '')}".strip()
    return f"{tool} {json.dumps(args, ensure_ascii=False)}"


def _short(s: Any, n: int = 120) -> str:
    s = json.dumps(str(s), ensure_ascii=False)
    return s if len(s) <= n else s[: n - 3] + '..."'


def _join(*parts: str | None) -> str | None:
    kept = [p.strip() for p in parts if p and p.strip()]
    return "\n\n".join(kept) if kept else None


def convert_row(row: dict[str, Any], *, source: str = SOURCE) -> Trajectory:
    resolved = row["resolved"]
    if resolved not in (0, 1):
        raise ValueError(f"{row.get('instance_id')}: resolved={resolved!r} is not a usable label")
    msgs = row["trajectory"]
    if len(msgs) >= 2 and msgs[0]["role"] == "system" and msgs[1]["role"] == "user" and len(msgs) < 3:
        raise EmptyTrajectory(f"{row.get('instance_id')}: no agent steps")
    if len(msgs) < 3 or msgs[0]["role"] != "system" or msgs[1]["role"] != "user":
        raise ValueError(f"{row.get('instance_id')}: unexpected message prefix {[m['role'] for m in msgs[:3]]}")
    task = extract_issue(msgs[1]["content"])
    if not any(m["role"] == "assistant" for m in msgs[2:]):
        raise EmptyTrajectory(f"{row.get('instance_id')}: no assistant turns")

    # Observations are looked up by tool_call_id so a missing or reordered tool message
    # cannot shift observations onto the wrong step.
    obs_by_id: dict[str, str] = {}
    for m in msgs[2:]:
        if m["role"] == "tool" and m.get("tool_call_id"):
            obs_by_id[m["tool_call_id"]] = m.get("content") or ""

    steps: list[Step] = []
    for m in msgs[2:]:
        if m["role"] != "assistant":
            continue
        content = m.get("content") or ""
        calls = m.get("tool_calls") or []
        if not calls:
            steps.append(Step(action="message", observation="", thought=content.strip() or None, extra={"tool": None, "args": {}}))
            continue
        for k, tc in enumerate(calls):
            fn = tc["function"]
            tool = fn["name"]
            try:
                args = json.loads(fn["arguments"]) if fn["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": fn["arguments"]}
            if not isinstance(args, dict):
                args = {"_raw": args}
            talk = None
            kept_args = args
            if tool in _TALK_TOOLS:
                # The agent's own words: keep them only in `thought`, so the
                # thoughts-removed view really removes them.
                key = _TALK_TOOLS[tool]
                talk = args.get(key)
                kept_args = {k2: v for k2, v in args.items() if k2 != key}
            thought = _join(content if k == 0 else None, str(talk) if talk is not None else None)
            steps.append(
                Step(
                    action=render_action(tool, args),
                    observation=obs_by_id.get(tc["id"], ""),
                    thought=thought,
                    extra={"tool": tool, "args": kept_args},
                )
            )

    instance_id = row["instance_id"]
    return Trajectory(
        traj_id=f"{TRAJ_PREFIX}/{row['trajectory_id']}",
        task_id=instance_id,
        domain="swe",
        scaffold=SCAFFOLD,
        policy_model=POLICY_MODEL,
        repo_or_site=repo_key(row["repo"]) if row.get("repo") else repo_key(instance_id),
        task=task,
        steps=steps,
        outcome=bool(resolved),
        meta={
            "source": source,
            "exit_status": row.get("exit_status"),
            "has_patch": bool((row.get("model_patch") or "").strip()),
            "gen_tests_correct": row.get("gen_tests_correct"),
            "pred_passes_gen_tests": row.get("pred_passes_gen_tests"),
            "n_messages": len(msgs),
        },
    )


def convert_parquet(path: str | Path, *, source: str = SOURCE, skipped: dict[str, int] | None = None) -> Iterator[Trajectory]:
    """Stream the (single, large) parquet row-group by row-group; skips resolved=-1 rows and empty runs."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(Path(path))
    cols = ["trajectory_id", "instance_id", "repo", "trajectory", "model_patch", "exit_status", "resolved", "gen_tests_correct", "pred_passes_gen_tests"]
    for rg in range(pf.num_row_groups):
        for row in pf.read_row_group(rg, columns=cols).to_pylist():
            if row["resolved"] not in (0, 1):
                if skipped is not None:
                    skipped["unlabelled"] = skipped.get("unlabelled", 0) + 1
                continue
            try:
                yield convert_row(row, source=source)
            except EmptyTrajectory:
                if skipped is not None:
                    skipped["empty"] = skipped.get("empty", 0) + 1
