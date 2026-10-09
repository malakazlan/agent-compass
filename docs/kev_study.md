# kev study — what the codebase actually is, and what agent-compass must change

Source: `D:\kev`, a **shallow clone (50 commits, `.git/shallow` present)** at HEAD `f2bb629`
("probs() runs a hybrid record's state once …", 2026-09-25). Every claim below cites `file:line` in that checkout.
Nothing in `D:\kev` was modified. Static reading only (local Python is 3.10; kev requires `>=3.12,<3.14`,
`pyproject.toml:328`), plus small verification commands noted inline.

> **Headline correction to the project brief §3.** The note "tested on Apple MPS; CUDA untested — port carefully" describes
> the September-17 prototype (`kev-0.5b`, `docs/model-cards/kev-0.5b.md:58,116`). The repo as cloned is a much larger
> system: every released checkpoint (Kev-0.8B/4B/9B/27B) was trained on H100/H200 through Modal with bf16 autocast
> (`runs/q35-4b-s23/00-trial-0/provenance.json` → `"gpu": "NVIDIA H100 80GB HBM3", "torch": "2.8.0+cu128"`),
> CUDA has dedicated serving code (`kev/cuda_graphs.py`, `kev/fused_qwen35.py`) and CUDA-only tests
> (`tests/test_model.py:170-341`). The riskier gap for us is different: the current family is built on **hybrid
> Qwen3.5 (Gated DeltaNet + attention)** backbones, for which the block-causal mask is *not* usable and each question
> runs as its own causal row (`kev/model.py:56-59, 228-233, 298-302`).

---

## 1. Architecture

### 1.1 Token layout (packed form)

`kev/model.py:74-111` (`encode`) builds one integer sequence per record:

```
[<state>] s_1 … s_n                                  seg=0, pos=0..n
[<q>] instr… [<opt>] o_1 … [</opt>] [<opt>] o_2 … [</opt>] … [<decide>]     seg=1, pos restarts at n
[<q>] instr… [<opt>] … [</opt>] … [<decide>]                                  seg=2, pos restarts at n
```

- The five delimiters are **existing Qwen special tokens re-used, not new embeddings**:
  `SPECIAL = ["<|fim_prefix|>", "<|fim_middle|>", "<|box_start|>", "<|box_end|>", "<|fim_suffix|>"]` for
  `<state>, <q>, <opt>, </opt>, <decide>` (`kev/model.py:9-11`). Rationale: no vocab resize; LoRA adapts their meaning.
  An optional `--special_embeddings 1` trains those five rows via peft `trainable_token_indices`
  (`kev/model.py:236`), but it leaked memory on MPS and blocks LoRA merging / MLX (`AGENTS.md:264-265`,
  `kev/checkpoint.py:654`, `kev/mlx_model.py:35-36`); it was a negative result (`PLAN.md:142-143`).
- The state is `[<state>] + user_tokens(state)[:max_state-1]` (`kev/model.py:88`); in strict mode a longer state
  raises `ContextOverflow` (`:86-87`).
- Per question `k` (1-based): `instr = [<q>] + tokens(instr)`; `spans = [[<opt>] + tokens(o) + [</opt>] for o in
  options]`; `br = instr + spans + [<decide>]` (`:93-95`). **Branch limit**: `len(br) > max_branch - len(S)` raises
  (`:96-97`), i.e. `max_branch` is a *row* limit (state + one branch), not a branch-only limit.
- Bookkeeping returned: `seg` (0 = state, k = question k), `pos` (branch positions restart at `len(S)`, `:98,104`),
  `decide_idx[k]` = index of the last token of branch k, `opt_idx[k][j]` = index of the `</opt>` token of option j
  (`:105-109`), `opt` (per-token option index, `OPT_NONE=-1`, `OPT_DECIDE=-2`, `:71,99`), `labels` (`:111`).
- User text can never forge delimiters: `user_tokens` rewrites `<|name|>` → `<¦name¦>` before tokenizing
  (`kev/model.py:62-68`; test `tests/test_unit.py:94-101`).
- `option_isolation=True` (ablation, off in every release): every option span is its own sub-branch with shared
  positions and `<decide>` at a fixed position after the longest span (`kev/model.py:100-103`, mask rule `:145-152`).
  Not available on hybrid backbones (`:232`).

### 1.2 Block-causal mask

`branch_mask_batch(segs, device, dtype, opts=None, length=None)` (`kev/model.py:128-154`):

- `allow(i, j) = causal(j <= i) AND (seg[j] == 0 OR seg[j] == seg[i]) AND seg[j] != -1 (pad)`; the diagonal is
  always allowed so padded query rows are never fully masked (`:141-153`).
- Returned as an **additive float mask `[B, 1, L, L]`** with `finfo(dtype).min` (not `-inf`) in the backbone's dtype
  (`:154`), passed straight into `self.lm(input_ids=…, position_ids=pos, attention_mask=mask)` (`:293`).
- Verified locally: I copied lines 128-154 verbatim into a Python 3.10 + torch 2.9 script and ran it on
  `seg=[0,0,0,1,1,2,2,2]`; rows 3-4 attend to state + themselves, rows 5-7 attend to state + themselves and never to
  columns 3-4. Matches `tests/test_unit.py:78-85`.
- Padding: rows are right-padded with the tokenizer pad id (`_pad_rows`, `:272-283`); on MPS in eval mode `L` is
  rounded up to `KEV_SHAPE_BUCKET=64` (`:270,277`). Pads are masked keys and sit after every real token, so they
  cannot change any real hidden state (`:273-275`; test `tests/test_model.py:66-80`).

### 1.3 Position ids per branch

Positions are explicit: `pos = list(range(len(S)))` for the state and `range(p0, p0+len(br))` with `p0 = len(S)` for
each branch (`kev/model.py:89,98,104`), so every question looks like "the state followed immediately by me"
(`tests/test_unit.py:104-111`). The same `pos` is reused in the row form (`rows_of`, `:157-171`), which is why
rows and packed give identical probabilities on attention-only models (`tests/test_model.py:92-109`).

### 1.4 Row form for hybrid backbones (what every current Kev actually runs)

- `is_hybrid(config)` = `"linear_attention" in config.layer_types` (`kev/model.py:56-59`); Qwen3.5/3.8 are hybrid
  (24 DeltaNet + 8 attention layers for 4B/9B, `docs/model-cards/kev-4b.md:179`).
