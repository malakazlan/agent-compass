"""Build the state text the model sees from a trajectory prefix.

Layout (all tags are plain text, no special tokens):

    <task>
    ...issue text...
    </task>
    <policy>swe-agent-llama-70b</policy>          # optional, 30% "unknown" dropout at train time
    <hist>
    1. $ find_file "memset.py" src -> Directory src not found
    2. $ open lexicon/providers/memset.py -> [File: ... (149 lines total)]
    </hist>
    <recent>
    ## step 3
    $ edit 141:143 ...
    Your proposed edit has introduced new syntax error(s)...
    </recent>

Older steps are compressed to one line each (action + first informative line of the
observation). The last `recent_steps` are kept in full with head/tail truncated
observations. Everything is fitted into `budget` tokens; when the budget is tight, the
history is dropped from the oldest step first and the task is truncated last.

Thoughts are excluded by default (the anti-shortcut view). With `thoughts=True` they
appear as `> thought` lines inside recent steps only.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent_compass.data.schema import Step, Trajectory

Tokenizer = Callable[[str], int]  # text -> token count


def chars_per_token_counter(chars_per_token: float = 3.5) -> Tokenizer:
    """Fail-Fast's proxy (3.5 chars/token). Used in tests and when no tokenizer file is given."""
    return lambda s: int(len(s) / chars_per_token + 0.999)


def hf_tokenizer_counter(tokenizer_json: str | Path) -> Tokenizer:
    from tokenizers import Tokenizer as _Tok

    tok = _Tok.from_file(str(tokenizer_json))
    return lambda s: len(tok.encode(s, add_special_tokens=False).ids)


# Lines that never help and cost tokens: SWE-agent shell footers, ANSI codes, long rules.
_FOOTER = re.compile(r"^\((?:Open file|Current directory): [^)]*\)\s*$|^bash-\$\s*$", re.M)
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RULE = re.compile(r"^[=\-_*#]{6,}\s*$", re.M)
# Harness-side result lines. None were found inside the audited datasets' message lists,
# but the filter is cheap insurance for sources that inline the evaluator's verdict.
_LEAK = re.compile(
    r"^.*\b(?:instance|task)\s+(?:resolved|unresolved)\b.*$|^\s*(?:RESOLVED|UNRESOLVED|FAIL_TO_PASS|PASS_TO_PASS)\b.*$",
    re.M | re.I,
)
_TEST_SUMMARY = re.compile(r"\b(\d+ (?:passed|failed|error|errors|skipped|xfailed|warnings?))\b(?:, |\s)?", re.I)
_ERROR_LINE = re.compile(r"^.*\b(?:Error|Exception|Traceback|FAILED|fatal:|not found|No such file|SyntaxError)\b.*$", re.M)


