"""Contestants: LLMs via OpenRouter, plus non-AI baselines."""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from collections import Counter
from datetime import date
from typing import Dict, List, Optional

import requests

from .games import GAMES

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_ATTEMPTS = 3


class PickError(Exception):
    pass


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
        "strategy": str(obj.get("strategy", ""))[:80],
        "rationale": str(obj.get("rationale", ""))[:600],
        "confidence": conf,
    }


def pick_openrouter(model: Dict, game_key: str, draw_date: date, prompt: Dict) -> Dict:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise PickError("OPENROUTER_API_KEY not set")
    messages = [{"role": "system", "content": prompt["system"]},
                {"role": "user", "content": prompt["user"]}]
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        body = {"model": model["model"], "messages": messages,
                **model.get("params", {})}
        try:
            resp = requests.post(
                OPENROUTER_URL, json=body, timeout=180,
                headers={"Authorization": f"Bearer {key}",
                         "HTTP-Referer": "https://github.com/tbeason/ai-lottery-picks",
                         "X-Title": "AI Lottery Picks"})
            resp.raise_for_status()
            data = resp.json()
            reply = data["choices"][0]["message"].get("content") or ""
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            last_err = f"API error: {e}"
            time.sleep(2 ** attempt)
            continue
        try:
            pick = validate_pick(game_key, draw_date, extract_json(reply))
        except PickError as e:
            last_err = str(e)
            # Tell the model what was wrong and let it try again.
            messages += [{"role": "assistant", "content": reply},
                         {"role": "user", "content": f"That was invalid: {e}. "
                          "Reply again with only the corrected JSON object."}]
            continue
        pick["attempts"] = attempt
        pick["served_model"] = data.get("model", model["model"])
        pick["raw_reply"] = reply[:4000]
        return pick
    raise PickError(f"failed after {MAX_ATTEMPTS} attempts: {last_err}")


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
