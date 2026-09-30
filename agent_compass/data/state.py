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
    """Token counter backed by a HF `tokenizer.json`. The returned callable also has a
    `.many(list[str]) -> list[int]` method that encodes in one batched Rust call."""
    from tokenizers import Tokenizer as _Tok

    tok = _Tok.from_file(str(tokenizer_json))

    def count(s: str) -> int:
        return len(tok.encode(s, add_special_tokens=False).ids)

    def many(texts: list[str]) -> list[int]:
        return [len(e.ids) for e in tok.encode_batch(texts, add_special_tokens=False)] if texts else []

    count.many = many  # type: ignore[attr-defined]
    return count


def count_many(count: Tokenizer, texts: list[str]) -> list[int]:
    many = getattr(count, "many", None)
    return many(texts) if many else [count(x) for x in texts]


def preslice(text: str, keep_chars: int) -> str:
    """Keep the first and last `keep_chars` characters of a very long text. Everything we
    ever render or summarise lives at the ends, and regexes over 50k-char observations
    were the second largest cost in the pipeline."""
    if len(text) <= 2 * keep_chars + 64:
        return text
    omitted = text[keep_chars:-keep_chars].count("\n")
    return text[:keep_chars] + f"\n... [{omitted} lines omitted] ...\n" + text[-keep_chars:]


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


_REPEAT = re.compile(r"([=\-_*#~.])\1{7,}")  # pytest banners padded to the terminal width (seen: 1,000 chars)
_INNER_SPACES = re.compile(r"(?<=\S) {3,}(?=\S)")  # column padding inside a line; leading indentation is untouched


def collapse_padding(text: str) -> str:
    """Cheap, applied to the raw observation before any slicing: a 1,000-char pytest banner
    becomes 8 chars, a test line padded with 900 spaces before `[ 50%]` keeps two."""
    text = _REPEAT.sub(lambda m: m.group(1) * 8, text)
    return _INNER_SPACES.sub("  ", text)


