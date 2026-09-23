"""Build the structured prompt every model receives.

Every model gets the exact same prompt for a given drawing. The prompt text is
saved next to the picks so anyone can audit what the models saw.

Kept deliberately compact (~700 characters) because input tokens cost money
on every call; `tests/test_core.py` enforces an upper bound.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Dict, List

from .games import GAMES

PROMPT_VERSION = 2
RECENT_DRAWS = 10
STATS_WINDOW = 100
TOP_N = 8

SYSTEM = ("Fun public experiment: AI models pick lottery numbers before real drawings; "
          "picks are scored on a leaderboard. Use any strategy. Reply with JSON only.")


def build_prompt(game_key: str, draw_date: date, history: List[Dict]) -> Dict:
    game = GAMES[game_key]
    era = game.era_for(draw_date)
    past = [h for h in history if h["date"] < draw_date.isoformat()]
    # Stats only use drawings under the current number ranges.
    same_era = [h for h in past if date.fromisoformat(h["date"]) >= era.start]
    window = same_era[-STATS_WINDOW:]

    wf = Counter(n for h in window for n in h["numbers"])
    bf = Counter(h["bonus"] for h in window)
    last_seen: Dict[int, int] = {}
    for i, h in enumerate(reversed(same_era)):
        for n in h["numbers"]:
            last_seen.setdefault(n, i)

    def top(freq: Counter, hi: int, reverse: bool) -> str:
        order = sorted(range(1, hi + 1), key=lambda n: ((-1 if reverse else 1) * freq[n], n))
        return " ".join(str(n) for n in order[:TOP_N])

    overdue = sorted(range(1, era.white_max + 1),
                     key=lambda n: (-last_seen.get(n, len(same_era)), n))[:TOP_N]
    recent = "\n".join(
        f"{h['date']} {' '.join(str(n) for n in h['numbers'])} | {h['bonus']}"
        for h in reversed(past[-RECENT_DRAWS:]))

    user = f"""{game.name}, drawing {draw_date.isoformat()}.
Pick 5 distinct numbers 1-{era.white_max} and 1 {game.bonus_name} 1-{era.bonus_max}.
Last {min(RECENT_DRAWS, len(past))} drawings (newest first, {game.bonus_name} after |):
{recent or 'none'}
Last {len(window)} drawings: hot {top(wf, era.white_max, True)}; cold {top(wf, era.white_max, False)}; \
{game.bonus_name} hot {top(bf, era.bonus_max, True)}; longest absent {" ".join(map(str, overdue))}
JSON: {{"numbers":[5 ints],"bonus":int,"strategy":"<=6 words","rationale":"<=30 words","confidence":0-100}}"""
    return {"system": SYSTEM, "user": user, "version": PROMPT_VERSION}
