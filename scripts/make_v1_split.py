"""Build the v1 (M4) files: all six questions per record, pairs for the ranking loss.

- Train: `--train-per-dataset` records per dataset, chosen trajectory-wise (seeded) with at most
  `--prefixes-per-traj` prefixes kept per trajectory (1 = maximal trajectory diversity).
- Pairs: within (task, policy) groups, each successful prefix is paired with a failed prefix whose
  prefix fraction is within `--pair-window`; both get `_meta.pair_id` and are written adjacently so a
  micro-batch of 2 holds the pair. Unpaired records are kept (no pair term).
- Dev and test: subsampled as in v0 (dev) or full (test), all questions, no pairs.

Writes data/v1/{train,dev,test}.jsonl and data/v1/stats.json.

    uv run --python 3.12 python scripts/make_v1_split.py --train-per-dataset 12000 --prefixes-per-traj 2 --dev-per-dataset 5000
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "data" / "examples"
OUT = ROOT / "data" / "v1"
DATASETS = ("swe_agent", "openhands")
FINISH_ACTIONS = {"submit", "finish"}
_LAST_ACTION = re.compile(r"^\$ (\S+)", re.M)


def last_visible_action(state: str) -> str:
    i = state.find("<recent>")
    acts = _LAST_ACTION.findall(state[i:] if i >= 0 else state)
    return acts[-1] if acts else ""


def stream(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def strict_ok(r: dict) -> bool:
    m = r["_meta"]
    return m["prefix_len"] < m["n_steps"] and last_visible_action(r["state"]) not in FINISH_ACTIONS


def pick_trajectories(path: Path, n_records: int, prefixes_per_traj: int, rng: random.Random) -> list[dict]:
    by_traj: dict[str, list[dict]] = defaultdict(list)
    for r in stream(path):
        if strict_ok(r):
            by_traj[r["_meta"]["traj_id"]].append(r)
    # Trajectories from (task, policy) groups with BOTH outcomes first, so the ranking loss gets pairs:
    # a group with only successes or only failures cannot form a success/failure pair.
    outcomes: dict[tuple[str, str], set[bool]] = defaultdict(set)
    for tid, recs in by_traj.items():
        m = recs[0]["_meta"]
        outcomes[(m["task_id"], m["policy_model"])].add(bool(m["outcome"]))
    mixed = [tid for tid, recs in by_traj.items() if len(outcomes[(recs[0]["_meta"]["task_id"], recs[0]["_meta"]["policy_model"])]) == 2]
    rest = [tid for tid in by_traj if tid not in set(mixed)]
    rng.shuffle(mixed)
    rng.shuffle(rest)
    ids = mixed + rest
    out: list[dict] = []
    for tid in ids:
        if len(out) >= n_records:
            break
        recs = sorted(by_traj[tid], key=lambda r: r["_meta"]["prefix_len"])
        if prefixes_per_traj and len(recs) > prefixes_per_traj:
            recs = rng.sample(recs, prefixes_per_traj)
        out.extend(recs)
    return out


def make_pairs(recs: list[dict], window: float, rng: random.Random) -> tuple[list[dict], dict]:
    """Assign pair ids and return records ordered so each pair is adjacent."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in recs:
        m = r["_meta"]
        groups[(m["task_id"], m["policy_model"])].append(r)
    paired: list[list[dict]] = []
    singles: list[dict] = []
    n_pairs = 0
    for key, rs in groups.items():
        succ = [r for r in rs if r["_meta"]["outcome"]]
        fail = [r for r in rs if not r["_meta"]["outcome"]]
        rng.shuffle(succ)
        rng.shuffle(fail)
        used: set[str] = set()
        for s in succ:
            cands = [f for f in fail if f["_meta"]["id"] not in used and abs(f["_meta"]["prefix_frac"] - s["_meta"]["prefix_frac"]) <= window]
            if not cands:
                singles.append(s)
                continue
            f = min(cands, key=lambda x: abs(x["_meta"]["prefix_frac"] - s["_meta"]["prefix_frac"]))
            used.add(f["_meta"]["id"])
            pid = f"pair/{key[0]}/{n_pairs}"
            s["_meta"]["pair_id"] = pid
            f["_meta"]["pair_id"] = pid
            paired.append([s, f] if rng.random() < 0.5 else [f, s])
            n_pairs += 1
        singles.extend(f for f in fail if f["_meta"]["id"] not in used)
    units: list[list[dict]] = paired + [[r] for r in singles]
    rng.shuffle(units)
    ordered = [r for u in units for r in u]
    return ordered, {"pairs": n_pairs, "paired_records": 2 * n_pairs, "single_records": len(singles),
                     "groups_with_both_outcomes": sum(1 for rs in groups.values() if any(r["_meta"]["outcome"] for r in rs) and not all(r["_meta"]["outcome"] for r in rs))}


