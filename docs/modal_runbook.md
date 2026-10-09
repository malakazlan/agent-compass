# Modal runbook: v2 retrain and server check on the free credit

Script: `scripts/modal/train_modal.py`. Everything runs in Modal containers built from the kev fork
(pinned commit) with the fused Qwen3.5 kernels; the agent-compass source is mounted from the local
checkout at launch. Outputs land on the Modal volume `agent-compass-runs`.

## One-time setup (laptop)

```bash
uv tool install modal          # or: pip install modal
modal setup                    # opens the browser, stores the token in ~/.modal.toml
```

Check the credit under Usage & billing > Credits before launching. Set a spend limit under Usage limit
so nothing beyond the credit is charged.

## Steps

| step | command | GPU | expected |
|---|---|---|---|
| 1. data to volume | `modal run scripts/modal/train_modal.py::data` | none | about 1.1 GB from the public dataset repo (v1 files, refined stuck labels) |
| 2. smoke | `modal run scripts/modal/train_modal.py::train --name v2-2b --smoke` | H100 | 600 records, 500-record evals, about 15 min including the image build; a few dollars |
| 3. full run | `modal run scripts/modal/train_modal.py::train --name v2-2b` | H100 | v1 recipe on 27k records: about 2 h 10 min train, 5k dev + 10k test + tau2 eval about 1 h; about $13 |
| 4. server check | `modal run scripts/modal/train_modal.py::check_server --v1 v2-2b` | H100 | loads v0 (Hub) and v2 (volume) through `agent_compass.server.app`, scores 20 dev records on both endpoints, prints client latency p50/p95 |
| 5. pull results | `modal volume get agent-compass-runs v2-2b runs/` | none | adapter, head, logs, eval reports, metrics, calibration |

The v2 run differs from v1 only by its labels: the stuck rule as refined after the judge QA
(`docs/label_qa_findings.md`). Its gate is the v1 table in `docs/m4_report.md`: p_success within noise,
stuck against refined labels at or above 0.973 with fewer false flags, the other heads unchanged.

Options: `--init-from ""` trains from the base instead of warm-starting from v0; `--test-limit 20000`
reproduces the full v1 test evaluation (adds about 40 min); `AC_GPU=A100-80GB` uses the cheaper GPU
(set `--checkpointing 1`, batch 1 x accumulation 8 stays). A run name cannot be reused; pick a new one.

## After the run

- Copy `runs/v2-2b/{training_config.json, training_metrics.json, metrics_*.json, calibration.json, eval-*/report.json, train.log, summary.json}` into the repository's `runs/` (weights and rows are gitignored) and upload the adapter to `azlanmalikai/agent-compass-2b` under `v2/`.
- Update the calibration constants in `agent_compass/sdk/calibration.py` from `calibration.json` if v2 replaces v1 in the routing table.
