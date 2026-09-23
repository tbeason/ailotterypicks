"""Build the structured prompt every model receives.

Every model gets the exact same prompt for a given drawing. The prompt text is
saved next to the picks so anyone can audit what the models saw.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Dict, List

from .games import GAMES

PROMPT_VERSION = 1
RECENT_DRAWS = 20
STATS_WINDOW = 100

SYSTEM = (
    "You are a contestant in a public, just-for-fun experiment where AI models "
    "pick lottery numbers before each real drawing. Your pick is recorded before "
    "the drawing and scored afterwards on a public leaderboard. Use any "
    "reasoning or strategy you like. Respond with a single JSON object and "
    "nothing else."
)


def _fmt_draw(d: Dict, bonus_name: str) -> str:
    whites = " ".join(f"{n:02d}" for n in d["numbers"])
    extra = f"  (jackpot {d['jackpot']})" if d.get("jackpot") else ""
    return f"{d['date']}  {whites}  {bonus_name}: {d['bonus']:02d}{extra}"


def build_prompt(game_key: str, draw_date: date, history: List[Dict]) -> Dict[str, str]:
    game = GAMES[game_key]
    era = game.era_for(draw_date)
    past = [h for h in history if h["date"] < draw_date.isoformat()]
    # Frequency stats only use drawings under the current number ranges.
    same_era = [h for h in past if date.fromisoformat(h["date"]) >= era.start]
    window = same_era[-STATS_WINDOW:]

    white_freq = Counter(n for h in window for n in h["numbers"])
    bonus_freq = Counter(h["bonus"] for h in window)
    last_seen: Dict[int, int] = {}
    for i, h in enumerate(reversed(same_era)):
        for n in h["numbers"]:
            last_seen.setdefault(n, i)

    def freq_line(freq: Counter, hi: int) -> str:
        return ", ".join(f"{n}:{freq.get(n, 0)}" for n in range(1, hi + 1))

    overdue = sorted(range(1, era.white_max + 1),
                     key=lambda n: -last_seen.get(n, len(same_era)))[:10]
    recent = "\n".join(_fmt_draw(h, game.bonus_name) for h in reversed(past[-RECENT_DRAWS:]))

    user = f"""GAME: {game.name}
DRAWING DATE: {draw_date.isoformat()} ({draw_date.strftime('%A')})
RULES: Choose {game.white_count} distinct white-ball numbers from 1 to {era.white_max} \
and 1 {game.bonus_name} from 1 to {era.bonus_max}. Ticket price ${era.ticket_price}.
Jackpot = all {game.white_count} white balls + {game.bonus_name}. Order of white balls does not matter.

MOST RECENT {min(RECENT_DRAWS, len(past))} DRAWINGS (newest first):
{recent or '(none available)'}

WHITE-BALL FREQUENCY over the last {len(window)} drawings (number:count):
{freq_line(white_freq, era.white_max)}

{game.bonus_name.upper()} FREQUENCY over the last {len(window)} drawings (number:count):
{freq_line(bonus_freq, era.bonus_max)}

LONGEST-ABSENT WHITE BALLS (drawings since last seen):
{", ".join(f"{n}:{last_seen.get(n, len(same_era))}" for n in overdue)}

Respond with ONLY this JSON object:
{{
  "numbers": [five distinct integers 1-{era.white_max}],
  "bonus": integer 1-{era.bonus_max},
  "strategy": "a short name for your approach (max 6 words)",
  "rationale": "why you chose these numbers (max 60 words)",
  "confidence": integer 0-100, your honest confidence this ticket wins any prize
}}"""
    return {"system": SYSTEM, "user": user, "version": PROMPT_VERSION}
