from __future__ import annotations

import argparse
import copy
import itertools
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from config_loader import load_yaml
from market_data import MarketDataClient, MarketSnapshot
from risk_engine import RiskEngine
from runtime_config import build_runtime_risk
from signal_engine import SignalEngine
from state_engine import StateEngine

ROOT = Path(__file__).resolve().parents[1]


def build_snapshot(
    symbol: str, klines: list[dict[str, float]], rolling_24_bars: int = 24
) -> MarketSnapshot:
    window = klines[-rolling_24_bars:] if len(klines) >= rolling_24_bars else klines
    last = klines[-1]
    first = window[0]
    high_24h = max(c["high"] for c in window)
    low_24h = min(c["low"] for c in window)
    quote_volume = sum(c.get("quote_volume", 0.0) for c in window)
    change_pct = ((last["close"] - first["open"]) / first["open"]) * 100 if first["open"] else 0.0
    volume = sum(c.get("volume", 0.0) for c in window)
    return MarketSnapshot(
        symbol=symbol,
        price=last["close"],
        volume=volume,
        change_24h_pct=change_pct,
        quote_volume=quote_volume,
        high_24h=high_24h,
        low_24h=low_24h,
        open_price=first["open"],
        klines=list(klines),
        source="backtest",
    )


def performance_from_trades(
    trades: list[dict],
    recent_n: int,
    equity_curve: list[dict] | None = None,
    initial_capital: float = 100.0,
) -> dict:
    total = len(trades)
    wins = sum(1 for t in trades if float(t.get("pnl_pct", 0.0)) > 0)
    losses = sum(1 for t in trades if float(t.get("pnl_pct", 0.0)) < 0)
    total_pnl_pct = round(sum(float(t.get("pnl_pct", 0.0)) for t in trades), 4) if trades else 0.0
    total_pnl_usdt = round(sum(float(t.get("pnl_usdt", 0.0)) for t in trades), 4) if trades else 0.0
    avg_pnl_pct = round(total_pnl_pct / total, 4) if total else 0.0
    win_rate = round((wins / total) * 100, 2) if total else 0.0

    gross_profit_usdt = round(
        sum(float(t.get("pnl_usdt", 0.0)) for t in trades if float(t.get("pnl_usdt", 0.0)) > 0), 4
    )
    gross_loss_usdt_abs = round(
        abs(
            sum(float(t.get("pnl_usdt", 0.0)) for t in trades if float(t.get("pnl_usdt", 0.0)) < 0)
        ),
        4,
    )
    profit_factor = (
        round(gross_profit_usdt / gross_loss_usdt_abs, 4)
        if gross_loss_usdt_abs > 0
        else (999.0 if gross_profit_usdt > 0 else 0.0)
    )

    holding_bars = [
        int(t.get("holding_bars", 0) or 0) for t in trades if int(t.get("holding_bars", 0) or 0) > 0
    ]
    avg_holding_bars = round(sum(holding_bars) / len(holding_bars), 2) if holding_bars else 0.0
    max_holding_bars = max(holding_bars) if holding_bars else 0

    max_drawdown_pct = 0.0
    max_drawdown_usdt = 0.0
    equity_curve = equity_curve or []
    if equity_curve:
        peak = float(equity_curve[0].get("equity_usdt", initial_capital) or initial_capital)
        for point in equity_curve:
            equity = float(point.get("equity_usdt", peak) or peak)
            if equity > peak:
                peak = equity
            drawdown_usdt = peak - equity
            drawdown_pct = (drawdown_usdt / peak) * 100 if peak else 0.0
            if drawdown_pct > max_drawdown_pct:
                max_drawdown_pct = drawdown_pct
                max_drawdown_usdt = drawdown_usdt
        max_drawdown_pct = round(max_drawdown_pct, 4)
        max_drawdown_usdt = round(max_drawdown_usdt, 4)

    return {
        "closed_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_pct": total_pnl_pct,
        "total_pnl_usdt": total_pnl_usdt,
        "gross_profit_usdt": gross_profit_usdt,
        "gross_loss_usdt_abs": gross_loss_usdt_abs,
        "profit_factor": profit_factor,
        "avg_holding_bars": avg_holding_bars,
        "max_holding_bars": max_holding_bars,
        "max_drawdown_pct": max_drawdown_pct,
        "max_drawdown_usdt": max_drawdown_usdt,
        "recent_n": recent_n,
        "recent_trades": trades[-recent_n:],
    }


