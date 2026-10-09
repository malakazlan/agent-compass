"""The six questions exactly as the models were trained on them (System One shape)."""

from __future__ import annotations

from agent_compass.data.examples import QUESTIONS

QUESTION_IDS: tuple[str, ...] = ("p_success", "stuck", "progress", "best_next", "escalate", "steps_left")
NOUL_IDS = frozenset(q for q, d in QUESTIONS.items() if d["type"] == "noul")
SCORE_IDS = frozenset(q for q, d in QUESTIONS.items() if d["type"] == "score")
CANDIDATE_MAX_CHARS = 600  # the training builder truncated candidate actions here


def build_questions(ids: tuple[str, ...] | list[str], candidates: list[str] | None = None) -> dict[str, dict]:
    """System One `questions` map for the given ids. `best_next` needs `candidates` (2 or more next actions)."""
    out: dict[str, dict] = {}
    for qid in ids:
        if qid not in QUESTIONS:
            raise ValueError(f"unknown question {qid!r}; known: {QUESTION_IDS}")
        q = {k: v for k, v in QUESTIONS[qid].items()}
        if qid == "best_next":
            if not candidates or len(candidates) < 2:
                raise ValueError("best_next needs at least two candidate actions")
            q["criteria"] = {f"option_{i + 1}": c[:CANDIDATE_MAX_CHARS] for i, c in enumerate(candidates)}
        out[qid] = q
    return out


def option_keys(qid: str, question: dict) -> list[str]:
    """Probability keys in option order, matching the server's answer shape."""
    if question["type"] == "noul":
        return ["false", "true"]
    if question["type"] == "choice":
        return list(question["criteria"])
    return [str(i) for i in range(len(question["criteria"]))]
