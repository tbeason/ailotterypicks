import json
import random
from datetime import date, timedelta
from datetime import datetime, timezone
from pathlib import Path

import pytest

import lottery.__main__ as cli
from lottery import results
from lottery.games import GAMES, MEGA_MILLIONS, POWERBALL
from lottery.budget import Budget, BudgetError
from lottery.http import redact
from lottery.pickers import ALLOWED_PARAMS, PickError, extract_json, pick_hot, pick_openrouter, pick_random, validate_pick
from lottery.prompt import build_prompt
from lottery.scoring import expected_random, parse_money, score_pick

ROOT = Path(__file__).resolve().parent.parent


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


NEW_CSV = """lottery,date,white_balls,bonus_ball,multiplier,jackpot,cash_value,jackpot_usd,cash_value_usd
powerball,2026-09-19,18 30 41 45 68,10,2,298 Million,127.2 Million,298000000,127200000
powerball,2026-09-21,02 07 09 17 58,20,2,314 Million,133.8 Million,314000000,133800000
"""
OLD_CSV = """lottery,date,jackpot,cash_value
powerball,2025-11-24,N/A,N/A
powerball,2025-11-22,$70 Million,$32.3 Million
"""


def test_parse_lotterywinners_both_schemas():
    new = results.parse_lotterywinners_csv(NEW_CSV)
    assert new["2026-09-21"] == {"jackpot": "314 Million", "cash_value": "133.8 Million",
                                 "jackpot_usd": 314000000, "numbers": [2, 7, 9, 17, 58], "bonus": 20}
    old = results.parse_lotterywinners_csv(OLD_CSV)
    assert old == {"2025-11-22": {"jackpot": "$70 Million", "cash_value": "$32.3 Million"}}


def test_reconcile_verifies_flags_and_fills(capsys):
    lw = results.parse_lotterywinners_csv(NEW_CSV)
    ny = [{"date": "2026-09-19", "numbers": [18, 30, 41, 45, 68], "bonus": 10, "source": "data.ny.gov"}]
    out = results.reconcile("powerball", ny, lw)
    assert out[0]["verified"] and out[0]["jackpot_usd"] == 298000000
    assert out[1]["date"] == "2026-09-21" and out[1]["source"] == "lotterywinners"
    bad = [{"date": "2026-09-19", "numbers": [1, 30, 41, 45, 68], "bonus": 10}]
    assert results.reconcile("powerball", bad, lw)[0]["verified"] is False
    assert "disagree" in capsys.readouterr().out


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


PRICING = {"vendor/m": {"prompt": 1e-6, "completion": 4e-6, "request": 0},
           "vendor/pricey": {"prompt": 60e-6, "completion": 240e-6, "request": 0}}


def make_budget(tmp_path, **limits):
    return Budget.load({"budget": limits}, tmp_path, pricing=PRICING)


def fake_api(monkeypatch, replies, cost=0.0003):
    calls = []
    replies = iter(replies)

    def fake_post(url, body, headers, timeout):
        calls.append(body)
        return {"model": body["model"], "usage": {"cost": cost},
                "choices": [{"message": {"content": next(replies)}}]}

    monkeypatch.setattr("lottery.pickers.post_json", fake_post)
    return calls