def summarize_group(trades: list[dict]) -> dict:
    total = len(trades)
    wins = sum(1 for t in trades if float(t.get("pnl_pct", 0.0)) > 0)
    losses = sum(1 for t in trades if float(t.get("pnl_pct", 0.0)) < 0)
    total_pnl_pct = round(sum(float(t.get("pnl_pct", 0.0)) for t in trades), 4) if trades else 0.0
    total_pnl_usdt = round(sum(float(t.get("pnl_usdt", 0.0)) for t in trades), 4) if trades else 0.0
    avg_pnl_pct = round(total_pnl_pct / total, 4) if total else 0.0
    win_rate_pct = round((wins / total) * 100, 2) if total else 0.0
    return {
        "count": total,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": win_rate_pct,
        "avg_pnl_pct": avg_pnl_pct,
        "total_pnl_pct": total_pnl_pct,
        "total_pnl_usdt": total_pnl_usdt,
    }


def summarize_records(records: list[dict], numeric_fields: list[str] | None = None) -> dict:
    total = len(records)
    numeric_fields = numeric_fields or []
    summary = {"count": total}
    for field in numeric_fields:
        values = [float(r.get(field, 0.0) or 0.0) for r in records]
        summary[field] = round(sum(values), 4) if values else 0.0
        summary[f"avg_{field}"] = round((sum(values) / len(values)), 4) if values else 0.0
    return summary


def build_analysis(trades: list[dict], blocked: list[dict] | None = None) -> dict:
    by_market_state: dict[str, list[dict]] = defaultdict(list)
    by_exit_reason: dict[str, list[dict]] = defaultdict(list)
    by_signal_score: dict[str, list[dict]] = defaultdict(list)
    by_signal_bucket: dict[str, list[dict]] = defaultdict(list)
    by_setup: dict[str, list[dict]] = defaultdict(list)
    by_symbol: dict[str, list[dict]] = defaultdict(list)

    for trade in trades:
        by_market_state[str(trade.get("market_state", "UNKNOWN"))].append(trade)
        by_exit_reason[str(trade.get("reason", "UNKNOWN"))].append(trade)
        by_setup[str(trade.get("setup", "none"))].append(trade)
        by_symbol[str(trade.get("symbol", "UNKNOWN"))].append(trade)
        score = trade.get("signal_score")
        by_signal_score[str(score)].append(trade)
        if score is None:
            bucket = "UNKNOWN"
        elif float(score) >= 5:
            bucket = "A_like(score>=5)"
        elif float(score) >= 3:
            bucket = "B_like(score>=3)"
        else:
            bucket = "C_like(score<3)"
        by_signal_bucket[bucket].append(trade)

    blocked = blocked or []
    by_blocked_reason: dict[str, list[dict]] = defaultdict(list)
    by_blocked_setup: dict[str, list[dict]] = defaultdict(list)
    by_blocked_grade: dict[str, list[dict]] = defaultdict(list)
    by_blocked_market_state: dict[str, list[dict]] = defaultdict(list)

    for item in blocked:
        by_blocked_reason[
            str(item.get("blocked_reason") or item.get("reason") or "UNKNOWN")
        ].append(item)
        by_blocked_setup[str(item.get("setup", "none"))].append(item)
        by_blocked_grade[str(item.get("grade", "UNKNOWN"))].append(item)
        by_blocked_market_state[str(item.get("market_state", "UNKNOWN"))].append(item)

    analysis = {
        "by_symbol": {k: summarize_group(v) for k, v in sorted(by_symbol.items())},
        "by_market_state": {k: summarize_group(v) for k, v in sorted(by_market_state.items())},
        "by_exit_reason": {k: summarize_group(v) for k, v in sorted(by_exit_reason.items())},
        "by_setup": {k: summarize_group(v) for k, v in sorted(by_setup.items())},
        "by_signal_score": {
            k: summarize_group(v) for k, v in sorted(by_signal_score.items(), key=lambda x: x[0])
        },
        "by_signal_bucket": {k: summarize_group(v) for k, v in sorted(by_signal_bucket.items())},
        "blocked": {
            "total": len(blocked),
            "by_reason": {
                k: summarize_records(v, ["score"]) for k, v in sorted(by_blocked_reason.items())
            },
            "by_setup": {
                k: summarize_records(v, ["score"]) for k, v in sorted(by_blocked_setup.items())
            },
            "by_grade": {
                k: summarize_records(v, ["score"]) for k, v in sorted(by_blocked_grade.items())
            },
            "by_market_state": {
                k: summarize_records(v, ["score"])
                for k, v in sorted(by_blocked_market_state.items())
            },
        },
    }
    return analysis


