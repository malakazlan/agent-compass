import json
from pathlib import Path

import pytest

from agent_compass.data.convert.nebius_openhands import convert_row as oh_convert, render_action
from agent_compass.data.convert.nebius_swe_agent import convert_row as sa_convert, split_thought_command, extract_issue

RAW = Path(__file__).resolve().parents[1] / ".scratch" / "raw"


# ---------------------------------------------------------------- SWE-agent

def sa_msg(role, text, system_prompt=""):
    return {"role": role, "text": text, "system_prompt": system_prompt, "mask": role == "ai", "cutoff_date": "01.01.2023"}


SA_ROW = {
    "instance_id": "AnalogJ__lexicon-336",
    "model_name": "swe-agent-llama-70b",
    "target": False,
    "exit_status": "submitted (exit_context)",
    "generated_patch": "diff --git a/x b/x",
    "eval_logs": "===== FAILED tests ... (never used)",
    "trajectory": [
        sa_msg("system", "", system_prompt="SETTING: You are an autonomous programmer..."),
        sa_msg("user", "We're currently solving the following issue within our repository. Here's the issue text:\nISSUE:\nMemset provider: TypeError\n\nWhen using memset I get an error.\n\nINSTRUCTIONS:\nNow, you're going to solve this issue on your own."),
        sa_msg("ai", "Let's reproduce the error first.\n\n```\nlexicon memset create example.com TXT\n```"),
        sa_msg("user", "Traceback (most recent call last):\n  HTTPError 403\n(Open file: n/a)\n(Current directory: /lexicon)\nbash-$"),
        sa_msg("ai", "```\nfind_file \"memset.py\" src\n```"),
        sa_msg("user", "Directory src not found\n(Open file: n/a)\n(Current directory: /lexicon)\nbash-$"),
        sa_msg("ai", "Two blocks here, the last one is the command.\n```python\nprint(1)\n```\nSo run:\n```\nsubmit\n```"),
    ],
}


def test_extract_issue_strips_wrapper():
    issue = extract_issue(SA_ROW["trajectory"][1]["text"])
    assert issue.startswith("Memset provider: TypeError")
    assert "INSTRUCTIONS" not in issue
    assert "We're currently solving" not in issue


def test_split_thought_command():
    thought, cmd, n = split_thought_command("Let's look.\n\n```\nls -la\n```")
    assert (thought, cmd, n) == ("Let's look.", "ls -la", 1)
    thought, cmd, n = split_thought_command("```\nsubmit\n```")
    assert (thought, cmd, n) == (None, "submit", 1)
    thought, cmd, n = split_thought_command("no command here")
    assert (thought, cmd, n) == ("no command here", "", 0)


def test_swe_agent_row_converts():
    t = sa_convert(SA_ROW, row_index=7, source="nebius/SWE-agent-trajectories", shard="train-00000")
    assert t.traj_id == "nebius-swe-agent/train-00000/7"
    assert t.task_id == "AnalogJ__lexicon-336"
    assert t.repo_or_site == "analogj/lexicon"
    assert t.domain == "swe" and t.scaffold == "swe-agent" and t.policy_model == "swe-agent-llama-70b"
    assert t.outcome is False
    assert len(t.steps) == 3
    assert t.steps[0].action == "lexicon memset create example.com TXT"
    assert t.steps[0].thought == "Let's reproduce the error first."
    assert t.steps[0].observation.startswith("Traceback")
    assert t.steps[1].thought is None
    assert t.steps[2].action == "submit"
    assert t.steps[2].extra["n_code_blocks"] == 2
    assert t.steps[2].observation == ""  # trajectory ended after the command
    assert t.meta["exit_status"] == "submitted (exit_context)"
    assert t.meta["has_patch"] is True
    assert "eval_logs" not in json.dumps(t.model_dump())


# ---------------------------------------------------------------- OpenHands

def oh_call(name, args, cid="call-1"):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def oh_msg(role, content, tool_calls=None, name=None, tool_call_id=None):
    return {"role": role, "content": content, "tool_calls": tool_calls, "name": name, "tool_call_id": tool_call_id}