def test_openrouter_retries_on_invalid_reply(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testkey123456")
    calls = fake_api(monkeypatch, ['{"numbers":[1,2,3],"bonus":1}',
                                   '{"numbers":[9,2,3,4,5],"bonus":1,"confidence":150}'])
    b = make_budget(tmp_path)
    p = pick_openrouter({"model": "vendor/m"}, "powerball", date(2026, 9, 23),
                        {"system": "s", "user": "u"}, budget=b)
    assert p["numbers"] == [2, 3, 4, 5, 9] and p["attempts"] == 2 and p["confidence"] == 100
    assert "Invalid" in calls[1]["messages"][-1]["content"]
    assert all(c["max_tokens"] == 200 for c in calls)
    assert p["cost_usd"] == 0.0006 and b.run_spent == 0.0006


def test_openrouter_gives_up_after_max_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testkey123456")
    calls = fake_api(monkeypatch, ["nope"] * 5)
    with pytest.raises(PickError) as e:
        pick_openrouter({"model": "vendor/m"}, "powerball", date(2026, 9, 23),
                        {"system": "s", "user": "u"}, budget=make_budget(tmp_path))
    assert len(calls) == 2 and e.value.cost == pytest.approx(0.0006)


def test_openrouter_requires_budget(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testkey123456")
    calls = fake_api(monkeypatch, ["{}"])
    with pytest.raises(PickError, match="budget"):
        pick_openrouter({"model": "vendor/m"}, "powerball", date(2026, 9, 23), {"system": "s", "user": "u"})
    assert calls == []


# --- cost limits ---------------------------------------------------------
def test_budget_blocks_expensive_model(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testkey123456")
    calls = fake_api(monkeypatch, ["{}"])
    with pytest.raises(PickError, match="per-call cap"):
        pick_openrouter({"model": "vendor/pricey"}, "powerball", date(2026, 9, 23),
                        {"system": "s", "user": "u" * 1000}, budget=make_budget(tmp_path))
    assert calls == []


def test_budget_blocks_unknown_model(tmp_path):
    with pytest.raises(BudgetError, match="not in OpenRouter price list"):
        make_budget(tmp_path).approve("vendor/unknown", 100)


def test_budget_monthly_limit_counts_committed_spend(tmp_path):
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    (tmp_path / "powerball").mkdir()
    (tmp_path / "powerball" / "x.json").write_text(json.dumps({"picks": {
        "a": {"picked_at": f"{month}-01T00:00:00+00:00", "cost_usd": 1.999},
        "b": {"picked_at": "1999-01-01T00:00:00+00:00", "cost_usd": 50}}}))
    b = make_budget(tmp_path, monthly_limit_usd=2.0)
    assert b.month_spent == pytest.approx(1.999)
    with pytest.raises(BudgetError, match="monthly limit"):
        b.approve("vendor/m", 1000)


def test_config_params_cannot_raise_output_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-testkey123456")
    calls = fake_api(monkeypatch, ["{}"])
    with pytest.raises(PickError, match="disallowed"):
        pick_openrouter({"model": "vendor/m", "params": {"max_completion_tokens": 99999}},
                        "powerball", date(2026, 9, 23), {"system": "s", "user": "u"},
                        budget=make_budget(tmp_path))
    assert calls == []


def test_shipped_config_is_within_budget():
    cfg = json.loads((ROOT / "config" / "models.json").read_text())
    lim = cfg["budget"]
    assert lim["max_output_tokens"] <= 200 and lim["max_attempts"] <= 2
    assert lim["max_cost_per_call_usd"] <= 0.02 and lim["monthly_limit_usd"] <= 5
    for c in cfg["contestants"]:
        assert set(c.get("params", {})) <= ALLOWED_PARAMS


# --- secrets -------------------------------------------------------------
def test_redact(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "abcdefgh12345678")
    s = redact("key abcdefgh12345678 and sk-or-v1-deadbeefdeadbeef and Bearer xyz123456789")
    assert "abcdefgh" not in s and "deadbeef" not in s and "xyz123" not in s


def test_model_reply_with_key_is_redacted(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-supersecretvalue")
    fake_api(monkeypatch, ['{"numbers":[1,2,3,4,5],"bonus":1,'
                           '"rationale":"my key is sk-or-v1-supersecretvalue"}'])
    p = pick_openrouter({"model": "vendor/m"}, "powerball", date(2026, 9, 23),
                        {"system": "s", "user": "u"}, budget=make_budget(tmp_path))
    assert "supersecret" not in json.dumps(p)


def test_no_secrets_in_repo():
    import re
    import subprocess
    pat = re.compile(r"sk-or-v1-[0-9a-f]{20,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}")
    try:
        files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                               text=True, check=True).stdout.split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("not a git checkout")
    for f in files:
        p = ROOT / f
        if p.is_file() and p.suffix != ".py":  # tests contain fake keys
            assert not pat.search(p.read_text(errors="ignore")), f"possible secret in {f}"


def test_no_third_party_imports():
    """The job holding the API key must run only stdlib code."""
    import ast
    import sys
    for f in (ROOT / "lottery").glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text())):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for n in names:
                top = n.split(".")[0]
                assert top in sys.stdlib_module_names or top == "__future__", f"{f.name} imports {n}"


# --- prompt --------------------------------------------------------------
def test_prompt_excludes_future_and_uses_era_ranges():
    hist = fake_history("megamillions", n=200, end=date(2026, 9, 29))
    p = build_prompt("megamillions", date(2026, 9, 25), hist)["user"]
    assert "2026-09-29" not in p
    assert "1 Mega Ball 1-24" in p


@pytest.mark.parametrize("game", ["powerball", "megamillions"])
def test_prompt_is_small(game):
    p = build_prompt(game, date(2026, 9, 25), fake_history(game, n=300, end=date(2026, 9, 24)))
    total = len(p["system"]) + len(p["user"])
    assert total < 900, total  # roughly <= 350 tokens


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


# --- pick scheduling -----------------------------------------------------
def at(y, m, d, hour):
    return datetime(y, m, d, hour, tzinfo=cli.ET)


def test_next_pick_date_picks_ahead_once_previous_results_are_in():
    hist = [{"date": "2026-09-22"}]  # Tue MM drawing has results
    # Wed morning: next MM drawing is Fri 9/25 and Tue's results are in -> pick now
    assert cli.next_pick_date("megamillions", at(2026, 9, 23, 11), hist) == date(2026, 9, 25)
    # without Tue's results, wait
    assert cli.next_pick_date("megamillions", at(2026, 9, 23, 11), []) is None


def test_next_pick_date_draw_day_and_cutoff():
    # Wed is a Powerball day: pick even if Monday's results are missing
    assert cli.next_pick_date("powerball", at(2026, 9, 23, 11), []) == date(2026, 9, 23)
    # after the cutoff, today's drawing is off-limits; next is Sat 9/26,
    # which needs Wed's results first
    assert cli.next_pick_date("powerball", at(2026, 9, 23, 22), []) is None
    assert cli.next_pick_date("powerball", at(2026, 9, 23, 22),
                              [{"date": "2026-09-23"}]) == date(2026, 9, 26)


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
    assert "longest absent" in rec["prompt"]["user"]

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