def format_analysis_panel(result: dict) -> str:
    perf = result.get("performance", {})
    analysis = result.get("analysis", {})
    lines = [
        "=== BACKTEST ANALYSIS PANEL ===",
        f"Mode: {result.get('mode')} | Symbols: {', '.join(result.get('symbols', []))}",
        f"Benchmark: {result.get('benchmark_symbol')} | signals_seen={result.get('signals_seen')}",
        (
            f"Performance: closed={perf.get('closed_trades')} wins={perf.get('wins')} losses={perf.get('losses')} "
            f"winRate={perf.get('win_rate_pct')}% avgPnL={perf.get('avg_pnl_pct')}% totalPnL={perf.get('total_pnl_usdt')} USDT"
        ),
        (
            f"Risk/quality: profitFactor={perf.get('profit_factor')} maxDD={perf.get('max_drawdown_pct')}% "
            f"({perf.get('max_drawdown_usdt')} USDT) avgHoldingBars={perf.get('avg_holding_bars')} maxHoldingBars={perf.get('max_holding_bars')}"
        ),
        f"Equity: start={result.get('initial_capital_usdt')} end={result.get('ending_equity_usdt')}",
    ]

    for title, groups in [
        ("By symbol", analysis.get("by_symbol", {})),
        ("By market state", analysis.get("by_market_state", {})),
        ("By setup", analysis.get("by_setup", {})),
        ("By signal bucket", analysis.get("by_signal_bucket", {})),
    ]:
        if not groups:
            continue
        lines.append(title + ":")
        for key, stats in groups.items():
            lines.append(
                f"- {key}: count={stats['count']} winRate={stats['win_rate_pct']}% avgPnL={stats['avg_pnl_pct']}% totalPnL={stats['total_pnl_usdt']} USDT"
            )

    blocked = analysis.get("blocked", {})
    if blocked:
        lines.append(f"Blocked decisions: total={blocked.get('total', 0)}")
        for key, stats in list(blocked.get("by_reason", {}).items())[:6]:
            lines.append(
                f"- blocked[{key}]: count={stats['count']} avgScore={stats.get('avg_score', 0.0)}"
            )
    return "\n".join(lines)


