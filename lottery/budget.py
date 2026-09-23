"""Hard cost limits for model calls.

Three layers, all enforced before any request is sent:
  1. per call: worst-case cost from live OpenRouter pricing, input estimate,
     and the max_tokens cap must be under `max_cost_per_call_usd`;
  2. per month: recorded spend (summed from committed pick files) plus the
     worst case must stay under `monthly_limit_usd`;
  3. the OpenRouter key itself should have a credit limit (see README).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .http import get_json

MODELS_URL = "https://openrouter.ai/api/v1/models"
# Digits and punctuation tokenize poorly; assume 2.5 chars/token to overestimate.
CHARS_PER_TOKEN = 2.5

DEFAULTS = {
    "max_output_tokens": 200,
    "max_attempts": 2,
    "max_cost_per_call_usd": 0.02,
    "monthly_limit_usd": 2.00,
}


class BudgetError(Exception):
    pass


def fetch_pricing() -> Dict[str, Dict[str, float]]:
    """model id -> USD per token (prompt, completion) and per request."""
    out = {}
    for m in get_json(MODELS_URL)["data"]:
        p = m.get("pricing") or {}
        try:
            out[m["id"]] = {k: float(p.get(k) or 0) for k in ("prompt", "completion", "request")}
        except (TypeError, ValueError):
            continue
    return out


def spent_in_month(picks_dir: Path, month: str) -> float:
    total = 0.0
    for f in picks_dir.glob("*/*.json"):
        for pk in json.loads(f.read_text()).get("picks", {}).values():
            if str(pk.get("picked_at", "")).startswith(month):
                total += float(pk.get("cost_usd") or 0)
    return total


@dataclass
class Budget:
    limits: Dict[str, float]
    pricing: Dict[str, Dict[str, float]]
    month_spent: float
    run_spent: float = 0.0
    log: list = field(default_factory=list)

    @classmethod
    def load(cls, config: Dict, picks_dir: Path, pricing: Optional[Dict] = None) -> "Budget":
        limits = {**DEFAULTS, **config.get("budget", {})}
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        return cls(limits, pricing if pricing is not None else fetch_pricing(),
                   spent_in_month(picks_dir, month))

    def worst_case(self, model_id: str, input_chars: int) -> float:
        price = self.pricing.get(model_id)
        if price is None:
            raise BudgetError(f"{model_id} not in OpenRouter price list; refusing to call")
        in_tok = input_chars / CHARS_PER_TOKEN
        out_tok = self.limits["max_output_tokens"]
        total = 0.0
        for attempt in range(int(self.limits["max_attempts"])):
            # A retry resends the conversation plus the previous reply.
            total += (in_tok + attempt * (out_tok + 60)) * price["prompt"]
            total += out_tok * price["completion"] + price["request"]
        return total

    def approve(self, model_id: str, input_chars: int) -> float:
        wc = self.worst_case(model_id, input_chars)
        per_call = self.limits["max_cost_per_call_usd"]
        if wc > per_call:
            raise BudgetError(f"{model_id} worst case ${wc:.4f} exceeds per-call cap ${per_call}")
        monthly = self.limits["monthly_limit_usd"]
        if self.month_spent + self.run_spent + wc > monthly:
            raise BudgetError(f"monthly limit ${monthly} would be exceeded "
                              f"(spent ${self.month_spent + self.run_spent:.4f})")
        return wc

    def actual_cost(self, model_id: str, usage: Dict) -> float:
        if usage.get("cost") is not None:
            return float(usage["cost"])
        price = self.pricing.get(model_id, {"prompt": 0, "completion": 0, "request": 0})
        return (usage.get("prompt_tokens", 0) * price["prompt"]
                + usage.get("completion_tokens", 0) * price["completion"] + price["request"])

    def record(self, cost: float) -> None:
        self.run_spent += cost
