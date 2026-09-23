import json
import random
from datetime import date, timedelta
from unittest import mock

import pytest

import lottery.__main__ as cli
from lottery import results
from lottery.games import GAMES, MEGA_MILLIONS, POWERBALL
from lottery.pickers import PickError, extract_json, pick_hot, pick_openrouter, pick_random, validate_pick
from lottery.prompt import build_prompt
from lottery.scoring import expected_random, parse_money, score_pick


def fake_history(game_key, n=150, end=date(2026, 9, 21), seed=1):
    game = GAMES[game_key]
    rng = random.Random(seed)
    dates = [d for d in game.draw_dates(end - timedelta(days=n * 4), end)][-n:]
    out = []
    for d in dates:
        era = game.era_for(d)
        out.append({"date": d.isoformat(),
                    "numbers": sorted(rng.sample(range(1, era.white_max + 1), 5)),
                    "bonus": rng.randint(1, era.bonus_max), "multiplier": 3})
    return out


# --- games ---------------------------------------------------------------
def test_powerball_schedule_change():
    assert not POWERBALL.is_draw_day(date(2021, 8, 16))  # Monday before change
    assert POWERBALL.is_draw_day(date(2021, 8, 23))      # first Monday drawing
    assert POWERBALL.is_draw_day(date(2026, 9, 23))      # Wednesday


def test_megamillions_era():
    assert MEGA_MILLIONS.era_for(date(2025, 4, 4)).bonus_max == 25
    assert MEGA_MILLIONS.era_for(date(2025, 4, 8)).bonus_max == 24
    assert MEGA_MILLIONS.era_for(date(2026, 1, 2)).ticket_price == 5
    assert MEGA_MILLIONS.is_draw_day(date(2026, 9, 22))  # Tuesday


# --- results parsing -----------------------------------------------------
def test_parse_ny_powerball():
    r = results.parse_ny_record("powerball", {
        "draw_date": "2026-09-19T00:00:00.000", "winning_numbers": "44 03 12 28 60 07",
        "multiplier": "2"})
    assert r == {"date": "2026-09-19", "numbers": [3, 12, 28, 44, 60], "bonus": 7,
                 "multiplier": 2, "source": "data.ny.gov"}


def test_parse_ny_megamillions():
    r = results.parse_ny_record("megamillions", {
        "draw_date": "2026-09-19T00:00:00.000", "winning_numbers": "05 11 20 34 49",
        "mega_ball": "09", "multiplier": "04"})
    assert r["numbers"] == [5, 11, 20, 34, 49] and r["bonus"] == 9 and r["multiplier"] == 4


def test_parse_ny_bad_rows():
    assert results.parse_ny_record("powerball", {"draw_date": "2026-09-19"}) is None
    assert results.parse_ny_record("powerball", {"draw_date": "x", "winning_numbers": "1 2 3"}) is None


def test_merge_skips_invalid():
    good = {"date": "2026-09-19", "numbers": [1, 2, 3, 4, 5], "bonus": 26}
    bad = {"date": "2026-09-21", "numbers": [1, 2, 3, 4, 70], "bonus": 1}
    old_rules = {"date": "2012-01-04", "numbers": [1, 2, 3, 4, 59], "bonus": 39}
    merged = results.merge_draws([], [good, bad, old_rules], "powerball")
    assert [m["date"] for m in merged] == ["2026-09-19"]


# --- picks ---------------------------------------------------------------
def test_extract_json_with_fences():
    assert extract_json('Sure!\n```json\n{"numbers":[1,2,3,4,5],"bonus":1}\n```') == \
        {"numbers": [1, 2, 3, 4, 5], "bonus": 1}


@pytest.mark.parametrize("obj", [
    {"numbers": [1, 2, 3, 4], "bonus": 1},
    {"numbers": [1, 1, 2, 3, 4], "bonus": 1},
    {"numbers": [1, 2, 3, 4, 70], "bonus": 1},
    {"numbers": [1, 2, 3, 4, 5], "bonus": 27},
    {"numbers": "lucky", "bonus": 1},
])
def test_validate_rejects(obj):
    with pytest.raises(PickError):
        validate_pick("powerball", date(2026, 9, 23), obj)


def test_validate_megamillions_bonus_range():
    d = date(2026, 9, 25)
    validate_pick("megamillions", d, {"numbers": [1, 2, 3, 4, 70], "bonus": 24})
    with pytest.raises(PickError):
        validate_pick("megamillions", d, {"numbers": [1, 2, 3, 4, 70], "bonus": 25})


def test_random_and_hot_are_valid():
    d = date(2026, 9, 23)
    hist = fake_history("powerball")
    for p in (pick_random({}, "powerball", d, {}), pick_hot({}, "powerball", d, {}, history=hist)):
        validate_pick("powerball", d, p)
    # hot is deterministic
    assert pick_hot({}, "powerball", d, {}, history=hist) == pick_hot({}, "powerball", d, {}, history=hist)


