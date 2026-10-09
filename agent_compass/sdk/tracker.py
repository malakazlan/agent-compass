"""Collect an agent's steps online and expose them as a Trajectory for scoring."""

from __future__ import annotations

from agent_compass.data.schema import Step, Trajectory


class TrajectoryTracker:
    """Append (action, observation) pairs as the agent runs; `trajectory()` gives the unified record.

    `policy_model` is shown to the model as the `<policy>` tag; pass "unknown" to score blind.
    """

    def __init__(self, task: str, policy_model: str = "unknown", scaffold: str = "custom", domain: str = "swe",
                 task_id: str = "task", repo_or_site: str = "unknown/unknown", traj_id: str | None = None):
        self.task = task
        self.policy_model = policy_model
        self.scaffold = scaffold
        self.domain = domain
        self.task_id = task_id
        self.repo_or_site = repo_or_site
        self.traj_id = traj_id or f"{scaffold}/{task_id}"
        self.steps: list[Step] = []

    def add(self, action: str, observation: str, thought: str | None = None, **extra) -> None:
        self.steps.append(Step(action=action, observation=observation, thought=thought, extra=dict(extra)))

    def __len__(self) -> int:
        return len(self.steps)

    def trajectory(self) -> Trajectory:
        if not self.steps:
            raise ValueError("no steps recorded yet; the model scores states after at least one step")
        return Trajectory(traj_id=self.traj_id, task_id=self.task_id, domain=self.domain, scaffold=self.scaffold,
                          policy_model=self.policy_model, repo_or_site=self.repo_or_site, task=self.task,
                          steps=list(self.steps), outcome=False, meta={"online": True})