- Recurrent layers ignore attention masks, so `rows_form()` is `True` for every hybrid record (`:298-302`) and
  `forward_batch` routes to `forward_rows_batch` (`:358-363`): one causal row per question = `S + branch_k` with
  `Sp + pos_k` (`:344-352`). The state is therefore **recomputed once per question in training** unless
  `--shared_prefix 1` (`kev/train.py:349-350`, default on only with `--full_ft`), which runs each state once and lets
  branches continue from a functional per-layer prefix (KV for attention layers, conv window + recurrent state for
  DeltaNet) with gradients flowing back (`kev/shared_prefix.py:1-35, 184-292`; exactness `~1e-5` fp32,
  `tests/test_model.py:170-219`).
- Attention-only backbones also fall back to rows when the packed sequence exceeds `SERVE_MAX_PACKED` (`:301-302`).
- Serving on hybrids: `prefix()` runs the state once with a `DynamicCache(config=…)` (`:385-392`), then
  `_branch_rows_from_prefix` replicates the cache per row (deep-copying DeltaNet conv/recurrent states, `:315-327`)
  and runs `rows_per_pass` rows per forward (`:33-39`, 16,384-token budget).

### 1.5 Pointer readout head

`PointerHead(d, dp=256)` (`kev/model.py:174-192`):

- `q = Linear(d, dp)`, `k = Linear(d, dp)`; `z = (k(h_opts) @ q(h_decide)) * dp^-0.5` → logits `[K]` (`:178-186`).
  `h_decide` = hidden state at `<decide>`, `h_opts` = hidden states at each option's `</opt>` token
  (`_readout`, `:295-296`; the option text is *summarised at its closing delimiter*).
- `many()` batches every question of a serving batch in one pass (`:189-192`); `_readout_many` turns picked hidden
  states into per-question softmaxes with one device sync (`:460-471`).
- Probabilities = `softmax(z)` over the K options (`:371, 382, 410, 428`). Head always runs in fp32
  (`last_hidden_state.float()`, `:293, 329`).
- Head capacity knob `--head_dim` (default 256; 512/1024 tried, no gain, `runs/leaderboard.md:46,110`,
  `PLAN.md:142-143`).

### 1.6 How the three question types are read out (all through the same pointer)

`kev/api.py:162-167` (module docstring) and `to_record` (`:263-278`):

| type | options fed to the pointer | answer (`to_answers`, `kev/api.py:299-310`) |
|---|---|---|
| `noul` | `["no[: false-desc]", "yes[: true-desc]"]` (`:269-271`) | `noul = p[1]` (`:302-303`) |
| `choice` | `["name: desc" or "name"]` per criteria entry (`:272-273`, `option_text` `:219-220`) | `choice = argmax`, `probabilities` by name, `confidence = (p_max − 1/K)/(1 − 1/K)` (`:281-283`) |
| `score` | rendered level descriptions in order (`:274-276`) | `score = Σ i·p_i` (expected level), `legend`, `probabilities` by index, `confidence = 1 − E|level − mode|/(L−1)` (`:286-290`) |

So there is **no binary head and no ordinal head**: a `noul` is a 2-option pointer question; a `score` is a plain
K-way softmax over level texts. Ordinality only enters through the optional RPS loss (`kev/train.py:57-60`) and the
`score_mae` / `ranked_probability_score` metrics (`kev/metrics.py:75-77, 98-99`). Keys are `["false","true"]`,
criteria names, or `"0".."K-1"` (`question_keys`, `kev/api.py:255-260`).

### 1.7 "None of the above"

Not a mechanism; a **data augmentation** (`kev/data.py:48-55, 320-343`):

- `NONE_OPTIONS` = 13 wordings (`"other: None of the above"`, `"none: None of these"`, `"not_listed"`, `"unknown:
  Cannot be determined…"`, …) because a single wording taught "this wording ⇒ pick it" in the first run (`:49-50`).
- Per Choice question with ≥3 options, per epoch: with `p_none=0.10` the true option is removed and a none-option
  becomes the label; with `p_none_distract=0.12` a none-option is added as a *wrong* alternative; with
  `p_distract=0.15` an irrelevant distractor (`"purple: The colour purple"`) is added (`:335-340`; defaults
  `kev/train.py:329-331`). Options are always permuted (`:341`).
- `none_pair` (`:346-359`) emits a minimal pair (same state/order; true option present vs removed) on 25 % of Choice
  records in every release (`--p_none_pair 0.25`, `README.md:293,297`).
- Evaluated by the suite variants `none_present` / `none_absent` / `permuted` (`kev/suite.py:184-214`) and the
  `evaluate.test_none_of_the_above` probe (`kev/evaluate.py:84-106`).

### 1.8 Losses (`kev/train.py`)

- `question_loss` (`:41-61`): CE on the pointer logits (`F.cross_entropy`, optional `label_smoothing`); if the question
  carries a soft `target` vector, `−Σ t·log_softmax(z)` instead (`:47-49`); optional `brier_w` (sum-squared error added,
  `:52-54`), `focal_gamma` (`:55-56`), and for `score` questions `ord_w ×` ranked probability score on cumulative
  probabilities (`:57-60`). Only one of smoothing/Brier/focal may be non-zero (`:44-46`).
- `anchor_loss` (`:64-69`): `KL(teacher ‖ student)` toward the frozen base's zero-shot letter-logit distribution
  (`kev/anchors.py`), skipped when the option set changed.
- `permutation_kl` (`:72-76`): symmetric KL between logits under two option orders (`perm_kl` weight, second forward
  pass on `perm_frac` of records, `:265-267, 278, 288-291`).
- Aggregation (`batch_loss`, `:271-294`): per variant, mean CE over its questions × `share`; anchor and perm terms
  added; `loss / group_records` then `backward()` (`:493-495`) so gradient accumulation weights by source records,
  not by none-pair siblings (`:491-492`).
- Optimiser: AdamW (`weight_decay 0.01`), separate `--head_lr` group (`:463-467`), OneCycleLR with 10 % warm-up over
  all steps (`:471`), global grad-norm clip 1.0 (`:398, 500`), TF32 on for CUDA training (`:426-427`),
  bf16 autocast only on CUDA (`:363-364, 428`).
- **Shipped recipes use plain CE only**: `perm_kl`, `ord_w`, anchoring, smoothing/Brier/focal are all recorded negative
  results (`README.md:300`, `PLAN.md:130-146`).

