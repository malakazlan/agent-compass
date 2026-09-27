"""Which prefixes of a trajectory become training / eval states.

A prefix of length L is the state after L steps. We always sample the fixed fractions
(so eval curves at 25/50/75/90% are comparable across models) plus a few random
positions so the model sees every part of the trajectory over the dataset.
"""

from __future__ import annotations

import random

FRACTIONS = (0.25, 0.5, 0.75, 0.9)


def sample_prefix_lengths(
    n_steps: int,
    fractions: tuple[float, ...] = FRACTIONS,
    n_random: int = 1,
    rng: random.Random | None = None,
    min_len: int = 1,
    include_full: bool = False,
) -> list[int]:
    """Sorted, de-duplicated prefix lengths in [min_len, n_steps]."""
    if n_steps < 1:
        return []
    rng = rng or random.Random(0)
    hi = n_steps if include_full else max(min_len, n_steps - 1)
    picks = {min(hi, max(min_len, round(f * n_steps))) for f in fractions}
    for _ in range(n_random):
        picks.add(rng.randint(min_len, hi))
    return sorted(picks)


def prefix_fraction(prefix_len: int, n_steps: int) -> float:
    return round(prefix_len / n_steps, 4)
