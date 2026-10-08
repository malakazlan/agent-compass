"""Converter for `AgentSuite/tau2-bench-trajectories` (tool-use customer-service agents).

One `{policy_model}.jsonl` per policy; each line is a run of one tau2-bench task:
    model_path, user_model_path, benchmark_name, task_name (airline|retail|telecom), messages[],
    eval_result {score, db_match}, meta {id, is_correct, finish_reason, ...}
Messages: system (domain policy, identical across tasks of a domain), then alternating assistant
turns (text to the user, or tool_calls [{id, name, arguments}]) with tool results (role=tool) and
simulated-user replies (role=user). Outcome is programmatic: eval_result.score == 1.0.

Mapping to the unified schema:
    task        "[<domain> customer-service task] " + the user's opening request (the long domain
                policy is dropped: it is the same for every task of the domain and would eat the budget)
    step        one per assistant turn; action = tool call(s) rendered as name(args) or "say: <text>";
                observation = the tool results and/or the user's reply that follow, until the next
                assistant turn; thought = None (tau2 transcripts carry no separate reasoning)
    outcome     eval_result.score == 1.0
    repo_or_site "tau2/<domain>-<task id>" so the repo-hash split holds out whole tasks (all 30
                policies' runs of a task land in one split)
    policy_model model_path basename; scaffold "tau2-bench"
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_compass.data.convert.nebius_swe_agent import EmptyTrajectory
from agent_compass.data.schema import Step, Trajectory

SOURCE = "AgentSuite/tau2-bench-trajectories"
SCAFFOLD = "tau2-bench"
TRAJ_PREFIX = "tau2"


def _text(content: Any) -> str:
    if content is None:
        return ""
    s = str(content)
    return "" if s == "None" else s


def render_call(call: dict) -> str:
    args = call.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    return f"{call.get('name')}({json.dumps(args, ensure_ascii=False, sort_keys=True) if not isinstance(args, str) else args})"


def convert_row(row: dict[str, Any], *, source: str = SOURCE) -> Trajectory:
    msgs = row["messages"]
    meta = row.get("meta") or {}
    domain = row.get("task_name") or "unknown"
    task_id = str(meta.get("id") or row.get("task_id") or "")
    if not task_id:
        raise ValueError("tau2 row without meta.id")
    policy = Path(str(row.get("model_path") or "unknown")).name
    assistant_turns = [i for i, m in enumerate(msgs) if m["role"] == "assistant"]
    if not assistant_turns:
        raise EmptyTrajectory(f"{task_id}: no assistant turns")
    first_user = next((_text(m.get("content")) for m in msgs if m["role"] == "user" and _text(m.get("content"))), "")
    task = f"[{domain} customer-service task] {first_user}".strip()

    steps: list[Step] = []
    for n, i in enumerate(assistant_turns):
        m = msgs[i]
        calls = m.get("tool_calls") or []
        content = _text(m.get("content"))
        if calls:
            action = " ; ".join(render_call(c) for c in calls)
            thought = content or None  # text alongside a tool call is the agent talking; keep it as thought
        else:
            action = "say: " + content if content else "say:"
            thought = None
        end = assistant_turns[n + 1] if n + 1 < len(assistant_turns) else len(msgs)
        obs_parts = []
        for k in range(i + 1, end):
            mk = msgs[k]
            t = _text(mk.get("content"))
            if not t:
                continue
            obs_parts.append(("tool: " if mk["role"] == "tool" else "user: ") + t)
        steps.append(Step(action=action, observation="\n".join(obs_parts), thought=thought,
                          extra={"tools": [c.get("name") for c in calls], "turn_idx": m.get("turn_idx")}))

    score = (row.get("eval_result") or {}).get("score")
    outcome = bool(score == 1.0) if score is not None else bool(meta.get("is_correct"))
    return Trajectory(
        traj_id=f"{TRAJ_PREFIX}/{policy}/{task_id}",
        task_id=f"{TRAJ_PREFIX}/{task_id}",
        domain="tool",
        scaffold=SCAFFOLD,
        policy_model=policy,
        repo_or_site=f"{TRAJ_PREFIX}/{domain}-{task_id}",
        task=task,
        steps=steps,
        outcome=outcome,
        meta={"source": source, "exit_status": meta.get("finish_reason"), "has_patch": False, "db_match": (row.get("eval_result") or {}).get("db_match"),
              "user_model": Path(str(row.get("user_model_path") or "")).name, "duration_seconds": meta.get("duration_seconds"),
              "n_messages": len(msgs), "domain_name": domain},
    )


def convert_jsonl(path: str | Path, *, source: str = SOURCE, skipped: dict[str, int] | None = None) -> Iterator[Trajectory]:
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                yield convert_row(row, source=source)
            except EmptyTrajectory:
                if skipped is not None:
                    skipped["empty"] = skipped.get("empty", 0) + 1
