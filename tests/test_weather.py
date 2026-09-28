"""Weather study: METAR parsing, rounding, and the local-standard-time climate day."""

from datetime import datetime, timezone

import pytest

import weather_study as ws

M = ("KNYC 251751Z AUTO 03007G21KT 310V130 10SM FEW001 20/06 A3008 RMK AO2 "
     "PK WND 03028/1721 SLP176 T02000056 10200 20144 5802")


def test_parse_t_group_and_six_hour_max():
    t, m6 = ws.parse_metar(M)
    assert t == pytest.approx(68.0) and m6 == pytest.approx(68.0)     # 20.0 C
    t, m6 = ws.parse_metar("KMDW 011651Z 27010KT 10SM CLR M02/M10 A3020 RMK AO2 T10221100")
    assert t == pytest.approx(ws.c_to_f(-2.2)) and m6 is None
    assert ws.parse_metar("KNYC 251051Z AUTO 04008KT 10SM CLR 14/06 A3017") == (None, None)


def test_six_hour_max_must_be_in_remarks():
    # "10SM" visibility must not be read as a 1snTTT group
    assert ws.parse_metar("KNYC 251051Z 10SM CLR 14/06 A3017 RMK AO2 T01440061")[1] is None


def test_whole_f_rounds_half_up():
    assert ws.whole_f(68.5) == 69 and ws.whole_f(68.49) == 68 and ws.whole_f(-0.5) == -1


def test_climate_day_is_local_standard_time():
    utc = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    obs = [(utc("2026-07-10 04:51"), 80.0, None),     # 23:51 EST 7/9 -> day 7/9
           (utc("2026-07-10 05:51"), 79.0, 85.0),     # 00:51 EST 7/10, 6h max spans 7/9
           (utc("2026-07-10 23:51"), 90.0, 91.0)]     # 18:51 EST 7/10
    days = ws.by_climate_day(obs, -5)
    assert [p[1] for p in days[datetime(2026, 7, 9).date()]] == [80.0]
    d10 = days[datetime(2026, 7, 10).date()]
    assert d10[0][2] is None                          # window reached into 7/9: dropped
    assert ws.running_max(d10, 24) == 91.0 and ws.running_max(d10, 12) == 79.0
    assert ws.current(d10, 24) == 90.0


def test_only_the_afternoon_six_hour_max_counts():
    utc = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    obs = [(utc("2026-07-10 17:51"), 80.0, 81.0),     # 12:51 EST: morning group
           (utc("2026-07-10 23:51"), 84.0, 86.0)]     # 18:51 EST: afternoon group
    d = ws.by_climate_day(obs, -5)[datetime(2026, 7, 10).date()]
    assert not ws.has_max6(d, 13.0) and ws.has_max6(d, 19.0)
