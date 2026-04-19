from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from backtest import build_snapshot
from config_loader import load_yaml
from derivatives_data import DerivativesDataClient
from intermarket_data import IntermarketDataClient
from market_data import MarketDataClient
from signal_engine import SignalEngine
from state_engine import StateEngine

ROOT = Path(__file__).resolve().parents[1]


def _normalize_blocked_reason(
    reason: str | None, free_text_reason: str | None = None
) -> str | None:
    if reason:
        return str(reason).strip()
    text = str(free_text_reason or "")
    if "missing_required_setup" in text:
        return "missing_required_setup"
    return None


def _load_journal_records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _build_journal_signal_rows(
    records: list[dict], symbols: set[str], sample_rows: int
) -> list[dict]:
    rows: list[dict] = []
    current_market_state: dict | None = None

    for record in records:
        event_type = record.get("event_type")
        payload = record.get("payload", {}) or {}
        ts = record.get("ts")

        if event_type == "market_state":
            current_market_state = {
                "ts": ts,
                "state": payload.get("state"),
                "reason": payload.get("reason"),
                "benchmark_symbol": payload.get("benchmark_symbol"),
                "benchmark_price": payload.get("benchmark_price"),
                "benchmark_change_24h_pct": payload.get("benchmark_change_24h_pct"),
                "state_interval": payload.get("state_interval"),
                "state_limit": payload.get("state_limit"),
                "signal_interval": payload.get("signal_interval"),
                "signal_limit": payload.get("signal_limit"),
            }
            continue

        if event_type != "signal":
            continue

        symbol = str(payload.get("symbol", "")).upper()
        if symbols and symbol not in symbols:
            continue

        snapshot_payload = payload.get("snapshot", {}) or {}
        row = {
            "journal_ts": ts,
            "symbol": symbol,
            "signal_close_time": snapshot_payload.get("last_kline_close_time"),
            "signal_open_time": snapshot_payload.get("last_kline_open_time"),
            "journal": {
                "symbol": symbol,
                "grade": payload.get("grade"),
                "score": payload.get("score"),
                "setup": payload.get("setup", "none"),
                "blocked_reason": _normalize_blocked_reason(
                    payload.get("blocked_reason"), payload.get("reason")
                ),
                "reason": payload.get("reason"),
                "side": payload.get("side"),
                "signal_interval": payload.get("signal_interval"),
                "explain": payload.get("explain"),
            },
            "market_state": current_market_state or {},
        }
        rows.append(row)

    if sample_rows > 0:
        rows = rows[-sample_rows:]
    return rows


def _parse_iso_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _signal_row(
    symbol: str,
    close_time: float,
    market_state: str,
    signal,
    signal_interval: str,
    state_interval: str,
) -> dict:
    explain = signal.explain or {}
    return {
        "symbol": symbol,
        "close_time": close_time,
        "signal_interval": signal_interval,
        "state_interval": state_interval,
        "market_state": market_state,
        "grade": signal.grade,
        "score": signal.score,
        "setup": signal.setup,
        "blocked_reason": _normalize_blocked_reason(explain.get("blocked_reason"), signal.reason),
        "reason": signal.reason,
        "decision": explain.get("decision"),
        "score_components": explain.get("score_components", []),
    }


def _compare_fields(journal_row: dict, replay_row: dict) -> dict:
    return {
        "grade": journal_row.get("grade") == replay_row.get("grade"),
        "score": journal_row.get("score") == replay_row.get("score"),
        "setup": journal_row.get("setup") == replay_row.get("setup"),
        "blocked_reason": journal_row.get("blocked_reason") == replay_row.get("blocked_reason"),
    }


def _infer_delta_reason(
    market_state_match: bool | None, checks_full: dict, checks_under_journal_state: dict
) -> str:
    if market_state_match is False:
        if all(checks_under_journal_state.values()):
            return "market_state_mismatch"
        return "market_state_and_signal_logic_mismatch"
    if not all(checks_under_journal_state.values()):
        return "signal_logic_mismatch_under_journal_state"
    if not all(checks_full.values()):
        return "replay_window_or_time_alignment_mismatch"
    return "consistent"