def clean_observation(text: str) -> str:
    text = collapse_padding(text)
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
    text = clean_observation(preslice(collapse_padding(text), 4000))
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
    text = preslice(text, max_tokens * 6)  # nothing further from the ends can survive anyway
    if count(text) <= max_tokens:
        return text
    lines = text.splitlines()
    line_tok = count_many(count, [ln + "\n" for ln in lines])  # one batched call
    head_budget = int(max_tokens * head_frac)
    tail_budget = max_tokens - head_budget
    head: list[str] = []
    used = 0
    for ln, c in zip(lines, line_tok):
        if used + c > head_budget:
            break
        head.append(ln)
        used += c
    tail: list[str] = []
    used = 0
    for ln, c in zip(reversed(lines[len(head):]), reversed(line_tok[len(head):])):
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
    if len(text) > max_tokens * 8:  # cannot fit; skip tokenizing the whole thing
        text = text[: max_tokens * 8]
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
    obs = clean_observation(preslice(collapse_padding(s.observation), cfg.obs_max_tokens * 6))
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
    """State text for the first `prefix_len` steps of `traj` (1 <= prefix_len <= len(steps)).

    Fitting works on per-block token counts (additive, computed once) and only tokenizes
    the assembled text at the end for a final check, so cost is linear in the number of
    blocks rather than quadratic.
    """
    if not 1 <= prefix_len <= len(traj.steps):
        raise ValueError(f"prefix_len must be in [1, {len(traj.steps)}], got {prefix_len}")
    count = count or chars_per_token_counter()
    t = cfg.tags
    steps = traj.steps[:prefix_len]
    nl = "\n"

    task_text = _cap(traj.task.strip(), count, cfg.task_max_tokens)
    policy_name = None
    if cfg.policy:
        policy_name = policy_override if policy_override is not None else (traj.policy_model or cfg.policy_unknown_token)

    def task_block(text: str) -> str:
        return f"<{t['task']}>{nl}{text}{nl}</{t['task']}>"

    def omitted_line(k: int) -> str:
        return f"... {k} earlier steps omitted ..."

    policy_block = f"<{t['policy']}>{policy_name}</{t['policy']}>" if policy_name is not None else ""

    # With thoughts removed, a `think` step is content-free ("$ think" / "Your thought has been
    # logged."), so it is not rendered; step numbers stay those of the original trajectory.
    visible = [i for i, s in enumerate(steps) if cfg.thoughts or s.action.strip() != "think"] or [len(steps) - 1]
    n_recent = min(cfg.recent_steps, len(visible))
    recent_idx = visible[len(visible) - n_recent :]
    hist_lines = [render_hist_line(i + 1, steps[i], cfg) for i in visible[: len(visible) - n_recent]]
    recent_blocks = [render_recent_step(i + 1, steps[i], cfg, count) for i in recent_idx]
    hist_tok = [count(x) + 1 for x in hist_lines]
    recent_tok = [count(x) + 2 for x in recent_blocks]
    fixed = count(task_block(task_text)) + (count(policy_block) + 1 if policy_block else 0) + 8  # tag lines
    omitted = 0

    def total() -> int:
        extra = (count(omitted_line(omitted)) + 1 if omitted else 0) + (4 if hist_lines or omitted else 0)
        return fixed + sum(hist_tok) + sum(recent_tok) + extra

    def drop_oldest_hist() -> None:
        nonlocal omitted
        hist_lines.pop(0)
        hist_tok.pop(0)
        omitted += 1

    def demote_oldest_recent() -> None:
        i = recent_idx.pop(0)
        recent_blocks.pop(0)
        recent_tok.pop(0)
        line = render_hist_line(i + 1, steps[i], cfg)
        hist_lines.append(line)
        hist_tok.append(count(line) + 1)

    # 1) drop history from the oldest line
    while total() > cfg.budget and hist_lines:
        drop_oldest_hist()
    # 2) demote the oldest recent steps to history lines
    while total() > cfg.budget and len(recent_blocks) > 1:
        demote_oldest_recent()
        while total() > cfg.budget and hist_lines:
            drop_oldest_hist()
    # 3) shrink the last observation
    if total() > cfg.budget:
        last = steps[recent_idx[-1]]
        for obs_tokens in (150, 80, 40):
            small = StateConfig(**{**cfg.__dict__, "obs_max_tokens": obs_tokens, "action_max_tokens": min(cfg.action_max_tokens, obs_tokens)})
            recent_blocks[-1] = render_recent_step(recent_idx[-1] + 1, last, small, count)
            recent_tok[-1] = count(recent_blocks[-1]) + 2
            if total() <= cfg.budget:
                break
    # 4) shrink the task
    if total() > cfg.budget:
        room = cfg.budget - (total() - count(task_block(task_text)))
        task_text = _cap(traj.task.strip(), count, max(16, room - 8))
        fixed = count(task_block(task_text)) + (count(policy_block) + 1 if policy_block else 0) + 8

    def assemble() -> str:
        blocks = [task_block(task_text)]
        if policy_block:
            blocks.append(policy_block)
        if hist_lines or omitted:
            body = ([omitted_line(omitted)] if omitted else []) + hist_lines
            blocks.append(f"<{t['hist']}>{nl}" + nl.join(body) + f"{nl}</{t['hist']}>")
        blocks.append(f"<{t['recent']}>{nl}" + (nl + nl).join(recent_blocks) + f"{nl}</{t['recent']}>")
        return nl.join(blocks)

    # Final check. Per-block sums can be off by a few tokens at joins, so when the estimate
    # is within `SAFETY` of the budget we tokenize the whole text once and adjust; when it is
    # comfortably below we trust the estimate (a full 4k-token encode costs ~10 ms).
    text = assemble()
    est = total()
    if est <= cfg.budget - SAFETY:
        _LAST_COUNT[0] = est
        return text
    n = count(text)
    while n > cfg.budget and (hist_lines or len(recent_blocks) > 1):
        if hist_lines:
            drop_oldest_hist()
        else:
            demote_oldest_recent()
        text = assemble()
        n = count(text)
    if n > cfg.budget:
        task_text = _cap(task_text, count, max(8, count(task_text) - (n - cfg.budget) - 4))
        text = assemble()
        n = count(text)
    _LAST_COUNT[0] = n
    return text


SAFETY = 64
_LAST_COUNT = [0]


def build_state_with_count(*args, **kwargs) -> tuple[str, int]:
    """`build_state` plus the token count it established (exact when near the budget, else the additive estimate)."""
    text = build_state(*args, **kwargs)
    return text, _LAST_COUNT[0]
