"""Watch any agent loop with agent-compass, and let it choose among candidate next actions.

Replace `fake_agent` with your loop: anything that yields (action, observation) per step.

    python examples/generic_monitor.py --server http://127.0.0.1:8008
"""

from __future__ import annotations

import argparse

from agent_compass.sdk import Compass, Guardian, RemoteBackend
from agent_compass.sdk.integrations.generic import CompassMonitor


def fake_agent():
    yield "pytest tests/test_parse.py", "FAILED tests/test_parse.py::test_dates - AssertionError: 3 != 2\n1 failed in 0.4s"
    yield "cat src/parse.py", "def parse_dates(s):\n    return s.split(',')\n"
    yield "pytest tests/test_parse.py", "FAILED tests/test_parse.py::test_dates - AssertionError: 3 != 2\n1 failed in 0.4s"
    yield "pytest tests/test_parse.py", "FAILED tests/test_parse.py::test_dates - AssertionError: 3 != 2\n1 failed in 0.4s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8008")
    a = ap.parse_args()
    compass = Compass(RemoteBackend(a.server))
    monitor = CompassMonitor("Make test_dates pass", compass, guardian=Guardian(budget="fpr_10", min_steps=2), policy_model="unknown")
    for action, observation in fake_agent():
        decision = monitor.on_step(action, observation)
        last = monitor.last_answers
        print(f"step {len(monitor.tracker)}: p_success={last.p_success:.2f} stuck={last.stuck:.2f} -> {decision.action} ({decision.reason})")
        if decision.action != "continue":
            break
    candidates = ["sed -i 's/split(\",\")/split(\";\")/' src/parse.py", "pytest tests/test_parse.py", "cat tests/test_parse.py"]
    print("best next:", candidates[monitor.pick(candidates)])


if __name__ == "__main__":
    main()
