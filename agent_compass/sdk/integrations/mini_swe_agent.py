"""mini-swe-agent hook (written against mini-swe-agent 2.4.6).

    from minisweagent.models import get_model
    from minisweagent.environments.local import LocalEnvironment
    from agent_compass.sdk import Compass, RemoteBackend, Guardian
    from agent_compass.sdk.integrations.mini_swe_agent import CompassAgent

    agent = CompassAgent(get_model(...), LocalEnvironment(), compass=Compass(RemoteBackend("http://gpu-box:8008")),
                         guardian=Guardian(budget="fpr_5"), best_of=4, **agent_config)
    exit_info = agent.run(task)

Two behaviours, both optional:
- guardian: after every executed step the state is scored; on "abort" or "escalate" the agent exits with
  that status (exit_status "CompassAbort" / "CompassEscalate") instead of spending more steps.
- best_of > 1: each turn samples `best_of` model responses and lets `best_next` choose the action; the
  other samples are discarded (they are still billed by the LM provider).
"""

from __future__ import annotations

from typing import Any

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import InterruptAgentFlow

from agent_compass.sdk.client import Compass
from agent_compass.sdk.guardian import Guardian
from agent_compass.sdk.tracker import TrajectoryTracker

_SCORE_QUESTIONS = ("p_success", "stuck", "escalate")


def _action_text(action: dict | str) -> str:
    return action.get("command", "") if isinstance(action, dict) else str(action)


def _observation_text(output: dict) -> str:
    text = output.get("output", "") or ""
    rc = output.get("returncode")
    if output.get("exception_info"):
        text = f"{text}\n{output['exception_info']}".strip()
    return f"{text}\n[exit {rc}]" if rc not in (None, 0) else text


class CompassAgent(DefaultAgent):
    def __init__(self, model, env, *, compass: Compass, guardian: Guardian | None = None, best_of: int = 1,
                 policy_model: str | None = None, **kwargs):
        super().__init__(model, env, **kwargs)
        self.compass = compass
        self.guardian = guardian
        self.best_of = max(1, int(best_of))
        self.policy_model = policy_model or getattr(getattr(model, "config", None), "model_name", None) or "unknown"
        self.tracker: TrajectoryTracker | None = None
        self.compass_log: list[dict[str, Any]] = []

    def run(self, task: str, **kwargs) -> dict:
        self.tracker = TrajectoryTracker(task=task, policy_model=self.policy_model, scaffold="mini-swe-agent")
        if self.guardian is not None:
            self.guardian.reset()
        self.compass_log = []
        return super().run(task, **kwargs)

    def query(self) -> dict:
        if self.best_of == 1 or self.tracker is None or len(self.tracker) == 0:
            return super().query()
        # sample best_of responses, keep the one best_next prefers; the first sample pays the limit checks
        first = super().query()
        samples = [first]
        for _ in range(self.best_of - 1):
            self.n_calls += 1
            m = self.model.query(self.messages[:-1])
            self.cost += m.get("extra", {}).get("cost", 0.0)
            samples.append(m)
        actions = [" && ".join(_action_text(a) for a in m.get("extra", {}).get("actions", [])) or m.get("content", "") for m in samples]
        distinct = {a for a in actions if a.strip()}
        if len(distinct) < 2:
            return first
        idx, answers = self.compass.pick(self.tracker.trajectory(), actions, policy=self.policy_model)
        self.compass_log.append({"step": len(self.tracker) + 1, "best_of": len(samples), "chosen": idx, "best_next": answers["best_next"].probabilities()})
        if idx != 0:
            self.messages[-1] = samples[idx]
        return samples[idx]

    def execute_actions(self, message: dict) -> list[dict]:
        actions = message.get("extra", {}).get("actions", [])
        outputs = [self.env.execute(action) for action in actions]
        observations = self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))
        if self.tracker is not None:
            for action, output in zip(actions, outputs):
                self.tracker.add(_action_text(action), _observation_text(output))
            if self.guardian is not None and len(self.tracker) > 0:
                answers = self.compass.score(self.tracker.trajectory(), _SCORE_QUESTIONS, policy=self.policy_model)
                decision = self.guardian.decide(answers, step=len(self.tracker))
                self.compass_log.append({"step": len(self.tracker), "decision": decision.action, "reason": decision.reason,
                                         "p_success": decision.p_success, "stuck": decision.stuck, "escalate": decision.escalate})
                if decision.action != "continue":
                    status = "CompassAbort" if decision.action == "abort" else "CompassEscalate"
                    raise InterruptAgentFlow({"role": "exit", "content": status, "extra": {"exit_status": status, "submission": "", "compass": decision.reason}})
        return observations

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize({"info": {"compass": self.compass_log}}, *extra_dicts)
