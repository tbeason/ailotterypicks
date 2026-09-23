"""Score picks against drawn numbers."""
from __future__ import annotations

import re
from datetime import date
from math import comb
from typing import Dict, Optional

from .games import GAMES, TIER_COLUMNS


def parse_money(s: Optional[str]) -> Optional[int]:
    """'$1.2 Billion' / '175 Million' -> dollars."""
    if not s:
        return None
    m = re.search(r"([\d,.]+)\s*(million|billion)?", s, re.I)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
    unit = (m.group(2) or "").lower()
    return int(val * {"million": 1e6, "billion": 1e9}.get(unit, 1))


def score_pick(game_key: str, pick: Dict, draw: Dict) -> Dict:
    game = GAMES[game_key]
    era = game.era_for(date.fromisoformat(draw["date"]))
    whites = len(set(pick["numbers"]) & set(draw["numbers"]))
    bonus = pick["bonus"] == draw["bonus"]
    key = (whites, bonus)
    jackpot = key == (5, True)
    official = draw.get("prizes") or {}
    if jackpot:
        prize = draw.get("jackpot_usd") or parse_money(draw.get("jackpot")) or 0
        source = "official" if draw.get("jackpot_usd") else "advertised"
    elif key not in TIER_COLUMNS:
        prize, source = 0, "none"
    elif TIER_COLUMNS[key] in official:
        # Per-drawing official prize (lotterywinners CSV). Differs from the
        # table only if the lottery changes its prize matrix.
        prize, source = official[TIER_COLUMNS[key]], "official"
    else:
        prize, source = era.prizes.get(key) or 0, "table"
    return {"white_matches": whites, "bonus_match": bonus, "prize": prize,
            "prize_source": source, "jackpot": jackpot, "cost": era.ticket_price}


def expected_random(game_key: str, d: date) -> Dict:
    """Exact expectations for a uniformly random ticket (jackpot excluded)."""
    game = GAMES[game_key]
    era = game.era_for(d)
    k, n, b = game.white_count, era.white_max, era.bonus_max
    total = comb(n, k)
    ev_prize = 0.0
    p_any = 0.0
    for w in range(k + 1):
        pw = comb(k, w) * comb(n - k, k - w) / total
        for hit, pb in ((True, 1 / b), (False, 1 - 1 / b)):
            prize = era.prizes.get((w, hit))
            if prize:  # jackpot (None) and non-winning tiers are skipped
                ev_prize += pw * pb * prize
            if (w, hit) in era.prizes:
                p_any += pw * pb
    return {
        "white_matches": k * k / n,
        "bonus_match": 1 / b,
        "win_rate": p_any,
        "return_per_dollar_ex_jackpot": ev_prize / era.ticket_price,
        "jackpot_odds": total * b,
    }
