"""best_next candidate sets from other runs of the same task.

At a prefix of length L of trajectory A, the "next action" is A.steps[L]. We build a
K-way choice question whose correct option is an action taken from a run that went on to
succeed, and whose distractors are actions taken at a similar step by runs that failed.

Two quality tiers, recorded on every candidate set so coverage can be reported honestly:

  branching      the positive and at least one negative come from runs whose first L
                 actions look like A's (prefix similarity >= `min_prefix_sim`), i.e. the
                 runs genuinely diverged around this state.
  hard_negative  the runs share the task but not the prefix. The label is then "an action
                 a successful run took around this point" vs "an action a failing run
                 took around this point", which is a weaker but still informative signal.

Sets are skipped when the positive action also appears among failing runs (ambiguous) or
when fewer than two distinct options exist.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from agent_compass.data.labels import action_family, normalize_action
from agent_compass.data.schema import Trajectory

FINISH = {"submit", "finish"}


@dataclass(frozen=True)
class Candidate:
    action: str
    from_traj: str
    from_step: int
    outcome: bool


@dataclass(frozen=True)
class CandidateSet:
    options: list[Candidate]
    correct: int
    tier: str  # "branching" | "hard_negative"
    prefix_sim: float  # similarity between A's prefix and the positive's run prefix
    meta: dict = field(default_factory=dict)


def prefix_similarity(a: Trajectory, b: Trajectory, length: int) -> float:
    """Jaccard similarity of action families over the first `length` steps."""
    fa = {action_family(s.action) for s in a.steps[:length]}
    fb = {action_family(s.action) for s in b.steps[:length]}
    if not fa and not fb:
        return 1.0
    return len(fa & fb) / len(fa | fb)


def _actions_near(run: Trajectory, step: int, tol: int) -> list[tuple[str, int]]:
    lo, hi = max(0, step - tol), min(len(run.steps) - 1, step + tol)
    out = []
    for i in range(lo, hi + 1):
        a = normalize_action(run.steps[i].action)
        if a:
            out.append((a, i))
    return out


def build_candidate_set(
    traj: Trajectory,
    prefix_len: int,
    runs: list[Trajectory],
    k: int = 4,
    step_tolerance: int = 2,
    min_prefix_sim: float = 0.5,
    rng: random.Random | None = None,
) -> CandidateSet | None:
    """`runs` = all trajectories of the same task, `traj` included or not."""
    if prefix_len >= len(traj.steps):
        return None  # no next action to rank
    rng = rng or random.Random(0)
    others = [r for r in runs if r.traj_id != traj.traj_id]
    succ = [r for r in others if r.outcome]
    fail = [r for r in others if not r.outcome]
    own_next = normalize_action(traj.steps[prefix_len].action)

    # positive
    if traj.outcome:
        positive = Candidate(own_next, traj.traj_id, prefix_len, True)
        pos_sim = 1.0
        pos_run = traj
    else:
        if not succ:
            return None
        ranked = sorted(succ, key=lambda r: -prefix_similarity(traj, r, prefix_len))
        pos_run = ranked[0]
        pos_sim = prefix_similarity(traj, pos_run, prefix_len)
        near = _actions_near(pos_run, prefix_len, step_tolerance)
        if not near:
            return None
        a, i = min(near, key=lambda ai: abs(ai[1] - prefix_len))
        positive = Candidate(a, pos_run.traj_id, i, True)

    # negatives: actions failing runs took near this step
    pool: list[Candidate] = []
    if not traj.outcome:
        pool.append(Candidate(own_next, traj.traj_id, prefix_len, False))
    for r in fail:
        for a, i in _actions_near(r, prefix_len, step_tolerance):
            pool.append(Candidate(a, r.traj_id, i, False))
    if not pool:
        return None

    # ambiguity guards: an action taken near this step by both a successful and a failing
    # run tells us nothing, so it can be neither the positive nor a negative.
    good_actions = {a for r in succ for a, _ in _actions_near(r, prefix_len, step_tolerance)}
    if traj.outcome:
        good_actions.add(own_next)
    pos_norm = normalize_action(positive.action)
    bad_actions = {normalize_action(c.action) for c in pool}
    if pos_norm in bad_actions:
        return None
    negatives: list[Candidate] = []
    seen = {pos_norm}
    rng.shuffle(pool)
    for c in pool:
        n = normalize_action(c.action)
        if n in seen or n in good_actions:
            continue
        if (n in FINISH) != (pos_norm in FINISH):
            continue  # do not let "submit vs anything" be the whole question
        seen.add(n)
        negatives.append(c)
        if len(negatives) >= k - 1:
            break
    if not negatives:
        return None

    neg_sims = [prefix_similarity(traj, r, prefix_len) for r in fail if any(c.from_traj == r.traj_id for c in negatives)]
    tier = "branching" if pos_sim >= min_prefix_sim and neg_sims and max(neg_sims) >= min_prefix_sim else "hard_negative"

    options = [positive] + negatives
    rng.shuffle(options)
    correct = next(i for i, c in enumerate(options) if c.outcome and normalize_action(c.action) == pos_norm)
    return CandidateSet(options=options, correct=correct, tier=tier, prefix_sim=round(pos_sim, 3),
                        meta={"n_runs": len(runs), "n_success_runs": len(succ) + int(traj.outcome), "k": len(options)})
