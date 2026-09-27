"""Build the v0 (M3) files: p_success only, both datasets mixed, sized for a ~2 h 2B run.

Writes data/v0/{train,dev,test}.jsonl in kev's record format with a single `p_success`
question per record, plus data/v0/stats.json. Train and dev are subsampled per dataset
(seeded, trajectory-level so all prefixes of a trajectory stay together); test keeps every
record so the held-out-repository number is final.

    uv run --python 3.12 python scripts/make_v0_split.py --train-per-dataset 25000 --dev-per-dataset 5000
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "data" / "examples"
OUT = ROOT / "data" / "v0"
DATASETS = ("swe_agent", "openhands")


def stream(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def keep_only(rec: dict, qids: tuple[str, ...]) -> dict:
    return {"state": rec["state"], "questions": {k: v for k, v in rec["questions"].items() if k in qids}, "_meta": rec["_meta"]}


def subsample_by_trajectory(path: Path, n_records: int, seed: int) -> list[dict]:
    """Pick whole trajectories at random until about n_records records are collected."""
    by_traj: dict[str, list[dict]] = defaultdict(list)
    for r in stream(path):
        by_traj[r["_meta"]["traj_id"]].append(r)
    ids = sorted(by_traj)
    random.Random(seed).shuffle(ids)
    out: list[dict] = []
    for tid in ids:
        if len(out) >= n_records:
            break
        out.extend(by_traj[tid])
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-per-dataset", type=int, default=25000)
    ap.add_argument("--dev-per-dataset", type=int, default=5000)
    ap.add_argument("--questions", default="p_success")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    qids = tuple(args.questions.split(","))
    OUT.mkdir(parents=True, exist_ok=True)
    stats: dict = {"questions": list(qids), "seed": args.seed, "files": {}}

    for split, per in (("train", args.train_per_dataset), ("dev", args.dev_per_dataset), ("test", None)):
        n = 0
        pos = 0
        per_ds: Counter[str] = Counter()
        tokens: list[int] = []
        all_recs: list[dict] = []
        for ds in DATASETS:
            src = EX / f"{ds}.{split}.jsonl"
            all_recs.extend(subsample_by_trajectory(src, per, args.seed) if per else stream(src))
        random.Random(args.seed + 1).shuffle(all_recs)  # every file is shuffled so a head cut is a fair mix of both datasets
        with (OUT / f"{split}.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for r in all_recs:
                if not all(q in r["questions"] for q in qids):
                    continue
                f.write(json.dumps(keep_only(r, qids), ensure_ascii=False) + "\n")
                n += 1
                pos += int(bool(r["questions"]["p_success"]["label"]))
                per_ds[r["_meta"]["source"].split("/")[-1]] += 1
                tokens.append(r["_meta"]["state_tokens"])
        tokens.sort()
        stats["files"][split] = {
            "records": n,
            "per_dataset": dict(per_ds),
            "positive_rate": round(pos / max(1, n), 4),
            "state_tokens_p50_p90": [tokens[len(tokens) // 2], tokens[int(0.9 * len(tokens))]] if tokens else None,
            "state_tokens_total": sum(tokens),
        }
        print(split, stats["files"][split], flush=True)
    (OUT / "stats.json").write_text(json.dumps(stats, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
