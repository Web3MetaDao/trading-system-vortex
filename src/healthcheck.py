from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def write_health_report(root: Path, payload: dict) -> Path:
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / "health_status.json"
    report = {
        "ts": datetime.now().isoformat(),
        **payload,
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_latest_backtest(root: Path) -> dict | None:
    candidates = [
        root / "logs" / "latest_backtest.json",
        root / "logs" / "backtest_latest.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _portfolio_state_age_seconds(root: Path) -> float | None:
    path = root / "logs" / "portfolio_state.json"
    if not path.exists():
        return None
    try:
        age = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds()
        return round(age, 2)
    except Exception:
        return None


def _build_delta_reason_hint(paper_summary: dict, backtest_result: dict) -> str:
    paper_market = paper_summary.get("market_state", {}) or {}
    paper_portfolio = paper_summary.get("portfolio", {}) or {}
    paper_open_positions = paper_portfolio.get("open_positions", []) or []
    backtest_open_positions = backtest_result.get("open_positions", []) or []

    paper_signal_interval = paper_market.get("signal_interval")
    paper_state_interval = paper_market.get("state_interval")
    backtest_signal_interval = backtest_result.get("signal_interval")
    backtest_state_interval = backtest_result.get("state_interval")

    if len(paper_open_positions) != len(backtest_open_positions):
        return "历史持仓差异优先：paper/backtest 当前持仓数量不同，delta 更可能来自组合路径而非评分口径。"

    if (
        paper_signal_interval != backtest_signal_interval
        or paper_state_interval != backtest_state_interval
    ):
        return "市场窗口差异优先：paper 与 backtest 的 state/signal 周期不一致。"

    paper_state_limit = paper_market.get("state_limit")
    paper_signal_limit = paper_market.get("signal_limit")
    backtest_state_bars = backtest_result.get("state_bars")
    backtest_signal_bars = backtest_result.get("signal_bars")
    if (
        paper_state_limit is not None
        and backtest_state_bars is not None
        and int(paper_state_limit) != int(backtest_state_bars)
    ) or (
        paper_signal_limit is not None
        and backtest_signal_bars is not None
        and int(paper_signal_limit) != int(backtest_signal_bars)
    ):
        return "市场窗口差异优先：paper 当前窗口与 latest_backtest 回测窗口长度不同。"

    delta = round(
        float((paper_summary.get("performance", {}) or {}).get("total_pnl_usdt", 0.0))
        - float((backtest_result.get("performance", {}) or {}).get("total_pnl_usdt", 0.0)),
        4,
    )
    if delta == 0:
        return "当前 compare 无明显 delta。"

    return "窗口与持仓表面一致但仍有 delta：优先怀疑逻辑变更、数据边界差异，或需运行 realtime/backtest 一致性审计脚本。"


def build_paper_backtest_compare(
    paper_summary: dict, backtest_result: dict | None, root: Path | None = None
) -> dict | None:
    if not backtest_result:
        return None

    paper_perf = paper_summary.get("performance", {})
    backtest_perf = backtest_result.get("performance", {})
    paper_market = paper_summary.get("market_state", {})

    compare = {
        "mode": backtest_result.get("mode", "single"),
        "symbol": backtest_result.get("symbol"),
        "benchmark_symbol": backtest_result.get("benchmark_symbol"),
        "paper_market_state": paper_market.get("state"),
        "paper_open_positions": len(paper_summary.get("portfolio", {}).get("open_positions", [])),
        "paper_closed_trades": paper_perf.get("closed_trades", 0),
        "paper_total_pnl_usdt": paper_perf.get("total_pnl_usdt", 0.0),
        "backtest_closed_trades": backtest_perf.get("closed_trades", 0),
        "backtest_total_pnl_usdt": backtest_perf.get("total_pnl_usdt", 0.0),
        "backtest_win_rate_pct": backtest_perf.get("win_rate_pct", 0.0),
        "delta_total_pnl_usdt": round(
            float(paper_perf.get("total_pnl_usdt", 0.0))
            - float(backtest_perf.get("total_pnl_usdt", 0.0)),
            4,
        ),
        "paper_state_age_seconds": _portfolio_state_age_seconds(root) if root else None,
        "backtest_window": {
            "signal_interval": backtest_result.get("signal_interval"),
            "signal_bars": backtest_result.get("signal_bars"),
            "state_interval": backtest_result.get("state_interval"),
            "state_bars": backtest_result.get("state_bars"),
        },
    }
    compare["delta_reason_hint"] = _build_delta_reason_hint(paper_summary, backtest_result)
    return compare