OH_ROW = {
    "trajectory_id": "chatcmpl-abc",
    "instance_id": "PlasmaFAIR__sdf-xarray-24",
    "repo": "PlasmaFAIR/sdf-xarray",
    "exit_status": "submit",
    "resolved": 1,
    "gen_tests_correct": 0.0,
    "pred_passes_gen_tests": 1.0,
    "tools": [],
    "model_patch": "diff --git a/y b/y",
    "trajectory": [
        oh_msg("system", "You are OpenHands agent..."),
        oh_msg("user", "<uploaded_files>\n/workspace/x\n</uploaded_files>\n\nConsider the following issue description:\n\n<issue_description>\nForward slashes break netcdf\n</issue_description>\n\nCan you help me implement the necessary changes?"),
        oh_msg("assistant", "I'll start by thinking.", [oh_call("think", {"thought": "The slash is the problem."}, "c1")]),
        oh_msg("tool", "Your thought has been logged.", name="think", tool_call_id="c1"),
        oh_msg("assistant", "Let me explore.", [oh_call("str_replace_editor", {"command": "view", "path": "/workspace"}, "c2")]),
        oh_msg("tool", "Here's the files...", name="str_replace_editor", tool_call_id="c2"),
        oh_msg("assistant", "", [oh_call("execute_bash", {"command": "cd /workspace && pytest -q"}, "c3")]),
        oh_msg("tool", "3 passed", name="execute_bash", tool_call_id="c3"),
        oh_msg("assistant", "All good.", [oh_call("finish", {"message": "## Summary\nI fixed it and all tests pass."}, "c4")]),
    ],
}


def test_render_action():
    assert render_action("execute_bash", {"command": "ls -la"}) == "ls -la"
    assert render_action("str_replace_editor", {"command": "view", "path": "/w"}) == "str_replace_editor view /w"
    assert render_action("str_replace_editor", {"command": "str_replace", "path": "/w/a.py", "old_str": "a", "new_str": "b"}).startswith("str_replace_editor str_replace /w/a.py")
    assert render_action("think", {"thought": "hmm"}) == "think"
    assert render_action("finish", {"message": "done"}) == "finish"


def test_openhands_row_converts():
    t = oh_convert(OH_ROW, source="nebius/SWE-rebench-openhands-trajectories")
    assert t.traj_id == "nebius-openhands/chatcmpl-abc"
    assert t.task_id == "PlasmaFAIR__sdf-xarray-24"
    assert t.repo_or_site == "plasmafair/sdf-xarray"
    assert t.scaffold == "openhands-0.54" and t.policy_model == "Qwen3-Coder-480B-A35B-Instruct"
    assert t.task == "Forward slashes break netcdf"
    assert t.outcome is True
    assert [s.action for s in t.steps] == ["think", "str_replace_editor view /workspace", "cd /workspace && pytest -q", "finish"]
    assert t.steps[0].thought == "I'll start by thinking.\n\nThe slash is the problem."
    assert t.steps[0].observation == "Your thought has been logged."
    assert t.steps[2].thought is None
    assert t.steps[3].thought == "All good.\n\n## Summary\nI fixed it and all tests pass."
    assert t.steps[3].observation == ""
    assert t.steps[1].extra == {"tool": "str_replace_editor", "args": {"command": "view", "path": "/workspace"}}
    assert t.meta["gen_tests_correct"] == 0.0 and t.meta["pred_passes_gen_tests"] == 1.0
    # thoughts-removed view carries no confident summary text
    v = t.without_thoughts()
    assert "all tests pass" not in json.dumps(v.model_dump()).lower()


def test_openhands_unresolved_minus_one_is_rejected():
    row = dict(OH_ROW, resolved=-1)
    with pytest.raises(ValueError):
        oh_convert(row, source="x")


# ---------------------------------------------------------------- real shards (optional)

@pytest.mark.skipif(not (RAW / "swe_agent_rg0.parquet").exists(), reason="raw sample not present")
def test_real_swe_agent_shard_converts():
    import pyarrow.parquet as pq

    rows = pq.read_table(RAW / "swe_agent_rg0.parquet").slice(0, 50).to_pylist()
    trajs = [sa_convert(r, row_index=i, source="nebius/SWE-agent-trajectories", shard="rg0") for i, r in enumerate(rows)]
    assert len(trajs) == 50
    assert all(len(t.steps) >= 1 for t in trajs)
    assert all(t.steps[-1].action for t in trajs)
    assert all("INSTRUCTIONS" not in t.task for t in trajs)


@pytest.mark.skipif(not (RAW / "openhands_rg0.parquet").exists(), reason="raw sample not present")
def test_real_openhands_shard_converts():
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(RAW / "openhands_rg0.parquet")
    rows = pf.read_row_group(0).slice(0, 50).to_pylist()
    trajs = [oh_convert(r, source="nebius/SWE-rebench-openhands-trajectories") for r in rows]
    assert len(trajs) == 50
    assert all(len(t.steps) >= 1 for t in trajs)
    assert all("<issue_description>" not in t.task for t in trajs)
