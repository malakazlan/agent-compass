"""Label QA report: label distributions plus a readable sample of rule-based labels.

Reads example records (output of build_examples.py) and writes a markdown report with
label histograms per split, stuck/progress rule frequencies, best_next coverage, and N
random stuck-positive / progress samples showing the recent steps and the rule that
fired, so a human (or a judge model) can check precision.

    uv run --python 3.12 python scripts/label_qa.py --in data/examples/swe_agent.dev.jsonl --out docs/label_qa_swe_agent.md --n 25
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, type=Path, nargs="+")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--n", type=int, default=25, help="samples per category")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-records", type=int, default=200000)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    hist: dict[str, Counter] = defaultdict(Counter)
    stuck_rules: Counter[str] = Counter()
    prog_rules: dict[int, Counter] = defaultdict(Counter)
    tiers: Counter[str] = Counter()
    frac_hist: Counter[str] = Counter()
    n = 0
    stuck_pos: list[dict] = []
    stuck_neg: list[dict] = []
    prog: dict[int, list[dict]] = defaultdict(list)
    esc: Counter[str] = Counter()
    p_by_frac: dict[str, Counter] = defaultdict(Counter)

    def reservoir(lst: list, item: dict, k: int, seen: int) -> None:
        if len(lst) < k:
            lst.append(item)
        else:
            j = rng.randint(0, seen)
            if j < k:
                lst[j] = item

    counts = Counter()
    for path in args.inp:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if n >= args.max_records:
                    break
                r = json.loads(line)
                n += 1
                m = r["_meta"]
                q = r["questions"]
                for qid, qq in q.items():
                    hist[qid][str(qq["label"]) if qid != "best_next" else "present"] += 1
                for rule in m.get("stuck_reasons", []):
                    stuck_rules[rule.split(" x")[0]] += 1
                pr = q["progress"]["label"]
                for rule in m.get("progress_reasons", []):
                    prog_rules[pr][rule.split(" ", 2)[0] + " " + rule.split(" ", 2)[1] if " " in rule else rule] += 1
                if m.get("best_next"):
                    tiers[m["best_next"]["tier"]] += 1
                bucket = f"{int(m['prefix_frac'] * 4) * 25}-{int(m['prefix_frac'] * 4) * 25 + 25}%"
                frac_hist[bucket] += 1
                p_by_frac[bucket][str(q["p_success"]["label"])] += 1
                if "escalate" in q:
                    esc[str(q["escalate"]["label"])] += 1
                item = {"id": m["id"], "state": r["state"], "stuck_reasons": m.get("stuck_reasons"), "progress": pr,
                        "progress_reasons": m.get("progress_reasons"), "outcome": m["outcome"]}
                if q["stuck"]["label"]:
                    counts["sp"] += 1
                    reservoir(stuck_pos, item, args.n, counts["sp"])
                else:
                    counts["sn"] += 1
                    reservoir(stuck_neg, item, args.n, counts["sn"])
                counts[f"p{pr}"] += 1
                reservoir(prog[pr], item, max(5, args.n // 3), counts[f"p{pr}"])

    def recent_block(state: str, max_chars: int = 1800) -> str:
        i = state.find("<recent>")
        block = state[i:] if i >= 0 else state
        return block if len(block) <= max_chars else "..." + block[-max_chars:]

    lines = [f"# Label QA report", "", f"Inputs: {', '.join(str(p) for p in args.inp)}  ", f"Records read: {n}", ""]
    lines += ["## Label histograms", ""]
    for qid, c in hist.items():
        total = sum(c.values())
        lines.append(f"- **{qid}** (n={total}): " + ", ".join(f"{k}: {v} ({100 * v / total:.1f}%)" for k, v in sorted(c.items())))
    lines += ["", "## p_success by prefix position", ""]
    for b in sorted(p_by_frac, key=lambda s: int(s.split("-")[0])):
        c = p_by_frac[b]
        tot = sum(c.values())
        lines.append(f"- {b}: n={tot}, success rate {100 * c.get('True', 0) / max(1, tot):.1f}%")
    lines += ["", "## Stuck rules fired (a record can fire several)", ""]
    for k, v in stuck_rules.most_common():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Progress rules by level", ""]
    for lvl in sorted(prog_rules):
        lines.append(f"- level {lvl}: " + ", ".join(f"{k} ({v})" for k, v in prog_rules[lvl].most_common(8)))
    lines += ["", "## best_next coverage", "", f"- candidate sets present in {sum(tiers.values())} of {n} records ({100 * sum(tiers.values()) / max(1, n):.1f}%)"]
    for k, v in tiers.items():
        lines.append(f"- tier {k}: {v}")
    lines += ["", f"## Samples: stuck = True ({len(stuck_pos)} of {counts['sp']})", ""]
    for it in stuck_pos:
        lines += [f"### {it['id']}  (outcome={it['outcome']})", f"rules: {it['stuck_reasons']}", "", "```", recent_block(it["state"]), "```", ""]
    lines += [f"## Samples: stuck = False ({len(stuck_neg)} of {counts['sn']})", ""]
    for it in stuck_neg[: max(5, args.n // 2)]:
        lines += [f"### {it['id']}  (outcome={it['outcome']})", "", "```", recent_block(it["state"], 1200), "```", ""]
    for lvl in sorted(prog):
        lines += [f"## Samples: progress = {lvl} ({len(prog[lvl])} of {counts[f'p{lvl}']})", ""]
        for it in prog[lvl]:
            lines += [f"### {it['id']}  (outcome={it['outcome']})", f"rule: {it['progress_reasons']}", "", "```", recent_block(it["state"], 1200), "```", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {args.out} ({n} records)")


if __name__ == "__main__":
    main()
