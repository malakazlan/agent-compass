"""Run mini-swe-agent on one task with agent-compass watching it.

    pip install mini-swe-agent            # 2.4.6 at the time of writing
    python examples/mini_swe_agent_guardian.py --server http://gpu-box:8008 --model gpt-4o-mini \
        --task "Make `pytest tests/test_utils.py` pass" --best-of 4

The agent exits with status CompassAbort / CompassEscalate when the guardian fires; `info.compass` in the
saved trajectory holds every score and decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from minisweagent.config import builtin_config_dir, get_config_path
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models import get_model
import yaml

from agent_compass.sdk import Compass, Guardian, RemoteBackend
from agent_compass.sdk.integrations.mini_swe_agent import CompassAgent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8008")
    ap.add_argument("--model", required=True, help="LM for the agent, in litellm naming")
    ap.add_argument("--task", required=True)
    ap.add_argument("--config", default=str(builtin_config_dir / "default.yaml"))
    ap.add_argument("--budget", default="fpr_5", choices=["fpr_5", "fpr_10", "fpr_25"])
    ap.add_argument("--best-of", type=int, default=1)
    ap.add_argument("--out", default="compass_run.traj.json")
    a = ap.parse_args()

    config = yaml.safe_load(Path(get_config_path(a.config)).read_text())
    model = get_model(a.model, config.get("model", {}))
    env = LocalEnvironment(**config.get("environment", {}))
    agent = CompassAgent(model, env, compass=Compass(RemoteBackend(a.server)), guardian=Guardian(budget=a.budget),
                         best_of=a.best_of, policy_model=a.model, **config.get("agent", {}), output_path=Path(a.out))
    info = agent.run(a.task)
    print(json.dumps({"exit_status": info.get("exit_status"), "steps": len(agent.tracker or []), "cost": round(agent.cost, 4),
                      "last_decision": agent.compass_log[-1] if agent.compass_log else None}, indent=1))


if __name__ == "__main__":
    main()
