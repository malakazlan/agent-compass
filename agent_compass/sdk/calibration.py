"""Post-hoc calibration applied on top of the probabilities a served checkpoint returns.

Temperatures were fitted by NLL on the dev split of each release (runs/m3-v0-2b/calibration.json,
runs/m4-v1-2b/calibration.json) on top of the probabilities the checkpoint serves, so they apply to
any endpoint that serves the checkpoint as evaluated. Abort thresholds come from the early-abort
simulation of v0 (runs/m3-v0-2b/early_abort_test.json): the calibrated p_success below which a run
is abandoned, chosen on dev at a false-abort budget and reported on held-out runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def temper(probs: list[float], temperature: float) -> list[float]:
    """softmax(log p / T): the same operation the calibration scripts apply to served probabilities."""
    if temperature == 1.0:
        return list(probs)
    z = [math.log(max(p, 1e-9)) / temperature for p in probs]
    m = max(z)
    e = [math.exp(v - m) for v in z]
    s = sum(e)
    return [v / s for v in e]


@dataclass(frozen=True)
class Calibration:
    """Per-question temperatures by adapter and abort thresholds on calibrated p_success."""

    temperatures: dict[str, dict[str, float]]  # adapter -> question id -> T
    abort_thresholds: dict[str, dict[str, float]]  # policy ("*" = pooled) -> budget -> threshold
    route: dict[str, str] = field(default_factory=dict)  # question id -> adapter

    def temperature(self, adapter: str, qid: str) -> float:
        return self.temperatures.get(adapter, {}).get(qid, 1.0)

    def abort_threshold(self, budget: str = "fpr_5", policy: str | None = None) -> float:
        table = self.abort_thresholds.get(policy or "", None) or self.abort_thresholds["*"]
        if budget not in table:
            raise ValueError(f"unknown budget {budget!r}; known: {sorted(table)}")
        return table[budget]


# Release values. v0 serves p_success; v1 serves the other five (and p_success when only v1 is loaded).
DEFAULT_CALIBRATION = Calibration(
    temperatures={
        "v0": {"p_success": 1.5948625634942193},
        "v1": {
            "p_success": 3.0239154645429576,
            "stuck": 1.0374775857018057,
            "escalate": 3.1301745084841737,
            "progress": 1.0064956892947436,
            "steps_left": 0.9736698410471025,
            "best_next": 1.1860336077796516,
        },
    },
    abort_thresholds={
        "*": {"fpr_5": 0.12, "fpr_10": 0.19, "fpr_25": 0.28},
        "Qwen3-Coder-480B-A35B-Instruct": {"fpr_5": 0.22, "fpr_10": 0.25, "fpr_25": 0.32},
        "swe-agent-llama-70b": {"fpr_5": 0.06, "fpr_10": 0.09, "fpr_25": 0.15},
    },
    route={"p_success": "v0", "stuck": "v1", "escalate": "v1", "progress": "v1", "steps_left": "v1", "best_next": "v1"},
)
