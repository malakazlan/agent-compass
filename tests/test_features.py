from agent_compass.baselines.features import FEATURE_NAMES, feature_matrix, features_from_record

STATE = """<task>
Fix it
</task>
<policy>llama-70b</policy>
<hist>
... 3 earlier steps omitted ...
4. $ open a.py -> [File: a.py]
5. $ edit 1:2 -> File updated
</hist>
<recent>
## step 6
$ python -m pytest tests/
Traceback (most recent call last):
ValueError: bad
1 passed, 2 failed

## step 7
$ python -m pytest tests/
3 passed

## step 8
$ open b.py
(no output)
</recent>"""

REC = {
    "state": STATE,
    "questions": {"p_success": {"label": True}, "stuck": {"label": False}, "progress": {"label": 2}},
    "_meta": {"prefix_len": 8, "state_tokens": 123, "policy_shown": "llama-70b", "n_steps": 40, "prefix_frac": 0.2},
}


def test_features_are_prefix_only_and_parse_blocks():
    f = features_from_record(REC)
    assert set(f) == set(FEATURE_NAMES)
    assert "n_steps" not in f and "prefix_frac" not in f  # nothing from the future
    assert f["prefix_len"] == 8 and f["state_tokens"] == 123
    assert f["n_recent"] == 3 and f["n_hist_lines"] == 2 and f["n_omitted"] == 3
    assert f["recent_error_steps"] == 1 and f["recent_error_lines"] >= 2
    assert f["recent_repeat_actions"] == 1 and f["recent_distinct_actions"] == 2
    assert f["recent_tests"] == 2 and f["recent_edits"] == 0
    assert f["last_passed"] == 0 and f["last_failed"] == 0 and f["last_has_tests"] == 0  # last step is `open b.py`
    assert f["last_no_output"] == 1 and f["last_is_finish"] == 0
    assert f["stuck_rule"] == 0 and f["progress_rule"] == 2 and f["policy_known"] == 1


def test_feature_matrix_shape_and_unknown_policy():
    rec = dict(REC, _meta={**REC["_meta"], "policy_shown": "unknown"})
    M = feature_matrix([REC, rec])
    assert len(M) == 2 and len(M[0]) == len(FEATURE_NAMES)
    assert M[1][FEATURE_NAMES.index("policy_known")] == 0
