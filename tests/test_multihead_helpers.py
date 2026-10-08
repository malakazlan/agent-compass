import random

import pytest

torch = pytest.importorskip("torch")

from agent_compass.train.losses import label_index, question_loss  # noqa: E402
from agent_compass.train.multihead import microbatches, units_from_records  # noqa: E402


def test_materialized_record_fields_are_accepted():
    # kev.data.materialize produces `qtype` and int label indices for every type
    assert label_index({"qtype": "noul", "keys": ["false", "true"], "label": 1}) == 1
    assert label_index({"qtype": "choice", "keys": ["a", "b", "c"], "label": 2}) == 2
    assert label_index({"qtype": "score", "keys": ["0", "1", "2", "3"], "label": 3}) == 3
    # raw records
    assert label_index({"type": "noul", "keys": ["false", "true"], "label": True}) == 1
    assert label_index({"type": "choice", "keys": ["a", "b", "c"], "label": "c"}) == 2
    z = torch.tensor([-9.0, 9.0, -9.0, -9.0])
    assert float(question_loss(z, {"qtype": "score", "keys": ["0", "1", "2", "3"], "label": 1})) < 0.01


def rec(i, pair=None):
    m = {"id": f"r{i}", "outcome": i % 2 == 0}
    if pair:
        m["pair_id"] = pair
    return {"_meta": m}


def test_units_keep_pairs_together_and_microbatches_respect_size():
    recs = [rec(0, "p1"), rec(1), rec(2, "p2"), rec(3, "p1"), rec(4), rec(5, "p2"), rec(6)]
    units = units_from_records(recs)
    assert sorted(map(sorted, units)) == [[0, 3], [1], [2, 5], [4], [6]]
    mbs = microbatches([u[:] for u in units], batch=2, rng=random.Random(0))
    flat = sorted(i for mb in mbs for i in mb)
    assert flat == list(range(7))
    for mb in mbs:
        assert len(mb) <= 2
        for pid, members in (("p1", {0, 3}), ("p2", {2, 5})):
            inter = members & set(mb)
            assert inter in (set(), members)  # a pair is either absent or whole


def test_microbatches_larger_than_batch_unit_still_emitted():
    units = [[0, 1, 2]]  # a unit larger than the batch is emitted alone rather than split
    assert microbatches(units, batch=2, rng=random.Random(0)) == [[0, 1, 2]]