### 1.9 Calibration / temperature scaling

- One scalar `PointerHead.temperature`, applied as `z / T` **in eval mode only**; training always sees T=1
  (`kev/model.py:180-187`; test `tests/test_unit.py:188-197`). Argmax is unchanged by construction.
- Stored in `head.pt` as `Meta.temperature` (`kev/checkpoint.py:428-452`) and applied at load (`:611`);
  `KEV_TEMPERATURE=1.0` restores raw logits (`:519-520`).
- Fit: `fit_temperature` = min mean NLL over a 121-point log grid on `[0.25, 4]`, micro-averaged
  (`kev/metrics.py:235-260`); released values are fitted **on the in-distribution development rows** by
  `scripts/calibrate_checkpoint.py:210-255`, which also reports a group-disjoint 5-fold out-of-fold ECE with bootstrap
  CIs (`kev/metrics.py:283-335`). Research trials instead fit on the suite's `calibration` partition (81-point grid,
  `kev/experiment.py:146`). Shipped T: 27B 1.38, 9B 2.30, 4B 2.41, 0.8B 2.35 (`README.md:342`).
- `kev/calibrate.py` reports raw / shipped / workload-fit / OOF arms for any `rows.json` (`:31-73`).
- Legacy `kev/evaluate.py:556-573` fits T with LBFGS on even records; not used for releases.
- Finding worth remembering: per-(type, K) temperatures and a logistic reliability head were **worse** OOD
  (`PLAN.md:101-102`, `docs/model-cards/kev-4b.md:167`), and a T fitted on easy rows does not transfer to hard
  workloads (`PLAN.md:96-102`).

---

## 2. Backbone support

| Model | HF id @ pinned revision | Where pinned |
|---|---|---|
| Kev-0.8B | `Qwen/Qwen3.5-0.8B-Base` @ `dc7cdfe2ee4154fa7e30f5b51ca41bfa40174e68` | `evals/v9/transfer-v9/manifest.json:207`, `README.md:292` |
| Kev-4B | `Qwen/Qwen3.5-4B-Base` @ `1001bb4d826a52d1f399e183466143f4da7b741b` | `…manifest.json:205`, `README.md:296` |
| Kev-9B | `Qwen/Qwen3.5-9B-Base` @ `68c46c4b3498877f3ef123c856ecfde50c39f404` | `…manifest.json:206` |
| Kev-27B | `Qwen/Qwen3.8-27B` @ `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` (post-trained, not -Base) | `modal_app.py:323` |
| Qwen3 gen. | `Qwen/Qwen3-0.6B-Base` @ `da87bfb6…`, `Qwen/Qwen3-4B-Base` @ `906bfd4b…`, `Qwen/Qwen3-8B-Base` @ `49e3418f…` | `evals/v7/decision-v7/manifest.json:3-6`, `experiments/v7-final.json:2` |
| Prototype | `Qwen/Qwen2.5-0.5B` | `docs/model-cards/kev-0.5b.md:55`, `kev/suite.py:19` |

- `kev.train` default `--base` is `Qwen/Qwen3-0.6B-Base` (`kev/train.py:301`), **not** Qwen2.5-0.5B.
- Library pins: `transformers>=5.17,<6`, `peft>=0.21`, `torch>=2.6,<2.9`, `accelerate>=1.15`, `datasets>=3.0`
  (`pyproject.toml:336-345`); `uv.lock` resolves transformers 5.17.0, peft 0.21.0, torch 2.8.0, triton 3.4.0
  (checked with a regex over `uv.lock`). Full-weight training needs torch ≥ 2.8 (`kev/full_ft.py:35-43`).
  The Modal image adds `flash-linear-attention==0.5.2`, `triton>=3.7.1` and a `causal-conv1d 1.7.0` cu12/torch2.8/cp313
  wheel (`modal_app.py:50-61`).
- Backbone load: `AutoModelForCausalLM.from_pretrained(...).model` — the LM head is dropped, nothing is ever generated
  (`kev/model.py:225-226`). Loading `dtype` fp32 by default (exact eval), bf16 for `--weights_dtype bf16` and serving.

### 2.1 LoRA config

`kev/model.py:234-244`: `LoraConfig(task_type="FEATURE_EXTRACTION", r=lora, lora_alpha=2*lora, lora_dropout=0.05,
target_modules=targets)`. Targets by `--lora_targets`: `all` = `q_proj k_proj v_proj o_proj gate_proj up_proj
down_proj` **plus, on hybrids, `in_proj_qkv in_proj_z in_proj_a in_proj_b out_proj`** (the Gated DeltaNet
projections, `:240-242`); `dense` = same minus DeltaNet; `attn`; `qv`. Released r=16, α=32
(`docs/model-cards/kev-4b.md:197`); trainable params 11.3 M (0.8B), 33.8 M (4B) (`kev-0.8b.md:281`, `kev-4b.md:62`).
`--lora` range in studies 1..64 (`kev/experiment.py:42`). Freezing DeltaNet projections (`dense`) lost ~1.7 pp transfer
(`runs/leaderboard.md:30` vs `:20`).

### 2.2 Qwen3 vs Qwen3.5 in the code

1. Detection `is_hybrid` (`kev/model.py:56-59`) → `self.hybrid` (`:231`) switches: packed mask ↔ rows
   (`:298-302`), `prefix_min_tokens` 384 ↔ 0 (`:252-257`), LoRA target list (`:240-242`), `option_isolation` refused (`:232`).
2. `kev/fused_qwen35.py` (serving only, **no backward**, `:18-19, 121-126`): rewrites `Qwen3_5GatedDeltaNet`,
   `Qwen3_5Attention`, MLP and RMSNorms with `fla` Triton kernels; concatenated projections (`:56-60, 137-149`);
   requires `fla.__version__ == "0.5.2"` exactly (`:53, 127-130`); refuses MoE (`:131-132`); a pass continuing a cached
   DeltaNet state does not write it back (`:62-65`).
