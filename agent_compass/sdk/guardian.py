"""Guardian: turn calibrated answers into continue / abort / escalate decisions.

The abort rule is the one measured offline (docs/m3_report.md, early abort): abandon a run when the
calibrated p_success falls under a threshold chosen on dev at a false-abort budget. Online the run
length is unknown, so the simulation's "after 20% of the run" guard becomes `min_steps`.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_compass.sdk.calibration import DEFAULT_CALIBRATION, Calibration
from agent_compass.sdk.client import Answers


@dataclass(frozen=True)
class Decision:
    action: str  # "continue" | "abort" | "escalate"
    reason: str
    p_success: float | None = None
    stuck: float | None = None
    escalate: float | None = None


@dataclass
class Guardian:
    """budget: "fpr_5" | "fpr_10" | "fpr_25" false-abort budget. policy: name used for per-policy thresholds when known.

    stuck_threshold: calibrated p(stuck) above which a run is flagged as looping; the released v1 head
    reaches about 89% recall of rule-stuck states at 5% false positives around 0.11 and over-flags
    somewhat (docs/m4_report.md), so the default is conservative. escalate_threshold likewise.
    """

    budget: str = "fpr_5"
    policy: str | None = None
    min_steps: int = 5
    patience: int = 2  # consecutive low-value states before aborting
    stuck_threshold: float = 0.5
    escalate_threshold: float = 0.7
    calibration: Calibration = DEFAULT_CALIBRATION

    def __post_init__(self) -> None:
        self._low_streak = 0

    @property
    def abort_threshold(self) -> float:
        return self.calibration.abort_threshold(self.budget, self.policy)

    def reset(self) -> None:
        self._low_streak = 0

    def decide(self, answers: Answers, step: int) -> Decision:
        p = answers.p_success
        stuck = answers.stuck
        esc = answers.escalate
        base = {"p_success": p, "stuck": stuck, "escalate": esc}
        if step < self.min_steps:
            return Decision("continue", f"step {step} < min_steps {self.min_steps}", **base)
        if esc is not None and esc >= self.escalate_threshold:
            return Decision("escalate", f"p(escalate) {esc:.2f} >= {self.escalate_threshold}", **base)
        if p is not None and p < self.abort_threshold:
            self._low_streak += 1
            if self._low_streak >= self.patience:
                return Decision("abort", f"p_success {p:.2f} < {self.abort_threshold:.2f} for {self._low_streak} steps ({self.budget})", **base)
            return Decision("continue", f"p_success {p:.2f} low, streak {self._low_streak}/{self.patience}", **base)
        self._low_streak = 0
        if stuck is not None and stuck >= self.stuck_threshold:
            return Decision("escalate", f"p(stuck) {stuck:.2f} >= {self.stuck_threshold}", **base)
        return Decision("continue", "ok", **base)
