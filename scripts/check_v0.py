"""Pre-launch checks on the v0 files: leakage, repo overlap, balance, and printed examples.

    uv run --python 3.12 python scripts/check_v0.py [--dir data/v0] [--examples 3]
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FINISH = {"submit", "finish"}
HARNESS = re.compile(r"\bFAIL_TO_PASS\b|\bPASS_TO_PASS\b|\bRESOLVED\b|\bUNRESOLVED\b|instance\s+(?:un)?resolved|eval_logs|model_patch|generated_patch", re.I)
CONFIDENT = re.compile(r"all tests pass|tests? (?:now )?pass(?:es|ed)? successfully|the fix works|successfully (?:fixed|implemented|resolved)|issue (?:is|has been) (?:fixed|resolved)|great, this works", re.I)
PYTEST = re.compile(r"\b\d+ passed\b")
LAST_ACTION = re.compile(r"^\$ (.*)$", re.M)


def stream(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def recent_block(state: str) -> str:
    i = state.find("<recent>")
    return state[i:] if i >= 0 else state


def check_file(path: Path, rng: random.Random, n_examples: int) -> dict:
    n = 0
    pos = 0
    full_prefix = 0
    one_step = 0
    last_finish = Counter()
    harness_hits = 0
    confident_hits = Counter()
    pytest_hits = Counter()
    repos: set[str] = set()
    tasks: set[str] = set()
    per_ds = defaultdict(lambda: [0, 0])
    ex_pos: list[dict] = []
    ex_neg: list[dict] = []
    seen_p = seen_n = 0
    for r in stream(path):
        n += 1
        m = r["_meta"]
        y = bool(r["questions"]["p_success"]["label"])
        pos += y
        per_ds[m["source"].split("/")[-1]][0] += 1
        per_ds[m["source"].split("/")[-1]][1] += y
        repos.add(m["repo"])
        tasks.add(m["task_id"])
        if m["prefix_len"] >= m["n_steps"]:
            full_prefix += 1
            if m["n_steps"] == 1:
                one_step += 1
        state = r["state"]
        acts = LAST_ACTION.findall(recent_block(state))
        last = acts[-1].split(" ")[0] if acts else ""
        if last in FINISH:
            last_finish[y] += 1
        if HARNESS.search(state):
            harness_hits += 1
        if CONFIDENT.search(state):
            confident_hits[y] += 1
        if PYTEST.search(state):
            pytest_hits[y] += 1
        # reservoir sample of examples
        if y:
            seen_p += 1
            if len(ex_pos) < n_examples:
                ex_pos.append(r)
            elif rng.random() < n_examples / seen_p:
                ex_pos[rng.randrange(n_examples)] = r
        else:
            seen_n += 1
            if len(ex_neg) < n_examples:
                ex_neg.append(r)
            elif rng.random() < n_examples / seen_n:
                ex_neg[rng.randrange(n_examples)] = r
    return {
        "n": n, "pos": pos, "pos_rate": pos / max(1, n),
        "per_dataset": {k: {"n": v[0], "pos_rate": round(v[1] / max(1, v[0]), 4)} for k, v in per_ds.items()},
        "prefix_equals_full": full_prefix, "of_which_one_step_trajectories": one_step,
        "last_action_is_submit_or_finish": dict(last_finish),
        "harness_pattern_hits": harness_hits,
        "confident_phrase_hits_by_label": dict(confident_hits),
        "pytest_summary_hits_by_label": dict(pytest_hits),
        "repos": repos, "tasks": tasks, "ex_pos": ex_pos, "ex_neg": ex_neg,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, default=ROOT / "data" / "v0")
    ap.add_argument("--examples", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    res = {sp: check_file(args.dir / f"{sp}.jsonl", rng, args.examples) for sp in ("train", "dev", "test")}

    print("## Balance")
    for sp, r in res.items():
        print(f"- {sp}: {r['n']} records, pass rate {100 * r['pos_rate']:.1f}%  per dataset: " + ", ".join(f"{k} {v['n']} @ {100 * v['pos_rate']:.1f}%" for k, v in r["per_dataset"].items()))

    print("\n## Leakage")
    for sp, r in res.items():
        print(f"- {sp}: prefix == full trajectory: {r['prefix_equals_full']} (one-step trajectories: {r['of_which_one_step_trajectories']}); "
              f"last visible action is submit/finish: {r['last_action_is_submit_or_finish'] or 0}; harness-pattern hits: {r['harness_pattern_hits']}; "
              f"confident-phrase hits by label: {r['confident_phrase_hits_by_label'] or 0}; pytest-summary hits by label: {r['pytest_summary_hits_by_label'] or 0}")

    print("\n## Repo / task overlap")
    for a, b in (("train", "test"), ("train", "dev"), ("dev", "test")):
        print(f"- {a} vs {b}: shared repos {len(res[a]['repos'] & res[b]['repos'])}, shared tasks {len(res[a]['tasks'] & res[b]['tasks'])}  "
              f"({len(res[a]['repos'])} / {len(res[b]['repos'])} repos)")

    print("\n## Examples (train)")
    for label, key in (("PASS", "ex_pos"), ("FAIL", "ex_neg")):
        for r in res["train"][key]:
            m = r["_meta"]
            s = r["state"]
            print(f"\n=== {label}  id={m['id']}  prefix {m['prefix_len']}/{m['n_steps']} steps  source={m['source'].split('/')[-1]}  policy shown={m['policy_shown']}")
            head = s[: s.find("<hist>") if "<hist>" in s else 600]
            print(head[:700].rstrip())
            rec = recent_block(s)
            print("... [hist omitted] ..." if "<hist>" in s else "")
            print(rec[-2200:] if len(rec) > 2200 else rec)


if __name__ == "__main__":
    main()
