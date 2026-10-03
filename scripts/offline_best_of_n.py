"""Offline best-of-N trajectory selection from scored test rows.

For every task with >= 2 scored runs, use each run's p_success at its latest scored prefix
to pick one run. Resolve rate of the picked runs is compared with picking at random (the
task's mean pass rate), picking the shortest run (a common heuristic), and an oracle.
Also reports the curve over N (random subsets of N runs per task, averaged over draws).

    uv run --python 3.12 --with numpy python scripts/offline_best_of_n.py \
        --rows runs/m3-v0-2b/eval-test/rows.json --records data/v0/test.jsonl --out runs/m3-v0-2b/best_of_n_test.json
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=Path, required=True)
    ap.add_argument("--records", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--temperature", type=float, default=1.0, help="monotone, so irrelevant for picking; kept for reporting")
    ap.add_argument("--draws", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    meta = {}
    with args.records.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            meta[r["_meta"]["id"]] = r["_meta"]
    rows = json.load(open(args.rows, encoding="utf-8"))
    rows = rows if isinstance(rows, list) else rows["rows"]
    keys = [str(k).lower() for k in rows[0]["keys"]]
    yes = keys.index("true") if "true" in keys else 1

    # latest scored prefix per trajectory
    best_prefix: dict[str, tuple[float, float, bool, str, int]] = {}  # traj -> (frac, p, outcome, task, n_steps)
    for r in rows:
        m = meta.get(r["id"])
        if not m:
            continue
        cur = best_prefix.get(m["traj_id"])
        if cur is None or m["prefix_frac"] > cur[0]:
            best_prefix[m["traj_id"]] = (m["prefix_frac"], float(r["p"][yes]), bool(m["outcome"]), m["task_id"], int(m["n_steps"]))

    by_task: dict[str, list[tuple]] = defaultdict(list)
    for traj, (frac, p, y, task, n) in best_prefix.items():
        by_task[task].append((p, y, n, traj, frac))
    tasks = {t: runs for t, runs in by_task.items() if len(runs) >= 2}
    src_of = lambda t: next(m for m in meta.values() if m["task_id"] == t)["source"].split("/")[-1]  # noqa: E731

    def summarize(task_set: dict, label: str) -> dict:
        n_tasks = len(task_set)
        model_pick = np.mean([max(runs, key=lambda x: x[0])[1] for runs in task_set.values()])
        shortest_pick = np.mean([min(runs, key=lambda x: x[2])[1] for runs in task_set.values()])
        random_pick = np.mean([np.mean([y for _, y, _, _, _ in runs]) for runs in task_set.values()])
        oracle = np.mean([any(y for _, y, _, _, _ in runs) for runs in task_set.values()])
        curve = {}
        for N in (1, 2, 4, 8):
            vals_model, vals_rand = [], []
            for runs in task_set.values():
                if len(runs) < N:
                    continue
                for _ in range(args.draws):
                    sub = rng.sample(runs, N)
                    vals_model.append(max(sub, key=lambda x: x[0])[1])
                    vals_rand.append(rng.choice(sub)[1])
            if vals_model:
                curve[f"N={N}"] = {"model": float(np.mean(vals_model)), "random": float(np.mean(vals_rand)), "tasks": sum(1 for r in task_set.values() if len(r) >= N)}
        # task-level bootstrap of the lift (model pick - random pick)
        per_task = [(max(runs, key=lambda x: x[0])[1] - np.mean([y for _, y, _, _, _ in runs])) for runs in task_set.values()]
        boots = sorted(np.mean(rng.choices(per_task, k=len(per_task))) for _ in range(1000)) if per_task else [0.0]
        lift_ci = [float(boots[int(0.025 * len(boots))]), float(boots[int(0.975 * len(boots)) - 1])]
        return {"label": label, "tasks": n_tasks, "runs": sum(len(r) for r in task_set.values()),
                "lift_model_minus_random": float(np.mean(per_task)) if per_task else 0.0, "lift_ci95": lift_ci,
                "runs_per_task_mean": round(sum(len(r) for r in task_set.values()) / max(1, n_tasks), 2),
                "resolve_rate_random_pick": float(random_pick), "resolve_rate_shortest_pick": float(shortest_pick),
                "resolve_rate_model_pick": float(model_pick), "resolve_rate_oracle": float(oracle), "curve": curve}

    result = {"rows": str(args.rows), "records": str(args.records), "all": summarize(tasks, "all")}
    for src in sorted({src_of(t) for t in tasks}):
        sub = {t: r for t, r in tasks.items() if src_of(t) == src}
        result[src] = summarize(sub, src)
    # mixed-outcome tasks only: where selection can matter at all
    mixed = {t: r for t, r in tasks.items() if any(y for _, y, _, _, _ in r) and not all(y for _, y, _, _, _ in r)}
    result["mixed_outcome_tasks_only"] = summarize(mixed, "mixed")

    args.out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    for k, s in result.items():
        if not isinstance(s, dict):
            continue
        print(f"\n== {s['label']}: {s['tasks']} tasks, {s['runs']} runs ({s['runs_per_task_mean']} per task)")
        print(f"   resolve rate: random pick {s['resolve_rate_random_pick']:.3f} | shortest {s['resolve_rate_shortest_pick']:.3f} | MODEL pick {s['resolve_rate_model_pick']:.3f} | oracle {s['resolve_rate_oracle']:.3f}")
        print("   best-of-N: " + "  ".join(f"{n}: model {v['model']:.3f} vs random {v['random']:.3f} ({v['tasks']} tasks)" for n, v in s["curve"].items()))
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
