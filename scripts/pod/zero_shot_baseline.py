"""Zero-shot baseline: an instruct model reads the same state text and answers yes/no.

p_success = P("yes") / (P("yes") + P("no")) from the next-token logits after the chat prompt,
no fine-tuning, thinking disabled. Writes a rows.json in kev's row shape (id, group, question,
keys, label, p) so scripts/eval_rows.py scores it exactly like the trained model.

Run inside kev's venv on the pod (transformers 5.x, torch cu128):
    cd /workspace/kev && UV_NO_SYNC=1 HF_HOME=/workspace/hf uv run python /workspace/agent-compass/scripts/pod/zero_shot_baseline.py \
        --model Qwen/Qwen3.5-2B --records /workspace/data/v0/test.jsonl --limit 5000 --out /workspace/kev/runs/zeroshot-qwen3.5-2b/eval-test
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

SYSTEM = "You are an expert reviewer of AI coding agents. You will see a task and the agent's trajectory so far."
QUESTION = (
    "Will this agent run end in success, meaning its final patch passes the task's hidden tests? "
    "Answer with a single word: yes or no."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3.5-2B")
    ap.add_argument("--records", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max_tokens", type=int, default=4600)
    ap.add_argument("--batch", type=int, default=4)
    a = ap.parse_args()

    recs = []
    with open(a.records, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                recs.append(json.loads(line))
            if a.limit and len(recs) >= a.limit:
                break
    print(f"records {len(recs)}", flush=True)

    tok = AutoTokenizer.from_pretrained(a.model)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(a.model, dtype=torch.bfloat16, device_map="cuda")
    model.eval()

    def tid(word: str) -> list[int]:
        ids = set()
        for v in (word, " " + word, word.capitalize(), " " + word.capitalize()):
            t = tok.encode(v, add_special_tokens=False)
            if len(t) == 1:
                ids.add(t[0])
        return sorted(ids)

    yes_ids, no_ids = tid("yes"), tid("no")
    print("yes token ids", yes_ids, "no token ids", no_ids, flush=True)

    prompts = []
    for r in recs:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": r["state"] + "\n\n" + QUESTION}]
        try:
            text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        prompts.append(text)

    rows = []
    t0 = time.time()
    with torch.no_grad():
        for i in range(0, len(prompts), a.batch):
            batch = prompts[i : i + a.batch]
            enc = tok(batch, return_tensors="pt", padding=True, truncation=True, max_length=a.max_tokens).to("cuda")
            logits = model(**enc).logits[:, -1, :].float()
            lp = torch.log_softmax(logits, -1)
            p_yes = torch.logsumexp(lp[:, yes_ids], -1)
            p_no = torch.logsumexp(lp[:, no_ids], -1)
            p = torch.softmax(torch.stack([p_no, p_yes], -1), -1)[:, 1].tolist()
            for r, py in zip(recs[i : i + a.batch], p):
                m = r["_meta"]
                rows.append({"id": m["id"], "group": m["group_id"], "question": "p_success", "source": m["source"], "type": "noul",
                             "variant": "clean", "keys": ["false", "true"], "label": int(bool(r["questions"]["p_success"]["label"])),
                             "p": [1 - py, py]})
            if (i // a.batch) % 50 == 0:
                print(f"  {i + len(batch)}/{len(prompts)}  {(time.time() - t0) / (i + len(batch)):.3f}s/rec", flush=True)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rows.json").write_text(json.dumps(rows), encoding="utf-8")
    (out / "report.json").write_text(json.dumps({"model": a.model, "records": len(rows), "seconds": round(time.time() - t0, 1),
                                                 "seconds_per_record": round((time.time() - t0) / max(1, len(rows)), 3),
                                                 "prompt": {"system": SYSTEM, "question": QUESTION}}, indent=1), encoding="utf-8")
    print(f"wrote {out / 'rows.json'}  ({len(rows)} rows, {(time.time() - t0) / 60:.1f} min)")


if __name__ == "__main__":
    main()