def test_openrouter_retries_on_invalid_reply(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    replies = iter(['{"numbers":[1,2,3],"bonus":1}',
                    '{"numbers":[9,2,3,4,5],"bonus":1,"strategy":"s","rationale":"r","confidence":150}'])
    calls = []

    def fake_post(url, json, timeout, headers):
        calls.append(json["messages"])
        m = mock.Mock()
        m.raise_for_status.return_value = None
        m.json.return_value = {"model": "vendor/m", "choices": [{"message": {"content": next(replies)}}]}
        return m

    monkeypatch.setattr("lottery.pickers.requests.post", fake_post)
    p = pick_openrouter({"model": "vendor/m"}, "powerball", date(2026, 9, 23), {"system": "s", "user": "u"})
    assert p["numbers"] == [2, 3, 4, 5, 9] and p["attempts"] == 2 and p["confidence"] == 100
    assert "invalid" in calls[1][-1]["content"]


# --- prompt --------------------------------------------------------------
def test_prompt_excludes_future_and_other_era():
    hist = fake_history("megamillions", n=200, end=date(2026, 9, 29))
    p = build_prompt("megamillions", date(2026, 9, 25), hist)["user"]
    assert "2026-09-29" not in p
    assert "Mega Ball from 1 to 24" in p
    assert "24:" in p and "25:" not in p.split("MEGA BALL FREQUENCY")[1].split("\n\n")[0]


# --- scoring -------------------------------------------------------------
def test_score_tiers():
    draw = {"date": "2026-09-19", "numbers": [1, 2, 3, 4, 5], "bonus": 10}
    s = score_pick("powerball", {"numbers": [1, 2, 3, 40, 50], "bonus": 10}, draw)
    assert (s["white_matches"], s["bonus_match"], s["prize"], s["cost"]) == (3, True, 100, 2)
    s = score_pick("powerball", {"numbers": [1, 20, 30, 40, 50], "bonus": 11}, draw)
    assert s["prize"] == 0


def test_score_megamillions_multiplier_and_jackpot():
    draw = {"date": "2026-09-22", "numbers": [1, 2, 3, 4, 5], "bonus": 10,
            "multiplier": 3, "jackpot": "$410 Million"}
    s = score_pick("megamillions", {"numbers": [1, 2, 30, 40, 50], "bonus": 10}, draw)
    assert s["prize"] == 30 and s["cost"] == 5
    s = score_pick("megamillions", {"numbers": [1, 2, 3, 4, 5], "bonus": 10}, draw)
    assert s["jackpot"] and s["prize"] == 410_000_000


def test_parse_money():
    assert parse_money("$1.2 Billion") == 1_200_000_000
    assert parse_money("81.2 Million") == 81_200_000
    assert parse_money("N/A") is None


def test_expected_random_powerball():
    e = expected_random("powerball", date(2026, 9, 23))
    assert e["jackpot_odds"] == 292_201_338
    assert abs(e["win_rate"] - 1 / 24.87) < 0.001  # official overall odds 1 in 24.87


# --- end to end ----------------------------------------------------------
def test_pick_score_build(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PICKS_DIR", tmp_path / "picks")
    monkeypatch.setattr(cli, "SITE_DATA", tmp_path / "site.json")
    monkeypatch.setattr(results, "DRAWS_DIR", tmp_path / "draws")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    hist = fake_history("powerball", end=date(2026, 9, 21))
    results.save_draws("powerball", hist)

    d = date(2026, 9, 23)
    rec = cli.run_picks("powerball", d)
    assert "numbers" in rec["picks"]["random"] and "numbers" in rec["picks"]["hot"]
    assert "error" in rec["picks"]["claude-opus"]  # no API key -> recorded error, no crash
    assert "LONGEST-ABSENT" in rec["prompt"]["user"]

    results.save_draws("powerball", hist + [
        {"date": d.isoformat(), "numbers": rec["picks"]["random"]["numbers"],
         "bonus": rec["picks"]["random"]["bonus"], "jackpot": "$100 Million"}])
    assert cli.run_picks("powerball", d) is None  # refuses to pick after the draw
    cli.cmd_score(None)
    scored = json.loads((tmp_path / "picks/powerball/2026-09-23.json").read_text())
    assert scored["scores"]["random"]["jackpot"]
    assert "claude-opus" not in scored["scores"]

    site = cli.build_site_data()
    top = site["leaderboard"][0]
    assert top["id"] == "random" and top["net"] == 100_000_000 - 2
    assert "raw_reply" not in json.dumps(site["draws"])
