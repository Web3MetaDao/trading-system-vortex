# Changelog

All notable changes to VORTEX Trading System are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [v2.0.0] - 2026-04-19

### Breaking Changes

- **Removed desktop application (Electron/GUI)**: The system is now a pure server-side quantitative trading engine. All GUI-related code, Electron build configurations, and desktop packaging scripts have been removed.
- **Renamed entry point**: `startup_v4.py` → `startup.py`. Update any scripts that reference the old filename.
- **Removed redundant modules**: `signal_engine_optimized.py`, `signal_engine_v2.py`, `risk_engine_optimized.py`, `main_optimized.py`, `startup_v3.py` have been consolidated into their canonical counterparts.

### Added

- **Oracle data flow integration**: `OracleSnapshot` from `multimodal_oracle.py` is now formally integrated into `MarketContext` via `data_provider.py`, with a 5-minute async TTL cache. The macro sentiment filter now actually works end-to-end.
- **`feature_flags.use_oracle_macro_filter`**: Graceful degradation switch — when Oracle is unavailable or in backtest mode, the system continues trading without blocking.
- **`.env.example`**: Environment configuration template for easy onboarding.
- **`CHANGELOG.md`**: This file.
- **`tools/` directory**: Utility scripts (`bench_perf.py`, `run_backtest_60days.py`, `run_optimized_backtest.py`) moved here to keep the root clean.
- **`docs/` directory**: All documentation consolidated under `docs/`.
- **CI/CD pipeline** (`.github/workflows/ci.yml`): Replaced desktop build workflow with a proper server-side CI pipeline covering lint, multi-version test matrix, security scan (bandit), and automated GitHub Release creation on tag push.

### Fixed

- **Interface contract break in `automated_trading.py`**: `_process_symbol` now correctly passes `strategy_config` dict to `signal_engine.evaluate()`, preventing silent fallback to default parameters.
- **`risk.yaml` missing keys**: Added `grade_a_risk_multiplier`, `grade_b_risk_multiplier`, `grade_a_max_position_pct`, `grade_b_max_position_pct`, `trailing_stop_pct`, `trailing_stop_activation_pct`.
- **Hardcoded `trailing_stop_pct = 1.5`**: Both `risk_engine.py` and `risk_engine_optimized.py` now read this value from config.
- **Arbitrage engine fee model**: Replaced top-of-book price spread with L2 order book VWAP calculation. Net profit now correctly deducts bilateral taker fees, withdrawal fees, and price drift risk premium.
- **`_idempotency_cache` memory leak**: Added TTL-based lazy eviction to prevent unbounded growth in long-running deployments.
- **Paper mode slippage model**: Upgraded from fixed ±0.1% random to ATR-aware model with 5% probability stress scenario.
- **`signal_engine.py` scoring**: `ema_alignment_bonus` and `pullback_bonus` config values are now correctly applied to the signal score.
- **`telegram_notifier.py` GC issue (RUF006)**: `asyncio.ensure_future` task now stored as instance attribute to prevent premature garbage collection.
- **`test_telegram_notifier.py`**: Fully rewritten to align with `aiohttp`-based async API (previously mocked deprecated `python-telegram-bot`).
- **`requirements.txt`**: Added missing `ccxt`, `stable-baselines3`, `orjson`, `gymnasium`.
- **`pyproject.toml` dependencies**: Synchronized with `requirements.txt`; removed `python-telegram-bot` (replaced by `aiohttp`).
- **Static analysis (ruff)**: Fixed all `F821` (undefined names), `F841` (unused variables), `F401` (unused imports), `B007` (loop variable), `F811` (redefined function).

### Changed

- **`pyproject.toml`**: Project renamed from `trading-system` to `vortex-trading-system`, version bumped to `2.0.0`, dependencies fully synchronized.
- **Repository structure**: Cleaned up root directory; moved utility scripts to `tools/`, documentation to `docs/`.
- **`startup.py`**: Wired the previously orphaned `arb_engine` into the main loop; fixed RL agent observation vector dimension.

### Performance (v2.0.0 Benchmarks)

| Module | P50 | P99 | Max | Memory Peak |
|--------|-----|-----|-----|-------------|
| SignalEngine.evaluate() | 0.289 ms | 1.020 ms | 1.789 ms | 63.3 KB |
| RiskEngine.size_position() | 0.003 ms | 0.028 ms | 0.157 ms | — |
| ChanlunEngine.evaluate_divergence() | 1.061 ms | 2.674 ms | 2.674 ms | 76.8 KB |
| Concurrent throughput (×20) | — | — | — | 1361 eval/s |

**Test Suite: 190 passed, 0 failed**

---

## [v1.0.0] - 2026-04-01

### Added

- Initial release of VORTEX Trading System as a desktop application.
- Core trading engine with Binance WebSocket integration.
- Chanlun (缠论) technical analysis engine.
- Signal engine with EMA, MACD, and RSI indicators.
- Risk engine with position sizing and stop-loss management.
- Iceberg order execution algorithm.
- Multimodal Oracle for macro sentiment filtering.
- Cross-exchange arbitrage engine (BTC/ETH/BNB).
- Reinforcement learning agent (PPO) for adaptive parameter tuning.
- Telegram notification system.
- Streamlit monitoring dashboard.
- Backtest framework with 60-day historical data support.
- GitHub Actions workflow for cross-platform desktop builds.
