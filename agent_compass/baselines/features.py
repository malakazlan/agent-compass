"""Hand features for the heuristic and gradient-boosted baselines.

Every feature is computed from what the model would also see: the state text and the
prefix length. Nothing derived from the trajectory's future (total length, prefix
fraction, per-task baseline that includes this run) is allowed here; those fields exist in
`_meta` for analysis only.
"""

from __future__ import annotations

import re

_STEP = re.compile(r"^## step (\d+)$", re.M)
_ACTION = re.compile(r"^\$ (.*)$", re.M)
_ERR = re.compile(r"\b(?:Traceback|Error|Exception|FAILED|fatal:|not found|No such file|SyntaxError|syntax error)\b", re.I)
_PASS = re.compile(r"\b(\d+) passed\b")
_FAIL = re.compile(r"\b(\d+) (?:failed|errors?)\b")
_EDIT = re.compile(r"^\$ (?:edit\b|str_replace_editor (?:str_replace|create|insert)\b|sed -i\b)", re.M)
_TEST = re.compile(r"^\$ .*\b(?:pytest|python -m pytest|unittest|tox|npm test|make test)\b", re.M)
_HIST_LINE = re.compile(r"^\d+\. \$ ", re.M)
_OMITTED = re.compile(r"\.\.\. (\d+) earlier steps omitted")

FEATURE_NAMES = (
    "prefix_len",
    "state_tokens",
    "n_recent",
    "n_hist_lines",
    "n_omitted",
    "recent_error_lines",
    "recent_error_steps",
    "recent_repeat_actions",
    "recent_distinct_actions",
    "recent_edits",
    "recent_tests",
    "last_passed",
    "last_failed",
    "last_has_tests",
    "last_is_finish",
    "last_no_output",
    "stuck_rule",
    "progress_rule",
    "policy_known",
)


def _recent_block(state: str) -> str:
    i = state.find("<recent>")
    j = state.rfind("</recent>")
    return state[i + len("<recent>") : j] if i >= 0 and j > i else ""


def _hist_block(state: str) -> str:
    i = state.find("<hist>")
    j = state.find("</hist>")
    return state[i + len("<hist>") : j] if i >= 0 and j > i else ""


def features_from_record(rec: dict) -> dict[str, float]:
    state: str = rec["state"]
    meta = rec["_meta"]
    q = rec["questions"]
    recent = _recent_block(state)
    hist = _hist_block(state)
    steps = _STEP.split(recent)  # ['', '3', body3, '4', body4, ...]
    bodies = steps[2::2]
    actions = [m.group(1).strip() for m in _ACTION.finditer(recent)]
    last_body = bodies[-1] if bodies else ""
    last_obs = last_body.split("\n", 2)[-1] if last_body else ""  # after "$ action" line
    err_steps = sum(1 for b in bodies if _ERR.search(b.split("\n", 2)[-1] if b else ""))
    passed = _PASS.findall(last_obs)
    failed = _FAIL.findall(last_obs)
    last_action = actions[-1] if actions else ""
    om = _OMITTED.search(hist)
    return {
        "prefix_len": float(meta["prefix_len"]),
        "state_tokens": float(meta.get("state_tokens", 0)),
        "n_recent": float(len(bodies)),
        "n_hist_lines": float(len(_HIST_LINE.findall(hist))),
        "n_omitted": float(om.group(1)) if om else 0.0,
        "recent_error_lines": float(len(_ERR.findall(recent))),
        "recent_error_steps": float(err_steps),
        "recent_repeat_actions": float(len(actions) - len(set(actions))),
        "recent_distinct_actions": float(len(set(actions))),
        "recent_edits": float(len(_EDIT.findall(recent))),
        "recent_tests": float(len(_TEST.findall(recent))),
        "last_passed": float(passed[-1]) if passed else 0.0,
        "last_failed": float(failed[-1]) if failed else 0.0,
        "last_has_tests": float(bool(passed or failed)),
        "last_is_finish": float(last_action.split(" ")[0] in ("submit", "finish")),
        "last_no_output": float(last_obs.strip() == "(no output)"),
        "stuck_rule": float(bool(q["stuck"]["label"])),
        "progress_rule": float(q["progress"]["label"]),
        "policy_known": float(meta.get("policy_shown") not in (None, "unknown")),
    }


def feature_matrix(records: list[dict]) -> list[list[float]]:
    rows = []
    for r in records:
        f = features_from_record(r)
        rows.append([f[k] for k in FEATURE_NAMES])
    return rows