def write(recs: list[dict], path: Path) -> dict:
    q_counts: Counter[str] = Counter()
    pos = 0
    tokens = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            q_counts.update(r["questions"].keys())
            pos += int(bool(r["questions"]["p_success"]["label"]))
            tokens += r["_meta"]["state_tokens"]
    return {"records": len(recs), "positive_rate": round(pos / max(1, len(recs)), 4), "questions": dict(q_counts), "state_tokens_total": tokens,
            "trajectories": len({r["_meta"]["traj_id"] for r in recs})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-per-dataset", type=int, default=12000)
    ap.add_argument("--dev-per-dataset", type=int, default=5000)
    ap.add_argument("--prefixes-per-traj", type=int, default=2, help="0 = keep all prefixes of a chosen trajectory")
    ap.add_argument("--pair-window", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--extra", default="", help="extra domains as tag:train_records:dev_records, comma-separated, e.g. tau2:3000:1000; "
                                                "their test records go to a separate test_<tag>.jsonl so the SWE test stays comparable to v0")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    OUT.mkdir(parents=True, exist_ok=True)
    stats: dict = {"args": vars(args), "files": {}}
    extras = [(p.split(":")[0], int(p.split(":")[1]), int(p.split(":")[2])) for p in args.extra.split(",") if p]

    train: list[dict] = []
    for ds in DATASETS:
        train.extend(pick_trajectories(EX / f"{ds}.train.jsonl", args.train_per_dataset, args.prefixes_per_traj, rng))
    for tag, n_tr, _ in extras:
        train.extend(pick_trajectories(EX / f"{tag}.train.jsonl", n_tr, args.prefixes_per_traj, rng))
    train, pair_stats = make_pairs(train, args.pair_window, rng)
    stats["files"]["train"] = {**write(train, OUT / "train.jsonl"), **pair_stats}
    print("train", stats["files"]["train"], flush=True)

    dev: list[dict] = []
    for ds in DATASETS:
        dev.extend(pick_trajectories(EX / f"{ds}.dev.jsonl", args.dev_per_dataset, 0, rng))
    for tag, _, n_dev in extras:
        dev.extend(pick_trajectories(EX / f"{tag}.dev.jsonl", n_dev, 0, rng))
    rng.shuffle(dev)
    stats["files"]["dev"] = write(dev, OUT / "dev.jsonl")
    print("dev", stats["files"]["dev"], flush=True)

    test: list[dict] = []
    for ds in DATASETS:
        test.extend(r for r in stream(EX / f"{ds}.test.jsonl") if strict_ok(r))
    random.Random(args.seed + 1).shuffle(test)
    stats["files"]["test"] = write(test, OUT / "test.jsonl")
    print("test", stats["files"]["test"], flush=True)
    for tag, _, _ in extras:
        extra_test = [r for r in stream(EX / f"{tag}.test.jsonl") if strict_ok(r)]
        random.Random(args.seed + 2).shuffle(extra_test)
        stats["files"][f"test_{tag}"] = write(extra_test, OUT / f"test_{tag}.jsonl")
        print(f"test_{tag}", stats["files"][f"test_{tag}"], flush=True)
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
