"""Convert raw shards to the unified schema, assign splits, dedupe, and write stats.

Streams: each trajectory is written as soon as it is converted and statistics are kept
as running counters, so memory stays flat for any dataset size.

Outputs, per dataset tag:
    data/unified/<tag>.jsonl          one Trajectory per line, `meta.split` filled in
    data/unified/<tag>.stats.json     counts used in docs/data_audit.md
and the frozen repo split table shared by every dataset:
    data/splits/repo_split.json

    uv run --python 3.12 --with pydantic --with pyarrow python scripts/convert.py [--only swe_agent,openhands] [--limit N] [--raw DIR]
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
from agent_compass.data.splits import VERIFIED_HOLDOUT, assign_split, split_for, verified_ids, verified_repos, write_split_table  # noqa: E402

RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "unified"
SPLITS = ROOT / "data" / "splits"

CONVERTERS = {
    "swe_agent": nebius_swe_agent.convert_parquet,
    "openhands": nebius_openhands.convert_parquet,
}


def iter_raw(tag: str, limit: int | None, raw: Path = RAW, skipped: dict[str, int] | None = None) -> Iterator[Trajectory]:
    files = sorted((raw / tag).rglob("*.parquet"))
    if not files:
        raise SystemExit(f"{tag}: no parquet under {raw / tag}; run scripts/download_data.py first")
    n = 0
    for f in files:
        for t in CONVERTERS[tag](f, skipped=skipped):
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


class RunningStats:
    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.source: str | None = None
        self.n = 0
        self.dup = 0
        self.pos = 0
        self.task_outcomes: dict[str, list[bool]] = defaultdict(list)
        self.repos: set[str] = set()
        self.steps: list[int] = []
        self.steps_success: list[int] = []
        self.steps_failure: list[int] = []
        self.policy: Counter[str] = Counter()
        self.scaffold: Counter[str] = Counter()
        self.exit: Counter[str] = Counter()
        self.split: Counter[str] = Counter()
        self.thought_steps = 0
        self.total_steps = 0
        self.verified_hits = 0

    def add(self, t: Trajectory) -> None:
        self.source = self.source or t.meta.get("source")
        self.n += 1
        self.pos += int(t.outcome)
        self.task_outcomes[t.task_id].append(t.outcome)
        self.repos.add(repo_key(t.repo_or_site))
        n = len(t.steps)
        self.steps.append(n)
        (self.steps_success if t.outcome else self.steps_failure).append(n)
        self.policy[t.policy_model] += 1
        self.scaffold[t.scaffold] += 1
        self.exit[str(t.meta.get("exit_status"))] += 1
        self.split[t.meta["split"]] += 1
        self.thought_steps += sum(1 for s in t.steps if s.thought)
        self.total_steps += n
        self.verified_hits += int(t.task_id in verified_ids())

    def report(self, skipped: dict[str, int], seconds: float) -> dict:
        mixed = sum(1 for v in self.task_outcomes.values() if any(v) and not all(v))
        n_tasks = len(self.task_outcomes)
        return {
            "tag": self.tag,
            "source": self.source,
            "n_trajectories": self.n,
            "n_dropped_duplicates": self.dup,
            "n_skipped": skipped,
            "outcome_positive": self.pos,
            "outcome_rate": round(self.pos / max(1, self.n), 4),
            "n_tasks": n_tasks,
            "runs_per_task_mean": round(self.n / max(1, n_tasks), 2),
            "tasks_with_mixed_outcomes": mixed,
            "tasks_with_mixed_outcomes_pct": round(100 * mixed / max(1, n_tasks), 1),
            "n_repos": len(self.repos),
            "steps_mean": round(statistics.fmean(self.steps), 1) if self.steps else 0,
            "steps_median": statistics.median(self.steps) if self.steps else 0,
            "steps_max": max(self.steps) if self.steps else 0,
            "steps_mean_success": round(statistics.fmean(self.steps_success), 1) if self.steps_success else None,
            "steps_mean_failure": round(statistics.fmean(self.steps_failure), 1) if self.steps_failure else None,
            "policy_models": dict(self.policy),
            "scaffolds": dict(self.scaffold),
            "exit_status": dict(self.exit.most_common(12)),
            "steps_with_thought_pct": round(100 * self.thought_steps / max(1, self.total_steps), 1),
            "splits": dict(self.split),
            "verified_instance_hits": self.verified_hits,
            "verified_holdout_trajectories": self.split.get(VERIFIED_HOLDOUT, 0),
            "seconds": round(seconds, 1),
        }


def convert(tag: str, limit: int | None, raw: Path, out_path: Path) -> dict:
    t0 = time.time()
    seen: set[str] = set()
    skipped: dict[str, int] = {}
    stats = RunningStats(tag)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="\n") as f:
        for t in iter_raw(tag, limit, raw, skipped):
            h = content_hash(t)
            if h in seen:
                stats.dup += 1
                continue
            seen.add(h)
            t = t.model_copy(update={"meta": {**t.meta, "split": split_for(t)}})
            f.write(t.to_json())
            f.write("\n")
            stats.add(t)
            if stats.n % 5000 == 0:
                print(f"  {tag}: {stats.n} trajectories, {time.time() - t0:.0f}s", flush=True)
    return stats.report(skipped, time.time() - t0)


def split_table_from_unified(out_dir: Path) -> dict[str, str]:
    """Rebuild the frozen repo table from every full unified file present, so the table is
    the union across datasets no matter how many invocations produced them."""
    table: dict[str, str] = {}
    for f in sorted(out_dir.glob("*.jsonl")):
        if ".limit" in f.name:
            continue
        with f.open("rb") as fh:
            for line in fh:
                if not line.strip():
                    continue
                head = json.loads(line)
                repo = repo_key(head["repo_or_site"])
                if repo not in table:
                    table[repo] = VERIFIED_HOLDOUT if repo in verified_repos() else assign_split(repo)
    return dict(sorted(table.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--limit", type=int, default=None, help="stop after N trajectories per dataset (smoke test)")
    ap.add_argument("--raw", type=Path, default=RAW, help="raw shard root (default data/raw)")
    args = ap.parse_args()
    tags = [t for t in args.only.split(",") if t] or list(CONVERTERS)

    for tag in tags:
        suffix = f".limit{args.limit}" if args.limit else ""
        stats = convert(tag, args.limit, args.raw, OUT / f"{tag}{suffix}.jsonl")
        (OUT / f"{tag}{suffix}.stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(stats, indent=1))
        if stats["verified_instance_hits"] and stats["verified_holdout_trajectories"] < stats["verified_instance_hits"]:
            raise SystemExit(f"{tag}: Verified instances found outside the holdout bucket")

    if not args.limit:
        table = split_table_from_unified(OUT)
        write_split_table(table, SPLITS / "repo_split.json")
        print(f"split table: {len(table)} repos over all unified files -> {SPLITS / 'repo_split.json'}")


if __name__ == "__main__":
    main()