3. `kev/cuda_graphs.py` — CUDA graphs and cross-request batching only for **hybrid on CUDA** (`kev/checkpoint.py:627-633`).
4. `kev/shared_prefix.py` — exact shared-state training pass for hybrids (`Prefix` duck-types the cache; `:184-224`).
5. `kev/mlx_model.py` — Apple-Silicon backend because MPS has no DeltaNet kernels (`:1-13`); irrelevant for us.
6. Attention-only Qwen3 keeps the packed path and was trained/served on CUDA with SDPA (`experiments/v7-final.json`
   trials are `dtype bf16` which `parse_args` only allows with `--device cuda`, `kev/train.py:363-364`;
   `runs/leaderboard.md:23`).

### 2.3 Full fine-tune path

`kev/train.py --full_ft 1 --weights_dtype bf16` (`:345-346, 378-379`): backbone bf16, `MasterAdamW` keeps fp32
masters + moments in host memory (one GPU) or sharded on the GPUs under FSDP2 (`kev/full_ft.py:1-19, 58-146,
164-175`); gradients accumulate in bf16; head replicated and all-reduced (`:131-133`). Resume points, `save_backbone`
via `save_pretrained` 5 GB shards (`:46, 291-301`). Checkpoint loader rule: `adapter_config.json` ⇒ LoRA, else
`config.json + model*.safetensors` ⇒ full (`kev/checkpoint.py:382-392, 559-567`). Measured: 27B on 8×H200
7.6 records/s, 71 GB peak per GPU (`AGENTS.md:44-46`). A single-GPU 27B run needs `--row_budget 8192` (`AGENTS.md:38-39`).

---

## 3. CUDA status

**Supported and exercised in production; not just "ported".**

- Device selection `cuda > mps > cpu` (`kev/device.py:5-6`); `sync/empty_cache/allocated_bytes` branch per device
  (`:9-30`); OOM detection covers `torch.OutOfMemoryError` (`:20-23`).
- Attention implementation: `attn = "sdpa" if device startswith "cuda" else "eager"` (`kev/model.py:222`); comment:
  eager is "known-good with our float 4D mask" on MPS/CPU, SDPA "accepts arbitrary additive masks". **No FlashAttention-2
  / FlexAttention anywhere** (grep `flash|flex` over `kev/*.py` finds only `flash-linear-attention` = fla for DeltaNet).
  For exact fp32 evaluation `LocalPredictor` turns TF32 and the flash/mem-efficient SDP kernels **off**
  (`kev/predictors.py:34-37`), so eval runs the math SDPA kernel; training keeps TF32 (`kev/train.py:426-427`).
- The custom mask and SDPA: the packed `[B,1,L,L]` additive mask is only ever used on attention-only bases; on hybrids
  the mask passed is a plain 2-D `[B, L]` padding mask (`kev/model.py:327-329`) or, in graphs, a dict
  `{"full_attention": 4-D additive, "linear_attention": [B,L]}` (`kev/cuda_graphs.py:202-204`,
  `kev/shared_prefix.py:109-110, 226-231` — boolean for SDPA, additive float for eager).
- Modal image is CUDA torch 2.8 + fla 0.5.2 + triton ≥3.7.1 + causal-conv1d (`modal_app.py:50-71`); default GPU H100
  (`:38`); `TRITON_CACHE_DIR` on the HF volume (`:42`). Note the comment: fla's gated-chunk backward is wrong on Hopper
  with Triton 3.4-3.7.0 (`:56`), so **triton ≥ 3.7.1 is mandatory for training Qwen3.5 on H100**.
- CUDA-only tests, run via `modal run modal_app.py::gpu_tests` (`modal_app.py:306-320`): shared-prefix parity in
  fp32 and bf16 (`tests/test_model.py:170-219`), CUDA graphs vs eager (`:222-252`), server OOM recovery on an H100
  (`:255-341`). CI itself has no GPU (`.github/workflows/ci.yml:8-18`).
- Trial provenance: H100 80GB HBM3, torch 2.8.0+cu128 (`runs/q35-4b-s23/00-trial-0/result.json` → `provenance`);
  27B on H200, peak 96.5 GB (`runs/r6-27b-v2/01-trial-1/result.json`).

**MPS-only assumptions still in the code** (harmless on Linux, but know them): shape bucketing `KEV_SHAPE_BUCKET`
(`kev/model.py:270,277`), per-step `empty_cache` on MPS (`kev/train.py:506`), `attn="sdpa"` override on MPS in serve
(`kev/serve.py:274`), MLX auto-backend (`kev/checkpoint.py:220-221`).

**Unix-only assumptions** (matter on your Windows dev box, not on RunPod): `import fcntl` (`kev/suite.py:3`,
`kev/experiment.py:14`), `import resource` + `getrusage` (`kev/train.py:13, 535`). `kev.train` will not even import on
Windows.

**Concrete porting risks for agent-compass on one H100**

1. *Hybrid rows form multiplies state cost by Q.* With 6 questions and an 8-16k-token state, plain `forward_batch`
   runs the state six times per record (`kev/model.py:344-352`). Use `--shared_prefix 1` (LoRA path allowed, it only
   defaults off, `kev/train.py:349-350, 360`) or the packed path on an attention-only base.
2. *Packed mask is O(L²) memory.* `branch_mask_batch` materialises `[B,1,L,L]` floats (`kev/model.py:154`): at L=16k
   in bf16 that is 512 MB per record before attention itself. The row form / shared prefix avoids it.
3. *fla version lock.* `fused_qwen35.fuse` refuses anything but fla 0.5.2 (`:127-130`); training doesn't need fused
   kernels, but `transformers` will use fla + causal-conv1d if importable, and `test_shared_prefix_matches_rows` skips
   CPU when causal-conv1d is installed because transformers routes CPU tensors to its CUDA kernel (`tests/test_model.py:184`).
4. *Grad checkpointing + shared prefix* is wired through `torch.utils.checkpoint` non-reentrant (`kev/shared_prefix.py:268-272`);
   any new head that reads *state* hidden states will find they are never returned by `branch_hidden` (`:233-235`,
   "the state's own hidden states are never needed").
5. *CUDA-graph serving has hard bucket limits*: `GRAPH_STATE=1024`, `GRAPH_ROW=1024`, `BANK_WIDTH=4096`,
   `GRAPH_TOKENS=32768` (`kev/cuda_graphs.py:45-52`). States > 4096 tokens never enter the graph bank; > 1024 run an
   eager state pass (`kev/model.py:441-450`). Our 8k-16k states would serve on the eager path (~130 ms/pass for 4B on
   H100 regardless of length, `runs/grouping-4b-h100/report.json` → `latency_eager`).
