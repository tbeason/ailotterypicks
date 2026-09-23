# AI Lottery Picks

AI models pick Powerball and Mega Millions numbers before every drawing. A random quick pick
keeps them honest.

**The honest part:** drawings are independent and uniform. No model can beat the random
baseline except by luck. The leaderboard is a noise generator. The rationales are the show.

## How it works

1. **Every day** (`.github/workflows/daily.yml`, 15:00 and 20:00 UTC):
   1. Fetch new winning numbers from [data.ny.gov](https://data.ny.gov) (Powerball `d6yy-54nr`,
      Mega Millions `5xaw-6ayf`). Attach jackpot sizes from
      [tbeason/lotterywinners](https://github.com/tbeason/lotterywinners).
   2. Score every stored pick whose drawing now has results.
   3. If tonight has a drawing, send every contestant the **same prompt**
      (`lottery/prompt.py`). The prompt contains the rules, the last 20 drawings, frequency
      tables for the last 100, and the longest-absent numbers. Each contestant must return
      JSON: `numbers`, `bonus`, `strategy`, `rationale`, `confidence`. Invalid replies are sent
      back with the error, up to 3 attempts.
   4. Commit picks to `data/picks/<game>/<date>.json` **before the drawing**. The commit
      timestamp is the proof that picks weren't changed after the fact. The full prompt is
      stored in the same file.
   5. Rebuild `site/data.json` and deploy `site/` to GitHub Pages.
2. The pick step refuses to run after 9pm ET, or for any drawing that already has results.

## Contestants

Defined in [`config/models.json`](config/models.json). LLMs go through
[OpenRouter](https://openrouter.ai), so one API key covers every vendor. Baselines:

- **Random quick pick**: `secrets.SystemRandom`. This is the benchmark that matters.
- **Hot-numbers bot**: deterministically picks the most frequent numbers from the last 100 drawings.

OpenRouter model IDs change. Run the **Check model IDs** workflow (or
`python -m lottery check-models`) after editing the config. It lists the valid IDs for any
vendor whose model it can't find. Optional per-model API parameters go in a `"params"` object,
e.g. `{"temperature": 1.0}`.

## Setup

1. Add repository secret `OPENROUTER_API_KEY`.
2. Settings → Pages → Source: **GitHub Actions**.
3. Actions → *Daily picks and results* → **Run workflow**. The first run backfills the
   drawing history.

Cost: roughly 5 drawings a week × each model × ~3k tokens.

## Local

```bash
pip install -r requirements.txt pytest
python -m pytest -q
python -m lottery update-results            # needs network
python -m lottery pick --date 2026-09-23    # needs OPENROUTER_API_KEY for LLMs
python -m lottery score && python -m lottery build-site
cd site && python -m http.server            # open http://localhost:8000
```

## Scoring

Each pick is scored as one ticket at the current price: $2 for Powerball; $5 for Mega Millions
since April 2025, with the multiplier built in. Scores use base prize tables
(`lottery/games.py`). Powerball Power Play is ignored. A jackpot is counted at the advertised
annuity. The site also shows the exact expectations for a uniformly random ticket.
