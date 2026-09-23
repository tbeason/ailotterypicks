"""Fetch winning numbers and store them in data/draws/<game>.json.

Primary source: New York State open data (Socrata), which republishes the
official multi-state Powerball and Mega Millions results.

Secondary source: the tbeason/lotterywinners scraped CSVs: exact jackpots, plus
winning numbers (newer CSVs) used to cross-check NY and to fill gaps.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .games import GAMES
from .http import HttpError, get_json, get_text

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DRAWS_DIR = DATA_DIR / "draws"

NY_DATASETS = {
    "powerball": "https://data.ny.gov/resource/d6yy-54nr.json",
    "megamillions": "https://data.ny.gov/resource/5xaw-6ayf.json",
}

LOTTERYWINNERS_CSV = {
    "powerball": "https://raw.githubusercontent.com/tbeason/lotterywinners/main/powerball_all_history.csv",
    "megamillions": "https://raw.githubusercontent.com/tbeason/lotterywinners/main/megamillions_all_history.csv",
}

def parse_ny_record(game_key: str, rec: Dict) -> Optional[Dict]:
    """Convert one NY open-data row to our draw format.

    Powerball rows put the Powerball as the 6th number in `winning_numbers`;
    Mega Millions rows have a separate `mega_ball` field.
    """
    try:
        d = rec["draw_date"][:10]
        nums = [int(x) for x in rec["winning_numbers"].split()]
    except (KeyError, ValueError, AttributeError):
        return None
    if "mega_ball" in rec:
        whites, bonus = nums[:5], int(rec["mega_ball"])
    elif len(nums) == 6:
        whites, bonus = nums[:5], nums[5]
    else:
        return None
    if len(whites) != 5:
        return None
    mult = rec.get("multiplier")
    try:
        mult = int(str(mult).lower().rstrip("x")) if mult not in (None, "") else None
    except ValueError:
        mult = None
    return {"date": d, "numbers": sorted(whites), "bonus": bonus,
            "multiplier": mult, "source": "data.ny.gov"}


def fetch_ny(game_key: str, limit: int = 60, since: Optional[str] = None) -> List[Dict]:
    params = {"$order": "draw_date DESC", "$limit": str(limit)}
    if since:
        params["$where"] = f"draw_date >= '{since}T00:00:00'"
    out = [parse_ny_record(game_key, r) for r in get_json(NY_DATASETS[game_key], params)]
    return [r for r in out if r]


def parse_lotterywinners_csv(text: str) -> Dict[str, Dict]:
    """date -> fields from a tbeason/lotterywinners CSV.

    Newer versions of the CSV carry `white_balls` ("02 07 09 17 58"),
    `bonus_ball` and `jackpot_usd`; older ones only have jackpot strings.
    """
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        rec: Dict = {}
        jp = (row.get("jackpot") or "").strip()
        if jp and jp != "N/A":
            rec["jackpot"] = jp
            rec["cash_value"] = row.get("cash_value")
        try:
            if row.get("jackpot_usd"):
                rec["jackpot_usd"] = int(float(row["jackpot_usd"]))
        except ValueError:
            pass
        try:
            whites = [int(x) for x in (row.get("white_balls") or "").split()]
            if len(whites) == 5 and row.get("bonus_ball"):
                rec["numbers"] = sorted(whites)
                rec["bonus"] = int(row["bonus_ball"])
        except ValueError:
            pass
        if rec and row.get("date"):
            out[row["date"]] = rec
    return out


def fetch_lotterywinners(game_key: str) -> Dict[str, Dict]:
    try:
        return parse_lotterywinners_csv(get_text(LOTTERYWINNERS_CSV[game_key]))
    except HttpError as e:
        print(f"[warn] could not fetch lotterywinners CSV for {game_key}: {e}")
        return {}


def reconcile(game_key: str, draws: List[Dict], lw: Dict[str, Dict]) -> List[Dict]:
    """Cross-check NY numbers against lotterywinners and fill gaps from it."""
    by_date = {r["date"]: r for r in draws}
    fallback = []
    for d, rec in lw.items():
        row = by_date.get(d)
        extra = {k: v for k, v in rec.items() if k in ("jackpot", "cash_value", "jackpot_usd")}
        if row is None:
            if "numbers" in rec:
                fallback.append({"date": d, "numbers": rec["numbers"], "bonus": rec["bonus"],
                                 "multiplier": None, "source": "lotterywinners", **extra})
            continue
        row.update(extra)
        if "numbers" in rec:
            if (rec["numbers"], rec["bonus"]) == (row["numbers"], row["bonus"]):
                row["verified"] = True
            else:
                row["verified"] = False
                print(f"[warn] {game_key} {d}: sources disagree: NY {row['numbers']}+{row['bonus']} "
                      f"vs lotterywinners {rec['numbers']}+{rec['bonus']}")
    return merge_draws(draws, fallback, game_key) if fallback else draws


def load_draws(game_key: str) -> List[Dict]:
    path = DRAWS_DIR / f"{game_key}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save_draws(game_key: str, draws: Iterable[Dict]) -> None:
    DRAWS_DIR.mkdir(parents=True, exist_ok=True)
    rows = sorted(draws, key=lambda r: r["date"])
    (DRAWS_DIR / f"{game_key}.json").write_text(json.dumps(rows, indent=1) + "\n")


def validate_draw(game_key: str, draw: Dict) -> Optional[str]:
    game = GAMES[game_key]
    era = game.era_for(date.fromisoformat(draw["date"]))
    nums = draw["numbers"]
    if len(set(nums)) != game.white_count or not all(1 <= n <= era.white_max for n in nums):
        return f"bad white balls {nums}"
    if not 1 <= draw["bonus"] <= era.bonus_max:
        return f"bad bonus {draw['bonus']}"
    return None


def merge_draws(existing: List[Dict], new: List[Dict], game_key: str) -> List[Dict]:
    by_date = {r["date"]: r for r in existing}
    earliest = GAMES[game_key].eras[0].start.isoformat()
    for r in new:
        if r["date"] < earliest:  # older number ranges aren't modeled
            continue
        err = validate_draw(game_key, r)
        if err:
            print(f"[warn] {game_key} {r['date']}: {err}; skipping")
            continue
        old = by_date.get(r["date"])
        if old and (old["numbers"] != r["numbers"] or old["bonus"] != r["bonus"]):
            print(f"[warn] {game_key} {r['date']}: source changed numbers "
                  f"{old['numbers']}+{old['bonus']} -> {r['numbers']}+{r['bonus']}")
        by_date[r["date"]] = {**(old or {}), **r}
    return sorted(by_date.values(), key=lambda r: r["date"])


def update(game_key: str, full: bool = False) -> List[Dict]:
    existing = load_draws(game_key)
    full = full or not existing  # first run backfills everything
    since = None if full else existing[-1]["date"]
    try:
        new = fetch_ny(game_key, limit=5000 if full else 60, since=since)
    except HttpError as e:
        print(f"[warn] NY open data unavailable for {game_key} ({e}); using lotterywinners only")
        new = []
    draws = merge_draws(existing, new, game_key)
    draws = reconcile(game_key, draws, fetch_lotterywinners(game_key))
    save_draws(game_key, draws)
    print(f"{game_key}: {len(draws)} draws stored, latest {draws[-1]['date'] if draws else 'none'}")
    return draws