def deep_merge(base: dict, updates: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (updates or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def apply_overrides(strategy: dict, risk: dict, overrides: dict | None) -> tuple[dict, dict]:
    strategy = copy.deepcopy(strategy)
    risk = copy.deepcopy(risk)
    overrides = overrides or {}

    if "ema_exit_period" in overrides:
        risk["ema_exit_period"] = int(overrides["ema_exit_period"])
    if "b_min_score" in overrides:
        strategy.setdefault("signal_levels", {}).setdefault("B", {})["min_score"] = int(
            overrides["b_min_score"]
        )
    if "allow_s3_entries" in overrides:
        strategy.setdefault("backtest", {})["allow_s3_entries"] = bool(
            overrides["allow_s3_entries"]
        )
    if "setup_filters" in overrides:
        strategy["setup_filters"] = deep_merge(
            strategy.get("setup_filters", {}), overrides["setup_filters"]
        )
    if "signal_params" in overrides:
        strategy["signal_params"] = deep_merge(
            strategy.get("signal_params", {}), overrides["signal_params"]
        )
    if "state_params" in overrides:
        strategy["state_params"] = deep_merge(
            strategy.get("state_params", {}), overrides["state_params"]
        )
    return strategy, risk


class MockPortfolio:
    def __init__(
        self,
        cash_usdt: float,
        open_positions: list[dict],
        closed_positions: list[dict] | None = None,
    ):
        self._cash_usdt = cash_usdt
        self._open_positions = open_positions
        self._closed_positions = closed_positions or []
        self.state = type(
            "State", (), {"cash_usdt": cash_usdt, "closed_positions": self._closed_positions}
        )()

    def has_open_position(self, symbol: str) -> bool:
        symbol = symbol.upper()
        return any(p.get("symbol") == symbol for p in self._open_positions)

    def open_position_count(self) -> int:
        return len(self._open_positions)

    def open_position_count_for_grade(self, signal_grade: str) -> int:
        grade = signal_grade.upper()
        return sum(
            1 for p in self._open_positions if str(p.get("signal_grade", "")).upper() == grade
        )

    def total_exposure_usdt(self) -> float:
        return round(sum(float(p.get("size_usdt", 0.0)) for p in self._open_positions), 2)


def run_backtest(
    symbol: str,
    benchmark_symbol: str,
    signal_limit: int,
    state_limit: int,
    overrides: dict | None = None,
    preloaded_signal_klines: (
        list[dict[str, float]] | dict[str, list[dict[str, float]]] | None
    ) = None,
    preloaded_benchmark_klines: list[dict[str, float]] | None = None,
) -> dict:
    load_dotenv(ROOT / ".env")
    strategy = load_yaml("strategy.yaml")
    risk = load_yaml("risk.yaml")
    symbols_cfg = load_yaml("symbols.yaml")
    strategy, risk = apply_overrides(strategy, risk, overrides)
    risk = build_runtime_risk(strategy, risk)

    market_data_cfg = strategy.get("market_data", {})
    state_interval = market_data_cfg.get("state_interval", "4h")
    signal_interval = market_data_cfg.get("signal_interval", "1h")
    performance_recent_n = int(risk.get("performance_recent_n", 10))
    initial_capital = float(risk.get("capital_usdt", 100))
    allow_s3_entries = bool(strategy.get("backtest", {}).get("allow_s3_entries", True))
    backtest_cfg = strategy.get("backtest", {})
    backtest_intermarket = dict(backtest_cfg.get("intermarket", {}))
    backtest_derivatives = backtest_cfg.get("derivatives", {})

    if symbol.upper() == "PORTFOLIO":
        watch_symbols = [s.upper() for s in symbols_cfg.get("core_watchlist", [])]
        mode = "portfolio"
    else:
        watch_symbols = [symbol.upper()]
        mode = "single"

    data_client = MarketDataClient()
    state_engine = StateEngine()
    signal_engine = SignalEngine()
    risk_engine = RiskEngine(risk)

    if preloaded_benchmark_klines is not None:
        benchmark_klines = list(preloaded_benchmark_klines)
    else:
        benchmark_klines = data_client.fetch_klines(
            benchmark_symbol, interval=state_interval, limit=state_limit
        )

    if isinstance(preloaded_signal_klines, dict):
        signal_klines_map = {str(k).upper(): list(v) for k, v in preloaded_signal_klines.items()}
    elif preloaded_signal_klines is not None and len(watch_symbols) == 1:
        signal_klines_map = {watch_symbols[0]: list(preloaded_signal_klines)}
    else:
        signal_klines_map = {}

    for s in watch_symbols:
        if s not in signal_klines_map:
            signal_klines_map[s] = data_client.fetch_klines(
                s, interval=signal_interval, limit=signal_limit
            )

    min_signal_bars = max(
        int(strategy.get("signal_params", {}).get("ema_slow_period", 50)),
        int(strategy.get("signal_params", {}).get("recent_window", 20)),
        24,
    )
    min_state_bars = max(int(strategy.get("state_params", {}).get("ema_slow_period", 50)), 20)
    max_len = min(len(v) for v in signal_klines_map.values()) if signal_klines_map else 0

    cash_usdt = initial_capital
    open_positions: list[dict] = []
    closed_trades: list[dict] = []
    blocked: list[dict] = []
    signals_seen = 0
    equity_curve: list[dict] = [
        {"bar_index": min_signal_bars, "equity_usdt": round(initial_capital, 4), "event": "start"}
    ]

    for idx in range(min_signal_bars, max_len):
        current_close_time = signal_klines_map[watch_symbols[0]][idx]["close_time"]
        close_time_iso = (
            datetime.fromtimestamp(float(current_close_time) / 1000.0, tz=UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        benchmark_slice = [c for c in benchmark_klines if c["close_time"] <= current_close_time]
        if len(benchmark_slice) < min_state_bars:
            continue

        benchmark_snapshot = build_snapshot(benchmark_symbol, benchmark_slice)
        market_state = state_engine.classify(
            {"strategy": strategy, "benchmark_snapshot": benchmark_snapshot}
        )

        # exits first, matching realtime flow
        remaining_positions: list[dict] = []
        for position in open_positions:
            klines = signal_klines_map[position["symbol"]][: idx + 1]
            snapshot = build_snapshot(position["symbol"], klines)
            exit_reason = risk_engine.exit_reason(position, snapshot, market_state.state)
            if not exit_reason:
                remaining_positions.append(position)
                continue

            pnl_pct = (
                (float(snapshot.price or 0.0) - float(position["entry_price"]))
                / float(position["entry_price"])
            ) * 100
            pnl_usdt = round(float(position["size_usdt"]) * (pnl_pct / 100.0), 4)
            cash_usdt = round(cash_usdt + float(position["size_usdt"]) + pnl_usdt, 4)
            closed_trades.append(
                {
                    "symbol": position["symbol"],
                    "side": position["side"],
                    "entry_price": round(float(position["entry_price"]), 4),
                    "exit_price": round(float(snapshot.price or 0.0), 4),
                    "pnl_pct": round(pnl_pct, 4),
                    "pnl_usdt": pnl_usdt,
                    "reason": exit_reason,
                    "market_state": market_state.state,
                    "signal_score": position["signal_score"],
                    "signal_grade": position["signal_grade"],
                    "setup": position["setup"],
                    "holding_bars": idx - int(position["entry_bar_index"]),
                    "closed_at": close_time_iso,
                }
            )
        open_positions = remaining_positions

        # entries in watchlist order, matching realtime flow
        for current_symbol in watch_symbols:
            current_signal_slice = signal_klines_map[current_symbol][: idx + 1]
            signal_snapshot = build_snapshot(current_symbol, current_signal_slice)
            signal = signal_engine.evaluate(
                current_symbol,
                market_state.state,
                {
                    "snapshot": signal_snapshot,
                    "strategy": strategy,
                    "benchmark_snapshot": benchmark_snapshot,
                    "intermarket": {
                        **backtest_intermarket,
                        "btc_change_24h_pct": benchmark_snapshot.change_24h_pct,
                    },
                    "derivatives": (
                        backtest_derivatives.get(current_symbol, {})
                        if isinstance(backtest_derivatives, dict)
                        else {}
                    ),
                },
            )
            signals_seen += 1

            if market_state.state == "S3" and not allow_s3_entries:
                blocked.append(
                    {
                        "symbol": current_symbol,
                        "reason": "S3 entries disabled by backtest override",
                        "blocked_reason": "backtest_s3_disabled",
                        "score": signal.score,
                        "grade": signal.grade,
                        "setup": signal.setup,
                        "market_state": market_state.state,
                    }
                )
                continue

            size_decision = risk_engine.size_position(signal.grade)
            if not (size_decision.approved and signal.side == "BUY"):
                blocked.append(
                    {
                        "symbol": current_symbol,
                        "reason": signal.reason,
                        "blocked_reason": signal.explain.get("blocked_reason")
                        if isinstance(signal.explain, dict)
                        else None,
                        "score": signal.score,
                        "grade": signal.grade,
                        "setup": signal.setup,
                        "market_state": market_state.state,
                        "side": signal.side,
                    }
                )
                continue

            mock_portfolio = MockPortfolio(
                cash_usdt=cash_usdt, open_positions=open_positions, closed_positions=closed_trades
            )
            open_filter = risk_engine.can_open_position(
                mock_portfolio, current_symbol, size_decision.position_size_usdt, signal.grade
            )
            if not open_filter.approved:
                blocked.append(
                    {
                        "symbol": current_symbol,
                        "reason": open_filter.reason,
                        "blocked_reason": "risk_open_filter_reject",
                        "score": signal.score,
                        "grade": signal.grade,
                        "setup": signal.setup,
                        "market_state": market_state.state,
                        "side": signal.side,
                    }
                )
                continue

            cash_usdt = round(cash_usdt - size_decision.position_size_usdt, 4)
            open_positions.append(
                {
                    "symbol": current_symbol,
                    "side": signal.side,
                    "entry_price": float(signal_snapshot.price or 0.0),
                    "size_usdt": float(size_decision.position_size_usdt),
                    "signal_grade": signal.grade,
                    "signal_score": signal.score,
                    "setup": signal.setup,
                    "entry_bar_index": idx,
                }
            )

        marked_equity = cash_usdt
        for position in open_positions:
            snapshot = build_snapshot(
                position["symbol"], signal_klines_map[position["symbol"]][: idx + 1]
            )
            pnl_pct = (
                (float(snapshot.price or 0.0) - float(position["entry_price"]))
                / float(position["entry_price"])
            ) * 100
            pnl_usdt = float(position["size_usdt"]) * (pnl_pct / 100.0)
            marked_equity += float(position["size_usdt"]) + pnl_usdt
        equity_curve.append(
            {"bar_index": idx, "equity_usdt": round(marked_equity, 4), "event": "mark_to_market"}
        )

    ending_equity = cash_usdt
    for position in open_positions:
        last_snapshot = build_snapshot(position["symbol"], signal_klines_map[position["symbol"]])
        pnl_pct = (
            (float(last_snapshot.price or 0.0) - float(position["entry_price"]))
            / float(position["entry_price"])
        ) * 100
        pnl_usdt = float(position["size_usdt"]) * (pnl_pct / 100.0)
        ending_equity += float(position["size_usdt"]) + pnl_usdt

    performance = performance_from_trades(
        closed_trades,
        performance_recent_n,
        equity_curve=equity_curve,
        initial_capital=initial_capital,
    )
    analysis = build_analysis(closed_trades, blocked=blocked)
    result = {
        "mode": mode,
        "symbol": symbol.upper(),
        "symbols": watch_symbols,
        "benchmark_symbol": benchmark_symbol,
        "signal_interval": signal_interval,
        "state_interval": state_interval,
        "signal_bars": max_len,
        "state_bars": len(benchmark_klines),
        "signals_seen": signals_seen,
        "initial_capital_usdt": initial_capital,
        "ending_cash_usdt": cash_usdt,
        "ending_equity_usdt": round(ending_equity, 4),
        "open_positions": open_positions,
        "blocked": blocked[-20:],
        "performance": performance,
        "analysis": analysis,
        "analysis_panel": "",
        "overrides": overrides or {},
    }
    result["analysis_panel"] = format_analysis_panel(result)
    return result


def run_parameter_scan(
    symbol: str,
    benchmark_symbol: str,
    signal_limit: int,
    state_limit: int,
    ema_periods: list[int],
    b_scores: list[int],
    allow_s3_values: list[bool],
) -> dict:
    results = []
    for ema_exit_period, b_min_score, allow_s3_entries in itertools.product(
        ema_periods, b_scores, allow_s3_values
    ):
        overrides = {
            "ema_exit_period": ema_exit_period,
            "b_min_score": b_min_score,
            "allow_s3_entries": allow_s3_entries,
        }
        result = run_backtest(
            symbol, benchmark_symbol, signal_limit, state_limit, overrides=overrides
        )
        perf = result["performance"]
        results.append(
            {
                "params": overrides,
                "ending_equity_usdt": result["ending_equity_usdt"],
                "closed_trades": perf["closed_trades"],
                "win_rate_pct": perf["win_rate_pct"],
                "avg_pnl_pct": perf["avg_pnl_pct"],
                "total_pnl_usdt": perf["total_pnl_usdt"],
                "total_pnl_pct": perf["total_pnl_pct"],
            }
        )

    ranking = sorted(
        results,
        key=lambda x: (
            x["ending_equity_usdt"],
            x["total_pnl_usdt"],
            x["win_rate_pct"],
            -x["closed_trades"],
        ),
        reverse=True,
    )
    lines = [
        "=== PARAMETER SCAN RANKING ===",
        f"Symbol: {symbol} | Benchmark: {benchmark_symbol} | combinations={len(results)}",
    ]
    for idx, item in enumerate(ranking[:10], start=1):
        p = item["params"]
        lines.append(
            f"{idx}. ema_exit={p['ema_exit_period']} b_min={p['b_min_score']} allow_s3={p['allow_s3_entries']} "
            f"equity={item['ending_equity_usdt']} totalPnL={item['total_pnl_usdt']} USDT winRate={item['win_rate_pct']}% trades={item['closed_trades']}"
        )
    return {
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "results": results,
        "ranking": ranking,
        "ranking_panel": "\n".join(lines),
    }


def parse_bool_list(text: str) -> list[bool]:
    items = []
    for raw in text.split(","):
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "y", "on"}:
            items.append(True)
        elif value in {"0", "false", "no", "n", "off"}:
            items.append(False)
    return items or [True, False]


def parse_int_list(text: str) -> list[int]:
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Historical backtest for trading-system")
    parser.add_argument(
        "--symbol", default="BTCUSDT", help="Use PORTFOLIO to run core_watchlist portfolio backtest"
    )
    parser.add_argument("--benchmark-symbol", default="BTCUSDT")
    parser.add_argument("--signal-limit", type=int, default=500)
    parser.add_argument("--state-limit", type=int, default=500)
    parser.add_argument("--out", default="")
    parser.add_argument("--scan", action="store_true")
    parser.add_argument("--ema-periods", default="10,20,30")
    parser.add_argument("--b-scores", default="3,4,5")
    parser.add_argument("--allow-s3-values", default="true,false")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.scan:
        result = run_parameter_scan(
            symbol=args.symbol.upper(),
            benchmark_symbol=args.benchmark_symbol.upper(),
            signal_limit=args.signal_limit,
            state_limit=args.state_limit,
            ema_periods=parse_int_list(args.ema_periods),
            b_scores=parse_int_list(args.b_scores),
            allow_s3_values=parse_bool_list(args.allow_s3_values),
        )
        print(result["ranking_panel"])
    else:
        result = run_backtest(
            symbol=args.symbol.upper(),
            benchmark_symbol=args.benchmark_symbol.upper(),
            signal_limit=args.signal_limit,
            state_limit=args.state_limit,
        )
        print(result["analysis_panel"])

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    default_latest = ROOT / "logs" / "latest_backtest.json"
    default_latest.parent.mkdir(parents=True, exist_ok=True)
    default_latest.write_text(text, encoding="utf-8")
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
