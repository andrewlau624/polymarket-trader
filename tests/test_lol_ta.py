"""Chart-only signals: bars, signal timing, and the cost of a round trip."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import lol_ta as ta
from src.pm_us.fees import taker_fee


def _path(mids, spread=0.01):
    t0 = datetime(2026, 9, 28, tzinfo=timezone.utc).timestamp()
    return [(t0 + 5 * i, round(m - spread / 2, 4), round(m + spread / 2, 4))
            for i, m in enumerate(mids)]


def test_trade_is_ask_in_bid_out_with_fees():
    b = [(0.49, 0.51, 0.50), (0.59, 0.61, 0.60)]
    assert ta.trade(b, 0, 1, +1) == pytest.approx(0.59 - 0.51 - taker_fee(0.51) - taker_fee(0.59))
    # buying B: pay 1 - bid_A, sell at 1 - ask_A
    assert ta.trade(b, 0, 1, -1) == pytest.approx(0.39 - 0.51 - taker_fee(0.51) - taker_fee(0.39))


def test_signals_use_only_past_bars():
    mids = [0.50] * 30 + [0.56] * 30
    s = ta.signals(mids, 30)
    assert s["momentum"] == 1 and s["reversion"] == -1 and s["breakout"] == 1
    assert ta.signals(mids[:31], 30) == s          # future bars change nothing


def test_a_trending_path_pays_momentum_and_a_flat_one_pays_nothing():
    trend = [0.20 + 0.01 * i for i in range(60)]            # +6c per 30 s (6 bars)
    res = ta.run({"g": _path(trend)})
    assert ta.mean([v for _, v in res["momentum"]]) > 0
    flat = ta.run({"g": _path([0.5] * 150)})
    assert not any(flat.values())


def test_run_on_a_file(tmp_path, capsys):
    t0 = datetime(2026, 9, 28, 18, tzinfo=timezone.utc)
    rows = [{"ts": (t0 + timedelta(seconds=3 * i)).isoformat(), "event": "lol-a-b-x",
             "game": 1, "best_of": 1,
             "q": {"match": [0.40 + 0.001 * i, 10, 0.41 + 0.001 * i, 10, "MARKET_STATE_OPEN"]}}
            for i in range(300)]
    fp = tmp_path / "o.jsonl"
    fp.write_text("\n".join(json.dumps(r) for r in rows))
    import sys
    sys.argv = ["lol_ta.py", "--obs", str(fp)]
    ta.main()
    out = capsys.readouterr().out
    assert "1 games" in out and "round trip here costs" in out
    cost = float(out.split("costs ")[1].split("/share")[0])
    assert 0.02 < cost < 0.06                      # two ~1.7c fees + a 1c spread
