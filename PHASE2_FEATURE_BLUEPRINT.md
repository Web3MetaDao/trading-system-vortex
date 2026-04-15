# Phase-2 Feature Blueprint

This file maps the next-generation signal blocks into the current trading-system config layout.

## Immediate candidates

### 1) VWAP deviation (`signal_features.vwap_dev`)
Use as a score component in `signal_engine.py`.

Suggested behavior:
- reclaim near +1.5σ to +2σ reversion zone -> +1 for reclaim continuation if other structure confirms
- breakout above anchored VWAP with mild z-score -> +1 if not already overextended
- exhaustion beyond extreme z-score -> -1 to avoid late entries

Data required:
- current bar OHLCV
- rolling VWAP over lookback bars
- rolling stddev of price-vs-VWAP distance

### 2) Intermarket pulse (`macro_filters.intermarket`)
Use as a macro filter above signal scoring.

Suggested behavior:
- BTC strong while DXY weak -> +1 risk-on bias
- BTC weak while DXY strong -> -1 risk-off bias
- optional: downgrade market state to defensive when macro filter is strongly negative

Data required:
- BTC proxy
- NQ100 proxy
- DXY proxy
- synchronized timeframe closes

### 3) Open interest change (`derivatives_filters.oi_change`)
Use as confirmation, not primary trigger.

Suggested behavior:
- price up + OI up -> trend confirmation +1
- price down + OI up in weak tape -> squeeze/liquidation risk or trend pressure depending on side
- price move without OI support -> no bonus

Data required:
- Binance futures OI history
- aligned bars to signal interval

### 4) Funding shift (`derivatives_filters.funding_shift`)
Use as crowding / contrarian overlay.

Suggested behavior:
- extremely positive funding -> crowded longs, penalty or contrarian watch
- extremely negative funding -> crowded shorts, contrarian bonus on reclaim setups

Data required:
- funding rate history
- small rolling window for shift detection

---

## Recommended implementation order

1. VWAP deviation
2. Intermarket pulse
3. OI change
4. Funding shift

Reason:
- VWAP is easiest to add with current bar-based engine
- Intermarket adds regime value without changing execution
- OI/funding require new upstream data fetchers

---

## Minimal code touch points

- `config/strategy.yaml`
  - feature flags and parameter skeletons already added
- `src/signal_engine.py`
  - add optional score components behind feature flags
- `src/state_engine.py`
  - optionally consume `macro_filters.intermarket`
- `src/market_data.py`
  - keep spot market data here
- `src/derivatives_data.py` (new, later)
  - futures OI and funding fetchers
- `src/intermarket_data.py` (new, later)
  - external macro market inputs

---

## Rule of thumb

Do not enable multiple new features at once.
Turn on one block at a time, then backtest + paper audit consistency before keeping it.
