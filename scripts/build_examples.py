"""Build kev-shaped example records from unified trajectories, one file per split.

Pass 1 indexes byte offsets per task and computes task statistics. Pass 2 hands each task
(all its runs) to a worker that builds the records; the parent writes them to the split
files. Memory stays flat regardless of dataset size.

    uv run --python 3.12 --with pydantic --with tokenizers python scripts/build_examples.py \
        --in data/unified/openhands.jsonl --out data/examples/openhands \
        --tokenizer .scratch/tok/qwen3.5-2b/tokenizer.json --budget 4096 --workers 4 \
        [--variant clean] [--limit N] [--splits train,dev,test] [--max-train-traj N]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.data.candidates import TaskIndex  # noqa: E402
from agent_compass.data.examples import BuildConfig, build_records  # noqa: E402
from agent_compass.data.labels import TaskStats  # noqa: E402
from agent_compass.data.schema import Trajectory  # noqa: E402
from agent_compass.data.state import StateConfig, chars_per_token_counter, hf_tokenizer_counter  # noqa: E402


def index_file(path: Path) -> tuple[dict[str, list[int]], dict[str, TaskStats], dict[str, str]]:
    """task -> byte offsets of its runs; task -> stats; traj_id -> split (for subsampling)."""
    offsets: dict[str, list[int]] = defaultdict(list)
    n: Counter[str] = Counter()
    s: Counter[str] = Counter()
    splits: dict[str, str] = {}
    with path.open("rb") as f:
        pos = f.tell()
        for line in f:
            if line.strip():
                head = json.loads(line)
                offsets[head["task_id"]].append(pos)
                n[head["task_id"]] += 1
                s[head["task_id"]] += int(head["outcome"])
                splits[head["traj_id"]] = head["meta"].get("split")
            pos = f.tell()
    return dict(offsets), {k: TaskStats(n[k], s[k]) for k in n}, splits


_W: dict = {}


def _init(inp: str, tokenizer: str | None, cfg_kwargs: dict) -> None:
    _W["f"] = open(inp, "rb")
    _W["count"] = hf_tokenizer_counter(tokenizer) if tokenizer else chars_per_token_counter()
    _W["cfg"] = BuildConfig(state=StateConfig(**cfg_kwargs.pop("state")), **cfg_kwargs)


def _work(job: tuple[str, list[int], TaskStats | None, set[str], set[str]]) -> tuple[list[tuple[str, str]], dict]:
    task_id, offs, stats, wanted, keep_ids = job
    f = _W["f"]
    runs = []
    for off in offs:
        f.seek(off)
        runs.append(Trajectory.from_json(f.readline().decode("utf-8")))
    out: list[tuple[str, str]] = []
    meta = {"trajectories": 0, "records": Counter(), "questions": Counter(), "labels": defaultdict(Counter), "tiers": Counter(), "tokens": []}
    index = TaskIndex(runs) if len(runs) > 1 else None  # action families derived once per task
    for traj in runs:
        split = traj.meta.get("split")
        if split not in wanted or (keep_ids and traj.traj_id not in keep_ids):
            continue
        meta["trajectories"] += 1
        for rec in build_records(traj, runs, stats, _W["cfg"], _W["count"], index=index):
            out.append((split, json.dumps(rec, ensure_ascii=False)))
            meta["records"][split] += 1
            meta["tokens"].append(rec["_meta"]["state_tokens"])
            for qid, q in rec["questions"].items():
                meta["questions"][qid] += 1
                meta["labels"][qid][str(q["label"]) if qid != "best_next" else "set"] += 1
            if rec["_meta"]["best_next"]:
                meta["tiers"][rec["_meta"]["best_next"]["tier"]] += 1
    return out, meta


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path, help="output prefix; writes <out>.<split>.jsonl and <out>.stats.json")
    ap.add_argument("--tokenizer", type=Path, default=None, help="tokenizer.json; default = 3.5 chars/token proxy")
    ap.add_argument("--budget", type=int, default=4096)
    ap.add_argument("--recent", type=int, default=8)
    ap.add_argument("--variant", default="clean", choices=["clean", "thoughts", "fake_confidence"])
    ap.add_argument("--policy-dropout", type=float, default=0.3)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=None, help="tasks to process (smoke test)")
    ap.add_argument("--splits", default="train,dev,test", help="which splits to emit")
    ap.add_argument("--max-train-traj", type=int, default=None, help="subsample the train split to this many trajectories (seeded)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    wanted = set(args.splits.split(","))
    t0 = time.time()
    offsets, stats, splits = index_file(args.inp)
    print(f"indexed {len(splits)} trajectories over {len(offsets)} tasks in {time.time() - t0:.1f}s", flush=True)

    keep_ids: set[str] = set()
    if args.max_train_traj:
        train_ids = sorted(t for t, sp in splits.items() if sp == "train")
        random.Random(args.seed).shuffle(train_ids)
        keep_ids = set(train_ids[: args.max_train_traj]) | {t for t, sp in splits.items() if sp != "train"}
        print(f"subsampled train to {min(args.max_train_traj, len(train_ids))} of {len(train_ids)} trajectories", flush=True)

    cfg_kwargs = {
        "state": {"budget": args.budget, "recent_steps": args.recent},
        "k_candidates": args.k,
        "policy_dropout": args.policy_dropout,
        "variant": args.variant,
        "seed": args.seed,
    }
    tasks = list(offsets.items())
    if args.limit:
        tasks = tasks[: args.limit]
    jobs = [(task_id, offs, stats.get(task_id), wanted, keep_ids) for task_id, offs in tasks]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    files = {sp: (args.out.parent / f"{args.out.name}.{sp}.jsonl").open("w", encoding="utf-8", newline="\n") for sp in wanted}
    agg = {"trajectories": 0, "records": Counter(), "questions": Counter(), "labels": defaultdict(Counter), "tiers": Counter(), "tokens": []}
    t1 = time.time()
    done = 0

    def consume(result: tuple[list[tuple[str, str]], dict]) -> None:
        nonlocal done
        out, meta = result
        for split, line in out:
            files[split].write(line + "\n")
        agg["trajectories"] += meta["trajectories"]
        agg["records"].update(meta["records"])
        agg["questions"].update(meta["questions"])
        for qid, c in meta["labels"].items():
            agg["labels"][qid].update(c)
        agg["tiers"].update(meta["tiers"])
        agg["tokens"].extend(meta["tokens"])
        done += 1
        if done % 200 == 0:
            el = time.time() - t1
            print(f"  {done}/{len(jobs)} tasks, {agg['trajectories']} trajectories, {sum(agg['records'].values())} records, {el:.0f}s, eta {el / done * (len(jobs) - done):.0f}s", flush=True)

    init_args = (str(args.inp), str(args.tokenizer) if args.tokenizer else None, cfg_kwargs)
    if args.workers > 1:
        with Pool(args.workers, initializer=_init, initargs=init_args) as pool:
            for result in pool.imap_unordered(_work, jobs, chunksize=4):
                consume(result)
    else:
        _init(*init_args)
        for job in jobs:
            consume(_work(job))
    for fh in files.values():
        fh.close()

    tokens = sorted(agg["tokens"])
    pct = lambda p: tokens[min(len(tokens) - 1, int(p * len(tokens)))] if tokens else 0  # noqa: E731
    report = {
        "input": str(args.inp),
        "variant": args.variant,
        "budget": args.budget,
        "tokenizer": str(args.tokenizer) if args.tokenizer else "chars/3.5",
        "tasks": len(jobs),
        "trajectories": agg["trajectories"],
        "records": dict(agg["records"]),
        "questions_present": dict(agg["questions"]),
        "label_histograms": {k: dict(v) for k, v in agg["labels"].items()},
        "best_next_tiers": dict(agg["tiers"]),
        "state_tokens_p50_p90_max": [pct(0.5), pct(0.9), tokens[-1] if tokens else 0],
        "seconds": round(time.time() - t0, 1),
    }
    (args.out.parent / f"{args.out.name}.stats.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
