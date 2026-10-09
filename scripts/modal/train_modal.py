"""Train, evaluate and check agent-compass on Modal (free monthly credit; H100 at $3.95/h as of 2026-10).

One-time:  pip install modal && modal setup          # browser login; token lands in ~/.modal.toml
Then, from the repository root:

    modal run scripts/modal/train_modal.py::data                       # v1 data from the HF dataset repo -> volume (CPU, free)
    modal run scripts/modal/train_modal.py::train --name v2-2b --smoke # 600 records + 500-record evals, ~15 min
    modal run scripts/modal/train_modal.py::train --name v2-2b         # full: 27k records, ~2h10m train + ~1h eval on H100
    modal run scripts/modal/train_modal.py::check_server --v0 azlanmalikai/agent-compass-2b --v1 v2-2b
    modal volume get agent-compass-runs v2-2b runs/                    # results (and weights) to the laptop

Design: the image clones the kev fork at AC_KEV_REF and installs it with the fused Qwen3.5 kernels exactly as
the pod did (scripts/pod/m0_reproduce_kev.sh); the agent-compass source is mounted from this checkout at
run time, so a local edit runs without an image rebuild. Everything lands on the volume `agent-compass-runs`
(/runs/<name>: adapter, head.pt, training_*.json, eval-*/, metrics_*.json, train.log); base weights are cached on
`agent-compass-hf`. Nothing here needs a secret: the data, the base model and the v0 adapter are public.
Set AC_GPU to change the GPU (A100-80GB fits 2B at batch 1 x 8 with checkpointing; the default run uses no checkpointing).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import modal

KEV_REPO = "https://github.com/malakazlan/kev.git"
KEV_REF = os.environ.get("AC_KEV_REF", "f2bb629d670f5b746f712fc05550a098526c836b")  # fork main as used by both pod sessions (2026-09-25)
GPU = os.environ.get("AC_GPU", "H100")
GPU_HOURLY = {"H100": 3.95, "A100-80GB": 2.50, "A100": 2.10, "L40S": 1.95}
KEV_ROOT, AC_ROOT, RUNS, HF = "/kev", "/agent-compass", "/runs", "/hf"
DATA_REPO = "azlanmalikai/agent-compass-data"
CAUSAL_CONV1D = "https://github.com/Dao-AILab/causal-conv1d/releases/download/v1.7.0/causal_conv1d-1.7.0%2Bcu12torch2.8cxx11abiTRUE-cp313-cp313-linux_x86_64.whl"
LOCAL_ROOT = Path(__file__).resolve().parents[2]

app = modal.App("agent-compass")
image = (
    modal.Image.debian_slim(python_version="3.13")
    .apt_install("git")
    .run_commands(f"git clone {KEV_REPO} {KEV_ROOT} && git -C {KEV_ROOT} checkout --quiet {KEV_REF}")
    .uv_pip_install(f"kev[serve] @ file://{KEV_ROOT}")
    # Gated DeltaNet kernels for the Qwen3.5 hybrids; torch 2.8 pins triton 3.4, fla needs >= 3.7.1 on Hopper
    .uv_pip_install("flash-linear-attention==0.5.2", "triton>=3.7.1", "scipy>=1.13", "huggingface_hub[cli]>=0.30")
    .uv_pip_install(CAUSAL_CONV1D, extra_options="--no-deps")
    .env({"HF_HOME": HF, "HF_HUB_DISABLE_PROGRESS_BARS": "1", "TOKENIZERS_PARALLELISM": "false", "PYTHONUNBUFFERED": "1",
          "TRITON_CACHE_DIR": f"{HF}/triton-cache", "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True", "PYTHONPATH": AC_ROOT})
    .add_local_dir(str(LOCAL_ROOT / "agent_compass"), f"{AC_ROOT}/agent_compass")
    .add_local_dir(str(LOCAL_ROOT / "scripts"), f"{AC_ROOT}/scripts")
    .add_local_dir(str(LOCAL_ROOT / "runs" / "baselines-swe_agent-v0"), f"{AC_ROOT}/runs/baselines-swe_agent-v0")
)
runs = modal.Volume.from_name("agent-compass-runs", create_if_missing=True)
hf_cache = modal.Volume.from_name("agent-compass-hf", create_if_missing=True)
VOLUMES = {RUNS: runs, HF: hf_cache}


def stage(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_logged(cmd: list[str], log_path: Path, cwd: str, echo=lambda line: True) -> None:
    """Run a command, tee its output to a log file, echo the lines `echo` selects."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log, subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=cwd) as proc:
        for line in proc.stdout:
            log.write(line)
            log.flush()
            if echo(line) or "Error" in line or "Traceback" in line:
                print(line.rstrip(), flush=True)
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, cmd, f"failed; see {log_path}")


