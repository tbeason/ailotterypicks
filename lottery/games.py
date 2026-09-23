"""Game rules, drawing schedules, and prize tables.

Prize tables are the *base* (non-jackpot) prizes. Verify against the official
sites if the games change their rules again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Era:
    """A period during which a game's rules were stable."""
    start: date
    white_max: int
    bonus_max: int
    ticket_price: int
    draw_weekdays: Tuple[int, ...]  # Monday=0
    # (white_matches, bonus_matched) -> prize in dollars. The jackpot is keyed
    # (5, True) with value None because it is parimutuel/variable.
    prizes: Dict[Tuple[int, bool], Optional[int]] = field(default_factory=dict)


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    bonus_name: str
    white_count: int
    eras: Tuple[Era, ...]  # sorted oldest -> newest
    home_url: str = ""
    results_url: str = ""  # format string with {date} or {ticks}

    def official_url(self, d: date) -> str:
        """Official results page for drawing date `d`."""
        # .NET DateTime ticks, as megamillions.com expects
        ticks = (d - date(1, 1, 1)).days * 864_000_000_000
        return self.results_url.format(date=d.isoformat(), ticks=ticks)

    def era_for(self, d: date) -> Era:
        chosen = None
        for era in self.eras:
            if d >= era.start:
                chosen = era
        if chosen is None:
            raise ValueError(f"{self.name} has no rules defined for {d}")
        return chosen

    def is_draw_day(self, d: date) -> bool:
        return d.weekday() in self.era_for(d).draw_weekdays

    def next_draw_on_or_after(self, d: date) -> date:
        while not self.is_draw_day(d):
            d += timedelta(days=1)
        return d

    def draw_dates(self, start: date, end: date) -> List[date]:
        out, d = [], start
        while d <= end:
            if self.is_draw_day(d):
                out.append(d)
            d += timedelta(days=1)
        return out


_PB_PRIZES = {
    (5, True): None, (5, False): 1_000_000, (4, True): 50_000, (4, False): 100,
    (3, True): 100, (3, False): 7, (2, True): 7, (1, True): 4, (0, True): 4,
}

POWERBALL = Game(
    key="powerball",
    name="Powerball",
    bonus_name="Powerball",
    white_count=5,
    eras=(
        Era(date(2015, 10, 7), 69, 26, 2, (2, 5), _PB_PRIZES),
        # Monday drawings added 2021-08-23.
        Era(date(2021, 8, 23), 69, 26, 2, (0, 2, 5), _PB_PRIZES),
    ),
    home_url="https://www.powerball.com",
    results_url="https://www.powerball.com/draw-result?gc=powerball&date={date}",
)

MEGA_MILLIONS = Game(
    key="megamillions",
    name="Mega Millions",
    bonus_name="Mega Ball",
    white_count=5,
    eras=(
        Era(date(2017, 10, 31), 70, 25, 2, (1, 4), {
            (5, True): None, (5, False): 1_000_000, (4, True): 10_000,
            (4, False): 500, (3, True): 200, (3, False): 10, (2, True): 10,
            (1, True): 4, (0, True): 2,
        }),
        # April 2025 redesign: $5 ticket, Mega Ball 1-24, and a random 2x-10x
        # multiplier on every *ticket* (not per drawing). Our picks are
        # hypothetical tickets with no multiplier, so we score them at the
        # guaranteed 2x minimum, the same figures megamillions.com lists.
        Era(date(2025, 4, 8), 70, 24, 5, (1, 4), {
            (5, True): None, (5, False): 2_000_000, (4, True): 20_000,
            (4, False): 1_000, (3, True): 400, (3, False): 20, (2, True): 20,
            (1, True): 14, (0, True): 10,
        }),
    ),
    home_url="https://www.megamillions.com",
    results_url=("https://www.megamillions.com/Winning-Numbers/Previous-Drawings/"
                 "Previous-Drawing-Page.aspx?date={ticks}"),
)

# Prize tier -> column prefix in the tbeason/lotterywinners CSVs.
TIER_COLUMNS: Dict[Tuple[int, bool], str] = {
    (5, True): "match_5_bonus", (5, False): "match_5", (4, True): "match_4_bonus",
    (4, False): "match_4", (3, True): "match_3_bonus", (3, False): "match_3",
    (2, True): "match_2_bonus", (1, True): "match_1_bonus", (0, True): "match_0_bonus",
}

GAMES: Dict[str, Game] = {g.key: g for g in (POWERBALL, MEGA_MILLIONS)}