6. *Python 3.12+ and `uv.lock` pinned to cp313 wheels* (`modal_app.py:50`); RunPod images with 3.10/3.11 need a
   different environment.

---

## 4. Data format

### 4.1 Records

Labelled request (what you write, `kev/data.py:362-386`, README `:147-156`):

```json
{"state": "<str | object | array>",
 "questions": {"<id>": {"type": "choice|noul|score", "instructions": "<str|obj|array>",
                        "criteria": {...} | [...], "label": "<opt name> | true/false | <level idx>",
                        "target": {"<key>": p, ...}   // optional soft target
                        "src": "<task name>"}},
 "_meta": {"source", "id", "group_id", "variant", "row", "split", "text_sha256", ...}}
```

`materialize` (`:397-410`) runs it through the **serving path** `api.to_record` so training text is byte-identical to
`/v1/systemone` text (`:1-6`), yielding the internal record
`{"state": str, "questions": [{"instr", "options": [str], "label": int, "src", "qtype", "qid", "keys", "target"?}]}`.
Structured states/instructions/criteria are flattened by `render` (`kev/api.py:210-216`: `k: v` lines, `- item`
lists, two-space indentation). Soft targets are keyed by option key, normalised (`kev/data.py:404-410`).

### 4.2 Sequence lengths

- Training context: `MAX_STATE=384`, `MAX_BRANCH=1024` (row: state+branch), `MAX_PACKED=2048` (`kev/model.py:14`);
  `--max_state` raises the state limit and grows branch/packed by the same amount (`training_context`, `:23-30`), up to
  `MAX_TRAIN_STATE = 8192 − (1024 − 384) = 7552` (`:20`; Kev-27B trained with `max_state 7552`,
  `runs/r6-27b-v2/01-trial-1/result.json` config). **Per-question branch budget beyond the state is therefore fixed at
  640 tokens** (instr + all options + delimiters).
- Serving context: `SERVE_MAX_STATE = SERVE_MAX_BRANCH = 8192`, `SERVE_MAX_PACKED = 16384` (`:16-17`).
- Frozen suites are admitted only if they encode strictly under the training context with 64 branch tokens of headroom
  under both pinned tokenizers (`kev/suite.py:24-26, 217-235`).

### 4.3 Collation and batching (`kev/train.py`)

- `encode_batch` (`:245-268`): per record and epoch, seeded augmentation (`augment`), optional `none_pair` siblings,
  strict encode, optional split by question under `--row_budget`, optional permuted copy for `perm_kl`. Produces
  `Variant(rec, enc, request_id, source, permuted, share)` (`:136-149`).
- `microbatch_plan` (`:178-206`): `--batch` records per micro-batch, `--accum` micro-batches per optimizer step;
  `--length_sort 1` balances padded cost across micro-batches and ranks using character counts as a proxy.
- `row_passes` (`:230-242`): under `--row_budget` a micro-batch is split into forward/backward passes by padded tokens
  (`pass_tokens`, `:158-163`: rows × longest row, or states × longest state + branches × longest branch when shared).
- Forward: `model.forward_batch([v.enc …], shared_prefix)` (`:277`) → packed `hidden_batch` with right-padding
  (`kev/model.py:285-293`) or rows (`:333-352`).

### 4.4 Data sources (decision-v7, the release training set)

`evals/v7/decision-v7/manifest.json:7-18` pins ten HF datasets: `CogComp/trec`, `legacy-datasets/banking77`,
`stanfordnlp/imdb`, `fancyzhx/ag_news`, `SetFit/amazon_reviews_multi_en`, `fancyzhx/dbpedia_14`, `google/boolq`,
`SetFit/sst5`, `nyu-mll/multi_nli`, `Yelp/yelp_review_full`; plus generated `legacy_policy` (896) and `compositional`
(1,680 records over 60 random rule structures) (`:37-50, 124-127`); 10,000 public records from `evals/public-pool-v4`
(`:120-121`). Partitions: train 12,576 records / 15,576 questions; calibration 968; development 1,204; test 1,176
(`:134-154`). Eval-only sources: `mmlu emotion tweet_offensive qnli paws sciq contrastive legacy_holdout
composition_holdout` (`:51-61`; policy in `kev/data.py:283-291`). Converters: `kev/data.py:100-281`. Development
partition question types (counted locally): choice 756, noul 472, score 240.

transfer-v9 (`evals/v9/transfer-v9/manifest.json`) adds `TIGER-Lab/MMLU-Pro` 10-way, "buried" states and
"unknowable" records with intact controls (`:220, 245-247, 291`); development 1,264 / test 1,264 records (`:294-300`).

---

## 5. Training recipe of the published checkpoints

| Checkpoint (stage 1, decision-v7) | plan | lr | batch × accum | epochs | steps | wall / peak | seed |
|---|---|---|---|---|---|---|---|
| Kev-0.8B `q35-08b/02-trial-2` | `experiments/q35-08b.json` | 1e-4 | 8 × 1 | 2 | 3,144 | 1,195 s H100, 56.8 GB (no checkpointing) | 2 |
| Kev-4B `q35-4b-s23/00-trial-0` | `experiments/q35-4b-s23.json` | 5e-5 | 4 × 2, `checkpointing 1` | 2 | 3,144 | 3,384 s H100, 24.6 GB | 2 |
| Kev-9B `q35-9b/01-trial-1` | `experiments/q35-9b.json` | 5e-5 | 2 × 4, `checkpointing 1` | 2 | 3,144 | 5,485 s H100, 39.5 GB | 1 |
| Kev-27B `r6-27b-v2/01-trial-1` | (archive) | 5e-5 | 1 × 8, `weights_dtype bf16`, `max_state 7552` | 1 | 1,926 | 19,152 s H200, 96.5 GB | 2 |

Sources: the plans; `runs/<study>/<trial>/result.json` → `training_resources` / `provenance` (read with Python);
`README.md:283-298`; model cards (`kev-4b.md:193,197`, `kev-9b.md:101,105`, `kev-0.8b.md:388`). All: LoRA r=16 α=32
on attention + MLP + DeltaNet projections, CE only, `--dtype bf16` autocast with fp32 LoRA/head, OneCycle,
`p_none_pair 0.25`, effective batch 8 records. Costs on Modal: $1.57 / $4.01 / $6.6 per trial
(`runs/leaderboard.md:89,24,18`).

