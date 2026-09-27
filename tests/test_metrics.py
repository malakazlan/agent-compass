import math

import numpy as np

from agent_compass.eval.metrics import (
    auroc,
    binary_report,
    brier,
    confident_error_rate,
    ece,
    grouped_bootstrap,
    ordinal_report,
    recall_at_fpr,
)


def test_auroc_basic_and_ties():
    assert auroc([0, 0, 1, 1], [0.1, 0.4, 0.35, 0.8]) == 0.75
    assert auroc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5
    assert auroc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert math.isnan(auroc([1, 1], [0.2, 0.3]))


def test_brier_ece_confident_error():
    assert brier([1, 0], [1.0, 0.0]) == 0.0
    assert brier([1, 0], [0.5, 0.5]) == 0.25
    # perfectly calibrated bins -> zero ECE
    y = [1] * 8 + [0] * 2 + [1] * 2 + [0] * 8
    p = [0.8] * 10 + [0.2] * 10
    assert ece(y, p, bins=10) < 1e-9
    assert confident_error_rate([1, 1, 0, 0], [0.95, 0.6, 0.95, 0.1]) == 0.25


def test_recall_at_fpr_operating_point():
    # failure scores: failures high, successes low, one confident wrong success
    y_fail = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
    score = [0.9, 0.8, 0.7, 0.3, 0.95, 0.2, 0.1, 0.15, 0.05, 0.1]
    op = recall_at_fpr(y_fail, score, fpr=0.0)
    assert op["recall"] == 0.0 and op["fired"] == 0.0  # the 0.95 success caps everything at 0% FPR
    op = recall_at_fpr(y_fail, score, fpr=0.2)  # may flag 1 of 6 successes -> threshold 0.2
    assert op["threshold"] == 0.2
    assert op["recall"] == 1.0 and op["precision"] == 0.8 and abs(op["fired"] - 0.5) < 1e-9


def test_binary_report_keys():
    r = binary_report([0, 1, 1, 0], [0.2, 0.7, 0.9, 0.4])
    assert set(r) == {"n", "positive_rate", "auroc", "accuracy", "brier", "nll", "ece15", "confident_error_0.9"}
    assert r["n"] == 4 and r["accuracy"] == 1.0


def test_ordinal_report():
    probs = [[0.7, 0.2, 0.1, 0.0], [0.0, 0.1, 0.8, 0.1], [0.25, 0.25, 0.25, 0.25]]
    r = ordinal_report([0, 2, 3], probs)
    assert r["accuracy"] == 2 / 3 and r["within_one"] == 2 / 3
    assert 0 < r["rps"] < 1 and r["mae"] > 0


def test_grouped_bootstrap_is_seeded_and_brackets_point():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 400)
    p = np.clip(y * 0.4 + rng.random(400) * 0.6, 0, 1)
    groups = [f"g{i // 8}" for i in range(400)]
    stat = lambda idx: auroc(y[idx], p[idx])  # noqa: E731
    point, lo, hi = grouped_bootstrap(groups, stat, n_boot=200, seed=1)
    point2, lo2, hi2 = grouped_bootstrap(groups, stat, n_boot=200, seed=1)
    assert (point, lo, hi) == (point2, lo2, hi2)
    assert lo <= point <= hi and hi - lo < 0.2
