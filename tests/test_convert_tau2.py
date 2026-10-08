import json
from pathlib import Path

import pytest

from agent_compass.data.convert.tau2_bench import convert_row, render_call
from agent_compass.data.schema import repo_key
from agent_compass.data.splits import split_for

RAW = Path(__file__).resolve().parents[1] / "data" / "raw" / "tau2"

ROW = {
    "model_path": "moonshotai/Kimi-K2-Instruct",
    "user_model_path": "openai/gpt-4o",
    "benchmark_name": "tau2-bench",
    "task_name": "airline",
    "messages": [
        {"role": "system", "content": "# Airline Agent Policy\n...long policy...", "turn_idx": 0},
        {"role": "assistant", "content": "Hi! How can I help you today?", "turn_idx": 1},
        {"role": "user", "content": "How many suitcases can I take? My id is anya_5901, confirmation JMO1MG.", "turn_idx": 2},
        {"role": "assistant", "content": "None", "tool_calls": [{"id": "c1", "name": "get_reservation_details", "arguments": {"reservation_id": "JMO1MG"}}], "turn_idx": 3},
        {"role": "tool", "content": "{\"reservation_id\": \"JMO1MG\", \"cabin\": \"economy\"}", "turn_idx": 4},
        {"role": "assistant", "content": "Let me also check your profile.", "tool_calls": [{"id": "c2", "name": "get_user_details", "arguments": "{\"user_id\": \"anya_5901\"}"}], "turn_idx": 5},
        {"role": "tool", "content": "{\"membership\": \"gold\"}", "turn_idx": 6},
        {"role": "user", "content": "Thanks!", "turn_idx": 7},
        {"role": "assistant", "content": "You can bring 3 free checked bags.", "turn_idx": 8},
        {"role": "user", "content": "###STOP###", "turn_idx": 9},
    ],
    "eval_result": {"score": 1.0, "db_match": True},
    "meta": {"id": "airline_3", "is_correct": True, "finish_reason": "user_stop", "duration_seconds": 42.0},
}


def test_render_call_handles_dict_and_string_args():
    assert render_call({"name": "f", "arguments": {"b": 1, "a": 2}}) == 'f({"a": 2, "b": 1})'
    assert render_call({"name": "g", "arguments": "{\"x\": 1}"}) == 'g({"x": 1})'


def test_tau2_row_converts():
    t = convert_row(ROW)
    assert t.traj_id == "tau2/Kimi-K2-Instruct/airline/airline_3" and t.task_id == "tau2/airline/airline_3"
    assert t.domain == "tool" and t.scaffold == "tau2-bench" and t.policy_model == "Kimi-K2-Instruct"
    assert t.task.startswith("[airline customer-service task] How many suitcases")
    assert t.outcome is True
    assert [s.action for s in t.steps] == [
        "say: Hi! How can I help you today?",
        'get_reservation_details({"reservation_id": "JMO1MG"})',
        'get_user_details({"user_id": "anya_5901"})',
        "say: You can bring 3 free checked bags.",
    ]
    assert t.steps[0].observation.startswith("user: How many suitcases")
    assert t.steps[1].observation == 'tool: {"reservation_id": "JMO1MG", "cabin": "economy"}'
    assert t.steps[2].observation == 'tool: {"membership": "gold"}\nuser: Thanks!'
    assert t.steps[2].thought == "Let me also check your profile."  # text next to a tool call -> thought
    assert t.steps[1].thought is None  # "None" content is dropped
    assert t.steps[3].observation == "user: ###STOP###"
    assert t.meta["exit_status"] == "user_stop" and t.meta["db_match"] is True and t.meta["domain_name"] == "airline"
    # split works on the synthetic repo key: whole tasks are held out together
    assert repo_key(t.repo_or_site) == "tau2/airline-airline_3"
    assert split_for(t) in {"train", "dev", "test"}
    assert "###STOP###" not in t.task and "Airline Agent Policy" not in t.task


def test_failed_run_outcome_and_missing_score():
    row = dict(ROW, eval_result={"score": 0.0, "db_match": False})
    assert convert_row(row).outcome is False
    row2 = dict(ROW, eval_result=None, meta={**ROW["meta"], "is_correct": False})
    assert convert_row(row2).outcome is False


@pytest.mark.skipif(not (RAW / "Kimi-K2-Instruct.jsonl").exists(), reason="raw tau2 file not present")
def test_real_tau2_file_converts():
    n = 0
    with (RAW / "Kimi-K2-Instruct.jsonl").open(encoding="utf-8") as f:
        for line in f:
            t = convert_row(json.loads(line))
            assert t.steps and t.task
            n += 1
            if n >= 50:
                break
    assert n == 50