Follow-up **delta** stages (`--init_from`, one epoch, lr 2e-5 for 4B/9B and 4e-5 for the 0.8B night-2 delta, 2,000-6,000
replayed decision-v7 records): dates/unknowable (`evals/night2`), then `documents-v1` (CFPB), `hard-v1`, `devtools-v1`
(`docs/model-cards/kev-4b.md:64,114,149`, `kev-0.8b.md:283`). Use lr ≤ 2e-5 for deltas (`AGENTS.md:150`).

Seeds matter: three 0.8B seeds gave transfer 0.622/0.634/0.643 (`kev-0.8b.md:354`); "a one-seed lead of a point is
noise" (`PLAN.md:128-129`).

---

## 6. Evaluation protocol

- **Suites** (`AGENTS.md:89-116`): `decision-v7` (in-distribution), `transfer-v4` (new sources, 764 dev / 764 test
  records), `transfer-v9`, `round3/*`, `hard-v1`, `devtools-v1`, `documents-v1/v2`, `breadth-v1` (private mirror),
  `external/{semif,scienthoon,wanli,typesafe,ekzhang-mmlupro}`. Each has `manifest.json` with sha256 per partition,
  pinned dataset/base revisions and `code_hashes`; large partitions are fetched from the HF mirror
  `jaredpalmer/kev-suites` and hash-checked (`kev/suite.py:32-36, 136-172`).
- **Dev vs locked test**: `load_split(..., "test")` raises unless `allow_test=True` (`kev/suite.py:139-140`);
  `kev.benchmark --allow-test` (`kev/benchmark.py:177`); on Modal `run_locked_test` refuses a second read of the same
  name (`modal_app.py:149-164`) and refuses trials that failed their gates unless named `-ungated` (`:177-178`).
  Rule set: "development partitions select; test read once per candidate" (`PLAN.md:148-177`).
- **Metrics** (`kev/metrics.py`): accuracy, NLL (exact from logits), Brier = Σ(p − onehot)² per question (`:60,74`),
  ECE with **10 equal-width bins on top-probability** (`:15-21`), `mean_conf`, `confident_error_rate` (p≥0.9 & wrong,
  over all questions), `coverage_at_0_9`, `coverage_at_{5,1}pct_error` (tie-aware selective coverage, `:103-111`),
  AURC (`:142-146`), `top_bins`, `selective@{0.5,0.8}`, and for score questions `score_mae` + RPS (`:75-77, 98-99`).
  Groupings by task / variant / held-out source; permutation `flip_rate` and `mean_max_delta`; contrastive
  `paired_flip`; `unknowable_report` (`kev/benchmark.py:72-102`, `kev/metrics.py:167-180`,
  `kev/contrastive.py:582-607`). KL appears only as a training loss, not as a reported metric.
- **Statistics**: paired cluster percentile bootstrap over `(source, group)` clusters, 1,000-2,000 resamples, micro or
  macro (`kev/metrics.py:274-280, 338-392`); `kev.compare` for two run dirs; `kev.rounds` for registered rounds.
- **Research gates** (`kev/experiment.py:137-138, 23-49`): isolation tolerance 1e-3 (sibling-question probe,
  `:1-20`), task/variant regression 5 pp, permutation flip +5 pp, held-out pairs ≥ 70 %, transfer confident errors ≤ 10 %.
- **Baselines**: zero-shot letter-logit readout of the base (`kev/evaluate.py:530-553`, `scripts/base_mmlu_probe.py`),
  Jev via AI Gateway (`kev/jev.py`, `kev/predictors.py:143-201`), AutoJev.
- **Run layout**: `runs/<study>/<NN-label>/{provenance.json, train.log, checkpoint/{adapter_model.safetensors,
  adapter_config.json, head.pt, tokenizer*, training_config.json, training_metrics.json}, calibration/, development/,
  transfer/, result.json}` (`kev/experiment.py:52-67, 109-175`; `kev/train.py:458-459, 523-536`). Each benchmark dir
  holds `predictions.jsonl`, `rows.json`, `report.json` (`kev/benchmark.py:123-164`). A row is
  `{id, group, question, source, task, type, variant, keys, label, p, logits, inference_temperature, control_id,
  pair_id, sibling, parent, raw_probability_sum, zero_count}` (`:56-67`). `result.json` = `summarize()` keys +
  `coverage`, `latency_ms{median,p95}`, `calibration`, `transfer`, `provenance{config, config_sha256, suite_sha256,
  source_hashes, git_commit, platform, torch, gpu}`, `mechanism_checks`, `gates`, `calibration_fit`, `wall_seconds`,
  `training_resources` (`:130-175`). Study ledger `results.jsonl` + `runs/leaderboard.md`
  (`kev/experiment.py:190-219`). README/model-card numbers are CI-checked against reports via `docs/claims.json`
  (760 entries; `scripts/verify_claims.py`).

---

## 7. Serving

- Request/response (`kev/api.py:174-207, 299-310`, `README.md:216-250`):

```jsonc
POST /v1/systemone
{"state": str|obj|array, "model": "kev-latest", "questions": {"<id>": {"type": "noul"|"choice"|"score",
   "instructions": str|obj|array|null, "criteria": {true?,false?} | {name: desc|null} (1..255) | [level,…] (1..255)}}}
→ {"model": …, "answers": {"<id>": {"type":"noul","noul":p} | {"type":"choice","choice":k,"confidence":c,"probabilities":{k:p}}
   | {"type":"score","score":E[level],"legend":{"0":…},"probabilities":{"0":p,…},"confidence":c}},
   "usage": {"input_tokens", "output_tokens"}, "latency_ms": ms}
```

  Probabilities rounded to 4 decimals (`:293-296`); 422 on invalid input; headers `x-typesafe-request-id`,
  `server-timing`; optional bearer auth `KEV_API_KEY` (`kev/serve.py:190-200`). Extra endpoints: `/v1/models`,
  `/v1/systemone/permute` (n_perm 1..64), `/v1/systemone/separate` (`:213-260`). The official `typesafe-sdk` works
  unchanged (`tests/test_api.py:90-122`).
- **Batching**: one model thread drains a queue of up to `MAX_BATCH=64` encoded requests and runs
  `model.probs_batch` (`kev/serve.py:29, 118-156`); with CUDA graphs, all queued requests share one state pass and one
  row pass (`kev/cuda_graphs.py:1-35, 273-299`); `PrefixCache` LRU of 4 state prefixes keyed on state token ids
  (`kev/serve.py:25, 33-63`); OOM ⇒ drop cache and retry once (`:140-156`). `/v1/systemone` is async so a container
  absorbs many concurrent requests (`:170-174`).
