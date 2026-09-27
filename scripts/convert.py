"""Convert raw shards to the unified schema, assign splits, dedupe, and write stats.

Outputs, per dataset tag:
    data/unified/<tag>.jsonl          one Trajectory per line, `meta.split` filled in
    data/unified/<tag>.stats.json     counts used in docs/data_audit.md
and the frozen repo split table shared by every dataset:
    data/splits/repo_split.json

    uv run --python 3.12 --with pydantic --with pyarrow python scripts/convert.py [--only swe_agent,openhands] [--limit N]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.data.convert import nebius_openhands, nebius_swe_agent  # noqa: E402
from agent_compass.data.schema import Trajectory, repo_key  # noqa: E402
from agent_compass.data.splits import VERIFIED_HOLDOUT, build_split_table, split_for, verified_ids, write_split_table  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "unified"
SPLITS = ROOT / "data" / "splits"

CONVERTERS = {
    "swe_agent": nebius_swe_agent.convert_parquet,
    "openhands": nebius_openhands.convert_parquet,
}


def iter_raw(tag: str, limit: int | None, raw: Path = RAW) -> Iterator[Trajectory]:
    files = sorted((raw / tag).rglob("*.parquet"))
    if not files:
        raise SystemExit(f"{tag}: no parquet under {raw / tag}; run scripts/download_data.py first")
    n = 0
    for f in files:
        for t in CONVERTERS[tag](f):
            yield t
            n += 1
            if limit and n >= limit:
                return


def content_hash(t: Trajectory) -> str:
    h = hashlib.sha1()
    h.update(t.task_id.encode())
    h.update(t.policy_model.encode())
    h.update(t.scaffold.encode())
    for s in t.steps:
        h.update(s.action.encode())
        h.update(b"\x00")
        h.update(s.observation.encode())
        h.update(b"\x01")
    return h.hexdigest()


def convert(tag: str, limit: int | None, raw: Path = RAW) -> tuple[list[Trajectory], dict]:
    t0 = time.time()
    seen: set[str] = set()
    kept: list[Trajectory] = []
    dropped_dup = 0
    for t in iter_raw(tag, limit, raw):
        h = content_hash(t)
        if h in seen:
            dropped_dup += 1
            continue
        seen.add(h)
        kept.append(t.model_copy(update={"meta": {**t.meta, "split": split_for(t)}}))
    stats = summarize(tag, kept, dropped_dup, time.time() - t0)
    return kept, stats


def summarize(tag: str, trajs: list[Trajectory], dropped_dup: int, seconds: float) -> dict:
    by_task: dict[str, list[bool]] = defaultdict(list)
    for t in trajs:
        by_task[t.task_id].append(t.outcome)
    steps = [len(t.steps) for t in trajs]
    split_counts = Counter(t.meta["split"] for t in trajs)
    verified_hits = sum(1 for t in trajs if t.task_id in verified_ids())
    mixed = sum(1 for v in by_task.values() if any(v) and not all(v))
    return {
        "tag": tag,
        "source": trajs[0].meta["source"] if trajs else None,
        "n_trajectories": len(trajs),
        "n_dropped_duplicates": dropped_dup,
        "outcome_positive": sum(t.outcome for t in trajs),
        "outcome_rate": round(sum(t.outcome for t in trajs) / max(1, len(trajs)), 4),
        "n_tasks": len(by_task),
        "runs_per_task_mean": round(len(trajs) / max(1, len(by_task)), 2),
        "tasks_with_mixed_outcomes": mixed,
        "tasks_with_mixed_outcomes_pct": round(100 * mixed / max(1, len(by_task)), 1),
        "n_repos": len({repo_key(t.repo_or_site) for t in trajs}),
        "steps_mean": round(statistics.fmean(steps), 1) if steps else 0,
        "steps_median": statistics.median(steps) if steps else 0,
        "steps_max": max(steps) if steps else 0,
        "steps_mean_success": round(statistics.fmean([len(t.steps) for t in trajs if t.outcome]), 1) if any(t.outcome for t in trajs) else None,
        "steps_mean_failure": round(statistics.fmean([len(t.steps) for t in trajs if not t.outcome]), 1) if any(not t.outcome for t in trajs) else None,
        "policy_models": dict(Counter(t.policy_model for t in trajs)),
        "scaffolds": dict(Counter(t.scaffold for t in trajs)),
        "exit_status": dict(Counter(str(t.meta.get("exit_status")) for t in trajs).most_common(12)),
        "steps_with_thought_pct": round(100 * sum(1 for t in trajs for s in t.steps if s.thought) / max(1, sum(steps)), 1),
        "splits": dict(split_counts),
        "verified_instance_hits": verified_hits,
        "verified_holdout_trajectories": split_counts.get(VERIFIED_HOLDOUT, 0),
        "seconds": round(seconds, 1),
    }


def write_jsonl(trajs: list[Trajectory], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for t in trajs:
            f.write(t.to_json())
            f.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=None, help="stop after N trajectories per dataset (smoke test)")
    ap.add_argument("--raw", type=Path, default=RAW, help="raw shard root (default data/raw)")
    args = ap.parse_args()
    tags = [t for t in args.only.split(",") if t] or list(CONVERTERS)

    all_trajs: list[Trajectory] = []
    for tag in tags:
        trajs, stats = convert(tag, args.limit, args.raw)
        suffix = f".limit{args.limit}" if args.limit else ""
        write_jsonl(trajs, OUT / f"{tag}{suffix}.jsonl")
        (OUT / f"{tag}{suffix}.stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(stats, indent=1))
        if stats["verified_instance_hits"] and stats["verified_holdout_trajectories"] < stats["verified_instance_hits"]:
            raise SystemExit(f"{tag}: Verified instances found outside the holdout bucket")
        all_trajs.extend(trajs)

    if not args.limit:
        table = build_split_table(all_trajs)
        write_split_table(table, SPLITS / "repo_split.json")
        print(f"split table: {len(table)} repos -> {SPLITS / 'repo_split.json'}")


if __name__ == "__main__":
    main()
