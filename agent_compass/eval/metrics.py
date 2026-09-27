"""Metrics for calibrated binary and ordinal predictions, with grouped bootstrap CIs.

Everything takes plain Python lists / numpy arrays so it runs on the CPU box without torch.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence

import numpy as np


def auroc(y: Sequence[int], p: Sequence[float]) -> float:
    """Mann-Whitney AUROC with tie handling. Returns nan if one class is missing."""
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    pos = p[y == 1]
    neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p), dtype=float)
    sorted_p = p[order]
    i = 0
    while i < len(p):  # average ranks for ties
        j = i
        while j + 1 < len(p) and sorted_p[j + 1] == sorted_p[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    r_pos = ranks[y == 1].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def brier(y: Sequence[int], p: Sequence[float]) -> float:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return float(np.mean((p - y) ** 2))


def nll(y: Sequence[int], p: Sequence[float], eps: float = 1e-7) -> float:
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(y: Sequence[int], p: Sequence[float], bins: int = 15) -> float:
    """Expected calibration error on the predicted probability of the positive class, equal-width bins."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    total = 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            total += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(total)


def confident_error_rate(y: Sequence[int], p: Sequence[float], threshold: float = 0.9) -> float:
    """Share of all predictions that are confident (max prob >= threshold) and wrong."""
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    conf = np.maximum(p, 1 - p) >= threshold
    pred = (p >= 0.5).astype(int)
    return float(np.mean(conf & (pred != y)))


def accuracy(y: Sequence[int], p: Sequence[float]) -> float:
    y = np.asarray(y, dtype=int)
    return float(np.mean((np.asarray(p) >= 0.5).astype(int) == y))


def reliability_table(y: Sequence[int], p: Sequence[float], bins: int = 15) -> list[dict]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        rows.append({"lo": float(lo), "hi": float(hi), "n": int(m.sum()), "confidence": float(p[m].mean()) if m.any() else None,
                     "accuracy": float(y[m].mean()) if m.any() else None})
    return rows


def recall_at_fpr(y_fail: Sequence[int], score: Sequence[float], fpr: float) -> dict:
    """Fail-Fast style operating point: flag runs whose failure score exceeds the threshold that
    keeps the false-positive rate (successful runs flagged) at most `fpr`.

    Returns recall (share of failures flagged), precision, fired (share flagged), threshold."""
    y = np.asarray(y_fail, dtype=int)
    s = np.asarray(score, dtype=float)
    succ_scores = np.sort(s[y == 0])
    if len(succ_scores) == 0:
        return {"recall": float("nan"), "precision": float("nan"), "fired": float("nan"), "threshold": float("nan")}
    k = int(math.floor(fpr * len(succ_scores)))  # allow at most k successes above threshold
    thr = succ_scores[-k - 1] if k < len(succ_scores) else -math.inf
    flagged = s > thr
    tp = int((flagged & (y == 1)).sum())
    return {
        "recall": tp / max(1, int((y == 1).sum())),
        "precision": tp / max(1, int(flagged.sum())),
        "fired": float(flagged.mean()),
        "threshold": float(thr),
    }


def binary_report(y: Sequence[int], p: Sequence[float]) -> dict:
    return {
        "n": int(len(y)),
        "positive_rate": float(np.mean(y)) if len(y) else float("nan"),
        "auroc": auroc(y, p),
        "accuracy": accuracy(y, p),
        "brier": brier(y, p),
        "nll": nll(y, p),
        "ece15": ece(y, p, 15),
        "confident_error_0.9": confident_error_rate(y, p, 0.9),
    }


def ordinal_report(y: Sequence[int], probs: Sequence[Sequence[float]]) -> dict:
    """For score questions: accuracy, MAE of the expected level, and ranked probability score."""
    y = np.asarray(y, dtype=int)
    P = np.asarray(probs, dtype=float)
    k = P.shape[1]
    levels = np.arange(k)
    expected = (P * levels).sum(1)
    pred = P.argmax(1)
    cum_p = np.cumsum(P, 1)
    cum_y = (levels[None, :] >= y[:, None]).astype(float)
    rps = float(np.mean(((cum_p - cum_y) ** 2).sum(1) / (k - 1)))
    return {"n": int(len(y)), "accuracy": float(np.mean(pred == y)), "mae": float(np.mean(np.abs(expected - y))), "rps": rps,
            "within_one": float(np.mean(np.abs(pred - y) <= 1))}


def grouped_bootstrap(
    groups: Sequence[str],
    stat: Callable[[np.ndarray], float],
    n_boot: int = 1000,
    seed: int = 0,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """(point, lo, hi): resample whole groups (tasks) with replacement, so rows from the same
    task are never treated as independent. `stat` receives an index array into the rows."""
    groups = np.asarray(groups)
    uniq, inv = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inv == g) for g in range(len(uniq))]
    rng = random.Random(seed)
    point = stat(np.arange(len(groups)))
    vals = []
    for _ in range(n_boot):
        pick = [members[rng.randrange(len(uniq))] for _ in range(len(uniq))]
        idx = np.concatenate(pick)
        v = stat(idx)
        if not (isinstance(v, float) and math.isnan(v)):
            vals.append(v)
    if not vals:
        return point, float("nan"), float("nan")
    vals.sort()
    lo = vals[int(alpha / 2 * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha / 2) * len(vals)))]
    return point, lo, hi
