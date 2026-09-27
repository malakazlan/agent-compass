"""Write docs/data_m1.md from the conversion and example-build statistics.

Reads data/unified/<tag>.stats.json, data/examples/<tag>.stats.json (if present) and
data/splits/repo_split.json, and renders one markdown page with the measured numbers.
Run after scripts/convert.py and scripts/build_examples.py.

    uv run --python 3.12 python scripts/report_data.py
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIFIED = ROOT / "data" / "unified"
EXAMPLES = ROOT / "data" / "examples"
SPLIT_TABLE = ROOT / "data" / "splits" / "repo_split.json"
OUT = ROOT / "docs" / "data_m1.md"


def load(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:.3f}" if v < 10 else f"{v:,.1f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def main() -> None:
    conv = {p.stem.replace(".stats", ""): load(p) for p in sorted(UNIFIED.glob("*.stats.json")) if ".limit" not in p.name}
    ex = {p.stem.replace(".stats", ""): load(p) for p in sorted(EXAMPLES.glob("*.stats.json"))}
    table = load(SPLIT_TABLE)

    L: list[str] = [f"# M1 data: measured on the full conversion ({date.today().isoformat()})", ""]
    L += ["Produced by `scripts/convert.py` (unified schema, repo-hash splits, dedupe) and `scripts/build_examples.py` "
          "(kev-shaped records). Numbers below are read from the `*.stats.json` files those scripts write.", ""]

    L += ["## Trajectories", "", "| dataset | trajectories | dropped dup | skipped | success rate | tasks | runs/task | mixed-outcome tasks | repos | steps mean / median / max | steps success vs failure | thoughts on steps |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for tag, s in conv.items():
        if not s:
            continue
        L.append(f"| `{s['source']}` | {fmt(s['n_trajectories'])} | {s['n_dropped_duplicates']} | {s.get('n_skipped') or '-'} | {100 * s['outcome_rate']:.1f}% | {fmt(s['n_tasks'])} | {s['runs_per_task_mean']} | "
                 f"{fmt(s['tasks_with_mixed_outcomes'])} ({s['tasks_with_mixed_outcomes_pct']}%) | {fmt(s['n_repos'])} | {s['steps_mean']} / {s['steps_median']} / {s['steps_max']} | "
                 f"{s['steps_mean_success']} vs {s['steps_mean_failure']} | {s['steps_with_thought_pct']}% |")
    L += ["", "### Policy models and exit status", ""]
    for tag, s in conv.items():
        if not s:
            continue
        L.append(f"- **{tag}** policies: " + ", ".join(f"{k} ({fmt(v)})" for k, v in s["policy_models"].items()))
        L.append(f"  exit status: " + ", ".join(f"{k} ({fmt(v)})" for k, v in list(s["exit_status"].items())[:8]))

    L += ["", "## Splits (unit = repository, one global hash table)", "", "| dataset | train | dev | test | verified_holdout | Verified instance hits |", "|---|---|---|---|---|---|"]
    for tag, s in conv.items():
        if not s:
            continue
        sp = s["splits"]
        L.append(f"| {tag} | {fmt(sp.get('train', 0))} | {fmt(sp.get('dev', 0))} | {fmt(sp.get('test', 0))} | {fmt(sp.get('verified_holdout', 0))} | {s['verified_instance_hits']} |")
    if table:
        c = Counter(table["repos"].values())
        L += ["", f"Frozen table `data/splits/repo_split.json`: {fmt(table['n_repos'])} repositories. Rule: {table['rule']}. "
              f"Repos per split: " + ", ".join(f"{k} {fmt(v)}" for k, v in sorted(c.items())) + ".", ""]
        L += ["Dev and test shares differ from 10/10 by trajectory count because a few very large repositories dominate; the split is by repository, so this is expected and must not be rebalanced by hand.", ""]

    if any(ex.values()):
        L += ["## Example records (kev-shaped, one per trajectory prefix)", ""]
        for tag, s in ex.items():
            if not s:
                continue
            L += [f"### {tag}", "", f"- input `{s['input']}`, variant `{s['variant']}`, budget {s['budget']} tokens, tokenizer `{s['tokenizer']}`",
                  f"- trajectories {fmt(s['trajectories'])} (tasks {fmt(s['tasks'])}), records: " + ", ".join(f"{k} {fmt(v)}" for k, v in s["records"].items()),
                  f"- state tokens p50 / p90 / max: {s['state_tokens_p50_p90_max']}",
                  f"- questions present: " + ", ".join(f"{k} {fmt(v)}" for k, v in s["questions_present"].items()),
                  f"- best_next tiers: " + (", ".join(f"{k} {fmt(v)}" for k, v in s["best_next_tiers"].items()) or "none"),
                  f"- build time {s['seconds']} s", "", "Label histograms:", ""]
            for qid, h in s["label_histograms"].items():
                tot = sum(h.values())
                L.append(f"- {qid}: " + ", ".join(f"{k} {fmt(v)} ({100 * v / tot:.1f}%)" for k, v in sorted(h.items())))
            L.append("")

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
