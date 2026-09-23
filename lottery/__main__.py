"""CLI: python -m lottery <command>

  update-results [--full]      fetch winning numbers into data/draws/
  pick [--date D] [--game G] [--only ID] [--force]
                               collect picks for drawings on date D (default: today, ET)
  score                        score every pick whose drawing has results
  build-site                   write site/data.json for the web page
  check-models                 verify OpenRouter model IDs exist
  daily                        update-results + score + pick + build-site
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from . import results
from .budget import Budget, BudgetError, fetch_models, fetch_pricing
from .games import GAMES
from .http import HttpError
from .pickers import PROVIDERS, PickError
from .prompt import build_prompt
from .scoring import expected_random, score_pick

ROOT = Path(__file__).resolve().parent.parent
PICKS_DIR = ROOT / "data" / "picks"
CONFIG = ROOT / "config" / "models.json"
SITE_DATA = ROOT / "site" / "data.json"
ET = ZoneInfo("America/New_York")
# Picks must be locked in before drawings (~10:59pm ET); stop at 9pm ET.
PICK_CUTOFF_HOUR_ET = 21


def load_config() -> Dict:
    return json.loads(CONFIG.read_text())


def load_contestants(enabled_only: bool = True) -> List[Dict]:
    cs = load_config()["contestants"]
    return [c for c in cs if c.get("enabled") or not enabled_only]


def picks_path(game_key: str, d: str) -> Path:
    return PICKS_DIR / game_key / f"{d}.json"


def load_json(p: Path) -> Optional[Dict]:
    return json.loads(p.read_text()) if p.exists() else None


def write_json(p: Path, obj: Dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1) + "\n")


def now_et() -> datetime:
    return datetime.now(ET)


# --------------------------------------------------------------------------
def cmd_update_results(args) -> None:
    for g in GAMES:
        try:
            results.update(g, full=args.full)
        except HttpError as e:
            print(f"[error] {g}: {e}", file=sys.stderr)


def run_picks(game_key: str, d: date, only: Optional[str] = None, force: bool = False,
              budget: Optional[Budget] = None) -> Optional[Dict]:
    path = picks_path(game_key, d.isoformat())
    history = results.load_draws(game_key)
    if any(h["date"] == d.isoformat() for h in history):
        print(f"{game_key} {d}: results already known; refusing to pick after the draw")
        return None
    prompt = build_prompt(game_key, d, history)
    rec = load_json(path) or {
        "game": game_key, "date": d.isoformat(),
        "prompt_version": prompt["version"],
        "prompt": {"system": prompt["system"], "user": prompt["user"]},
        "picks": {},
    }
    for c in load_contestants():
        if only and c["id"] != only:
            continue
        existing = rec["picks"].get(c["id"])
        if existing and "numbers" in existing and not force:
            continue
        fn = PROVIDERS[c["provider"]]
        kwargs = {"history": history} if c["provider"] == "hot" else {}
        if c["provider"] == "openrouter":
            kwargs["budget"] = budget
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            pick = fn(c, game_key, d, prompt, **kwargs)
            print(f"  {c['id']:<12} {pick['numbers']} + {pick['bonus']}")
        except PickError as e:
            pick = {"error": str(e), "cost_usd": round(e.cost, 6)}
            print(f"  {c['id']:<12} ERROR {e}")
        rec["picks"][c["id"]] = {"label": c["label"], "model": c["model"],
                                 "picked_at": stamp, **pick}
    write_json(path, rec)
    return rec


def cmd_pick(args) -> None:
    now = now_et()
    d = date.fromisoformat(args.date) if args.date else now.date()
    if not args.date and now.hour >= PICK_CUTOFF_HOUR_ET:
        print(f"Past {PICK_CUTOFF_HOUR_ET}:00 ET; too close to the drawing. Skipping.")
        return
    games = [g for g, game in GAMES.items()
             if (not args.game or g == args.game) and game.is_draw_day(d)]
    if not games:
        return
    budget = None
    if any(c["provider"] == "openrouter" for c in load_contestants()):
        try:
            budget = Budget.load(load_config(), PICKS_DIR)
            print(f"Budget: ${budget.month_spent:.4f} spent this month of "
                  f"${budget.limits['monthly_limit_usd']:.2f}")
        except (HttpError, BudgetError) as e:
            print(f"[warn] no budget ({e}); LLM contestants will be skipped")
    for g in games:
        print(f"{GAMES[g].name} {d}:")
        run_picks(g, d, only=args.only, force=args.force, budget=budget)
    if budget:
        print(f"This run spent ${budget.run_spent:.4f}")


def cmd_score(args) -> None:
    for g in GAMES:
        draws = {r["date"]: r for r in results.load_draws(g)}
        for p in sorted((PICKS_DIR / g).glob("*.json")):
            rec = load_json(p)
            draw = draws.get(rec["date"])
            if not draw:
                continue
            rec["result"] = {k: draw.get(k) for k in ("numbers", "bonus", "multiplier", "jackpot")}
            rec["scores"] = {cid: score_pick(g, pk, draw)
                             for cid, pk in rec["picks"].items() if "numbers" in pk}
            write_json(p, rec)


def build_site_data() -> Dict:
    contestants = {c["id"]: c for c in load_contestants(enabled_only=False)}
    board: Dict[str, Dict] = {}
    draws_out = []
    for g, game in GAMES.items():
        for p in sorted((PICKS_DIR / g).glob("*.json")):
            rec = load_json(p)
            picks = {cid: {k: v for k, v in pk.items() if k != "raw_reply"}
                     for cid, pk in rec["picks"].items()}
            draws_out.append({k: rec.get(k) for k in ("game", "date", "result", "scores")}
                             | {"picks": picks, "game_name": game.name,
                                "bonus_name": game.bonus_name})
            for cid, s in (rec.get("scores") or {}).items():
                b = board.setdefault(cid, {
                    "id": cid, "label": contestants.get(cid, {}).get("label", cid),
                    "baseline": bool(contestants.get(cid, {}).get("baseline")),
                    "tickets": 0, "spent": 0, "won": 0, "wins": 0, "white_matches": 0,
                    "bonus_matches": 0, "best": None, "by_game": {}})
                b["tickets"] += 1
                b["spent"] += s["cost"]
                b["won"] += s["prize"]
                b["wins"] += s["prize"] > 0
                b["white_matches"] += s["white_matches"]
                b["bonus_matches"] += s["bonus_match"]
                gb = b["by_game"].setdefault(g, {"tickets": 0, "spent": 0, "won": 0})
                gb["tickets"] += 1
                gb["spent"] += s["cost"]
                gb["won"] += s["prize"]
                tier = (s["white_matches"], s["bonus_match"])
                if b["best"] is None or tier > tuple(b["best"]["tier"]):
                    b["best"] = {"tier": list(tier), "date": rec["date"], "game": g}
    for b in board.values():
        n = b["tickets"] or 1
        b["net"] = b["won"] - b["spent"]
        b["avg_white_matches"] = b["white_matches"] / n
        b["bonus_rate"] = b["bonus_matches"] / n
    today = now_et().date()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "leaderboard": sorted(board.values(), key=lambda b: -b["net"]),
        "draws": sorted(draws_out, key=lambda r: (r["date"], r["game"]), reverse=True),
        "expected_random": {g: expected_random(g, today) for g in GAMES},
        "contestants": [{k: c.get(k) for k in ("id", "label", "model", "enabled", "baseline")}
                        for c in contestants.values()],
    }


def cmd_build_site(args) -> None:
    write_json(SITE_DATA, build_site_data())
    print(f"wrote {SITE_DATA}")


def cmd_check_models(args) -> None:
    """Verify model IDs exist and show each one's worst-case cost per call.

    For a missing ID, list that vendor's newest models (by release date) with
    their worst-case cost, so picking a replacement is easy.
    """
    models = fetch_models()
    pricing = fetch_pricing(models)
    budget = Budget.load(load_config(), PICKS_DIR, pricing=pricing)
    sample = build_prompt("powerball", now_et().date(), results.load_draws("powerball"))
    chars = len(sample["system"]) + len(sample["user"])

    def cost_note(mid: str) -> str:
        try:
            return f"${budget.worst_case(mid, chars):.5f}"
        except BudgetError:
            return "unpriced"

    bad = 0
    for c in load_contestants():
        if c["provider"] != "openrouter":
            continue
        if c["model"] not in pricing:
            bad += 1
            vendor = c["model"].split("/")[0]
            newest = sorted((m for m in models if m["id"].startswith(vendor + "/")
                             and ":" not in m["id"]),
                            key=lambda m: -(m.get("created") or 0))[:12]
            print(f"MISSING  {c['model']}. Newest {vendor} models (worst case per call):")
            for m in newest:
                print(f"           {m['id']:<45} {cost_note(m['id'])}")
            continue
        try:
            wc = budget.approve(c["model"], chars)
            print(f"ok       {c['model']:<40} worst case ${wc:.5f}/call")
        except BudgetError as e:
            bad += 1
            print(f"TOO EXPENSIVE  {e}")
    if bad:
        raise SystemExit(f"{bad} model(s) missing or over budget")


def cmd_daily(args) -> None:
    cmd_update_results(argparse.Namespace(full=False))
    cmd_score(args)
    cmd_pick(argparse.Namespace(date=None, game=None, only=None, force=False))
    cmd_build_site(args)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="lottery")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("update-results"); s.add_argument("--full", action="store_true")
    s.set_defaults(fn=cmd_update_results)
    s = sub.add_parser("pick")
    s.add_argument("--date"); s.add_argument("--game", choices=list(GAMES))
    s.add_argument("--only"); s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_pick)
    sub.add_parser("score").set_defaults(fn=cmd_score)
    sub.add_parser("build-site").set_defaults(fn=cmd_build_site)
    sub.add_parser("check-models").set_defaults(fn=cmd_check_models)
    sub.add_parser("daily").set_defaults(fn=cmd_daily)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
