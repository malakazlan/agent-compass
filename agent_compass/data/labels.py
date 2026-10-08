"""Labels for one prefix of one trajectory.

Every label is either a value or None (masked). Rule-based labels return the rule that
fired so the QA report can show why. Definitions are documented here because they are
part of the benchmark, not an implementation detail.

p_success   final outcome of the run (Monte-Carlo target). Also `baseline` (pass rate of
            all runs of the task) and `advantage = outcome - baseline`.
steps_left  bins of remaining steps for successful runs: 0: 1-5, 1: 6-15, 2: 16-40, 3: 40+.
            Failed runs are masked.
stuck       True if, inside the last `window` steps, the agent repeated the same action
            >= `repeat` times, hit the same error summary >= `repeat` times, or cycled
            edits on the same location >= `repeat` times.
progress    for the last step of the prefix, 0 regressed / 1 no change / 2 small / 3 big:
            3  tests newly pass (failed count drops to 0, or passed count rises with no failures),
               or the run submits/finishes right after a passing test run
            2  a successful edit, a new file or location explored, or a passing self-check
            0  a test run with more failures than before, an edit that errored, or an
               action that produced the same error as the previous step
            1  otherwise (repeated action, reading the same file again, no new signal)
escalate    True if the run fails AND the task is clearly hard for this policy class: the
            leave-one-out task baseline (pass rate of the OTHER runs of the task) is at most
            `escalate_ratio` times the policy's overall success rate. A task the policy
            usually solves is not an escalation case even when this run fails; a task it
            almost never solves is. Masked when the task has fewer than `min_runs` other runs.
            Baselines everywhere are leave-one-out so a run's own outcome never leaks into
            its baseline or advantage.
best_next   built in `candidates.py` from other runs of the same task.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field

from agent_compass.data.schema import Step, Trajectory
from agent_compass.data.state import summarize_observation

STEPS_LEFT_BINS = ((1, 5), (6, 15), (16, 40), (41, 10**9))

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\b\d+\b")
_TEST_PASS = re.compile(r"\b(\d+) passed\b")
_TEST_FAIL = re.compile(r"\b(\d+) (?:failed|errors?)\b")
_ERRORISH = re.compile(r"\b(?:Traceback|Error|Exception|FAILED|fatal:|not found|No such file|SyntaxError|syntax error)\b", re.I)
_EDIT_OK = re.compile(r"File updated|has been edited|Your changes have been saved|successfully|The file .* has been created", re.I)
_EDIT_BAD = re.compile(r"Your proposed edit has introduced|did not appear verbatim|No replacement was performed|Invalid `path`|is not a valid|does not exist", re.I)
_EXPLORE = re.compile(r"^(?:open|cat|less|head|tail|view|ls|find|find_file|search_dir|search_file|grep|rg|str_replace_editor view|tree)\b")
# Reading and moving around is never "stuck" by itself, however often it repeats.
_NAVIGATION = re.compile(r"^(?:scroll_down|scroll_up|goto|open|ls|cd|pwd|cat|less|head|tail|find|find_file|search_dir|search_file|grep|rg|tree|str_replace_editor view|git (?:status|diff|log))\b")
_EDIT = re.compile(r"^(?:edit\b|str_replace_editor (?:str_replace|create|insert)\b|sed -i\b|cat >\s*|echo .* >\s*)")
_FINISH = {"submit", "finish"}
_PATH = re.compile(r"(?:/[\w.\-]+)+|[\w.\-]+\.(?:py|js|ts|rs|go|java|c|cpp|h|md|txt|toml|cfg|ini|yaml|yml|json)\b")


def normalize_action(action: str) -> str:
    return _WS.sub(" ", action.strip())


_EDIT_RANGE = re.compile(r"^edit (\d+):(\d+)")


def action_family(action: str) -> str:
    """Command word plus paths (numbers removed) plus, for SWE-agent `edit A:B`, the line
    range bucketed to tens: `edit 141:143` and `edit 140:142` share a family, `edit 20:22`
    does not."""
    a = normalize_action(action)
    head = a.split(" ", 1)[0]
    if head == "str_replace_editor" and " " in a:
        head = " ".join(a.split(" ", 2)[:2])
    loc = ""
    m = _EDIT_RANGE.match(a)
    if m:
        loc = f"L{int(m.group(1)) // 10}"
    paths = " ".join(sorted(set(_PATH.findall(a))))
    return (_NUM.sub("#", f"{head} {paths}".strip()) + (f" {loc}" if loc else "")).strip()


_UNITTEST_RAN = re.compile(r"^Ran (\d+) tests? in", re.M)
_UNITTEST_FAILED = re.compile(r"^FAILED \((?:failures=(\d+))?(?:, )?(?:errors=(\d+))?", re.M)
_UNITTEST_OK = re.compile(r"^OK\b", re.M)


def parse_test_counts(observation: str) -> tuple[int | None, int | None]:
    """(passed, failed) from a pytest summary ("3 passed, 2 failed") or a unittest footer
    ("Ran 4 tests ... OK" / "FAILED (failures=1, errors=1)"); (None, None) when neither is present."""
    p = _TEST_PASS.findall(observation)
    f = _TEST_FAIL.findall(observation)
    if p or f:
        return (int(p[-1]) if p else 0), (int(f[-1]) if f else 0)
    ran = _UNITTEST_RAN.findall(observation)
    if ran:
        n = int(ran[-1])
        m = _UNITTEST_FAILED.search(observation)
        if m:
            bad = int(m.group(1) or 0) + int(m.group(2) or 0)
            return max(0, n - bad), bad
        if _UNITTEST_OK.search(observation):
            return n, 0
    return None, None


_EXC_LINE = re.compile(r"^\s*(?:[\w.]+\.)?\w*(?:Error|Exception|Warning)\b.*$", re.M)


_LISTING_LINE = re.compile(r"^\s*(?:\d+[:\t|]|Line \d+:|#|\.\.\.)")  # file views, search hits, comments: never an error
_STRICT_ERR = re.compile(r"\b(?:\w*Error|\w*Exception)\b\s*:|^\s*Traceback \(most recent|No such file or directory|not found\b|FAILED\b|fatal:", re.M)


def error_signature(observation: str) -> str | None:
    """A numbers-free line identifying the error the agent hit, or None.

    Lines that are part of a file listing or search result (`12:...`, `Line 12:`, `#` comments)
    are ignored, otherwise `except Exception as e:` inside displayed code would count as an
    error (seen in label QA). Preference: the last `SomeError: ...` line, else the first line
    with a strict error marker (`Error:`, `Traceback`, `No such file`, `FAILED`)."""
    lines = [ln for ln in observation.splitlines() if ln.strip() and not _LISTING_LINE.match(ln)]
    exc = [ln for ln in lines if _EXC_LINE.match(ln)]
    if exc:
        return _NUM.sub("#", exc[-1].strip())[:160]
    for ln in lines:
        if _STRICT_ERR.search(ln):
            return _NUM.sub("#", ln.strip())[:160]
    return None


# --------------------------------------------------------------------------- outcome-derived

@dataclass(frozen=True)
class TaskStats:
    n_runs: int
    n_success: int

    @property
    def baseline(self) -> float:
        return self.n_success / self.n_runs if self.n_runs else 0.0

    def loo_baseline(self, outcome: bool) -> float | None:
        """Pass rate of the other runs of the task (this run's outcome removed)."""
        n = self.n_runs - 1
        return (self.n_success - int(outcome)) / n if n > 0 else None


def task_stats(trajs: Iterable[Trajectory]) -> dict[str, TaskStats]:
    n: Counter[str] = Counter()
    s: Counter[str] = Counter()
    for t in trajs:
        n[t.task_id] += 1
        s[t.task_id] += int(t.outcome)
    return {k: TaskStats(n[k], s[k]) for k in n}


def steps_left_bin(remaining: int) -> int:
    for i, (lo, hi) in enumerate(STEPS_LEFT_BINS):
        if lo <= remaining <= hi:
            return i
    return len(STEPS_LEFT_BINS) - 1


# --------------------------------------------------------------------------- rule-based

@dataclass(frozen=True)
class RuleConfig:
    window: int = 6
    repeat: int = 3
    escalate_ratio: float = 0.5  # task baseline <= ratio * policy success rate -> hard task
    min_runs: int = 3  # other runs of the task needed before baseline-derived labels exist


@dataclass(frozen=True)
class RuleResult:
    value: int | bool | None
    reasons: tuple[str, ...] = field(default_factory=tuple)


def stuck_label(steps: list[Step], cfg: RuleConfig = RuleConfig()) -> RuleResult:
    win = steps[-cfg.window :]
    reasons: list[str] = []
    # Same action repeated. Label QA (2026-10-09) showed two kinds of false positives: re-running a
    # reproduce/test script between edits (normal debugging: the error changes each time) and
    # repeated navigation (scroll, goto, ls, search). So: navigation never counts; a repeated run
    # command counts only when at least two of its repeats produced the same error.
    acts = Counter(normalize_action(s.action) for s in win)
    top_a, n_a = acts.most_common(1)[0]
    if n_a >= cfg.repeat and top_a not in _FINISH and not _NAVIGATION.match(top_a):
        if _EDIT.match(top_a):
            reasons.append(f"same action x{n_a}: {top_a[:60]}")
        else:
            sigs = Counter(e for e in (error_signature(s.observation) for s in win if normalize_action(s.action) == top_a) if e)
            if sigs and sigs.most_common(1)[0][1] >= 2:
                reasons.append(f"same action x{n_a}: {top_a[:60]}")
    errs = Counter(e for e in (error_signature(s.observation) for s in win) if e)
    if errs:
        top_e, n_e = errs.most_common(1)[0]
        if n_e >= cfg.repeat:
            reasons.append(f"same error x{n_e}: {top_e[:60]}")
    # Edit cycle: repeated edits at the same location that did not go through. Three
    # successful edits in different places is ordinary work, not being stuck.
    edits = Counter(
        action_family(s.action)
        for s in win
        if _EDIT.match(normalize_action(s.action)) and (_EDIT_BAD.search(s.observation) or error_signature(s.observation))
    )
    if edits:
        top_f, n_f = edits.most_common(1)[0]
        if n_f >= cfg.repeat:
            reasons.append(f"edit cycle x{n_f}: {top_f[:60]}")
    return RuleResult(bool(reasons), tuple(reasons))


def progress_label(steps: list[Step]) -> RuleResult:
    """Progress of the LAST step in `steps` relative to what came before."""
    cur = steps[-1]
    prev = steps[:-1]
    a = normalize_action(cur.action)
    obs = cur.observation
    passed, failed = parse_test_counts(obs)
    prev_tests = [parse_test_counts(s.observation) for s in prev]
    prev_tests = [(p, f) for p, f in prev_tests if p is not None]
    err = error_signature(obs)

    if a in _FINISH:
        if prev and prev_tests and prev_tests[-1][1] == 0 and prev_tests[-1][0] > 0:
            return RuleResult(3, ("finish after passing tests",))
        return RuleResult(1, ("finish without a passing test run",))

    if passed is not None:
        if prev_tests:
            pp, pf = prev_tests[-1]
            if failed == 0 and pf > 0:
                return RuleResult(3, (f"failures {pf} -> 0",))
            if failed > pf:
                return RuleResult(0, (f"failures {pf} -> {failed}",))
            if failed < pf or (passed > pp and failed == 0):
                return RuleResult(2, (f"tests improved {pp}p/{pf}f -> {passed}p/{failed}f",))
            return RuleResult(1, ("tests unchanged",))
        if failed == 0 and passed > 0:
            return RuleResult(3, ("first passing test run",))
        return RuleResult(2, ("first test run, failures present",))

    if prev and normalize_action(prev[-1].action) == a:
        if err and error_signature(prev[-1].observation) == err:
            return RuleResult(0, ("repeated action, same error",))
        return RuleResult(1, ("repeated action",))

    if _EDIT.match(a):
        if _EDIT_BAD.search(obs) or (err and not _EDIT_OK.search(obs)):
            return RuleResult(0, ("edit failed",))
        return RuleResult(2, ("edit applied",))

    if _EXPLORE.match(a):
        fam = action_family(cur.action)
        seen = {action_family(s.action) for s in prev}
        if fam in seen or any(normalize_action(s.action) == a for s in prev):
            return RuleResult(1, ("re-reading a known location",))
        if err:
            return RuleResult(1, ("exploration hit an error",))
        return RuleResult(2, ("new location explored",))

    if err:
        if prev and error_signature(prev[-1].observation) == err:
            return RuleResult(0, ("same error as previous step",))
        return RuleResult(1, ("command errored",))
    return RuleResult(1, ("no signal",))


def escalate_label(outcome: bool, stats: TaskStats | None, policy_rate: float | None, cfg: RuleConfig = RuleConfig()) -> bool | None:
    """See module docstring. `policy_rate` = overall success rate of this policy model on the dataset."""
    if stats is None or policy_rate is None or stats.n_runs - 1 < cfg.min_runs:
        return None
    loo = stats.loo_baseline(outcome)
    if loo is None:
        return None
    return (not outcome) and loo <= cfg.escalate_ratio * policy_rate


# --------------------------------------------------------------------------- all together

@dataclass(frozen=True)
class PrefixLabels:
    p_success: bool
    baseline: float | None
    advantage: float | None
    steps_left: int | None
    stuck: bool
    stuck_reasons: tuple[str, ...]
    progress: int
    progress_reasons: tuple[str, ...]
    escalate: bool | None

    def as_dict(self) -> dict:
        return {
            "p_success": self.p_success,
            "baseline": self.baseline,
            "advantage": self.advantage,
            "steps_left": self.steps_left,
            "stuck": self.stuck,
            "stuck_reasons": list(self.stuck_reasons),
            "progress": self.progress,
            "progress_reasons": list(self.progress_reasons),
            "escalate": self.escalate,
        }


def label_prefix(
    traj: Trajectory,
    prefix_len: int,
    stats: TaskStats | None,
    cfg: RuleConfig = RuleConfig(),
    policy_rate: float | None = None,
) -> PrefixLabels:
    if not 1 <= prefix_len <= len(traj.steps):
        raise ValueError(f"prefix_len must be in [1, {len(traj.steps)}], got {prefix_len}")
    steps = traj.steps[:prefix_len]
    remaining = len(traj.steps) - prefix_len
    st = stuck_label(steps, cfg)
    pr = progress_label(steps)
    baseline = stats.loo_baseline(traj.outcome) if stats and stats.n_runs - 1 >= cfg.min_runs else None
    return PrefixLabels(
        p_success=traj.outcome,
        baseline=baseline,
        advantage=(float(traj.outcome) - baseline) if baseline is not None else None,
        steps_left=steps_left_bin(max(1, remaining)) if traj.outcome else None,
        stuck=bool(st.value),
        stuck_reasons=st.reasons,
        progress=int(pr.value),
        progress_reasons=pr.reasons,
        escalate=escalate_label(traj.outcome, stats, policy_rate, cfg),
    )


def group_by_task(trajs: Iterable[Trajectory]) -> dict[str, list[Trajectory]]:
    g: dict[str, list[Trajectory]] = defaultdict(list)
    for t in trajs:
        g[t.task_id].append(t)
    return dict(g)
