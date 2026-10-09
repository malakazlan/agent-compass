"""Server routing and calibration with fake adapters (no model, no kev)."""

import sys
import types

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from agent_compass.data.schema import Trajectory  # noqa: E402
from agent_compass.sdk.calibration import DEFAULT_CALIBRATION, temper  # noqa: E402
from agent_compass.server.app import Runtime, make_app  # noqa: E402


class FakeKevServer:
    """Mimics kev.serve.Server.answer_async for a System One request."""

    def __init__(self, noul: float):
        self.noul = noul
        self.requests = []

    async def answer_async(self, req):
        self.requests.append(req)
        answers = {}
        for qid, q in req.questions.items():
            if q.type == "noul":
                answers[qid] = {"type": "noul", "noul": self.noul}
            elif q.type == "choice":
                keys = list(q.criteria)
                answers[qid] = {"type": "choice", "choice": keys[-1], "confidence": 0.5, "probabilities": {k: (0.7 if i == len(keys) - 1 else 0.3 / (len(keys) - 1)) for i, k in enumerate(keys)}}
            else:
                n = len(q.criteria)
                answers[qid] = {"type": "score", "score": 1.0, "legend": {}, "probabilities": {str(i): 1 / n for i in range(n)}}
        return {"model": req.model, "answers": answers, "usage": {"input_tokens": 100}, "latency_ms": 10.0}


@pytest.fixture
def fake_kev_api(monkeypatch):
    """A stand-in for kev.api.SystemOneRequest so the server imports without kev installed."""
    from pydantic import BaseModel

    class Q(BaseModel):
        type: str
        instructions: object = None
        criteria: object = None

    class SystemOneRequest(BaseModel):
        state: object
        model: str = "kev-latest"
        questions: dict[str, Q]

    mod = types.ModuleType("kev.api")
    mod.SystemOneRequest = SystemOneRequest
    pkg = types.ModuleType("kev")
    pkg.api = mod
    monkeypatch.setitem(sys.modules, "kev", pkg)
    monkeypatch.setitem(sys.modules, "kev.api", mod)
    return SystemOneRequest


def make_client(v0=0.8, v1=0.3):
    servers = {"v0": FakeKevServer(v0), "v1": FakeKevServer(v1)}
    runtime = Runtime(servers, DEFAULT_CALIBRATION, renderer=lambda traj, policy: f"<task>{traj.task}</task> policy={policy} steps={len(traj.steps)}")
    return TestClient(make_app(runtime)), servers


def test_systemone_routes_and_calibrates(fake_kev_api):
    client, servers = make_client()
    body = {"state": "<task>x</task>", "questions": {
        "p_success": {"type": "noul", "instructions": "?"},
        "stuck": {"type": "noul", "instructions": "?"},
        "best_next": {"type": "choice", "instructions": "?", "criteria": {"option_1": "a", "option_2": "b"}},
        "custom": {"type": "noul", "instructions": "anything"}}}
    r = client.post("/v1/systemone", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["adapters"] == {"p_success": "v0", "stuck": "v1", "best_next": "v1", "custom": "v1"}
    assert set(servers["v0"].requests[0].questions) == {"p_success"}
    assert set(servers["v1"].requests[0].questions) == {"stuck", "best_next", "custom"}
    expected = temper([0.2, 0.8], DEFAULT_CALIBRATION.temperature("v0", "p_success"))[1]
    assert abs(out["answers"]["p_success"]["noul"] - expected) < 1e-3
    assert out["answers"]["custom"]["temperature"] == 1.0 and out["answers"]["custom"]["noul"] == 0.3
    assert out["answers"]["best_next"]["choice"] == "option_2"
    assert out["latency_ms"] == 20.0 and out["usage"]["input_tokens"] == 100


def test_score_endpoint_with_trajectory_and_guardian(fake_kev_api):
    client, servers = make_client(v0=0.005)  # raw 0.005 -> 0.035 after T=1.6, under the 0.06 llama-70b threshold
    traj = Trajectory(traj_id="t", task_id="k", domain="swe", scaffold="custom", policy_model="swe-agent-llama-70b", repo_or_site="o/r",
                      task="Fix", steps=[{"action": "ls", "observation": "x"}] * 6, outcome=False)
    r = client.post("/v1/score", json={"trajectory": traj.model_dump(), "questions": ["p_success", "stuck"], "guardian": {"budget": "fpr_5"}})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["policy"] == "swe-agent-llama-70b"
    assert "steps=6" in servers["v0"].requests[0].state
    assert out["decision"]["action"] == "abort" and "0.06" in out["decision"]["reason"]
    assert out["answers"]["p_success"]["adapter"] == "v0"


def test_score_endpoint_validation(fake_kev_api):
    client, _ = make_client()
    assert client.post("/v1/score", json={"questions": ["p_success"]}).status_code == 422
    assert client.post("/v1/score", json={"state": "s", "questions": ["best_next"]}).status_code == 422
    r = client.post("/v1/score", json={"state": "s"})  # default set drops best_next
    assert r.status_code == 200 and "best_next" not in r.json()["answers"]
    assert client.get("/health").json()["adapters"] == ["v0", "v1"]
