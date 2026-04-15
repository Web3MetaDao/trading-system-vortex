from __future__ import annotations

import json
import logging
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv

from config_loader import load_yaml
from data_provider import UnifiedDataProvider
from execution_engine import ExecutionEngine
from healthcheck import build_paper_backtest_compare, load_latest_backtest, write_health_report
from journal import Journal
from market_data import MarketDataClient
from portfolio_manager import PortfolioManager
from risk_engine import RiskEngine
from runtime_config import build_runtime_risk
from signal_engine import SignalEngine
from state_engine import StateEngine

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]


def _round_decimal(value: float, decimals: int = 4) -> float:
    """
    使用 Decimal 进行精确的浮点数舍入，避免浮点数精度问题。
    
    Args:
        value: 要舍入的浮点数
        decimals: 保留的小数位数
    
    Returns:
        舍入后的浮点数
    """
    if value is None or not isinstance(value, (int, float)):
        return 0.0
    try:
        d = Decimal(str(value))
        quantize_str = '0.' + '0' * decimals if decimals > 0 else '1'
        quantized = d.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
        return float(quantized)
    except Exception as e:
        logger.warning(f"Decimal rounding failed for {value}: {e}, using standard round")
        return round(value, decimals)


def _format_brief_summary(summary: dict) -> str:
    market = summary.get("market_state", {})
    portfolio = summary.get("portfolio", {})
    performance = summary.get("performance", {})
    entries = summary.get("entries", [])
    exits = summary.get("exits", [])
    blocked = summary.get("blocked", [])
    monitors = summary.get("position_monitor", [])
    errors = summary.get("errors", [])
    compare = summary.get("paper_backtest_compare")

    lines = [
        "=== PAPER CYCLE SUMMARY ===",
        f"Market: {market.get('state')} | {market.get('reason')}",
        f"Benchmark: {market.get('benchmark_symbol')} @ {market.get('benchmark_price')} ({market.get('benchmark_change_24h_pct')}%)",
        f"Portfolio: cash={portfolio.get('cash_usdt')} exposure={portfolio.get('exposure_usdt')} open={len(portfolio.get('open_positions', []))}",
        f"Actions: entries={len(entries)} exits={len(exits)} blocked={len(blocked)} errors={len(errors)}",
        (
            f"Performance: closed={performance.get('closed_trades', 0)} wins={performance.get('wins', 0)} "
            f"losses={performance.get('losses', 0)} winRate={performance.get('win_rate_pct', 0)}% "
            f"avgPnL={performance.get('avg_pnl_pct', 0)}% totalPnL={performance.get('total_pnl_usdt', 0)} USDT"
        ),
    ]

    if compare:
        lines.append(
            f"Compare: mode={compare.get('mode')} paperPnL={compare.get('paper_total_pnl_usdt')} vs backtestPnL={compare.get('backtest_total_pnl_usdt')} delta={compare.get('delta_total_pnl_usdt')} USDT"
        )
        lines.append(
            f"Compare explain: paper_state_age={compare.get('paper_state_age_seconds')}s backtest_window={compare.get('backtest_window')}"
        )
        lines.append(f"Compare hint: {compare.get('delta_reason_hint')}")

    if monitors:
        lines.append("Monitors:")
        for item in monitors:
            lines.append(
                f"- {item.get('symbol')} {item.get('side')} grade={item.get('signal_grade')} pnl={item.get('pnl_pct')}% "
                f"toSL={item.get('distance_to_stop_loss_pct')}% toTP={item.get('distance_to_take_profit_pct')}%"
            )

    if entries:
        lines.append("Entries:")
        for item in entries:
            lines.append(f"- {item.get('symbol')}: {item.get('result')}")

    if exits:
        lines.append("Exits:")
        for item in exits:
            lines.append(f"- {item.get('symbol')}: {item.get('reason')}")

    recent = performance.get("recent_trades", [])
    if recent:
        lines.append(f"Recent {performance.get('recent_n', len(recent))} trades:")
        for item in recent:
            lines.append(
                f"- {item.get('symbol')} {item.get('side')} grade={item.get('signal_grade')} pnl={item.get('pnl_pct')}% ({item.get('pnl_usdt')} USDT) reason={item.get('reason')}"
            )

    if blocked:
        lines.append("Blocked:")
        for item in blocked:
            lines.append(f"- {item.get('symbol')}: {item.get('reason')}")

    if errors:
        lines.append("Errors:")
        for item in errors:
            lines.append(f"- {item.get('stage')}: {item.get('symbol')} -> {item.get('error')}")

    return "\n".join(lines)


