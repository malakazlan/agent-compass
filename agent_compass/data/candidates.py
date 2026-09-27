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

Cost control: tasks can have 100+ runs. `TaskIndex` derives action families and normalized
actions once per run, and each call looks at a seeded sample of at most `max_others`
other runs, so the work per prefix is bounded.
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


class TaskIndex:
    """Per-run derived data for all runs of one task, computed once."""

    def __init__(self, runs: list[Trajectory]) -> None:
        self.runs = runs
        self.by_id = {r.traj_id: r for r in runs}
        self.norm: dict[str, list[str]] = {r.traj_id: [normalize_action(s.action) for s in r.steps] for r in runs}
        self.fam: dict[str, list[str]] = {r.traj_id: [action_family(s.action) for s in r.steps] for r in runs}
        self._famsets: dict[tuple[str, int], frozenset[str]] = {}

    def famset(self, traj_id: str, length: int) -> frozenset[str]:
        key = (traj_id, length)
        fs = self._famsets.get(key)
        if fs is None:
            fs = frozenset(self.fam[traj_id][:length])
            self._famsets[key] = fs
        return fs

    def similarity(self, a: str, b: str, length: int) -> float:
        fa, fb = self.famset(a, length), self.famset(b, length)
        if not fa and not fb:
            return 1.0
        return len(fa & fb) / len(fa | fb)

    def actions_near(self, traj_id: str, step: int, tol: int) -> list[tuple[str, int]]:
        acts = self.norm[traj_id]
        lo, hi = max(0, step - tol), min(len(acts) - 1, step + tol)
        return [(acts[i], i) for i in range(lo, hi + 1) if acts[i]]


def prefix_similarity(a: Trajectory, b: Trajectory, length: int) -> float:
    """Jaccard similarity of action families over the first `length` steps."""
    fa = {action_family(s.action) for s in a.steps[:length]}
    fb = {action_family(s.action) for s in b.steps[:length]}
    if not fa and not fb:
        return 1.0
    return len(fa & fb) / len(fa | fb)


def build_candidate_set(
    traj: Trajectory,
    prefix_len: int,
    runs: list[Trajectory],
    k: int = 4,
    step_tolerance: int = 2,
    min_prefix_sim: float = 0.5,
    rng: random.Random | None = None,
    index: TaskIndex | None = None,
    max_others: int = 24,
) -> CandidateSet | None:
    """`runs` = all trajectories of the same task, `traj` included or not."""
    if prefix_len >= len(traj.steps):
        return None  # no next action to rank
    rng = rng or random.Random(0)
    idx = index if index is not None and traj.traj_id in index.by_id else TaskIndex(runs + ([traj] if all(r.traj_id != traj.traj_id for r in runs) else []))
    others = [r for r in runs if r.traj_id != traj.traj_id]
    if len(others) > max_others:
        others = rng.sample(others, max_others)
    succ = [r for r in others if r.outcome]
    fail = [r for r in others if not r.outcome]
    own_next = idx.norm[traj.traj_id][prefix_len]

    # positive
    if traj.outcome:
        positive = Candidate(own_next, traj.traj_id, prefix_len, True)
        pos_sim = 1.0
    else:
        if not succ:
            return None
        pos_run = max(succ, key=lambda r: idx.similarity(traj.traj_id, r.traj_id, prefix_len))
        pos_sim = idx.similarity(traj.traj_id, pos_run.traj_id, prefix_len)
        near = idx.actions_near(pos_run.traj_id, prefix_len, step_tolerance)
        if not near:
            return None
        a, i = min(near, key=lambda ai: abs(ai[1] - prefix_len))
        positive = Candidate(a, pos_run.traj_id, i, True)

    # negatives: actions failing runs took near this step
    pool: list[Candidate] = []
    if not traj.outcome:
        pool.append(Candidate(own_next, traj.traj_id, prefix_len, False))
    for r in fail:
        for a, i in idx.actions_near(r.traj_id, prefix_len, step_tolerance):
            pool.append(Candidate(a, r.traj_id, i, False))
    if not pool:
        return None

    # ambiguity guards: an action taken near this step by both a successful and a failing
    # run tells us nothing, so it can be neither the positive nor a negative.
    good_actions = {a for r in succ for a, _ in idx.actions_near(r.traj_id, prefix_len, step_tolerance)}
    if traj.outcome:
        good_actions.add(own_next)
    pos_norm = positive.action
    bad_actions = {c.action for c in pool}
    if pos_norm in bad_actions:
        return None
    negatives: list[Candidate] = []
    seen = {pos_norm}
    rng.shuffle(pool)
    for c in pool:
        n = c.action
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

    neg_ids = {c.from_traj for c in negatives} - {traj.traj_id}
    neg_sims = [idx.similarity(traj.traj_id, t, prefix_len) for t in neg_ids]
    tier = "branching" if pos_sim >= min_prefix_sim and neg_sims and max(neg_sims) >= min_prefix_sim else "hard_negative"

    options = [positive] + negatives
    rng.shuffle(options)
    correct = next(i for i, c in enumerate(options) if c.outcome and c.action == pos_norm)
    return CandidateSet(options=options, correct=correct, tier=tier, prefix_sim=round(pos_sim, 3),
                        meta={"n_runs": len(runs), "n_success_runs": sum(r.outcome for r in runs), "k": len(options)})
