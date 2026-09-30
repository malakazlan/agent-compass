from pathlib import Path

import pytest

from agent_compass.data.schema import Step, Trajectory
from agent_compass.data.state import (
    StateConfig,
    build_state,
    chars_per_token_counter,
    clean_observation,
    hf_tokenizer_counter,
    summarize_observation,
    truncate_middle,
)

TOK = Path(__file__).resolve().parents[1] / ".scratch" / "tok" / "qwen3.5-2b" / "tokenizer.json"
count = chars_per_token_counter()


def traj(n_steps=12, thought="I think so.", obs_lines=5):
    steps = [
        Step(
            action=f"cmd_{i} --flag",
            observation="\n".join(f"line {i}.{j} output" for j in range(obs_lines)) + "\n(Open file: n/a)\n(Current directory: /repo)\nbash-$",
            thought=thought,
        )
        for i in range(n_steps)
    ]
    return Trajectory(
        traj_id="t/1", task_id="o__r-1", domain="swe", scaffold="swe-agent", policy_model="llama-70b",
        repo_or_site="o/r", task="Fix the crash when saving.", steps=steps, outcome=True, meta={},
    )


def test_clean_observation_strips_footers_ansi_and_leaks():
    raw = "\x1b[31mERROR\x1b[0m boom\n\n\n\n(Open file: /x.py)\n(Current directory: /r)\nbash-$\nInstance resolved: True\n=========="
    assert clean_observation(raw) == "ERROR boom"


def test_summarize_prefers_test_summary_then_error_then_first_line():
    assert summarize_observation("collected 3 items\n....\n2 passed, 1 failed in 0.3s") == "2 passed, 1 failed"
    assert summarize_observation("some text\nTraceback (most recent call last):\n  File x\nValueError: bad") == "Traceback (most recent call last):"
    assert summarize_observation("Directory src not found\n(Open file: n/a)") == "Directory src not found"
    assert summarize_observation("") == "(no output)"
    assert len(summarize_observation("x" * 500, max_chars=50)) == 50


def test_truncate_middle_keeps_head_and_tail():
    text = "\n".join(f"L{i}" for i in range(100))
    out = truncate_middle(text, count, max_tokens=40)
    assert out.startswith("L0\n") and out.endswith("L99")
    assert "lines omitted" in out
    assert count(out) <= 48  # marker line adds a little


def test_default_view_has_no_thoughts_and_has_tags():
    s = build_state(traj(), prefix_len=12, count=count)
    assert s.startswith("<task>\nFix the crash when saving.\n</task>\n<policy>llama-70b</policy>\n<hist>")
    assert "I think so." not in s
    assert "## step 12" in s and "## step 5" in s and "## step 4" not in s  # 8 recent steps
    assert "4. $ cmd_3 --flag -> line 3.0 output" in s  # older steps as one-liners
    assert "(Open file" not in s and "bash-$" not in s


def test_thoughts_view_only_in_recent_steps():
    s = build_state(traj(), prefix_len=12, cfg=StateConfig(thoughts=True), count=count)
    assert s.count("> I think so.") == 8


def test_policy_override_and_off():
    s = build_state(traj(), prefix_len=3, count=count, policy_override="unknown")
    assert "<policy>unknown</policy>" in s
    s = build_state(traj(), prefix_len=3, cfg=StateConfig(policy=False), count=count)
    assert "<policy>" not in s


def test_prefix_len_respected():
    s = build_state(traj(), prefix_len=1, count=count)
    assert "## step 1" in s and "## step 2" not in s and "<hist>" not in s
    with pytest.raises(ValueError):
        build_state(traj(), prefix_len=0, count=count)
    with pytest.raises(ValueError):
        build_state(traj(), prefix_len=13, count=count)


def test_budget_is_enforced_by_dropping_history_first():
    t = traj(n_steps=60, obs_lines=30)
    full = build_state(t, prefix_len=60, cfg=StateConfig(budget=100000), count=count)
    assert "1. $ cmd_0" in full
    tight = build_state(t, prefix_len=60, cfg=StateConfig(budget=1200), count=count)
    assert count(tight) <= 1200
    assert "## step 60" in tight  # the latest step always survives
    assert "earlier steps omitted" in tight
    assert "Fix the crash" in tight


def test_budget_extreme_still_keeps_task_and_last_step():
    t = traj(n_steps=20, obs_lines=200)
    s = build_state(t, prefix_len=20, cfg=StateConfig(budget=200), count=count)
    assert count(s) <= 200
    assert "## step 20" in s and "<task>" in s


@pytest.mark.skipif(not TOK.exists(), reason="tokenizer file not present")
def test_real_tokenizer_budget():
    real = hf_tokenizer_counter(TOK)
    t = traj(n_steps=40, obs_lines=60)
    s = build_state(t, prefix_len=40, cfg=StateConfig(budget=2048), count=real)
    assert real(s) <= 2048


def test_collapse_padding_shrinks_pytest_banners_and_column_padding():
    from agent_compass.data.state import collapse_padding

    banner = "=" * 400 + " 2 passed in 0.5s " + "=" * 400
    padded = "test_x.py::test_a PASSED" + " " * 900 + "[ 50%]"
    code = "    def f():\n        return 1"  # leading indentation must survive
    out = collapse_padding("\n".join([banner, padded, code]))
    lines = out.splitlines()
    assert lines[0] == "=" * 8 + " 2 passed in 0.5s " + "=" * 8
    assert lines[1] == "test_x.py::test_a PASSED  [ 50%]"
    assert lines[2:] == ["    def f():", "        return 1"]
    assert summarize_observation(banner) == "2 passed"


def test_think_steps_are_hidden_without_thoughts_and_kept_with_thoughts():
    steps = [
        Step(action="ls", observation="a.py"),
        Step(action="think", observation="Your thought has been logged.", thought="Let me plan."),
        Step(action="open a.py", observation="[File: a.py]"),
    ]
    t = Trajectory(traj_id="t", task_id="o__r-1", domain="swe", scaffold="openhands-0.54", policy_model="q",
                   repo_or_site="o/r", task="fix", steps=steps, outcome=True, meta={})
    off = build_state(t, 3, count=count)
    assert "$ think" not in off and "thought has been logged" not in off
    assert "## step 1" in off and "## step 3" in off  # original numbering kept
    on = build_state(t, 3, cfg=StateConfig(thoughts=True), count=count)
    assert "$ think" in on and "> Let me plan." in on
    # a prefix that is only a think step still renders something
    only = build_state(t.model_copy(update={"steps": steps[1:2]}), 1, count=count)
    assert "## step 1" in only
