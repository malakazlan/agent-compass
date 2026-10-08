"""Losses for the multi-head run, all on kev's pointer logits (one logit per option).

noul   : 2-option pointer [false, true] -> cross-entropy
choice : K-option pointer -> cross-entropy
score  : K ordered levels -> cumulative-link (CORAL-style) loss on the softmax distribution,
         plus a cross-entropy term so the argmax stays sharp (`ordinal=False` gives plain CE)
pairs  : success/failure prefixes of the same task -> softplus(-(d_s - d_f)) on the yes/no
         logit difference d = z[true] - z[false]  (Fail-Fast's Bradley-Terry term)
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

DEFAULT_WEIGHTS = {"p_success": 1.0, "stuck": 0.5, "progress": 0.5, "steps_left": 0.5, "best_next": 0.5, "escalate": 0.25}
ORDINAL_CE_MIX = 0.5


def qtype(q: dict) -> str:
    """kev's materialized records use `qtype`; raw records use `type`."""
    return q.get("qtype") or q["type"]


def label_index(q: dict) -> int:
    """Index of the true option. Materialized records already carry an int index; raw records carry
    a bool (noul), an option name (choice) or a level index (score)."""
    keys = q["keys"]
    label = q["label"]
    if isinstance(label, bool):
        return int(label)
    if isinstance(label, int):
        return label
    if qtype(q) == "noul":
        return keys.index(str(label).lower())
    if qtype(q) == "choice":
        return keys.index(label)
    return int(label)


def ordinal_loss(z: torch.Tensor, y: int, ce_mix: float = ORDINAL_CE_MIX) -> torch.Tensor:
    """z: logits over K ordered levels, y: true level index. Cumulative-link loss: for every
    threshold j in 0..K-2, BCE(P(level > j), 1[y > j]) where P(level > j) is the softmax mass
    above j. A prediction one level off costs little; one at the far end costs a lot."""
    z = z.float()
    p = torch.softmax(z, -1)
    k = p.shape[-1]
    cum_above = torch.flip(torch.cumsum(torch.flip(p, [-1]), -1), [-1])[1:]  # P(level > j) for j = 0..K-2
    target = (torch.arange(k - 1, device=z.device) < y).float()  # 1[y > j]
    cum_above = cum_above.clamp(1e-6, 1 - 1e-6)
    coral = F.binary_cross_entropy(cum_above, target)
    if ce_mix:
        coral = coral + ce_mix * F.cross_entropy(z[None], torch.tensor([y], device=z.device))
    return coral


def pairwise_loss(z_success: torch.Tensor, z_fail: torch.Tensor) -> torch.Tensor:
    """Bradley-Terry: the successful prefix must score higher on p_success than the failed one."""
    d_s = z_success[1] - z_success[0]
    d_f = z_fail[1] - z_fail[0]
    return F.softplus(-(d_s.float() - d_f.float()))


def question_loss(z: torch.Tensor, q: dict, ordinal: bool = True) -> torch.Tensor:
    y = label_index(q)
    if qtype(q) == "score" and ordinal:
        return ordinal_loss(z, y)
    return F.cross_entropy(z.float()[None], torch.tensor([y], device=z.device))


def weighted_record_loss(logits: dict[str, torch.Tensor], questions: dict[str, dict], weights: dict[str, float] | None = None,
                         ordinal: bool = True) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
    """Sum of per-question losses weighted by question id. Questions absent from `weights` get 1.0."""
    weights = DEFAULT_WEIGHTS if weights is None else weights
    terms: dict[str, torch.Tensor] = {}
    total = None
    for qid, q in questions.items():
        if qid not in logits:
            continue
        t = question_loss(logits[qid], q, ordinal=ordinal)
        terms[qid] = t
        w = weights.get(qid, 1.0)
        total = t * w if total is None else total + t * w
    if total is None:
        total = torch.zeros((), dtype=torch.float32)
    return total, terms