- **Latency** (median model time, new text / cached text; `README.md:361-372`, `runs/grouping-4b-h100/report.json`):
  Kev-4B H100 6 questions short text **18.1 / 12.9 ms**, 5 questions on 2,200 tokens 89.4 / 22.5 ms, 100.8 req/s at
  64 clients; L40S 41.5 / 27.7 ms; Kev-0.8B L4 22.7 / 16.1 ms; Kev-9B H100 24.0 / 16.6 ms; Kev-27B H100 75.0 / 52.0 ms.
  Without graphs the eager path is ~123-132 ms per new request at any length (`report.json` → `latency_eager`).
  bf16 serving differs from fp32 eval by ≤ ~0.03 in probability (`README.md:383`).

---

## 8. What agent-compass must change or add (and where it plugs in)

Baseline decision: keep kev's `encode`/mask/rows machinery, `Checkpoint`/`Meta`, `metrics`, `benchmark` and serving
skeleton; replace/extend the readout, losses, data builders and context limits.

| Need | Where it plugs in | Notes |
|---|---|---|
| **Six typed questions** (`p_success` noul, `stuck` noul, `escalate` noul, `progress` score(4), `steps_left` score(4 bins), `best_next` choice(K)) | Already expressible as one kev record; question order/branches unchanged. `kev/data.py:362-386` `load_records` accepts our JSONL directly. | Each question costs a 640-token branch budget beyond the state (`kev/model.py:20, 96-97`). Fine for noul/score; `best_next` with K candidate actions × ~100 tokens may need a larger per-branch allowance → change `training_context` (`:23-30`) so branch budget is independent of state. |
| **Ordinal (CORAL) head** for `progress`, `steps_left` | New head class beside `PointerHead` (`kev/model.py:174-192`); read `h[decide]` only (or keep `</opt>` level spans and add a rank-consistent readout). Wire into `_readout` (`:295-296`), `forward_rows_batch` readouts (`:342, 351`), `_readout_many` (`:460-471`), loss in `question_loss` (`kev/train.py:41-61`), `Meta` fields + `head.pt` (`kev/checkpoint.py:428-452`), `to_answers` score branch (`kev/api.py:307-309`) expects per-level probs → convert cumulative → per-level. | kev's own `ord_w` RPS term (`kev/train.py:57-60`) never helped (`PLAN.md:142-143`); a real CORAL head is untested territory here. Keep the pointer as the ablation control. |
| **Binary heads sharing a readout** | Same as above: a `Linear(d,1)` on `h[decide]` per question or a shared one with a question-id embedding. Alternative with zero code change: keep noul as a 2-option pointer (kev's default). | Kev shows noul-via-pointer calibrates fine (`kev-4b.md:85-88`). |
| **Pairwise ranking loss** (success vs failure prefix, same task, similar step) | Batch construction: `encode_batch` (`kev/train.py:245-268`) and `Variant` (`:136-149`) — add `pair_id`; loss term next to `permutation_kl` in `batch_loss` (`:271-294`): logistic/margin on `z_success[true] − z_fail[true]`. `microbatch_plan` (`:178-206`) must keep pairs in one micro-batch (as `none_pair` siblings already are, `:252-254`). | `_meta.group_id` already exists as the bootstrap unit (`kev/suite.py:177`); reuse it for pairing so the paired bootstrap stays valid. |
| **TD smoothing across consecutive prefixes** | Same place as the pairwise loss (sibling variants of one trajectory in one micro-batch). | Interacts with `--row_budget` splitting, which forbids cross-variant terms (`kev/train.py:382-386`). |
| **Per-head temperature** | `PointerHead.temperature` is one float (`kev/model.py:183-187`); make it `dict[qid → T]` in `Meta` (`kev/checkpoint.py:440`), fit per question id with `fit_temperature` on each qid's rows (`kev/metrics.py:242-260`, rows carry `question` = qid, `kev/benchmark.py:56`), apply in `_readout*` by qid (encoded record carries `qid`, `kev/data.py:403`; serving `meta["id"]`, `kev/api.py:268`). | kev found per-(type,K) temperatures worse OOD (`PLAN.md:101-102`), but our question set is fixed, so per-qid is the natural unit. Also add conformal thresholds on top of `coverage_at_error` (`kev/metrics.py:103-111, 149-154`). |
| **Long-context history (8k-16k)** | Raise `MAX_STATE`/`training_context` and `SERVE_MAX_*` (`kev/model.py:14-20`); train with `--shared_prefix 1` on hybrids (`kev/train.py:349`) or the packed mask on attention-only bases; `--row_budget`/`--length_sort` (`:347-352`) for memory. History compression/truncation happens before `encode` in our converter (state is free text via `api.render`). | Packed mask memory O(L²) (`kev/model.py:154`); `cuda_graphs` bank caps at 4,096 state tokens (`kev/cuda_graphs.py:52`); Kev accuracy drops on long documents and buried synthetic states are not real documents (`README.md:196`, `PLAN.md:122-125`). Budget: 27B needed `row_budget 8192` on one H200; 4B at 384-token states used 24.6 GB — expect ~10-20× more activation memory at 8k. |
| **Policy token / `<policy>`** | Cheapest: a `policy: <model name>` field in the state object (rendered as text by `kev/api.py:210-216`) with 30 % "unknown" dropout applied in our data builder. A true special token would need a sixth entry in `SPECIAL` (`kev/model.py:11`) and `--special_embeddings`, which is a recorded negative + merge/MLX incompatibility (`kev/checkpoint.py:654`). | Text field keeps every serving path unchanged. |
| **Thoughts on/off, leak stripping, fake-confidence robustness set** | Entirely in our data layer (`avm/data`), producing kev-shaped JSONL; suite variants like `contrast_cases` (`kev/suite.py:184-214`) are the template for an injected-thought variant scored by `variants` in `summarize` (`kev/benchmark.py:77`). | — |
| **Split by repo/task, leakage** | `freeze` dedups by normalised state text and assigns partitions per source (`kev/suite.py:247-341`); we need family-level (repo/task) grouping → set `_meta.group_id` = task and add a group-disjoint split. `grouped_folds` (`kev/metrics.py:283-296`) already splits by `(source, group)`. | — |
| **AUROC at 25/50/75/90 % prefix, latency p50/p95** | Not in `kev/metrics.py`; add AUROC (binary questions) and a `prefix_frac` field in `_meta`; `latency_ms` median/p95 already reported per benchmark (`kev/benchmark.py:160`). | — |
| **Distributional value head** (optional) | A `score` question over outcome bins already gives a distribution; a dedicated head plugs in like the ordinal head. | — |

Ablation flags that already exist and should be kept as one-liners: `--lora_targets`, `--lora`, `--head_dim`,
`--max_state`, `--shared_prefix`, `--full_ft`, `--perm_kl`, `--ord_w`, `--p_none*`, `--dtype`, `--weights_dtype`
(`kev/train.py:299-358`; allow-listed for studies in `kev/experiment.py:38-50`).

---

## 9. Licensing

- Code: Apache-2.0 (`LICENSE`, `pyproject.toml:325-326`, `README.md:438-440`). Authors: Jared Palmer (with Devin).
- Weights: adapters + heads Apache-2.0; bases Qwen3 / Qwen3.5 / Qwen3.8 Apache-2.0 (`README.md:440`,
  `docs/model-cards/*.md` "License"). Kev-27B's base is post-trained on unknown data (`README.md:393`).
- Training data: the ten public datasets "carry their own licenses" and the repo records **only their HF ids and
  revisions**, not licence terms (`docs/model-cards/kev-4b.md:15-25, 214`; `evals/v7/decision-v7/manifest.json:7-18`).
  Later suites do record licences per source: `devtools-v1` (CC-BY-4.0 CodeReviewer, MIT commitpackft, per-repo SPDX
  filtering, `evals/devtools-v1/manifest.json` → `sources`, `scripts/devtools_v1_licences.json`), `breadth-v1` (some
  non-commercial / share-alike, kept private, `AGENTS.md:113-114`), `documents-v1` CFPB public domain (`PLAN.md:250-251`).
  The Kev-27B SFT corpus (`evals/sft-v1`) is private, manifest only (`PLAN.md:179-187`).
- Rules the project imposes on itself that we should inherit: no Jev outputs in training; open-weight teachers only for
  training labels; frozen files never change (`PLAN.md:169-174`).

---

## 10. Prioritised porting risks and open questions

1. **Backbone choice decides the whole compute path.** Hybrid Qwen3.5 (kev's family): rows form, state × Q recompute
   unless `--shared_prefix`, needs fla 0.5.2 + triton ≥ 3.7.1 + causal-conv1d on Hopper, exact-eval parity caveats
   (TF32-like rounding in fla, `kev/shared_prefix.py:172-173`). Attention-only Qwen3: packed block-causal mask with
   SDPA, simpler, but O(L²) mask memory at 8-16k and an older base. the project brief §5.1 says "latest small Qwen base"; the
   latest (Qwen3.5) is hybrid. Decide early; kev's 4B/9B results say Qwen3.5 > Qwen3 by +7 pp locked transfer
   (`docs/model-cards/kev-9b.md:90`).
2. **Long context is untested in kev beyond 7.5k state tokens** and only the 27B "holds up" on buried states
   (`README.md:196`); our default 8k / 16k history is outside every measured regime. Budget a memory probe first
   (`modal_app.py::smoke_base` pattern, `:249-279`).
3. **Per-branch budget of 640 tokens** (`kev/model.py:20-30`) is too small for `best_next` with several long candidate
   actions; changing it changes what `training_context`/suite admission mean.
4. **M0 as written in the project brief ("kev-0.5b reproduces its README numbers") is not reproducible as stated**: the README
   no longer carries kev-0.5b numbers; the card's numbers (0.799 acc / ECE 0.065, `kev-0.5b.md:146`) come from the
   legacy `kev.evaluate` path on freshly built HF splits (`kev/evaluate.py:186-207`), the card's reproduce command
   (`:124`) omits `--base Qwen/Qwen2.5-0.5B` while `kev.train` now defaults to Qwen3-0.6B-Base (`kev/train.py:301`),
   and augmentation is re-seeded per epoch so runs are not bit-identical (`kev-0.5b.md:127`). Better M0 target:
   reproduce `q35-08b` (Qwen3.5-0.8B, decision-v7, ~20 min H100, dev acc 0.829 / transfer 0.643 at seed 2,
   `runs/leaderboard.md:89`) within seed noise (±1 pp on transfer, `PLAN.md:128-129`), plus the smoke run.
5. **Python/env**: kev needs 3.12/3.13, `uv.lock`, Linux (`fcntl`, `resource`); the local Windows box can only do
   static work and data prep. RunPod H100 image must match torch 2.8 + cu12 + cp313 wheels (`modal_app.py:50-61`).
6. **Shallow clone**: history before 2026-09-22 and the `research-archive-2026-09-24` tag are not present
   (`git rev-list --count HEAD` = 50, `.git/shallow`); `PLAN.md` pointers `A:<path>` cannot be followed locally.
7. **Temperature fitting subtleties**: released T fitted on dev rows, trials on the calibration partition; a T from easy
   rows does not transfer to hard workloads (`PLAN.md:96-102`). We need a calibration split drawn from the same
   prefix-point distribution we serve at, per question.
8. **Serving latency target (~100 ms, 6 questions)**: kev hits 18 ms on H100 for 253-token inputs with CUDA graphs, but
   our states are 30-60× longer and above the graph bank limit; expect the eager path (~130 ms fixed overhead + compute).
   Prefix caching across steps of the same trajectory is a natural win (`PrefixCache` keys on exact state token ids,
   `kev/serve.py:45-51`, so append-only histories will miss unless we cache on a prefix).
9. **Calibration losses were all negative in kev** (`PLAN.md:131`); do not spend early runs on Brier/focal/smoothing.
10. Open questions for the owner: (a) hybrid Qwen3.5 vs attention-only base for the fast tier? (b) accept kev's
    "pointer for everything" as the v0 and add CORAL/binary heads as M4 ablations? (c) what per-question token budget
    for `best_next` candidates (K and length)? (d) do we adopt kev's suite/manifest/`rows.json` conventions wholesale so
    `kev.benchmark`, `kev.calibrate`, `kev.compare` work unchanged on AVM-Bench?