def run_cycle() -> dict:
    """
    执行一个完整的交易周期，包含全局异常处理和精度管理。
    
    Returns:
        包含周期执行结果的字典
    """
    load_dotenv(ROOT / ".env")

    try:
        strategy = load_yaml("strategy.yaml")
        risk = build_runtime_risk(strategy, load_yaml("risk.yaml"))
        symbols = load_yaml("symbols.yaml")
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}", exc_info=True)
        return {
            "status": "error",
            "error": f"Configuration load failed: {str(e)}",
            "errors": [{"stage": "config_load", "error": str(e)}],
        }

    try:
        trading_mode = strategy.get("execution_mode", "paper")
        benchmark_symbol = strategy.get("benchmark_symbol", "BTCUSDT")
        market_data_cfg = strategy.get("market_data", {})
        state_interval = market_data_cfg.get("state_interval", "4h")
        state_limit = int(market_data_cfg.get("state_limit", 120))
        signal_interval = market_data_cfg.get("signal_interval", "1h")
        signal_limit = int(market_data_cfg.get("signal_limit", 120))
        performance_recent_n = int(risk.get("performance_recent_n", 10))

        data_client = MarketDataClient()
        data_provider = UnifiedDataProvider(data_client)
        state_engine = StateEngine()
        signal_engine = SignalEngine()
        risk_engine = RiskEngine(risk)
        execution_engine = ExecutionEngine(mode=trading_mode)
        portfolio = PortfolioManager(ROOT, capital_usdt=float(risk.get("capital_usdt", 100)))
        journal = Journal(ROOT)

        summary: dict = {
            "market_state": None,
            "portfolio": {
                "cash_usdt": _round_decimal(portfolio.state.cash_usdt),
                "exposure_usdt": _round_decimal(portfolio.total_exposure_usdt()),
                "open_positions": portfolio.state.open_positions,
            },
            "position_monitor": [],
            "entries": [],
            "exits": [],
            "blocked": [],
            "errors": [],
            "performance": {},
            "paper_backtest_compare": None,
            "data_health": {},
            "brief": "",
        }

        # 数据获取阶段 - 包含异常处理
        try:
            context = data_provider.build_context(
                benchmark_symbol=benchmark_symbol,
                watchlist=[str(s).upper() for s in symbols.get("core_watchlist", [])],
                state_interval=state_interval,
                state_limit=state_limit,
                signal_interval=signal_interval,
                signal_limit=signal_limit,
            )
            summary["data_health"] = context.data_health
        except Exception as e:
            logger.error(f"Data provider build_context failed: {e}", exc_info=True)
            error = {
                "stage": "data_provider",
                "symbol": "SYSTEM",
                "error": f"build_context failed: {str(e)}",
            }
            summary["errors"].append(error)
            summary["data_health"] = {"status": "degraded", "reason": str(e)}
            summary["brief"] = _format_brief_summary(summary)
            journal.log("cycle_error", error)
            return summary

        benchmark_snapshot = context.benchmark_snapshot
        if benchmark_snapshot.degraded:
            error = {
                "stage": "benchmark_fetch",
                "symbol": benchmark_symbol,
                "error": benchmark_snapshot.error,
            }
            summary["errors"].append(error)
            summary["market_state"] = {
                "state": "DEGRADED",
                "reason": f"Benchmark fetch failed: {benchmark_snapshot.error}",
                "benchmark_symbol": benchmark_symbol,
                "benchmark_price": None,
                "benchmark_change_24h_pct": None,
                "state_interval": state_interval,
                "state_limit": state_limit,
                "signal_interval": signal_interval,
                "signal_limit": signal_limit,
            }
            summary["performance"] = portfolio.performance_stats(recent_n=performance_recent_n)
            summary["brief"] = _format_brief_summary(summary)
            journal.log("cycle_error", error)
            write_health_report(ROOT, {"status": "degraded", "summary": summary})
            print(summary["brief"])
            print("Cycle summary:")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return summary

        # 市场状态分类 - 包含异常处理
        try:
            market_state = state_engine.classify(
                {"strategy": strategy, "benchmark_snapshot": benchmark_snapshot}
            )
        except Exception as e:
            logger.error(f"State engine classification failed: {e}", exc_info=True)
            error = {
                "stage": "state_classification",
                "symbol": benchmark_symbol,
                "error": f"classify failed: {str(e)}",
            }
            summary["errors"].append(error)
            summary["market_state"] = {
                "state": "ERROR",
                "reason": f"State classification failed: {str(e)}",
                "benchmark_symbol": benchmark_symbol,
                "benchmark_price": benchmark_snapshot.price,
                "benchmark_change_24h_pct": benchmark_snapshot.change_24h_pct,
            }
            summary["brief"] = _format_brief_summary(summary)
            journal.log("cycle_error", error)
            return summary

        summary["market_state"] = {
            "state": market_state.state,
            "reason": market_state.reason,
            "benchmark_symbol": benchmark_snapshot.symbol,
            "benchmark_price": _round_decimal(benchmark_snapshot.price),
            "benchmark_change_24h_pct": _round_decimal(benchmark_snapshot.change_24h_pct),
            "state_interval": state_interval,
            "state_limit": state_limit,
            "signal_interval": signal_interval,
            "signal_limit": signal_limit,
        }
        journal.log("market_state", summary["market_state"])

        print(f"Market state: {market_state.state} | reason: {market_state.reason}")
        print(f"Execution mode: {execution_engine.mode}")
        print(f"State klines: interval={state_interval} limit={state_limit}")
        print(f"Signal klines: interval={signal_interval} limit={signal_limit}")
        print(
            f"Portfolio cash: {_round_decimal(portfolio.state.cash_usdt)} | exposure: {_round_decimal(portfolio.total_exposure_usdt())}"
        )
        print(
            f"Benchmark [{benchmark_snapshot.symbol}] price={_round_decimal(benchmark_snapshot.price)} change24h={_round_decimal(benchmark_snapshot.change_24h_pct)}%"
        )

        # 持仓监控循环 - 包含异常处理
        for position in list(portfolio.state.open_positions):
            try:
                symbol = position.get("symbol")
                if not symbol:
                    continue
                snapshot = context.signal_snapshots.get(str(symbol).upper())
                if snapshot is None:
                    error = {
                        "stage": "position_monitor",
                        "symbol": symbol,
                        "error": "missing_context_snapshot",
                    }
                    summary["errors"].append(error)
                    journal.log("cycle_error", error)
                    print(f"[{symbol}] monitor degraded -> missing_context_snapshot")
                    continue
                if snapshot.degraded:
                    error = {"stage": "position_monitor", "symbol": symbol, "error": snapshot.error}
                    summary["errors"].append(error)
                    journal.log("cycle_error", error)
                    print(f"[{symbol}] monitor degraded -> {snapshot.error}")
                    continue

                monitor = risk_engine.position_monitor(position, snapshot)
                summary["position_monitor"].append(monitor)
                journal.log("position_monitor", monitor)
                print(
                    f"[MONITOR {symbol}] side={monitor['side']} grade={monitor['signal_grade']} entry={monitor['entry_price']} current={monitor['current_price']} "
                    f"to_sl={monitor['distance_to_stop_loss_pct']}% to_tp={monitor['distance_to_take_profit_pct']}%"
                )

                exit_reason = risk_engine.exit_reason(position, snapshot, market_state.state)
                if not exit_reason:
                    continue

                close_side = "SELL" if position.get("side") == "BUY" else "BUY"
                result = execution_engine.close_order(
                    symbol, close_side, float(position.get("size_usdt", 0.0)), exit_reason
                )
                if result.accepted:
                    closed = portfolio.close_position(symbol, snapshot.price, exit_reason)
                    event = {
                        "symbol": symbol,
                        "result": result.detail,
                        "reason": exit_reason,
                        "closed": closed,
                    }
                    summary["exits"].append(event)
                    journal.log("exit", event)
                    print(f"[{symbol}] exit -> {result.detail}")
            except Exception as e:
                logger.error(f"Position monitoring failed for {position.get('symbol')}: {e}", exc_info=True)
                error = {
                    "stage": "position_monitor",
                    "symbol": position.get("symbol", "UNKNOWN"),
                    "error": f"exception: {str(e)}",
                }
                summary["errors"].append(error)
                journal.log("cycle_error", error)
                continue

        # 信号评估循环 - 包含异常处理
        for symbol in symbols.get("core_watchlist", []):
            try:
                snapshot = context.signal_snapshots.get(str(symbol).upper())
                if snapshot is None:
                    error = {"stage": "symbol_fetch", "symbol": symbol, "error": "missing_context_snapshot"}
                    summary["errors"].append(error)
                    journal.log("cycle_error", error)
                    print(f"[{symbol}] fetch degraded -> missing_context_snapshot")
                    continue
                if snapshot.degraded:
                    error = {"stage": "symbol_fetch", "symbol": symbol, "error": snapshot.error}
                    summary["errors"].append(error)
                    journal.log("cycle_error", error)
                    print(f"[{symbol}] fetch degraded -> {snapshot.error}")
                    continue

                journal.log(
                    "market_snapshot",
                    {
                        "symbol": snapshot.symbol,
                        "price": _round_decimal(snapshot.price),
                        "volume": snapshot.volume,
                        "quote_volume": snapshot.quote_volume,
                        "change_24h_pct": _round_decimal(snapshot.change_24h_pct),
                        "high_24h": _round_decimal(snapshot.high_24h),
                        "low_24h": _round_decimal(snapshot.low_24h),
                        "source": snapshot.source,
                        "signal_kline_count": len(snapshot.klines),
                        "signal_interval": signal_interval,
                    },
                )

                print(
                    f"[{snapshot.symbol}] price={_round_decimal(snapshot.price)} change24h={_round_decimal(snapshot.change_24h_pct)}% volume={snapshot.volume} quote_volume={snapshot.quote_volume} klines={len(snapshot.klines)}"
                )

                signal = signal_engine.evaluate(
                    symbol,
                    market_state.state,
                    {
                        "snapshot": snapshot,
                        "strategy": strategy,
                        "benchmark_snapshot": benchmark_snapshot,
                        "intermarket": context.intermarket,
                        "derivatives": context.derivatives.get(str(symbol).upper(), {}),
                        "data_health": context.data_health,
                    },
                )
                size_decision = risk_engine.size_position(signal.grade)

                signal_payload = {
                    "symbol": symbol,
                    "grade": signal.grade,
                    "score": signal.score,
                    "side": signal.side,
                    "setup": signal.setup,
                    "reason": signal.reason,
                    "blocked_reason": signal.explain.get("blocked_reason"),
                    "explain": signal.explain,
                    "market_state": {
                        "state": market_state.state,
                        "reason": market_state.reason,
                    },
                    "snapshot": {
                        "price": _round_decimal(snapshot.price),
                        "volume": snapshot.volume,
                        "quote_volume": snapshot.quote_volume,
                        "change_24h_pct": _round_decimal(snapshot.change_24h_pct),
                        "high_24h": _round_decimal(snapshot.high_24h),
                        "low_24h": _round_decimal(snapshot.low_24h),
                        "open_price": _round_decimal(snapshot.open_price),
                        "source": snapshot.source,
                    },
                    "size_decision": {
                        "approved": size_decision.approved,
                        "position_size_usdt": _round_decimal(size_decision.position_size_usdt),
                        "reason": size_decision.reason,
                    },
                }

                if signal.side == "BUY" and size_decision.approved:
                    can_open = risk_engine.can_open_position(
                        portfolio,
                        symbol,
                        size_decision.position_size_usdt,
                        signal_grade=signal.grade,
                        data_health=context.data_health,
                    )
                    if can_open.approved:
                        result = execution_engine.open_order(
                            symbol, "BUY", size_decision.position_size_usdt, snapshot.price
                        )
                        if result.accepted:
                            portfolio.add_position(
                                symbol,
                                size_decision.position_size_usdt,
                                "BUY",
                                snapshot.price,
                                signal_grade=signal.grade,
                            )
                            event = {
                                "symbol": symbol,
                                "result": result.detail,
                                "grade": signal.grade,
                                "size": _round_decimal(size_decision.position_size_usdt),
                                "entry_price": _round_decimal(snapshot.price),
                            }
                            summary["entries"].append(event)
                            journal.log("entry", event)
                            print(f"[{symbol}] entry -> {result.detail}")
                        else:
                            event = {
                                "symbol": symbol,
                                "reason": f"execution failed: {result.detail}",
                            }
                            summary["blocked"].append(event)
                            journal.log("entry_blocked", event)
                            print(f"[{symbol}] entry blocked -> {result.detail}")
                    else:
                        event = {
                            "symbol": symbol,
                            "reason": can_open.reason,
                        }
                        summary["blocked"].append(event)
                        journal.log("entry_blocked", event)
                        print(f"[{symbol}] entry blocked -> {can_open.reason}")
                else:
                    event = {
                        "symbol": symbol,
                        "reason": size_decision.reason if not size_decision.approved else "no buy signal",
                    }
                    summary["blocked"].append(event)
                    journal.log("entry_blocked", event)

                journal.log("signal", signal_payload)

            except Exception as e:
                logger.error(f"Signal evaluation failed for {symbol}: {e}", exc_info=True)
                error = {
                    "stage": "signal_evaluation",
                    "symbol": symbol,
                    "error": f"exception: {str(e)}",
                }
                summary["errors"].append(error)
                journal.log("cycle_error", error)
                continue

        summary["performance"] = portfolio.performance_stats(recent_n=performance_recent_n)
        summary["brief"] = _format_brief_summary(summary)

        print(summary["brief"])
        print("Cycle summary:")
        print(json.dumps(summary, ensure_ascii=False, indent=2))

        return summary

    except Exception as e:
        logger.error(f"Unexpected error in run_cycle: {e}", exc_info=True)
        return {
            "status": "error",
            "error": f"Cycle execution failed: {str(e)}",
            "errors": [{"stage": "cycle_execution", "error": str(e)}],
        }


if __name__ == "__main__":
    run_cycle()
