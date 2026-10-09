"""FastAPI server for agent-compass.

    python -m agent_compass.server.app --v0 azlanmalikai/agent-compass-2b --v1 runs/agent-compass-2b/v1 --port 8008

Loads one or two checkpoints with the kev runtime (batched model thread, prefix cache, CUDA graphs when
available) and exposes:

- POST /v1/systemone   System One request -> calibrated answers. Questions are routed to the adapter that
                       answers them best (p_success -> v0, the rest -> v1) and merged into one response.
                       Unknown question ids go to v1 uncalibrated, so the endpoint stays general.
- POST /v1/score       {"trajectory": <unified record>, "questions": [...], "candidates": [...], "policy": str,
                       "guardian": {"budget": "fpr_5", "min_steps": 5}} -> answers + decision.
- GET  /v1/models, GET /health

Requires `pip install git+https://github.com/jaredpalmer/kev fastapi uvicorn` and a GPU for practical latency.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import replace
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agent_compass import __version__
from agent_compass.data.schema import Trajectory
from agent_compass.sdk.calibration import DEFAULT_CALIBRATION, Calibration, temper
from agent_compass.sdk.client import Answers, ScoredQuestion
from agent_compass.sdk.guardian import Guardian
from agent_compass.sdk.questions import QUESTION_IDS, build_questions, option_keys


class Runtime:
    """The loaded adapters. `servers[name]` answers a System One request dict with raw probabilities."""

    def __init__(self, servers: dict[str, Any], calibration: Calibration = DEFAULT_CALIBRATION, renderer=None):
        if not servers:
            raise ValueError("at least one adapter")
        self.servers = servers
        self.calibration = calibration
        self.renderer = renderer  # Trajectory -> state text

    def adapter_for(self, qid: str) -> str:
        preferred = self.calibration.route.get(qid)
        if preferred in self.servers:
            return preferred
        return "v1" if "v1" in self.servers else next(iter(self.servers))

    async def answer(self, state: Any, questions: dict[str, dict], model_name: str = "agent-compass") -> dict:
        by_adapter: dict[str, dict[str, dict]] = {}
        for qid, q in questions.items():
            by_adapter.setdefault(self.adapter_for(qid), {})[qid] = q
        parts = await asyncio.gather(*[self._one(name, state, qs, model_name) for name, qs in by_adapter.items()])
        answers: dict[str, dict] = {}
        latency = 0.0
        tokens = 0
        for name, qs, resp in zip(by_adapter, by_adapter.values(), parts):
            latency += float(resp.get("latency_ms") or 0.0)
            tokens = max(tokens, int(resp.get("usage", {}).get("input_tokens") or 0))
            for qid, q in qs.items():
                answers[qid] = self._calibrate(name, qid, q, resp["answers"][qid])
        return {"model": model_name, "answers": answers, "usage": {"input_tokens": tokens}, "latency_ms": round(latency, 1),
                "adapters": {qid: self.adapter_for(qid) for qid in questions}}

    async def _one(self, name: str, state: Any, questions: dict[str, dict], model_name: str) -> dict:
        from kev.api import SystemOneRequest

        req = SystemOneRequest(state=state, model=model_name, questions={qid: {k: v for k, v in q.items() if k != "label"} for qid, q in questions.items()})
        server = self.servers[name]
        try:
            return await server.answer_async(req)
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    def _calibrate(self, name: str, qid: str, q: dict, answer: dict) -> dict:
        T = self.calibration.temperature(name, qid)
        if q["type"] == "noul":
            p = temper([1.0 - answer["noul"], answer["noul"]], T)
            return {"type": "noul", "noul": round(p[1], 4), "raw": round(answer["noul"], 4), "temperature": T, "adapter": name}
        keys = option_keys(qid, q)
        probs = temper([answer["probabilities"][k] for k in keys], T)
        dist = {k: round(v, 4) for k, v in zip(keys, probs)}
        if q["type"] == "choice":
            best = max(range(len(probs)), key=lambda i: probs[i])
            return {"type": "choice", "choice": keys[best], "probabilities": dist, "confidence": round((probs[best] - 1 / len(probs)) / (1 - 1 / len(probs)) if len(probs) > 1 else 1.0, 4),
                    "raw": answer["probabilities"], "temperature": T, "adapter": name}
        score = sum(i * p for i, p in enumerate(probs))
        return {"type": "score", "score": round(score, 4), "legend": answer.get("legend", {}), "probabilities": dist, "raw": answer["probabilities"], "temperature": T, "adapter": name}


class ScoreRequest(BaseModel):
    trajectory: dict | None = None
    state: str | None = None
    questions: list[str] = Field(default_factory=lambda: list(QUESTION_IDS))
    candidates: list[str] | None = None
    policy: str | None = None
    guardian: dict[str, Any] | None = None


def make_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="agent-compass", version=__version__)
    app.state.runtime = runtime

    @app.get("/health")
    def health():
        return {"status": "ok", "adapters": list(runtime.servers), "version": __version__}

    @app.get("/v1/models")
    def models():
        return {"models": [{"name": "agent-compass", "adapters": list(runtime.servers), "route": runtime.calibration.route,
                            "temperatures": runtime.calibration.temperatures, "version": __version__}]}

    @app.post("/v1/systemone")
    async def systemone(body: dict):
        from kev.api import SystemOneRequest

        try:
            req = SystemOneRequest.model_validate(body)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, str(e)) from None
        questions = {qid: q.model_dump() for qid, q in req.questions.items()}
        return await runtime.answer(req.state, questions, req.model)

    @app.post("/v1/score")
    async def score(req: ScoreRequest):
        qids = list(req.questions)
        if "best_next" in qids and not req.candidates:
            if set(qids) == set(QUESTION_IDS):
                qids.remove("best_next")
            else:
                raise HTTPException(422, "best_next needs candidates")
        policy = req.policy
        if req.trajectory is not None:
            try:
                traj = Trajectory.model_validate(req.trajectory)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(422, f"invalid trajectory: {e}") from None
            if runtime.renderer is None:
                raise HTTPException(500, "no state renderer configured")
            policy = policy if policy is not None else traj.policy_model
            state = runtime.renderer(traj, policy)
            n_steps = len(traj.steps)
        elif req.state is not None:
            state = req.state
            n_steps = None
        else:
            raise HTTPException(422, "trajectory or state is required")
        try:
            qmap = build_questions(qids, req.candidates)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        resp = await runtime.answer(state, qmap)
        scored = {}
        for qid, q in qmap.items():
            a = resp["answers"][qid]
            keys = option_keys(qid, q)
            cal = [1 - a["noul"], a["noul"]] if q["type"] == "noul" else [a["probabilities"][k] for k in keys]
            raw = [1 - a["raw"], a["raw"]] if q["type"] == "noul" else [a["raw"][k] for k in keys]
            scored[qid] = ScoredQuestion(qid=qid, type=q["type"], keys=keys, raw=raw, calibrated=cal, adapter=a["adapter"])
        answers = Answers(questions=scored, state=state, latency_ms=resp["latency_ms"], input_tokens=resp["usage"]["input_tokens"], policy=policy)
        out = answers.to_dict()
        if req.guardian is not None:
            g = Guardian(**{k: v for k, v in req.guardian.items() if k in {"budget", "policy", "min_steps", "patience", "stuck_threshold", "escalate_threshold"}})
            if g.policy is None:
                g = replace(g, policy=policy if policy in runtime.calibration.abort_thresholds else None)
            g.patience = 1  # stateless endpoint: one call, one decision
            d = g.decide(answers, step=n_steps if n_steps is not None else g.min_steps)
            out["decision"] = {"action": d.action, "reason": d.reason}
        return out

    return app


def load_runtime(v0: str | None, v1: str | None, max_state: int = 4352) -> Runtime:
    """Load the adapters with kev's serving defaults (bf16, fused kernels and CUDA graphs when available)."""
    import torch
    from kev.checkpoint import Checkpoint, LoadOptions, fused_available
    from kev.device import default_device
    from kev.serve import Server

    from agent_compass.data.state import StateConfig, build_state, chars_per_token_counter, hf_tokenizer_counter

    dev = default_device()
    opts = LoadOptions.from_env()
    if dev != "cpu" and opts.dtype is None:
        opts = replace(opts, dtype=torch.bfloat16)
    if dev == "cuda" and opts.cuda_graphs is None:
        opts = replace(opts, cuda_graphs=True)
    if dev == "cuda" and opts.fused is None:
        opts = replace(opts, fused=fused_available())
    if opts.backend is None:
        opts = replace(opts, backend="auto")
    servers: dict[str, Any] = {}
    counter = None
    for name, run in (("v0", v0), ("v1", v1)):
        if not run:
            continue
        ck = Checkpoint(run)
        tok, model = ck.load(dev, opts)
        servers[name] = Server(ck, tok, model, dev)
        if counter is None:
            tok_json = os.path.join(ck.path, "tokenizer.json")
            counter = hf_tokenizer_counter(tok_json) if os.path.exists(tok_json) else chars_per_token_counter()
        print(f"loaded {name} from {run} on {dev} via {model.backend} ({model.dtype})", flush=True)
    cfg = StateConfig(budget=max_state - 256)

    def renderer(traj: Trajectory, policy: str | None) -> str:
        return build_state(traj, len(traj.steps), cfg, counter, policy_override=policy)

    return Runtime(servers, DEFAULT_CALIBRATION, renderer)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v0", default="azlanmalikai/agent-compass-2b", help="p_success adapter (checkpoint dir or hub id); '' to skip")
    ap.add_argument("--v1", default="", help="six-question adapter (checkpoint dir, e.g. a download's v1/ folder); '' to skip")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8008)
    ap.add_argument("--max_state", type=int, default=4352)
    a = ap.parse_args()
    if not a.v0 and not a.v1:
        raise SystemExit("give --v0 and/or --v1")
    runtime = load_runtime(a.v0 or None, a.v1 or None, a.max_state)
    import uvicorn

    uvicorn.run(make_app(runtime), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