def clean_observation(text: str) -> str:
    text = _ANSI.sub("", text)
    text = _LEAK.sub("", text)
    text = _FOOTER.sub("", text)
    text = _RULE.sub("", text)
    lines = [ln.rstrip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:  # collapse blank runs
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def summarize_observation(text: str, max_chars: int = 100) -> str:
    """The one line most likely to tell you what happened: a test summary, else the first error line, else the first line."""
    text = clean_observation(text)
    if not text:
        return "(no output)"
    m = _TEST_SUMMARY.findall(text)
    if m:
        return ", ".join(dict.fromkeys(m))[:max_chars]
    e = _ERROR_LINE.search(text)
    line = (e.group(0) if e else text.splitlines()[0]).strip()
    return line if len(line) <= max_chars else line[: max_chars - 3] + "..."


def one_line(action: str, max_chars: int = 80) -> str:
    a = " ".join(action.split())
    return a if len(a) <= max_chars else a[: max_chars - 3] + "..."


def truncate_middle(text: str, count: Tokenizer, max_tokens: int, head_frac: float = 0.6) -> str:
    """Keep the head and the tail of a long observation; the middle is where the noise is."""
    if count(text) <= max_tokens:
        return text
    lines = text.splitlines()
    head_budget = int(max_tokens * head_frac)
    tail_budget = max_tokens - head_budget
    head: list[str] = []
    used = 0
    for ln in lines:
        c = count(ln + "\n")
        if used + c > head_budget:
            break
        head.append(ln)
        used += c
    tail: list[str] = []
    used = 0
    for ln in reversed(lines[len(head):]):
        c = count(ln + "\n")
        if used + c > tail_budget:
            break
        tail.append(ln)
        used += c
    omitted = len(lines) - len(head) - len(tail)
    if omitted <= 0:
        return text
    return "\n".join(head + [f"... [{omitted} lines omitted] ..."] + list(reversed(tail)))


@dataclass(frozen=True)
class StateConfig:
    budget: int = 4096  # total tokens for the state text
    recent_steps: int = 8  # steps rendered in full
    obs_max_tokens: int = 300  # per recent observation, head/tail truncated
    action_max_tokens: int = 200  # per recent action (edits can be long)
    task_max_tokens: int = 1024
    hist_line_chars: int = 100
    thoughts: bool = False
    thought_max_tokens: int = 120
    policy: bool = True
    policy_unknown_token: str = "unknown"
    tags: dict[str, str] = field(default_factory=lambda: {"task": "task", "policy": "policy", "hist": "hist", "recent": "recent"})


def _cap(text: str, count: Tokenizer, max_tokens: int) -> str:
    if count(text) <= max_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:  # binary search on characters
        mid = (lo + hi + 1) // 2
        if count(text[:mid] + "...") <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "..."


def render_recent_step(idx: int, s: Step, cfg: StateConfig, count: Tokenizer) -> str:
    parts = [f"## step {idx}"]
    if cfg.thoughts and s.thought:
        parts.append("> " + _cap(" ".join(s.thought.split()), count, cfg.thought_max_tokens))
    parts.append("$ " + _cap(s.action, count, cfg.action_max_tokens))
    obs = clean_observation(s.observation)
    parts.append(truncate_middle(obs, count, cfg.obs_max_tokens) if obs else "(no output)")
    return "\n".join(parts)


def render_hist_line(idx: int, s: Step, cfg: StateConfig) -> str:
    return f"{idx}. $ {one_line(s.action)} -> {summarize_observation(s.observation, cfg.hist_line_chars)}"


def build_state(
    traj: Trajectory,
    prefix_len: int,
    cfg: StateConfig = StateConfig(),
    count: Tokenizer | None = None,
    policy_override: str | None = None,
) -> str:
    """State text for the first `prefix_len` steps of `traj` (1 <= prefix_len <= len(steps))."""
    if not 1 <= prefix_len <= len(traj.steps):
        raise ValueError(f"prefix_len must be in [1, {len(traj.steps)}], got {prefix_len}")
    count = count or chars_per_token_counter()
    t = cfg.tags
    steps = traj.steps[:prefix_len]

    task = _cap(traj.task.strip(), count, cfg.task_max_tokens)
    task_block = f"<{t['task']}>\n{task}\n</{t['task']}>"
    policy_block = ""
    if cfg.policy:
        name = policy_override if policy_override is not None else (traj.policy_model or cfg.policy_unknown_token)
        policy_block = f"<{t['policy']}>{name}</{t['policy']}>"

    n_recent = min(cfg.recent_steps, len(steps))
    recent_idx = list(range(len(steps) - n_recent, len(steps)))
    hist_idx = list(range(0, len(steps) - n_recent))

    recent_blocks = [render_recent_step(i + 1, steps[i], cfg, count) for i in recent_idx]
    hist_lines = [render_hist_line(i + 1, steps[i], cfg) for i in hist_idx]

    def assemble(hist: list[str], recent: list[str], omitted: int) -> str:
        blocks = [task_block]
        if policy_block:
            blocks.append(policy_block)
        if hist or omitted:
            body = ([f"... {omitted} earlier steps omitted ..."] if omitted else []) + hist
            blocks.append(f"<{t['hist']}>\n" + "\n".join(body) + f"\n</{t['hist']}>")
        blocks.append(f"<{t['recent']}>\n" + "\n\n".join(recent) + f"\n</{t['recent']}>")
        return "\n".join(blocks)

    omitted = 0
    text = assemble(hist_lines, recent_blocks, omitted)
    # 1) drop history from the oldest line
    while count(text) > cfg.budget and hist_lines:
        hist_lines.pop(0)
        omitted += 1
        text = assemble(hist_lines, recent_blocks, omitted)
    # 2) demote the oldest recent steps to history lines
    while count(text) > cfg.budget and len(recent_blocks) > 1:
        i = recent_idx.pop(0)
        recent_blocks.pop(0)
        hist_lines.append(render_hist_line(i + 1, steps[i], cfg))
        text = assemble(hist_lines, recent_blocks, omitted)
        while count(text) > cfg.budget and hist_lines:
            hist_lines.pop(0)
            omitted += 1
            text = assemble(hist_lines, recent_blocks, omitted)
    # 3) shrink the last observation, then the task
    if count(text) > cfg.budget:
        last = steps[recent_idx[-1]]
        for obs_tokens in (150, 80, 40):
            small = StateConfig(**{**cfg.__dict__, "obs_max_tokens": obs_tokens})
            recent_blocks[-1] = render_recent_step(recent_idx[-1] + 1, last, small, count)
            text = assemble(hist_lines, recent_blocks, omitted)
            if count(text) <= cfg.budget:
                break
    if count(text) > cfg.budget:
        room = cfg.budget - count(text.replace(task, ""))
        task_block = f"<{t['task']}>\n{_cap(traj.task.strip(), count, max(32, room))}\n</{t['task']}>"
        text = assemble(hist_lines, recent_blocks, omitted)
    return text