def run_consistency_audit(
    symbols: list[str],
    benchmark_symbol: str,
    signal_limit: int,
    state_limit: int,
    sample_rows: int,
    journal_path: Path,
) -> dict:
    strategy = load_yaml("strategy.yaml")
    market_data_cfg = strategy.get("market_data", {})
    default_signal_interval = market_data_cfg.get("signal_interval", "1h")
    default_state_interval = market_data_cfg.get("state_interval", "4h")

    journal_records = _load_journal_records(journal_path)
    journal_rows = _build_journal_signal_rows(journal_records, set(symbols), sample_rows)

    data_client = MarketDataClient()
    state_engine = StateEngine()
    signal_engine = SignalEngine()
    intermarket_client = IntermarketDataClient(data_client)
    derivatives_client = DerivativesDataClient()

    signal_interval = default_signal_interval
    state_interval = default_state_interval

    results: list[dict] = []
    mismatches: list[dict] = []
    replayable_rows = 0
    reason_buckets: dict[str, int] = {}

    for item in journal_rows:
        journal_ts = item.get("journal_ts")
        signal_close_time = item.get("signal_close_time")
        journal_dt = _parse_iso_ts(journal_ts)
        symbol = item.get("symbol")
        if not symbol:
            continue

        if signal_close_time is not None:
            end_ms = int(float(signal_close_time))
        elif journal_dt is not None:
            end_ms = int(journal_dt.timestamp() * 1000)
        else:
            continue

        signal_klines = data_client.fetch_klines(
            symbol, interval=signal_interval, limit=signal_limit, end_time=end_ms
        )
        benchmark_klines = data_client.fetch_klines(
            benchmark_symbol, interval=state_interval, limit=state_limit, end_time=end_ms
        )
        if not signal_klines or not benchmark_klines:
            continue

        close_time = float(signal_klines[-1].get("close_time", 0.0))
        signal_snapshot = build_snapshot(symbol, signal_klines)
        benchmark_snapshot = build_snapshot(benchmark_symbol, benchmark_klines)
        recomputed_market_state = state_engine.classify(
            {"strategy": strategy, "benchmark_snapshot": benchmark_snapshot}
        )

        intermarket = intermarket_client.fetch_context(benchmark_snapshot=benchmark_snapshot)
        derivatives = derivatives_client.fetch_symbol_metrics(symbol)
        data_health = {
            "status": "ok" if not benchmark_snapshot.degraded else "degraded",
            "benchmark_status": "degraded" if benchmark_snapshot.degraded else "ok",
            "intermarket_status": intermarket.get("status", "unknown"),
            "derivatives_degraded_symbols": [symbol]
            if derivatives.get("status") == "degraded"
            else [],
            "derivatives_partial_symbols": [symbol]
            if derivatives.get("status") == "partial"
            else [],
        }

        replay_signal_full = signal_engine.evaluate(
            symbol,
            recomputed_market_state.state,
            {
                "snapshot": signal_snapshot,
                "strategy": strategy,
                "benchmark_snapshot": benchmark_snapshot,
                "intermarket": intermarket,
                "derivatives": derivatives,
                "data_health": data_health,
            },
        )
        replay_row_full = _signal_row(
            symbol,
            close_time,
            recomputed_market_state.state,
            replay_signal_full,
            signal_interval,
            state_interval,
        )

        journal_market_state = (item.get("market_state") or {}).get("state")
        if journal_market_state:
            replay_signal_under_journal_state = signal_engine.evaluate(
                symbol,
                journal_market_state,
                {
                    "snapshot": signal_snapshot,
                    "strategy": strategy,
                    "benchmark_snapshot": benchmark_snapshot,
                    "intermarket": intermarket,
                    "derivatives": derivatives,
                    "data_health": data_health,
                },
            )
            replay_row_under_journal_state = _signal_row(
                symbol,
                close_time,
                journal_market_state,
                replay_signal_under_journal_state,
                signal_interval,
                state_interval,
            )
        else:
            replay_row_under_journal_state = replay_row_full

        journal_signal = item.get("journal", {})
        checks_full = _compare_fields(journal_signal, replay_row_full)
        checks_under_journal_state = _compare_fields(journal_signal, replay_row_under_journal_state)
        same_market_state = (
            journal_market_state == replay_row_full.get("market_state")
            if journal_market_state
            else None
        )

        delta_reason = _infer_delta_reason(
            same_market_state, checks_full, checks_under_journal_state
        )
        consistent = delta_reason == "consistent"
        reason_buckets[delta_reason] = reason_buckets.get(delta_reason, 0) + 1

        result_row = {
            "symbol": symbol,
            "journal_ts": journal_ts,
            "journal_signal_close_time": signal_close_time,
            "matched_close_time": close_time,
            "journal_market_state": journal_market_state,
            "recomputed_market_state": replay_row_full.get("market_state"),
            "market_state_match": same_market_state,
            "journal": journal_signal,
            "replay_full": replay_row_full,
            "replay_under_journal_state": replay_row_under_journal_state,
            "consistent": consistent,
            "delta_reason": delta_reason,
            "checks_full": checks_full,
            "checks_under_journal_state": checks_under_journal_state,
        }
        results.append(result_row)
        replayable_rows += 1
        if not consistent:
            mismatches.append(result_row)

    summary = {
        "mode": "journal_vs_backtest_replay",
        "symbols": symbols,
        "benchmark_symbol": benchmark_symbol,
        "signal_interval": signal_interval,
        "state_interval": state_interval,
        "signal_limit": signal_limit,
        "state_limit": state_limit,
        "journal_path": str(journal_path),
        "journal_signal_rows": len(journal_rows),
        "replayable_rows": replayable_rows,
        "mismatch_rows": len(mismatches),
        "consistency_rate": (
            round(((replayable_rows - len(mismatches)) / replayable_rows) * 100, 2)
            if replayable_rows
            else 0.0
        ),
        "delta_reason_buckets": reason_buckets,
        "generated_at": datetime.now().isoformat(),
    }

    return {
        "summary": summary,
        "mismatches": mismatches[:200],
        "rows": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit journal signals vs backtest replay")
    parser.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    parser.add_argument("--benchmark-symbol", default="BTCUSDT")
    parser.add_argument("--signal-limit", type=int, default=240)
    parser.add_argument("--state-limit", type=int, default=240)
    parser.add_argument("--sample-rows", type=int, default=80)
    parser.add_argument("--journal", default="")
    parser.add_argument("--out", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    journal_path = Path(args.journal) if args.journal else ROOT / "logs" / "journal.jsonl"
    result = run_consistency_audit(
        symbols=symbols,
        benchmark_symbol=args.benchmark_symbol.upper(),
        signal_limit=args.signal_limit,
        state_limit=args.state_limit,
        sample_rows=args.sample_rows,
        journal_path=journal_path,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
