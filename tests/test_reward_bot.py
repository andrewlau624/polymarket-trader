"""M2 reward bot: which sides count as empty, and what we would post."""

from hunt import reward_bot as rb


def book(bids, offers, state="MARKET_STATE_OPEN"):
    lv = lambda xs: [{"px": {"value": str(p)}, "qty": str(q)} for p, q in xs]
    return {"marketData": {"state": state, "bids": lv(bids), "offers": lv(offers)}}


def test_empty_bid_side_gets_a_tenth_cent_bid_for_target():
    out = rb.plan_side(book([], [(0.01, 20000)]), target=5000, half=2.5)
    assert out == [{"side": "buy", "price": 0.001, "qty": 5001, "collateral": 5.0, "pay_day": 2.5}]


def test_side_with_a_real_bid_or_enough_depth_is_left_alone():
    assert rb.plan_side(book([(0.02, 10)], [(0.03, 9000)]), 5000, 2.5) == []        # bid above 1c
    assert rb.plan_side(book([(0.005, 4000)], [(0.03, 9000)]), 5000, 2.5) == []     # 80% of target


def test_empty_ask_near_one_is_a_cheap_sell():
    out = rb.plan_side(book([(0.995, 9000)], []), target=2000, half=1.5)
    assert out[0]["side"] == "sell" and out[0]["price"] == 0.999
    assert out[0]["collateral"] == round(2001 * 0.001, 2)


def test_closed_market_and_programme_filters():
    assert rb.plan_side(book([], [(0.01, 1)], state="MARKET_STATE_CLOSED"), 5000, 2.5) == []
    progs = [{"programId": "p", "period": "daily", "state": "INSTRUMENT_STATE_OPEN", "status": "active",
              "rewardPool": 10, "targetSize": 5000, "slug": s} for s in ("a", "b")]
    assert [c["half"] for c in rb.candidates(progs)] == [2.5, 2.5]
    assert rb.candidates([dict(progs[0], maxSpread=0.03)]) == []
    assert rb.candidates([dict(progs[0], period="live")]) == []


def test_past_dated_slugs_are_skipped():
    assert rb.slug_date_passed("cpc-btc-100k-09-30-2026", today="2026-10-01")
    assert rb.slug_date_passed("aec-nfl-atl-no-2026-09-28", today="2026-10-01")
    assert not rb.slug_date_passed("ewc-usgub-mi-2026-11-03-mikdug", today="2026-10-01")
    assert not rb.slug_date_passed("tec-wnba-mvp-2026-10-15-w-caicla", today="2026-10-01")


def test_tenth_cent_bid_behind_a_one_cent_bid_scores_nothing():
    # 72 shares at 1c are 9 ticks ahead of us: df^9 makes our share ~0
    assert rb.plan_side(book([(0.01, 72)], [(0.02, 9000)]), 15000, 3.7, df=0.25) == []
    # a few shares at the same 0.1c price do not stop us
    out = rb.plan_side(book([(0.001, 50)], [(0.02, 9000)]), 15000, 3.7, df=0.25)
    assert out and out[0]["pay_day"] > 3.6