def resolve_run(run: str) -> str:
    """A run name on the volume -> its directory; a Hub id or path is passed through."""
    runs.reload()
    cand = Path(RUNS) / run
    return str(cand) if (cand / "head.pt").exists() else run


# --- remote functions -----------------------------------------------------------------------------------------------

@app.function(image=image, cpu=2, memory=8192, timeout=3600, volumes=VOLUMES)
def fetch_data(include: str = "v1/*") -> dict:
    """Download the data files from the public HF dataset repo to /runs/data (once; later runs reuse them)."""
    from huggingface_hub import snapshot_download

    out = Path(RUNS) / "data"
    out.mkdir(parents=True, exist_ok=True)
    snapshot_download(DATA_REPO, repo_type="dataset", allow_patterns=[include], local_dir=str(out))
    runs.commit()
    files = {str(p.relative_to(out)): p.stat().st_size for p in out.rglob("*.jsonl")}
    stage(f"data on volume: {json.dumps(files)}")
    return files


@app.function(image=image, gpu=GPU, cpu=4, memory=(32768, 131072), timeout=6 * 3600, retries=0, volumes=VOLUMES)
def run_train(name: str, cfg: dict) -> dict:
    """Train with agent_compass.train.multihead, evaluate dev and test with the kev scorer, write agent-compass metrics."""
    import torch

    runs.reload()
    out = Path(RUNS) / name
    if (out / "head.pt").exists() or (out / "training_config.json").exists():
        raise FileExistsError(f"/runs/{name} already holds a run; choose a new name")
    data = Path(RUNS) / "data" / cfg["data_dir"]
    train_file, dev_file, test_file = data / "train.jsonl", data / "dev.jsonl", data / "test.jsonl"
    for f in (train_file, dev_file, test_file):
        if not f.exists():
            raise FileNotFoundError(f"{f} missing: run `modal run scripts/modal/train_modal.py::data` first")
    started = time.time()
    init_from = resolve_run(cfg["init_from"]) if cfg["init_from"] else None
    gpu = torch.cuda.get_device_name(0)
    stage(f"{gpu}: training {name} from {cfg['init_from'] or 'scratch'} on {train_file}; config {json.dumps(cfg)}")

    cmd = [sys.executable, "-m", "agent_compass.train.multihead", "--data", str(train_file), "--out", str(out), "--base", cfg["base"],
           "--epochs", cfg["epochs"], "--lr", cfg["lr"], "--batch", cfg["batch"], "--accum", cfg["accum"], "--max_state", cfg["max_state"],
           "--shared_prefix", 1, "--pair_weight", cfg["pair_weight"], "--ordinal", cfg["ordinal"], "--checkpointing", cfg["checkpointing"],
           "--seed", cfg["seed"], "--device", "cuda"]
    if init_from:
        cmd += ["--init_from", init_from]
    if cfg["weights"]:
        cmd += ["--weights", cfg["weights"]]
    if cfg["max_records"]:
        cmd += ["--max_records", cfg["max_records"]]
    cmd = [str(c) for c in cmd]
    try:
        run_logged(cmd, out / "train.log", KEV_ROOT, echo=lambda l: l.startswith(("ep", "saved", "trainable", "warm")) or "records encoded" in l)
    finally:
        runs.commit()
        hf_cache.commit()
    train_s = time.time() - started
    (out / "config.json").write_text(json.dumps({"name": name, "config": cfg, "kev_ref": KEV_REF, "gpu": gpu, "train_seconds": round(train_s)}, indent=1))
    stage(f"train wall {train_s / 60:.1f} min; evaluating dev {cfg['dev_limit']} / test {cfg['test_limit']} records")

    bench = f"{AC_ROOT}/scripts/pod/kev_benchmark_long.py"
    env_bf16 = {**os.environ, "KEV_DTYPE": "bf16"}
    for split, path, limit in (("dev", dev_file, cfg["dev_limit"]), ("test", test_file, cfg["test_limit"])):
        t = time.time()
        subprocess.run([sys.executable, bench, "--run", str(out), "--data", str(path), "--device", "cuda", "--max_state", str(cfg["max_state"]),
                        "--limit", str(limit), "--out", str(out / f"eval-{split}")], check=True, cwd=KEV_ROOT, env=env_bf16)
        extra = ["--baseline", f"{AC_ROOT}/runs/baselines-swe_agent-v0/result.json"] if split == "test" else []
        subprocess.run([sys.executable, f"{AC_ROOT}/scripts/eval_rows.py", "--rows", str(out / f"eval-{split}" / "rows.json"), "--records", str(path),
                        "--out", str(out / f"metrics_{split}.json"), *extra], check=True, cwd=AC_ROOT)
        stage(f"eval {split}: {(time.time() - t) / 60:.1f} min")
        runs.commit()
    if (data / "test_tau2.jsonl").exists() and cfg["tau2"]:
        subprocess.run([sys.executable, bench, "--run", str(out), "--data", str(data / "test_tau2.jsonl"), "--device", "cuda", "--max_state", str(cfg["max_state"]),
                        "--out", str(out / "eval-test-tau2")], check=True, cwd=KEV_ROOT, env=env_bf16)
        subprocess.run([sys.executable, f"{AC_ROOT}/scripts/eval_rows.py", "--rows", str(out / "eval-test-tau2" / "rows.json"), "--records", str(data / "test_tau2.jsonl"),
                        "--out", str(out / "metrics_test_tau2.json")], check=True, cwd=AC_ROOT)
    subprocess.run([sys.executable, f"{AC_ROOT}/scripts/calibrate_per_question.py", "--dev-rows", str(out / "eval-dev/rows.json"), "--dev-records", str(dev_file),
                    "--test-rows", str(out / "eval-test/rows.json"), "--test-records", str(test_file), "--out", str(out / "calibration.json")], check=False, cwd=AC_ROOT)
    wall = time.time() - started
    summary = {"name": name, "gpu": gpu, "train_minutes": round(train_s / 60, 1), "wall_minutes": round(wall / 60, 1),
               "estimated_cost_usd": round(wall / 3600 * GPU_HOURLY.get(GPU, 4.0), 2)}
    for split in ("dev", "test", "test_tau2"):
        p = out / f"metrics_{split}.json"
        if p.exists():
            qs = json.loads(p.read_text()).get("questions", {})
            summary[f"{split}_p_success_auroc"] = qs.get("p_success", {}).get("auroc")
            summary[f"{split}_stuck_auroc"] = qs.get("stuck", {}).get("auroc")
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    runs.commit()
    hf_cache.commit()
    stage(json.dumps(summary))
    return summary


