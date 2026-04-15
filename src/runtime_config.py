from __future__ import annotations


def build_runtime_risk(strategy: dict, risk: dict) -> dict:
    signal_levels = strategy.get("signal_levels", {})
    grade_caps = {
        "max_a_positions": int(
            signal_levels.get("A", {}).get("max_positions", risk.get("max_open_positions", 2))
        ),
        "max_b_positions": int(
            signal_levels.get("B", {}).get("max_positions", risk.get("max_open_positions", 2))
        ),
    }
    return {**risk, **grade_caps}
