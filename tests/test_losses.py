import math

import pytest

torch = pytest.importorskip("torch")

from agent_compass.train.losses import (  # noqa: E402
    DEFAULT_WEIGHTS,
    ordinal_loss,
    pairwise_loss,
    question_loss,
    weighted_record_loss,
)


def test_ordinal_loss_prefers_near_misses():
    # 4 ordered levels; true level 3. A distribution peaked at 2 must cost less than one peaked at 0.
    near = torch.tensor([0.05, 0.05, 0.6, 0.3]).log()
    far = torch.tensor([0.6, 0.05, 0.05, 0.3]).log()  # same mass on the true level, the rest far away
    assert ordinal_loss(near, 3) < ordinal_loss(far, 3)
    # plain cross-entropy would rate them equal (same mass on the true level), the ordinal term must not
    ce_near = torch.nn.functional.cross_entropy(near[None], torch.tensor([3]))
    ce_far = torch.nn.functional.cross_entropy(far[None], torch.tensor([3]))
    assert math.isclose(float(ce_near), float(ce_far), rel_tol=1e-5)


def test_ordinal_loss_is_zero_only_when_certain():
    z = torch.tensor([-30.0, -30.0, 30.0, -30.0])
    assert float(ordinal_loss(z, 2)) < 1e-4
    assert float(ordinal_loss(z, 1)) > 1.0


def test_pairwise_loss_direction_and_zero_gap():
    # logits for noul questions are [false, true]; d = true - false
    succ = torch.tensor([0.0, 2.0])  # d = 2
    fail = torch.tensor([0.0, -1.0])  # d = -1
    good = pairwise_loss(succ, fail)
    bad = pairwise_loss(fail, succ)
    assert good < bad
    assert float(pairwise_loss(succ, succ)) == pytest.approx(math.log(2), rel=1e-5)  # softplus(0)


def test_question_loss_dispatch():
    q_noul = {"type": "noul", "label": True, "keys": ["false", "true"]}
    assert float(question_loss(torch.tensor([-5.0, 5.0]), q_noul)) < 0.01
    q_choice = {"type": "choice", "label": "b", "keys": ["a", "b", "c"]}
    assert float(question_loss(torch.tensor([-5.0, 5.0, -5.0]), q_choice)) < 0.01
    q_score = {"type": "score", "label": 2, "keys": ["0", "1", "2", "3"]}
    assert float(question_loss(torch.tensor([-9.0, -9.0, 9.0, -9.0]), q_score, ordinal=True)) < 0.01
    assert float(question_loss(torch.tensor([-9.0, -9.0, 9.0, -9.0]), q_score, ordinal=False)) < 0.01


def test_weighted_record_loss_uses_weights_and_skips_unknown():
    logits = {"p_success": torch.tensor([0.0, 0.0]), "progress": torch.tensor([0.0, 0.0, 0.0, 0.0])}
    qs = {"p_success": {"type": "noul", "label": True, "keys": ["false", "true"]},
          "progress": {"type": "score", "label": 1, "keys": ["0", "1", "2", "3"]}}
    total, terms = weighted_record_loss(logits, qs, weights={"p_success": 1.0, "progress": 0.0})
    assert "p_success" in terms and "progress" in terms
    assert float(total) == pytest.approx(float(terms["p_success"]), rel=1e-6)  # progress weight 0
    assert set(DEFAULT_WEIGHTS) == {"p_success", "stuck", "progress", "steps_left", "best_next", "escalate"}
