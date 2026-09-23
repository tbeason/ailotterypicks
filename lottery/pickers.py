"""Contestants: LLMs via OpenRouter, plus non-AI baselines."""
from __future__ import annotations

import json
import os
import re
import secrets
from collections import Counter
from datetime import date
from typing import Dict, List, Optional

from .budget import Budget, BudgetError
from .games import GAMES
from .http import HttpError, post_json, redact

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Only these per-model params may come from config; anything that could raise
# output length (max_completion_tokens, n, ...) is rejected.
ALLOWED_PARAMS = {"temperature", "top_p", "reasoning", "provider", "seed"}


class PickError(Exception):
    def __init__(self, message: str, cost: float = 0.0):
        super().__init__(redact(message))
        self.cost = cost


def extract_json(text: str) -> Dict:
    """Pull the first JSON object out of a model reply (tolerates code fences)."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise PickError("no JSON object in reply")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as e:
                    raise PickError(f"invalid JSON: {e}") from e
    raise PickError("unterminated JSON object")


def validate_pick(game_key: str, draw_date: date, obj: Dict) -> Dict:
    game = GAMES[game_key]
    era = game.era_for(draw_date)
    try:
        nums = [int(n) for n in obj["numbers"]]
        bonus = int(obj["bonus"])
    except (KeyError, TypeError, ValueError) as e:
        raise PickError(f"missing or non-integer numbers/bonus: {e}") from e
    if len(nums) != game.white_count or len(set(nums)) != game.white_count:
        raise PickError(f"need {game.white_count} distinct white balls, got {nums}")
    if not all(1 <= n <= era.white_max for n in nums):
        raise PickError(f"white balls must be 1-{era.white_max}, got {nums}")
    if not 1 <= bonus <= era.bonus_max:
        raise PickError(f"{game.bonus_name} must be 1-{era.bonus_max}, got {bonus}")
    conf = obj.get("confidence")
    try:
        conf = max(0, min(100, int(conf))) if conf is not None else None
    except (TypeError, ValueError):
        conf = None
    return {
        "numbers": sorted(nums),
        "bonus": bonus,
        "strategy": redact(str(obj.get("strategy", ""))[:80]),
        "rationale": redact(str(obj.get("rationale", ""))[:400]),
        "confidence": conf,
    }


def pick_openrouter(model: Dict, game_key: str, draw_date: date, prompt: Dict,
                    budget: Optional[Budget] = None) -> Dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise PickError("OPENROUTER_API_KEY not set")
    if budget is None:
        raise PickError("no budget configured; refusing to make a paid call")
    messages = [{"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]}]
    attempts = int(budget.limits["max_attempts"])
    spent = 0.0
    last_err = None
    for attempt in range(1, attempts + 1):
        try:
            budget.approve(model["model"], sum(len(m["content"]) for m in messages))
        except BudgetError as e:
            raise PickError(str(e), cost=spent) from None
        params = model.get("params", {})
        bad = set(params) - ALLOWED_PARAMS
        if bad:
            raise PickError(f"disallowed params in config: {sorted(bad)}")
        body = {**params, "model": model["model"], "messages": messages,
                "max_tokens": int(budget.limits["max_output_tokens"]),
                "usage": {"include": True}}
        try:
            data = post_json(OPENROUTER_URL, body, timeout=120, headers={
                "Authorization": f"Bearer {key}",
                "HTTP-Referer": "https://github.com/tbeason/ai-lottery-picks",
                "X-Title": "AI Lottery Picks"})
        except HttpError as e:
            # Don't retry paid calls on transport errors; next scheduled run will.
            raise PickError(f"API error: {e}", cost=spent) from None
        cost = budget.actual_cost(model["model"], data.get("usage") or {})
        budget.record(cost)
        spent += cost
        try:
            reply = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            last_err = "malformed API response"
            continue
        try:
            pick = validate_pick(game_key, draw_date, extract_json(reply))
        except PickError as e:
            last_err = str(e)
            messages += [{"role": "assistant", "content": reply[:1000]},
                         {"role": "user", "content": f"Invalid: {e}. Reply with only the corrected JSON."}]
            continue
        pick["attempts"] = attempt
        pick["served_model"] = str(data.get("model", model["model"]))[:100]
        pick["cost_usd"] = round(spent, 6)
        pick["raw_reply"] = redact(reply[:2000])
        return pick
    raise PickError(f"failed after {attempts} attempts: {last_err}", cost=spent)


def pick_random(model: Dict, game_key: str, draw_date: date, prompt: Dict) -> Dict:
    era = GAMES[game_key].era_for(draw_date)
    rng = secrets.SystemRandom()
    return {
        "numbers": sorted(rng.sample(range(1, era.white_max + 1), GAMES[game_key].white_count)),
        "bonus": rng.randint(1, era.bonus_max),
        "strategy": "Uniform random (quick pick)",
        "rationale": "Every combination is equally likely, so a quick pick is as good as anything.",
        "confidence": None,
    }


def pick_hot(model: Dict, game_key: str, draw_date: date, prompt: Dict,
             history: Optional[List[Dict]] = None) -> Dict:
    """Deterministic 'hot numbers' baseline: most frequent in the last 100 draws."""
    game = GAMES[game_key]
    era = game.era_for(draw_date)
    window = [h for h in (history or [])
              if era.start.isoformat() <= h["date"] < draw_date.isoformat()][-100:]
    wf = Counter(n for h in window for n in h["numbers"])
    bf = Counter(h["bonus"] for h in window)
    # Tie-break by lowest number so the pick is reproducible.
    whites = sorted(range(1, era.white_max + 1), key=lambda n: (-wf[n], n))[:game.white_count]
    bonus = sorted(range(1, era.bonus_max + 1), key=lambda n: (-bf[n], n))[0]
    return {
        "numbers": sorted(whites),
        "bonus": bonus,
        "strategy": "Hottest numbers, last 100 draws",
        "rationale": "Picks the most frequently drawn numbers. (The gambler's heuristic, done mechanically.)",
        "confidence": None,
    }


def pick_jev(model: Dict, game_key: str, draw_date: date, prompt: Dict) -> Dict:
    # TODO: Jev (TypeSafe AI) is a classifier, not a chat model. Wire this up
    # once we know its API shape, e.g. classify each candidate number as
    # "will be drawn" and take the top-scoring ones.
    raise PickError("Jev adapter not implemented yet")


PROVIDERS = {
    "openrouter": pick_openrouter,
    "random": pick_random,
    "hot": pick_hot,
    "jev": pick_jev,
}
