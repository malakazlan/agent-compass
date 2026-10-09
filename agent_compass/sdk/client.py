"""Compass client: render a state, ask the questions, return calibrated answers.

Backends return raw per-option probabilities from a served checkpoint; the client applies the
release calibration and routes each question to the adapter that answers it best.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_compass.data.schema import Trajectory
from agent_compass.data.state import StateConfig, Tokenizer, build_state, chars_per_token_counter
from agent_compass.sdk.calibration import DEFAULT_CALIBRATION, Calibration, temper
from agent_compass.sdk.questions import QUESTION_IDS, build_questions, option_keys


class Backend(Protocol):
    """Scores one state against a System One `questions` map.

    Returns {qid: probabilities in option order} and may set `last_stats` ({"latency_ms", "input_tokens"}).
    """

    def score(self, state: str, questions: dict[str, dict]) -> dict[str, list[float]]: ...


class RemoteBackend:
    """Any System One endpoint (`POST /v1/systemone`): the agent-compass server or kev.serve."""

    def __init__(self, base_url: str, api_key: str | None = None, model: str = "kev-latest", timeout: float = 120.0, retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.last_stats: dict[str, Any] = {}

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps(body).encode("utf-8")
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(self.base_url + path, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:500]
                if 400 <= e.code < 500:
                    raise RuntimeError(f"server rejected the request ({e.code}): {detail}") from None
                last = RuntimeError(f"server error {e.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
            if attempt < self.retries:
                time.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"could not reach {self.base_url}: {last}")

    def score(self, state: str, questions: dict[str, dict]) -> dict[str, list[float]]:
        body = {"state": state, "model": self.model, "questions": {qid: {k: v for k, v in q.items() if k != "label"} for qid, q in questions.items()}}
        resp = self._post("/v1/systemone", body)
        self.last_stats = {"latency_ms": resp.get("latency_ms"), "input_tokens": resp.get("usage", {}).get("input_tokens")}
        return answers_to_probs(resp["answers"], questions)


def answers_to_probs(answers: dict[str, dict], questions: dict[str, dict]) -> dict[str, list[float]]:
    """System One answers -> probabilities in option order (the inverse of the server's answer mapping)."""
    out: dict[str, list[float]] = {}
    for qid, q in questions.items():
        a = answers[qid]
        if q["type"] == "noul":
            p = float(a["noul"])
            out[qid] = [1.0 - p, p]
        else:
            keys = option_keys(qid, q)
            out[qid] = [float(a["probabilities"][k]) for k in keys]
    return out


class LocalBackend:
    """In-process scoring with the kev runtime (`pip install git+https://github.com/jaredpalmer/kev`).

    `run` is a checkpoint directory or hub id, for example `azlanmalikai/agent-compass-2b` (v0) or a local
    download's `v1/` folder. Needs a GPU for practical latency; the fused Qwen3.5 kernels for speed.
    """

    def __init__(self, run: str, device: str | None = None, max_state: int = 4352, dtype: str = "bf16"):
        import os

        os.environ.setdefault("KEV_DTYPE", dtype)
        from kev.checkpoint import LoadOptions
        from kev.device import default_device
        from kev.model import training_context
        from kev.predictors import LocalPredictor

        self.predictor = LocalPredictor(run, device or default_device(), LoadOptions.from_env(), context=training_context(max_state))
        self.last_stats: dict[str, Any] = {}

    def score(self, state: str, questions: dict[str, dict]) -> dict[str, list[float]]:
        record = {"state": state, "questions": {qid: {**q, "label": q.get("label", 0 if q["type"] != "choice" else next(iter(q["criteria"])))} for qid, q in questions.items()}}
        out = self.predictor(record)
        self.last_stats = {"latency_ms": out["latency_ms"], "input_tokens": out["input_tokens"]}
        return {qid: [out["probabilities"][qid][k] for k in option_keys(qid, q)] for qid, q in questions.items()}


@dataclass(frozen=True)
class ScoredQuestion:
    qid: str
    type: str
    keys: list[str]
    raw: list[float]
    calibrated: list[float]
    adapter: str

    @property
    def p_true(self) -> float | None:
        return self.calibrated[1] if self.type == "noul" else None

    @property
    def argmax(self) -> str:
        return self.keys[max(range(len(self.calibrated)), key=lambda i: self.calibrated[i])]

    @property
    def expected_level(self) -> float | None:
        return sum(i * p for i, p in enumerate(self.calibrated)) if self.type == "score" else None

    def probabilities(self) -> dict[str, float]:
        return dict(zip(self.keys, self.calibrated))


@dataclass
class Answers:
    questions: dict[str, ScoredQuestion]
    state: str
    latency_ms: float | None = None
    input_tokens: int | None = None
    policy: str | None = None

    def __getitem__(self, qid: str) -> ScoredQuestion:
        return self.questions[qid]

    def __contains__(self, qid: str) -> bool:
        return qid in self.questions

    @property
    def p_success(self) -> float | None:
        return self.questions["p_success"].p_true if "p_success" in self.questions else None

    @property
    def stuck(self) -> float | None:
        return self.questions["stuck"].p_true if "stuck" in self.questions else None

    @property
    def escalate(self) -> float | None:
        return self.questions["escalate"].p_true if "escalate" in self.questions else None

    @property
    def progress(self) -> float | None:
        return self.questions["progress"].expected_level if "progress" in self.questions else None

    @property
    def steps_left(self) -> float | None:
        return self.questions["steps_left"].expected_level if "steps_left" in self.questions else None

    @property
    def best_next(self) -> int | None:
        """Index (0-based) of the chosen candidate."""
        if "best_next" not in self.questions:
            return None
        return int(self.questions["best_next"].argmax.split("_")[1]) - 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "answers": {qid: {"type": q.type, "adapter": q.adapter, "probabilities": q.probabilities(), "raw": dict(zip(q.keys, q.raw)),
                              **({"p": q.p_true} if q.type == "noul" else {}), **({"expected": q.expected_level} if q.type == "score" else {}),
                              **({"choice": q.argmax} if q.type == "choice" else {})}
                        for qid, q in self.questions.items()},
            "latency_ms": self.latency_ms, "input_tokens": self.input_tokens, "policy": self.policy,
        }


