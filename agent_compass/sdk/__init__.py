"""agent-compass SDK: score an agent state, get calibrated answers, decide what to do.

    from agent_compass.sdk import Compass, RemoteBackend, TrajectoryTracker, Guardian

    compass = Compass(RemoteBackend("http://localhost:8008"))
    tracker = TrajectoryTracker(task="Fix the failing test in utils.py", policy_model="gpt-4o-mini")
    tracker.add(action="pytest tests/test_utils.py", observation="FAILED tests/test_utils.py::test_parse ...")
    answers = compass.score(tracker.trajectory(), questions=("p_success", "stuck"))
    decision = Guardian().decide(answers, step=len(tracker))

Everything here is pure Python with no model dependency; `LocalBackend` imports the kev runtime lazily.
"""

from agent_compass.sdk.calibration import DEFAULT_CALIBRATION, Calibration
from agent_compass.sdk.client import Answers, Backend, Compass, LocalBackend, RemoteBackend, ScoredQuestion
from agent_compass.sdk.guardian import Decision, Guardian
from agent_compass.sdk.questions import QUESTION_IDS, build_questions
from agent_compass.sdk.tracker import TrajectoryTracker

__all__ = [
    "Answers",
    "Backend",
    "Calibration",
    "Compass",
    "DEFAULT_CALIBRATION",
    "Decision",
    "Guardian",
    "LocalBackend",
    "QUESTION_IDS",
    "RemoteBackend",
    "ScoredQuestion",
    "TrajectoryTracker",
    "build_questions",
]
