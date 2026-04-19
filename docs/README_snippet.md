# Trading System

## Quick start

```bash
cd /Users/micheal/.openclaw/workspace/trading-system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 src/main.py
```

## Timed runner

Single test loop:

```bash
python3 src/runner.py --interval-seconds 60 --max-cycles 2
```

Continuous paper runner:

```bash
python3 src/runner.py --interval-seconds 900
```

## Simple backtest

Run a lightweight historical replay with the current S1~S5 / A-B-C / exit rules:

```bash
python3 src/backtest.py --symbol BTCUSDT --benchmark-symbol BTCUSDT --signal-limit 500 --state-limit 500
```

Save JSON output:

```bash
python3 src/backtest.py --symbol ETHUSDT --benchmark-symbol BTCUSDT --signal-limit 500 --state-limit 500 --out logs/backtest_eth.json
```

The backtest also prints an analysis panel grouped by:
- market state
- setup type (breakout / pullback / reclaim)
- signal score / score bucket
- exit reason

## Small parameter scan

Scan EMA exit period, B-score threshold, and whether S3 entries are allowed:

```bash
python3 src/backtest.py --scan --symbol BTCUSDT --benchmark-symbol BTCUSDT --signal-limit 300 --state-limit 300 --ema-periods 10,20,30 --b-scores 3,4,5 --allow-s3-values true,false
```

## Strategy Lab / Research Engine v1

Run the first-pass strategy lab across multiple setup candidates and state profiles:

```bash
python3 src/strategy_lab.py --symbol BTCUSDT --benchmark-symbol BTCUSDT --signal-limit 300 --state-limit 300 --out logs/strategy_lab_btc.json
```

The lab currently compares:
- `breakout_only`
- `breakout_pullback`
- `full_stack_setups`

Across state profiles:
- `s1s2_only`
- `s1s2_s3_selective`
- `global_default`

Output includes:
- ranking panel
- candidate leaderboard
- auto learning summary
- winner snapshot written into `logs/journal.jsonl`

## Notes
- Keep Binance credentials only in `.env`
- Do not enable withdrawal permissions
- Start with `TRADING_MODE=paper`
- Portfolio state persists in `logs/portfolio_state.json`
- Each cycle now prints a compact paper summary for cron/log review
- BUY entries now require a detected setup by default
