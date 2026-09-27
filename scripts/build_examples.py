"""Build kev-shaped example records from unified trajectories, one file per split.

Streams the unified JSONL twice: pass 1 indexes byte offsets per task and computes task
statistics; pass 2 builds records, loading the other runs of a task by offset when
best_next candidates are needed. Memory stays flat regardless of dataset size.

    uv run --python 3.12 --with pydantic --with tokenizers python scripts/build_examples.py \
        --in data/unified/openhands.jsonl --out data/examples/openhands \
        --tokenizer .scratch/tok/qwen3.5-2b/tokenizer.json --budget 4096 [--variant clean] [--limit N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_compass.data.examples import BuildConfig, build_records  # noqa: E402
from agent_compass.data.labels import TaskStats  # noqa: E402
from agent_compass.data.schema import Trajectory  # noqa: E402
from agent_compass.data.state import StateConfig, chars_per_token_counter, hf_tokenizer_counter  # noqa: E402


def index_file(path: Path) -> tuple[dict[str, list[int]], dict[str, TaskStats]]:
    offsets: dict[str, list[int]] = defaultdict(list)
    n: Counter[str] = Counter()
    s: Counter[str] = Counter()
    with path.open("rb") as f:
        pos = f.tell()
        for line in f:
            if line.strip():
                head = json.loads(line)
                offsets[head["task_id"]].append(pos)
                n[head["task_id"]] += 1
                s[head["task_id"]] += int(head["outcome"])
            pos = f.tell()
    return dict(offsets), {k: TaskStats(n[k], s[k]) for k in n}


def load_at(f, offsets: list[int]) -> list[Trajectory]:
    out = []
    for off in offsets:
        f.seek(off)
        out.append(Trajectory.from_json(f.readline().decode("utf-8")))
    return out


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
    ap.add_argument("--limit", type=int, default=None, help="trajectories to process (smoke test)")
    ap.add_argument("--splits", default="train,dev,test", help="which splits to emit")
    args = ap.parse_args()

    count = hf_tokenizer_counter(args.tokenizer) if args.tokenizer else chars_per_token_counter()
    cfg = BuildConfig(
        state=StateConfig(budget=args.budget, recent_steps=args.recent),
        k_candidates=args.k,
        policy_dropout=args.policy_dropout,
        variant=args.variant,
    )
    wanted = set(args.splits.split(","))

    t0 = time.time()
    offsets, stats = index_file(args.inp)
    print(f"indexed {sum(len(v) for v in offsets.values())} trajectories over {len(offsets)} tasks in {time.time() - t0:.1f}s")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    files = {sp: (args.out.parent / f"{args.out.name}.{sp}.jsonl").open("w", encoding="utf-8", newline="\n") for sp in wanted}
    counts: Counter[str] = Counter()
    q_counts: Counter[str] = Counter()
    tiers: Counter[str] = Counter()
    label_hist: dict[str, Counter] = defaultdict(Counter)
    tokens: list[int] = []
    done = 0
    t1 = time.time()
    with args.inp.open("rb") as f:
        for task_id, offs in offsets.items():
            runs = load_at(f, offs)
            for traj in runs:
                split = traj.meta.get("split")
                if split not in wanted:
                    continue
                for rec in build_records(traj, runs, stats.get(task_id), cfg, count):
                    files[split].write(json.dumps(rec, ensure_ascii=False) + "\n")
                    counts[split] += 1
                    tokens.append(rec["_meta"]["state_tokens"])
                    for qid, q in rec["questions"].items():
                        q_counts[qid] += 1
                        label_hist[qid][str(q["label"]) if qid != "best_next" else "set"] += 1
                    if rec["_meta"]["best_next"]:
                        tiers[rec["_meta"]["best_next"]["tier"]] += 1
                done += 1
                if done % 500 == 0:
                    print(f"  {done} trajectories, {sum(counts.values())} records, {(time.time() - t1) / done:.2f}s/traj", flush=True)
                if args.limit and done >= args.limit:
                    break
            if args.limit and done >= args.limit:
                break
    for fh in files.values():
        fh.close()

    tokens.sort()
    pct = lambda p: tokens[min(len(tokens) - 1, int(p * len(tokens)))] if tokens else 0  # noqa: E731
    report = {
        "input": str(args.inp),
        "variant": args.variant,
        "budget": args.budget,
        "tokenizer": str(args.tokenizer) if args.tokenizer else "chars/3.5",
        "trajectories": done,
        "records": dict(counts),
        "questions_present": dict(q_counts),
        "label_histograms": {k: dict(v) for k, v in label_hist.items()},
        "best_next_tiers": dict(tiers),
        "state_tokens_p50_p90_max": [pct(0.5), pct(0.9), tokens[-1] if tokens else 0],
        "seconds": round(time.time() - t0, 1),
    }
    (args.out.parent / f"{args.out.name}.stats.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
