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
    """Memory-light: pass 1 keeps only (offset, prefix_len, meta) per record; pass 2 loads the chosen ones."""
    index: dict[str, list[tuple[int, int]]] = defaultdict(list)  # traj -> [(offset, prefix_len)]
    head_meta: dict[str, dict] = {}
    with path.open("rb") as f:
        pos = f.tell()
        for line in f:
            if line.strip():
                r = json.loads(line)
                if strict_ok(r):
                    m = r["_meta"]
                    index[m["traj_id"]].append((pos, m["prefix_len"]))
                    head_meta.setdefault(m["traj_id"], {"task_id": m["task_id"], "policy_model": m["policy_model"], "outcome": bool(m["outcome"])})
            pos = f.tell()
    by_traj: dict[str, list[dict]] = {}

    def load_records(offsets: list[int]) -> list[dict]:
        out = []
        with path.open("rb") as f:
            for off in offsets:
                f.seek(off)
                out.append(json.loads(f.readline()))
        return out

    # select trajectories first, then load only their records
    outcomes_by_group: dict[tuple[str, str], set[bool]] = defaultdict(set)
    for tid, hm in head_meta.items():
        outcomes_by_group[(hm["task_id"], hm["policy_model"])].add(hm["outcome"])
    mixed_ids = [tid for tid, hm in head_meta.items() if len(outcomes_by_group[(hm["task_id"], hm["policy_model"])]) == 2]
    rest_ids = [tid for tid in head_meta if tid not in set(mixed_ids)]
    rng.shuffle(mixed_ids)
    rng.shuffle(rest_ids)
    chosen: list[tuple[str, list[int]]] = []
    total = 0
    for tid in mixed_ids + rest_ids:
        if total >= n_records:
            break
        entries = sorted(index[tid], key=lambda e: e[1])
        if prefixes_per_traj and len(entries) > prefixes_per_traj:
            entries = rng.sample(entries, prefixes_per_traj)
        chosen.append((tid, [off for off, _ in entries]))
        total += len(entries)
    out: list[dict] = []
    for tid, offs in chosen:
        out.extend(load_records(offs))
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


def write_streamed(src_paths: list[Path], out_path: Path, rng: random.Random, transform=None) -> dict:
    """Shuffle by byte offset and write one record at a time: the full test set never sits in memory."""
    offsets: list[tuple[Path, int]] = []
    for sp in src_paths:
        with sp.open("rb") as f:
            pos = f.tell()
            for line in f:
                if line.strip() and strict_ok(json.loads(line)):
                    offsets.append((sp, pos))
                pos = f.tell()
    rng.shuffle(offsets)
    q_counts: Counter[str] = Counter()
    pos_n = 0
    tokens = 0
    trajs: set[str] = set()
    handles = {sp: sp.open("rb") for sp in src_paths}
    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        for sp, off in offsets:
            handles[sp].seek(off)
            r = json.loads(handles[sp].readline())
            if transform:
                r = transform(r)
            out.write(json.dumps(r, ensure_ascii=False) + "\n")
            q_counts.update(r["questions"].keys())
            pos_n += int(bool(r["questions"]["p_success"]["label"]))
            tokens += r["_meta"]["state_tokens"]
            trajs.add(r["_meta"]["traj_id"])
    for h in handles.values():
        h.close()
    return {"records": len(offsets), "positive_rate": round(pos_n / max(1, len(offsets)), 4), "questions": dict(q_counts),
            "state_tokens_total": tokens, "trajectories": len(trajs)}


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
    def drop_progress(recs: list[dict]) -> list[dict]:
        # the progress rules read coding observations (tests, edits, files); on tool-use transcripts the
        # label is a constant, so the question is masked for extra domains rather than taught as a constant
        for r in recs:
            r["questions"].pop("progress", None)
        return recs

    for tag, n_tr, _ in extras:
        train.extend(drop_progress(pick_trajectories(EX / f"{tag}.train.jsonl", n_tr, args.prefixes_per_traj, rng)))
    train, pair_stats = make_pairs(train, args.pair_window, rng)
    stats["files"]["train"] = {**write(train, OUT / "train.jsonl"), **pair_stats}
    print("train", stats["files"]["train"], flush=True)

    dev: list[dict] = []
    for ds in DATASETS:
        dev.extend(pick_trajectories(EX / f"{ds}.dev.jsonl", args.dev_per_dataset, 0, rng))
    for tag, _, n_dev in extras:
        dev.extend(drop_progress(pick_trajectories(EX / f"{tag}.dev.jsonl", n_dev, 0, rng)))
    rng.shuffle(dev)
    stats["files"]["dev"] = write(dev, OUT / "dev.jsonl")
    print("dev", stats["files"]["dev"], flush=True)

    stats["files"]["test"] = write_streamed([EX / f"{ds}.test.jsonl" for ds in DATASETS], OUT / "test.jsonl", random.Random(args.seed + 1))
    print("test", stats["files"]["test"], flush=True)
    for tag, _, _ in extras:
        def _drop(r: dict) -> dict:
            r["questions"].pop("progress", None)
            return r
        stats["files"][f"test_{tag}"] = write_streamed([EX / f"{tag}.test.jsonl"], OUT / f"test_{tag}.jsonl", random.Random(args.seed + 2), transform=_drop)
        print(f"test_{tag}", stats["files"][f"test_{tag}"], flush=True)
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
