from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class PortfolioState:
    cash_usdt: float = 100.0
    open_positions: list[dict] = field(default_factory=list)
    closed_positions: list[dict] = field(default_factory=list)


class PortfolioManager:
    def __init__(self, root: Path, capital_usdt: float = 100.0):
        self.path = root / "logs" / "portfolio_state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_or_create(capital_usdt)

    def _load_or_create(self, capital_usdt: float) -> PortfolioState:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                state = PortfolioState(
                    cash_usdt=float(data.get("cash_usdt", capital_usdt)),
                    open_positions=list(data.get("open_positions", [])),
                    closed_positions=list(data.get("closed_positions", [])),
                )
                self._normalize_positions(state)
                self._save_state(state)
                return state
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                broken_name = (
                    f"portfolio_state.broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
                )
                broken_path = self.path.with_name(broken_name)
                try:
                    self.path.rename(broken_path)
                except OSError:
                    pass

        state = PortfolioState(cash_usdt=capital_usdt)
        self._save_state(state)
        return state

    def _normalize_positions(self, state: PortfolioState) -> None:
        for item in state.open_positions:
            grade = (item.get("signal_grade") or "").upper().strip()
            item["signal_grade"] = grade or "UNKNOWN"
        for item in state.closed_positions:
            grade = (item.get("signal_grade") or "").upper().strip()
            item["signal_grade"] = grade or "UNKNOWN"

    def _save_state(self, state: PortfolioState) -> None:
        payload = {
            "cash_usdt": state.cash_usdt,
            "open_positions": state.open_positions,
            "closed_positions": state.closed_positions,
        }
        temp_path = self.path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.path)

    def save(self) -> None:
        self._save_state(self.state)

    def has_open_position(self, symbol: str) -> bool:
        symbol = symbol.upper()
        return any(p.get("symbol") == symbol for p in self.state.open_positions)

    def open_position_count(self) -> int:
        return len(self.state.open_positions)

    def open_position_count_for_grade(self, signal_grade: str) -> int:
        grade = signal_grade.upper()
        return sum(
            1 for p in self.state.open_positions if str(p.get("signal_grade", "")).upper() == grade
        )

    def total_exposure_usdt(self) -> float:
        return round(sum(float(p.get("size_usdt", 0.0)) for p in self.state.open_positions), 2)

    def add_position(
        self,
        symbol: str,
        size_usdt: float,
        side: str,
        entry_price: float | None,
        signal_grade: str | None = None,
    ) -> None:
        self.state.open_positions.append(
            {
                "symbol": symbol.upper(),
                "size_usdt": round(size_usdt, 2),
                "side": side.upper(),
                "entry_price": entry_price,
                "signal_grade": (signal_grade or "UNKNOWN").upper(),
            }
        )
        self.state.cash_usdt = round(self.state.cash_usdt - size_usdt, 2)
        self.save()

    def close_position(self, symbol: str, exit_price: float | None, reason: str) -> dict | None:
        symbol = symbol.upper()
        for idx, position in enumerate(self.state.open_positions):
            if position.get("symbol") != symbol:
                continue
            closed = self.state.open_positions.pop(idx)
            closed["exit_price"] = exit_price
            closed["exit_reason"] = reason
            closed["closed_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
            closed["realized_pnl_pct"] = self._calc_pnl_pct(closed)
            closed["realized_pnl_usdt"] = self._calc_pnl_usdt(closed)
            self.state.closed_positions.append(closed)
            self.state.cash_usdt = round(
                self.state.cash_usdt
                + float(closed.get("size_usdt", 0.0))
                + float(closed.get("realized_pnl_usdt", 0.0)),
                4,
            )
            self.save()
            return closed
        return None

    def performance_stats(self, recent_n: int = 10) -> dict:
        closed = list(self.state.closed_positions)
        total = len(closed)
        wins = 0
        losses = 0
        pnl_pct_values: list[float] = []
        pnl_usdt_values: list[float] = []

        for item in closed:
            pnl_pct = item.get("realized_pnl_pct")
            if pnl_pct is None:
                pnl_pct = self._calc_pnl_pct(item)
            pnl_usdt = item.get("realized_pnl_usdt")
            if pnl_usdt is None:
                pnl_usdt = self._calc_pnl_usdt(item)

            if pnl_pct is not None:
                pnl_pct_values.append(float(pnl_pct))
                if float(pnl_pct) > 0:
                    wins += 1
                elif float(pnl_pct) < 0:
                    losses += 1
            if pnl_usdt is not None:
                pnl_usdt_values.append(float(pnl_usdt))

        recent = closed[-recent_n:]
        recent_summary = [
            {
                "symbol": item.get("symbol"),
                "side": item.get("side"),
                "entry_price": item.get("entry_price"),
                "exit_price": item.get("exit_price"),
                "signal_grade": item.get("signal_grade") or "UNKNOWN",
                "pnl_pct": (
                    item.get("realized_pnl_pct")
                    if item.get("realized_pnl_pct") is not None
                    else self._calc_pnl_pct(item)
                ),
                "pnl_usdt": (
                    item.get("realized_pnl_usdt")
                    if item.get("realized_pnl_usdt") is not None
                    else self._calc_pnl_usdt(item)
                ),
                "reason": item.get("exit_reason"),
            }
            for item in recent
        ]

        avg_pnl_pct = round(sum(pnl_pct_values) / len(pnl_pct_values), 4) if pnl_pct_values else 0.0
        total_pnl_pct = round(sum(pnl_pct_values), 4) if pnl_pct_values else 0.0
        total_pnl_usdt = round(sum(pnl_usdt_values), 4) if pnl_usdt_values else 0.0
        win_rate = round((wins / total) * 100, 2) if total else 0.0

        return {
            "closed_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": win_rate,
            "avg_pnl_pct": avg_pnl_pct,
            "total_pnl_pct": total_pnl_pct,
            "total_pnl_usdt": total_pnl_usdt,
            "recent_n": recent_n,
            "recent_trades": recent_summary,
        }

    def _calc_pnl_pct(self, position: dict) -> float | None:
        entry = position.get("entry_price")
        exit_price = position.get("exit_price")
        if entry in (None, 0) or exit_price in (None, 0):
            return None
        pnl_pct = ((float(exit_price) - float(entry)) / float(entry)) * 100
        if position.get("side") == "SELL":
            pnl_pct *= -1
        return round(pnl_pct, 4)

    def _calc_pnl_usdt(self, position: dict) -> float | None:
        pnl_pct = self._calc_pnl_pct(position)
        size_usdt = position.get("size_usdt")
        if pnl_pct is None or size_usdt in (None, 0):
            return None
        return round(float(size_usdt) * (float(pnl_pct) / 100.0), 4)
