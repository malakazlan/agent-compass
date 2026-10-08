"""Multi-head training on kev's DecisionModel with agent-compass losses.

Runs inside kev's project venv on the pod (kev importable, fused kernels installed):

    cd /workspace/kev && UV_NO_SYNC=1 HF_HOME=/workspace/hf uv run python -m agent_compass.train.multihead \
        --data /workspace/data/v1/train.jsonl --out runs/m4-v1-2b --base Qwen/Qwen3.5-2B-Base \
        --init_from runs/m3-v0-2b --epochs 1 --lr 5e-5 --batch 2 --accum 4 --max_state 4352

(`PYTHONPATH=/workspace/agent-compass` or an editable install makes `agent_compass` importable.)

What it keeps from kev: the model (LoRA backbone + pointer head), record encoding, the packed /
shared-prefix forward, the checkpoint layout (adapter, head.pt, tokenizer) so kev.benchmark and
kev.serve work unchanged. What it adds: per-question loss weights, the ordinal loss for score
questions, and the pairwise ranking loss on `_meta.pair_id` pairs that share a micro-batch.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path

import torch

from agent_compass.train.losses import DEFAULT_WEIGHTS, pairwise_loss, weighted_record_loss

MAX_GRAD_NORM = 1.0


def units_from_records(recs: list) -> list[list[int]]:
    """Indices grouped so that records sharing a pair_id stay adjacent in one unit."""
    units: list[list[int]] = []
    by_pair: dict[str, list[int]] = defaultdict(list)
    for i, r in enumerate(recs):
        pid = r["_meta"].get("pair_id")
        if pid:
            by_pair[pid].append(i)
        else:
            units.append([i])
    units.extend(by_pair.values())
    return units


def microbatches(units: list[list[int]], batch: int, rng: random.Random) -> list[list[int]]:
    rng.shuffle(units)
    out: list[list[int]] = []
    cur: list[int] = []
    for u in units:
        if cur and len(cur) + len(u) > batch:
            out.append(cur)
            cur = []
        cur.extend(u)
    if cur:
        out.append(cur)
    return out


def main() -> None:
    from kev.checkpoint import Checkpoint, Meta, write_meta
    from kev.data import load_records, materialize
    from kev.model import ContextOverflow, DecisionModel, load_tokenizer, training_context
    from kev.suite import write_json

    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="Qwen/Qwen3.5-2B-Base")
    ap.add_argument("--base_revision", default=None)
    ap.add_argument("--init_from", default=None, help="checkpoint dir or Hub id to warm-start adapter + head from (v0)")
    ap.add_argument("--lora", type=int, default=16)
    ap.add_argument("--head_dim", type=int, default=256)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--head_lr", type=float, default=0.0)
    ap.add_argument("--weight_decay", type=float, default=0.01)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--max_state", type=int, default=4352)
    ap.add_argument("--shared_prefix", type=int, default=1)
    ap.add_argument("--checkpointing", type=int, default=0)
    ap.add_argument("--dtype", choices=["fp32", "bf16"], default="bf16")
    ap.add_argument("--weights", default="", help='JSON dict of per-question loss weights, e.g. {"escalate": 0}')
    ap.add_argument("--ordinal", type=int, default=1, help="1 = cumulative-link loss on score questions, 0 = plain CE")
    ap.add_argument("--pair_weight", type=float, default=0.25)
    ap.add_argument("--max_records", type=int, default=0)
    ap.add_argument("--max_steps", type=int, default=0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--log_every", type=int, default=10)
    a = ap.parse_args()

    torch.manual_seed(a.seed)
    rng = random.Random(a.seed)
    dev = "cuda"
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = {**DEFAULT_WEIGHTS, **(json.loads(a.weights) if a.weights else {})}

    tok = load_tokenizer(a.base, a.base_revision)
    model = DecisionModel(a.base, tok, dev, lora=a.lora, revision=a.base_revision, head_dim=a.head_dim, dtype=torch.float32)
    if a.checkpointing:
        model.lm.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.lm.config.use_cache = False
    meta = Meta(base=a.base, base_revision=a.base_revision, lora=a.lora, head_dim=a.head_dim, option_isolation=False,
                special_embeddings=False, weights_dtype="fp32", holdout=[], weights="lora")
    init_source = None
    if a.init_from:
        init_source = Checkpoint(a.init_from).warm_start(model, meta)
        print(f"warm start from {init_source['resolved']}", flush=True)
    print(f"trainable params {sum(p.numel() for p in model.trainable_parameters()) / 1e6:.1f}M", flush=True)

    # records -> encodings (once); records that do not fit the context are dropped and counted
    ctx = training_context(a.max_state)
    raw = load_records(a.data)
    if a.max_records:
        raw = raw[: a.max_records]
    recs, encs, dropped = [], [], 0
    for r in raw:
        m = materialize(r)
        try:
            e = model.encode(tok, m, max_state=ctx["max_state"], max_branch=ctx["max_branch"], strict=True)
        except ContextOverflow:
            dropped += 1
            continue
        if len(e["ids"]) > ctx["max_packed"]:
            dropped += 1
            continue
        recs.append({"rec": m, "_meta": r["_meta"]})
        encs.append(e)
    qtypes = defaultdict(int)
    for r in recs:
        for q in r["rec"]["questions"]:
            qtypes[q["qid"] if "qid" in q else q.get("id", "?")] += 1
    print(f"{len(recs)} records encoded, {dropped} dropped (context), questions {dict(qtypes)}", flush=True)
    write_json(out_dir / "training_config.json", {"args": vars(a), "weights": weights, "init_source": init_source, "records": len(recs), "dropped": dropped,
                                                 "ordinal_objective": "cumulative_link" if a.ordinal else "cross_entropy"})

    units = units_from_records(recs)
    plan = microbatches(units, a.batch, random.Random(a.seed))
    steps_per_epoch = max(1, len(plan) // a.accum)
    total_steps = a.epochs * steps_per_epoch
    if a.max_steps:
        total_steps = min(total_steps, a.max_steps)

    head_params = list(model.head.parameters())
    head_ids = {id(p) for p in head_params}
    groups = [{"params": [p for p in model.trainable_parameters() if id(p) not in head_ids], "lr": a.lr},
              {"params": head_params, "lr": a.head_lr or a.lr}]
    opt = torch.optim.AdamW(groups, lr=a.lr, weight_decay=a.weight_decay)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[a.lr, a.head_lr or a.lr], total_steps=max(total_steps, 1), pct_start=0.1)
    autocast = torch.autocast("cuda", dtype=torch.bfloat16) if a.dtype == "bf16" else torch.autocast("cuda", enabled=False)

    def qid_of(q: dict) -> str:
        return q["qid"] if "qid" in q else q["id"]

    model.train()
    step = 0
    seen = 0
    t0 = time.time()
    term_sums: dict[str, float] = defaultdict(float)
    term_n: dict[str, int] = defaultdict(int)
    stop = False
    for epoch in range(a.epochs):
        plan = microbatches(units, a.batch, random.Random(a.seed + epoch))
        for mb_i, mb in enumerate(plan):
            with autocast:
                logits_per_rec = model.forward_batch([encs[i] for i in mb], shared_prefix=bool(a.shared_prefix))
            loss = torch.zeros((), device=dev, dtype=torch.float32)
            by_pair: dict[str, dict[bool, torch.Tensor]] = defaultdict(dict)
            for i, logits in zip(mb, logits_per_rec):
                r = recs[i]
                qs = {qid_of(q): q for q in r["rec"]["questions"]}
                zs = {qid_of(q): z for q, z in zip(r["rec"]["questions"], logits)}
                rec_loss, terms = weighted_record_loss(zs, qs, weights, ordinal=bool(a.ordinal))
                loss = loss + rec_loss
                for k, v in terms.items():
                    term_sums[k] += float(v)
                    term_n[k] += 1
                pid = r["_meta"].get("pair_id")
                if pid and "p_success" in zs:
                    by_pair[pid][bool(r["_meta"]["outcome"])] = zs["p_success"]
            for pid, d in by_pair.items():
                if True in d and False in d:
                    pl = pairwise_loss(d[True], d[False])
                    loss = loss + a.pair_weight * pl
                    term_sums["pair"] += float(pl)
                    term_n["pair"] += 1
            (loss / len(mb) / a.accum).backward()
            seen += len(mb)
            if (mb_i + 1) % a.accum == 0 or mb_i + 1 == len(plan):
                torch.nn.utils.clip_grad_norm_(model.trainable_parameters(), MAX_GRAD_NORM)
                opt.step()
                sched.step()
                opt.zero_grad(set_to_none=True)
                step += 1
                if step % a.log_every == 0 or step == total_steps:
                    el = time.time() - t0
                    terms_s = " ".join(f"{k}={term_sums[k] / max(1, term_n[k]):.3f}" for k in sorted(term_sums))
                    print(f"ep{epoch} step {step}/{total_steps} {terms_s} {el / max(1, seen):.3f}s/rec", flush=True)
                    term_sums.clear()
                    term_n.clear()
                if step >= total_steps:
                    stop = True
                    break
        if stop:
            break

    wall = time.time() - t0
    model.lm.save_pretrained(a.out)
    meta.head, meta.extra = model.head.state_dict(), {"args": vars(a), "init_source": init_source, "trainer": "agent_compass.train.multihead"}
    write_meta(a.out, meta)
    tok.save_pretrained(a.out)
    write_json(out_dir / "training_metrics.json", {"wall_seconds": wall, "records_seen": seen, "optimizer_steps": step, "records": len(recs),
                                                  "dropped": dropped, "peak_device_bytes": torch.cuda.max_memory_allocated(), "batch": a.batch, "accum": a.accum})
    print(f"saved {a.out}  wall {wall:.0f}s  {wall / max(1, seen):.3f}s/rec", flush=True)


if __name__ == "__main__":
    main()
