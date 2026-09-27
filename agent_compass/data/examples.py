"""Turn unified trajectories into kev-shaped training records.

One record per (trajectory, prefix). Shape (what `kev.data.load_records` accepts):

    {"state": "<task>...</task>...", "questions": {qid: {type, instructions, criteria?, label}}, "_meta": {...}}

Questions whose label is masked for this prefix (steps_left on a failed run, escalate
without enough runs, best_next without candidates) are simply absent from the record.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from agent_compass.data.candidates import build_candidate_set
from agent_compass.data.labels import RuleConfig, TaskStats, label_prefix
from agent_compass.data.prefixes import prefix_fraction, sample_prefix_lengths
from agent_compass.data.schema import Step, Trajectory
from agent_compass.data.state import StateConfig, Tokenizer, build_state_with_count, chars_per_token_counter

QUESTIONS = {
    "p_success": {
        "type": "noul",
        "instructions": "Will this agent run end in success, meaning its final patch passes the task's hidden tests?",
    },
    "stuck": {
        "type": "noul",
        "instructions": "Is the agent stuck: repeating the same actions, hitting the same error, or cycling edits without progress?",
    },
    "escalate": {
        "type": "noul",
        "instructions": "Should this task be handed off now to a stronger model or a human, because this agent is unlikely to solve it?",
    },
    "progress": {
        "type": "score",
        "instructions": "How much did the most recent step move the task forward?",
        "criteria": ["regressed", "no change", "small progress", "big progress"],
    },
    "steps_left": {
        "type": "score",
        "instructions": "How many more steps will the agent need before it finishes?",
        "criteria": ["1 to 5 steps", "6 to 15 steps", "16 to 40 steps", "more than 40 steps"],
    },
    "best_next": {
        "type": "choice",
        "instructions": "Which candidate next action is most likely to lead to a successful outcome from this state?",
    },
}

FAKE_CONFIDENCE = "Great, this works! The fix is complete and all tests pass now."


@dataclass(frozen=True)
class BuildConfig:
    state: StateConfig = StateConfig()
    rules: RuleConfig = RuleConfig()
    n_random_prefixes: int = 1
    k_candidates: int = 4
    candidate_max_chars: int = 600
    policy_dropout: float = 0.3  # probability the policy name is replaced by "unknown"
    variant: str = "clean"  # clean | thoughts | fake_confidence
    seed: int = 0


def _with_fake_confidence(traj: Trajectory) -> Trajectory:
    steps = list(traj.steps)
    last = steps[-1]
    steps[-1] = Step(action=last.action, observation=last.observation, thought=FAKE_CONFIDENCE, extra=last.extra)
    return traj.model_copy(update={"steps": steps})


def build_records(
    traj: Trajectory,
    runs: list[Trajectory],
    stats: TaskStats | None,
    cfg: BuildConfig = BuildConfig(),
    count: Tokenizer | None = None,
    prefix_lens: list[int] | None = None,
) -> Iterator[dict]:
    count = count or chars_per_token_counter()
    rng = random.Random(f"{cfg.seed}:{traj.traj_id}")
    state_cfg = cfg.state
    if cfg.variant in ("thoughts", "fake_confidence") and not state_cfg.thoughts:
        state_cfg = StateConfig(**{**state_cfg.__dict__, "thoughts": True})
    lens = prefix_lens or sample_prefix_lengths(len(traj.steps), n_random=cfg.n_random_prefixes, rng=rng)

    for L in lens:
        view = traj.model_copy(update={"steps": traj.steps[:L]})
        if cfg.variant == "fake_confidence":
            view = _with_fake_confidence(view)
        policy = None
        if state_cfg.policy and rng.random() < cfg.policy_dropout:
            policy = state_cfg.policy_unknown_token
        state, state_tokens = build_state_with_count(view, L, state_cfg, count, policy_override=policy)
        labels = label_prefix(traj, L, stats, cfg.rules)

        qs: dict[str, dict] = {}
        qs["p_success"] = {**QUESTIONS["p_success"], "label": labels.p_success}
        qs["stuck"] = {**QUESTIONS["stuck"], "label": labels.stuck}
        qs["progress"] = {**QUESTIONS["progress"], "label": labels.progress}
        if labels.escalate is not None:
            qs["escalate"] = {**QUESTIONS["escalate"], "label": labels.escalate}
        if labels.steps_left is not None:
            qs["steps_left"] = {**QUESTIONS["steps_left"], "label": labels.steps_left}

        cand = build_candidate_set(traj, L, runs, k=cfg.k_candidates, rng=rng) if len(runs) > 1 else None
        cand_meta = None
        if cand is not None:
            criteria = {f"option_{i + 1}": c.action[: cfg.candidate_max_chars] for i, c in enumerate(cand.options)}
            qs["best_next"] = {**QUESTIONS["best_next"], "criteria": criteria, "label": f"option_{cand.correct + 1}"}
            cand_meta = {"tier": cand.tier, "prefix_sim": cand.prefix_sim, "k": len(cand.options),
                         "sources": [[c.from_traj, c.from_step, c.outcome] for c in cand.options]}

        yield {
            "state": state,
            "questions": qs,
            "_meta": {
                "id": f"{traj.traj_id}@{L}",
                "group_id": traj.task_id,
                "traj_id": traj.traj_id,
                "task_id": traj.task_id,
                "source": traj.meta.get("source"),
                "split": traj.meta.get("split"),
                "domain": traj.domain,
                "scaffold": traj.scaffold,
                "policy_model": traj.policy_model,
                "policy_shown": policy or traj.policy_model,
                "repo": traj.repo_or_site,
                "prefix_len": L,
                "n_steps": len(traj.steps),
                "prefix_frac": prefix_fraction(L, len(traj.steps)),
                "outcome": traj.outcome,
                "baseline": labels.baseline,
                "advantage": labels.advantage,
                "stuck_reasons": list(labels.stuck_reasons),
                "progress_reasons": list(labels.progress_reasons),
                "best_next": cand_meta,
                "variant": cfg.variant,
                "state_tokens": state_tokens,
            },
        }


def write_records(records: Iterator[dict], path: str | Path) -> int:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n
