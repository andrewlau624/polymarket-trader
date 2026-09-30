"""Buy-the-recoverable-dip: entries, the state filter, exits, settlement."""

import pytest

import lol_dip as D
from src.pm_us.fees import taker_fee


def pts(mids, feed, spread=0.01):
    return [(3.0 * i, round(m - spread / 2, 4), round(m + spread / 2, 4), feed)
            for i, m in enumerate(mids)]


CLOSE = {"minute": 20.0, "gold": -1500, "inhibs": 0}
LOST = {"minute": 20.0, "gold": -6000, "inhibs": 0}


def test_drop_on_team_a_enters_long_a_only_when_live():
    mids = [0.60] * 10 + [0.45] * 10 + [0.55] * 110
    assert D.entries(pts(mids, CLOSE), True) == [(10, 1)]
    assert D.entries(pts(mids, LOST), True) == []           # gold gap too big
    assert D.entries(pts(mids, LOST), False) == [(10, 1)]   # control ignores state
    early = {**CLOSE, "minute": 8.0}
    assert D.entries(pts(mids, early), True) == []


def test_a_rise_is_a_drop_for_team_b():
    mids = [0.40] * 10 + [0.55] * 20
    assert D.entries(pts(mids, {**CLOSE, "gold": 1500}), True) == [(10, -1)]


def test_exits_and_hold_to_end():
    mids = [0.60] * 10 + [0.45] * 10 + [0.58] * 120 + [0.99] * 5
    p = pts(mids, CLOSE)
    out = D.outcomes(p, 10, 1, D.winner(p))
    cost = 0.455
    assert out["bracket"] == pytest.approx(0.575 - cost - taker_fee(cost) - taker_fee(0.575))
    assert out["hold_to_end"] == pytest.approx(1 - cost - taker_fee(cost))
    assert out["sell60s"] == pytest.approx(0.575 - cost - taker_fee(cost) - taker_fee(0.575))


def test_undecided_recording_has_no_hold_result():
    p = pts([0.60] * 10 + [0.45] * 120, CLOSE)
    assert D.winner(p) is None
    assert "hold_to_end" not in D.outcomes(p, 10, 1, None)