class Compass:
    """Score states with one or two adapters.

    backends: {"v0": backend, "v1": backend}. With one adapter, every question goes to it and its
    calibration is used where fitted (v0 has only p_success; v1 has all six).
    """

    def __init__(self, backends: Backend | dict[str, Backend], calibration: Calibration = DEFAULT_CALIBRATION,
                 state_config: StateConfig = StateConfig(), token_counter: Tokenizer | None = None):
        self.backends = {"v1": backends} if not isinstance(backends, dict) else dict(backends)
        if not self.backends:
            raise ValueError("at least one backend is required")
        self.calibration = calibration
        self.state_config = state_config
        self.count = token_counter or chars_per_token_counter()

    def adapter_for(self, qid: str) -> str:
        preferred = self.calibration.route.get(qid)
        if preferred in self.backends:
            return preferred
        return "v1" if "v1" in self.backends else next(iter(self.backends))

    def render(self, traj: Trajectory, policy: str | None = None) -> str:
        return build_state(traj, len(traj.steps), self.state_config, self.count, policy_override=policy)

    def score(self, state: str | Trajectory, questions: tuple[str, ...] | list[str] = QUESTION_IDS, candidates: list[str] | None = None,
              policy: str | None = None) -> Answers:
        """`state` is a rendered state text or a Trajectory (rendered here with the training state builder).

        `questions` without candidates drops best_next silently when it was the default set; asking for it
        explicitly without candidates is an error.
        """
        qids = list(questions)
        if "best_next" in qids and not candidates:
            if tuple(questions) == QUESTION_IDS:
                qids.remove("best_next")
            else:
                raise ValueError("best_next needs candidates")
        if isinstance(state, Trajectory):
            policy = policy if policy is not None else state.policy_model
            text = self.render(state, policy)
        else:
            text = state
        qmap = build_questions(qids, candidates)
        by_adapter: dict[str, dict[str, dict]] = {}
        for qid, q in qmap.items():
            by_adapter.setdefault(self.adapter_for(qid), {})[qid] = q
        scored: dict[str, ScoredQuestion] = {}
        latency = 0.0
        tokens = None
        for adapter, qs in by_adapter.items():
            backend = self.backends[adapter]
            probs = backend.score(text, qs)
            stats = getattr(backend, "last_stats", {}) or {}
            if stats.get("latency_ms") is not None:
                latency += float(stats["latency_ms"])
            if stats.get("input_tokens") is not None:
                tokens = max(tokens or 0, int(stats["input_tokens"]))
            for qid, q in qs.items():
                raw = list(probs[qid])
                scored[qid] = ScoredQuestion(qid=qid, type=q["type"], keys=option_keys(qid, q), raw=raw,
                                             calibrated=temper(raw, self.calibration.temperature(adapter, qid)), adapter=adapter)
        return Answers(questions={qid: scored[qid] for qid in qids}, state=text, latency_ms=latency or None, input_tokens=tokens, policy=policy)

    def pick(self, state: str | Trajectory, candidates: list[str], policy: str | None = None) -> tuple[int, Answers]:
        """Best-of-N over candidate next actions: (index of the chosen candidate, answers)."""
        answers = self.score(state, ("best_next",), candidates=candidates, policy=policy)
        return answers.best_next, answers


FakeScorer = Callable[[str, dict[str, dict]], dict[str, list[float]]]


@dataclass
class FunctionBackend:
    """A backend from a plain function; for tests and for wrapping custom runtimes."""

    fn: FakeScorer
    last_stats: dict[str, Any] = field(default_factory=dict)

    def score(self, state: str, questions: dict[str, dict]) -> dict[str, list[float]]:
        return self.fn(state, questions)
