"""Recompute the rule-based labels of existing example records without rebuilding states.

State text and best_next candidate sets are expensive and do not depend on the label
rules; stuck / progress / escalate / steps_left do. When a rule changes, run this over the
example files instead of rebuilding: it looks each record's trajectory up by byte offset in
the unified file, recomputes `label_prefix`, and rewrites the record in place.

    uv run --python 3.12 --with pydantic python scripts/relabel_examples.py \
        --unified data/unified/swe_agent.jsonl --examples data/examples/swe_agent.dev.jsonl [more files...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.data.examples import QUESTIONS  # noqa: E402
from agent_compass.data.labels import TaskStats, label_prefix  # noqa: E402
from agent_compass.data.schema import Trajectory  # noqa: E402


def index_unified(path: Path) -> tuple[dict[str, int], dict[str, TaskStats]]:
    offsets: dict[str, int] = {}
    n: Counter[str] = Counter()
    s: Counter[str] = Counter()
    pn: Counter[str] = Counter()
    ps: Counter[str] = Counter()
    with path.open("rb") as f:
        pos = f.tell()
        for line in f:
            if line.strip():
                head = json.loads(line)
                offsets[head["traj_id"]] = pos
                n[head["task_id"]] += 1
                s[head["task_id"]] += int(head["outcome"])
                pn[head["policy_model"]] += 1
                ps[head["policy_model"]] += int(head["outcome"])
            pos = f.tell()
    return offsets, {k: TaskStats(n[k], s[k]) for k in n}, {k: ps[k] / pn[k] for k in pn}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--unified", type=Path, required=True)
    ap.add_argument("--examples", type=Path, required=True, nargs="+")
    args = ap.parse_args()
    t0 = time.time()
    offsets, stats, policy_rates = index_unified(args.unified)
    rates = {k: round(v, 3) for k, v in policy_rates.items()}
    print(f"indexed {len(offsets)} trajectories in {time.time() - t0:.0f}s; policy success rates {rates}", flush=True)

    with args.unified.open("rb") as uf:
        cache: dict[str, Trajectory] = {}
        for ex in args.examples:
            tmp = ex.with_suffix(".jsonl.tmp")
            changed = Counter()
            n = 0
            with ex.open(encoding="utf-8") as fin, tmp.open("w", encoding="utf-8", newline="\n") as fout:
                for line in fin:
                    rec = json.loads(line)
                    m = rec["_meta"]
                    tid = m["traj_id"]
                    traj = cache.get(tid)
                    if traj is None:
                        if len(cache) > 64:
                            cache.clear()
                        uf.seek(offsets[tid])
                        traj = Trajectory.from_json(uf.readline().decode("utf-8"))
                        cache[tid] = traj
                    L = label_prefix(traj, m["prefix_len"], stats.get(traj.task_id), policy_rate=policy_rates.get(traj.policy_model))
                    q = rec["questions"]
                    old = {k: q[k]["label"] for k in ("stuck", "progress", "escalate", "steps_left") if k in q}
                    q["stuck"] = {**QUESTIONS["stuck"], "label": L.stuck}
                    q["progress"] = {**QUESTIONS["progress"], "label": L.progress}
                    q.pop("escalate", None)
                    q.pop("steps_left", None)
                    if L.escalate is not None:
                        q["escalate"] = {**QUESTIONS["escalate"], "label": L.escalate}
                    if L.steps_left is not None:
                        q["steps_left"] = {**QUESTIONS["steps_left"], "label": L.steps_left}
                    m["stuck_reasons"] = list(L.stuck_reasons)
                    m["progress_reasons"] = list(L.progress_reasons)
                    m["baseline"] = L.baseline
                    m["advantage"] = L.advantage
                    for k in ("stuck", "progress", "escalate", "steps_left"):
                        if old.get(k) != (q[k]["label"] if k in q else None):
                            changed[k] += 1
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    n += 1
            os.replace(tmp, ex)
            print(f"{ex.name}: {n} records, changed labels: {dict(changed)}", flush=True)
    print(f"done in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
