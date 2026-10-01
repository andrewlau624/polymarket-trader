"""S4 (international book -> Polymarket US): entry rule, sides, markout."""

import pytest

from hunt import lag_report as lr
from src.xvenue import core


def obs(t, us, intl, slug="g", tu=None, ti=None):
    return {"t": t, "tu": t if tu is None else tu, "ti": t if ti is None else ti,
            "us_slug": slug, "us": us, "intl": intl}


OPEN = "MARKET_STATE_OPEN"
DEEP = 10000.0


def test_long_entry_when_us_ask_below_intl_mid():
    r = obs(0, [0.40, 100, 0.41, 100, OPEN], [0.44, DEEP, 0.45, DEEP])
    e = lr.entries([r])
    assert [(x["team"], x["cost"]) for x in e] == [("L", 0.41)]


def test_short_entry_uses_one_minus_us_bid():
    # intl says long team 0.40; US long bid 0.44 -> short costs 0.56 vs fair 0.60
    r = obs(0, [0.44, 100, 0.45, 100, OPEN], [0.395, DEEP, 0.405, DEEP])
    e = lr.entries([r])
    assert [(x["team"], round(x["cost"], 4)) for x in e] == [("S", 0.56)]


def test_filters_wide_thin_skewed_closed_and_cooldown():
    good_us, good_i = [0.40, 100, 0.41, 100, OPEN], [0.44, DEEP, 0.45, DEEP]
    assert lr.entries([obs(0, good_us, [0.42, DEEP, 0.47, DEEP])]) == []            # 5c intl spread
    assert lr.entries([obs(0, good_us, [0.44, 10, 0.45, 10])]) == []                # $4 deep
    assert lr.entries([obs(0, good_us, good_i, tu=0, ti=1.5)]) == []                # 1.5 s apart
    assert lr.entries([obs(0, [0.40, 100, 0.41, 100, "MARKET_STATE_CLOSED"], good_i)]) == []
    assert len(lr.entries([obs(0, good_us, good_i), obs(300, good_us, good_i)])) == 1
    assert len(lr.entries([obs(0, good_us, good_i), obs(700, good_us, good_i)])) == 2


def test_markout_sells_into_the_bid_with_both_fees():
    e = {"t": 0, "game": "g", "team": "L", "cost": 0.41}
    later = obs(65, [0.43, 100, 0.44, 100, OPEN], [0.44, DEEP, 0.45, DEEP])
    m = lr.markout(e, {"g": [later]}, 60)
    assert m == pytest.approx(0.43 - 0.41 - core.pm_fee(0.41) - core.pm_fee(0.43))
    s = dict(e, team="S", cost=0.56)                          # short exits at 1 - ask
    assert lr.markout(s, {"g": [later]}, 60) == pytest.approx(0.56 - 0.56 - core.pm_fee(0.56) * 2)
    assert lr.markout(e, {"g": [obs(400, later["us"], later["intl"])]}, 60) is None    # too stale
