"""Framework-agnostic callback: wrap any agent loop that exposes (action, observation) per step.

    monitor = CompassMonitor(task, compass, guardian=Guardian(), policy_model="gpt-4o-mini")
    for action, observation in my_agent_loop():
        decision = monitor.on_step(action, observation)
        if decision.action != "continue":
            break

Works as an OpenHands or LangGraph callback the same way: call `on_step` from the event handler that
sees each executed action and its observation.
"""

from __future__ import annotations

from typing import Any

from agent_compass.sdk.client import Answers, Compass
from agent_compass.sdk.guardian import Decision, Guardian
from agent_compass.sdk.tracker import TrajectoryTracker


class CompassMonitor:
    def __init__(self, task: str, compass: Compass, guardian: Guardian | None = None, policy_model: str = "unknown",
                 scaffold: str = "custom", questions: tuple[str, ...] = ("p_success", "stuck", "escalate")):
        self.compass = compass
        self.guardian = guardian or Guardian()
        self.tracker = TrajectoryTracker(task=task, policy_model=policy_model, scaffold=scaffold)
        self.questions = questions
        self.log: list[dict[str, Any]] = []
        self.last_answers: Answers | None = None

    def on_step(self, action: str, observation: str, thought: str | None = None) -> Decision:
        self.tracker.add(action, observation, thought)
        answers = self.compass.score(self.tracker.trajectory(), self.questions, policy=self.tracker.policy_model)
        self.last_answers = answers
        decision = self.guardian.decide(answers, step=len(self.tracker))
        self.log.append({"step": len(self.tracker), "decision": decision.action, "reason": decision.reason, **answers.to_dict()})
        return decision

    def pick(self, candidates: list[str]) -> int:
        """Index of the candidate `best_next` prefers for the next step."""
        idx, _ = self.compass.pick(self.tracker.trajectory(), candidates, policy=self.tracker.policy_model)
        return idx
