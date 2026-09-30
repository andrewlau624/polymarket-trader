"""Weather recorder and report: bands, live state, pricing, grading."""

import json
from datetime import datetime, timezone

import pytest

import weather_recorder as rec
import weather_report as rep
from src.weather import wx


def test_parse_bands():
    assert wx.parse("tc-temp-nychigh-2026-09-25-lt67f") == ("NYC", "2026-09-25", "lt67f", None, 66)
    # venue wording, checked 2026-09-30: "between 70F and 71F"
    assert wx.parse("tc-temp-sfohigh-2026-09-28-gte70lt71f")[3:] == (70, 71)
    assert wx.parse("tc-temp-miahigh-2026-09-28-gte85f")[3:] == (85, None)
    assert wx.parse("tc-temp-xyzhigh-2026-09-28-gte85f") is None
    assert wx.parse("aec-nfl-kc-lv-2026-09-28") is None


def test_band_prob_sums_the_distribution():
    dist = {"0": 0.6, "1": 0.3, "2": 0.1}
    assert wx.band_prob(dist, 70, 70, 70) == pytest.approx(0.6)
    assert wx.band_prob(dist, 70, 71, None) == pytest.approx(0.4)
    assert wx.band_prob(dist, 70, None, 69) == pytest.approx(0.0)


def test_climate_day_is_local_standard():
    # 04:30Z Sep 29 is 23:30 EST Sep 28 for NYC, and 20:30 PST for LAX
    now = datetime(2026, 9, 29, 4, 30, tzinfo=timezone.utc)
    assert wx.climate_day(now, "NYC") == ("2026-09-28", 23)
    assert wx.climate_day(now, "LAX") == ("2026-09-28", 20)


def test_markets_keeps_today_and_yesterday_only():
    now = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)
    mk = rec.markets(["tc-temp-nychigh-2026-09-28-gte70f", "tc-temp-nychigh-2026-09-27-lt60f",
                      "tc-temp-nychigh-2026-09-26-lt60f", "tc-temp-nychigh-2026-09-29-lt60f"], now)
    assert set(mk) == {("NYC", "2026-09-28"), ("NYC", "2026-09-27")}


def test_state_from_live_obs():
    utc = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    obs = [(utc("2026-09-28 16:51"), 66.2, None), (utc("2026-09-28 19:51"), 68.0, None),
           (utc("2026-09-28 23:51"), 64.9, 68.7)]
    s = wx.state(obs, "NYC", "2026-09-28")
    assert s["M"] == 69 and s["g6"] and s["drop"] == pytest.approx(3.8)


def test_report_prices_and_grades(tmp_path, monkeypatch, capsys):
    table = {"LAX": {"16": {"1": {"n": 700, "p": {"0": 0.98, "1": 0.02}}}}}
    tp = tmp_path / "t.json"
    tp.write_text(json.dumps(table))
    r = {"ts": "2026-09-28T23:55:00+00:00", "st": "LAX", "day": "2026-09-28", "hour": 16,
         "state": {"M": 75, "g6": True, "drop": 3.0},
         "bands": {"gte75lt76f": [75, 75], "gte77lt78f": [77, 77]},
         "books": {"gte75lt76f": [0.80, 40, 0.82, 25, "MARKET_STATE_OPEN"],
                   "gte77lt78f": [0.10, 30, 0.12, 20, "MARKET_STATE_OPEN"]}}
    fp = tmp_path / "rec-2026-09-28.jsonl"
    fp.write_text(json.dumps(r))
    monkeypatch.setattr(rep, "truths", lambda recs: {("LAX", "2026-09-28"): 75})
    import sys
    monkeypatch.setattr(sys, "argv", ["weather_report.py", "--recs", str(fp), "--table", str(tp)])
    rep.main()
    out = capsys.readouterr().out
    assert "2 trades over 1 station-days" in out            # YES 75 at 0.82, NO 76 at 0.90
    assert "KNOWN WINNER" in out and "1 times over 1 station-days, won 1" in out


def test_only_final_cli_reports_grade():
    t = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    # NYC is UTC-5 standard: 10:00 local on 9/29 is 15:00Z
    assert not rep.final("NYC", "2026-09-28", t("2026-09-28 22:00"))    # partial-day CLI
    assert not rep.final("NYC", "2026-09-28", t("2026-09-29 14:59"))
    assert rep.final("NYC", "2026-09-28", t("2026-09-29 15:00"))
    assert rep.final("NYC", "2026-09-28", t("2026-10-01 00:00"))


def test_bounds_from_the_name_ignores_old_recorded_widths():
    assert wx.bounds("gte76lt77f") == (76, 77)
    assert wx.bounds("lt80f") == (None, 79) and wx.bounds("gte88f") == (88, None)