@app.function(image=image, gpu=GPU, cpu=2, memory=(16384, 65536), timeout=3600, retries=0, volumes=VOLUMES)
def run_check_server(v0: str, v1: str, n: int = 20) -> dict:
    """Load the adapters with agent_compass.server.app, score n dev records through both endpoints, time them."""
    from fastapi.testclient import TestClient

    from agent_compass.server.app import load_runtime, make_app

    runs.reload()
    data = Path(RUNS) / "data" / "v1" / "dev.jsonl"
    records = []
    with data.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
            if len(records) >= n:
                break
    runtime = load_runtime(resolve_run(v0) if v0 else None, resolve_run(v1) if v1 else None)
    client = TestClient(make_app(runtime))
    assert client.get("/health").json()["status"] == "ok"
    lat, agree = [], 0
    for r in records:
        qs = {qid: {k: v for k, v in q.items() if k != "label"} for qid, q in r["questions"].items()}
        t = time.perf_counter()
        resp = client.post("/v1/systemone", json={"state": r["state"], "questions": qs})
        lat.append((time.perf_counter() - t) * 1000)
        assert resp.status_code == 200, resp.text
        a = resp.json()["answers"]
        label = r["questions"]["p_success"]["label"]
        agree += int((a["p_success"]["noul"] >= 0.5) == bool(label))
    r = client.post("/v1/score", json={"state": records[0]["state"], "questions": ["p_success", "stuck", "escalate"], "guardian": {"budget": "fpr_5"}})
    assert r.status_code == 200, r.text
    lat.sort()
    result = {"records": len(records), "latency_ms_p50_client": round(lat[len(lat) // 2], 1), "latency_ms_p95_client": round(lat[int(len(lat) * 0.95) - 1], 1),
              "p_success_accuracy_at_0.5": round(agree / len(records), 3), "score_endpoint_decision": r.json().get("decision"), "adapters": list(runtime.servers)}
    stage(json.dumps(result))
    return result


# --- local entrypoints -------------------------------------------------------------------------------------------------

@app.local_entrypoint()
def data(include: str = "v1/*"):
    print(json.dumps(fetch_data.remote(include), indent=1))


@app.local_entrypoint()
def train(name: str, smoke: bool = False, data_dir: str = "v1", base: str = "Qwen/Qwen3.5-2B-Base", init_from: str = "azlanmalikai/agent-compass-2b",
          epochs: int = 1, lr: float = 5e-5, batch: int = 1, accum: int = 8, max_state: int = 4352, pair_weight: float = 0.25, ordinal: int = 1,
          checkpointing: int = 0, weights: str = "", max_records: int = 0, dev_limit: int = 5000, test_limit: int = 10000, tau2: bool = True, seed: int = 0):
    """Defaults reproduce the v1 recipe (docs/m4_report.md) on the refined-label v1 data; `--smoke` runs 600 records."""
    cfg = dict(data_dir=data_dir, base=base, init_from=init_from, epochs=epochs, lr=lr, batch=batch, accum=accum, max_state=max_state, pair_weight=pair_weight,
               ordinal=ordinal, checkpointing=checkpointing, weights=weights, max_records=max_records, dev_limit=dev_limit, test_limit=test_limit, tau2=tau2, seed=seed)
    if smoke:
        name = f"{name}-smoke"
        cfg.update(max_records=600, dev_limit=500, test_limit=500, tau2=False)
    est_h = 0.4 if smoke else (2.3 + 0.3 * (dev_limit / 5000) + 0.75 * (test_limit / 10000) + (0.25 if tau2 else 0))
    print(f"launching {name} on {GPU}: estimated {est_h:.1f} h, about ${est_h * GPU_HOURLY.get(GPU, 4.0):.0f}", flush=True)
    print(json.dumps(run_train.remote(name, cfg), indent=1))


@app.local_entrypoint()
def check_server(v0: str = "azlanmalikai/agent-compass-2b", v1: str = "", n: int = 20):
    print(json.dumps(run_check_server.remote(v0, v1, n), indent=1))
