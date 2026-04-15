from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path

from backtest import run_backtest
from config_loader import load_yaml
from journal import Journal
from market_data import MarketDataClient

ROOT = Path(__file__).resolve().parents[1]


def preload_lab_market_inputs(
    symbol: str, benchmark_symbol: str, signal_limit: int, state_limit: int
) -> dict:
    strategy = load_yaml("strategy.yaml")
    market_data_cfg = strategy.get("market_data", {})
    signal_interval = market_data_cfg.get("signal_interval", "1h")
    state_interval = market_data_cfg.get("state_interval", "4h")

    client = MarketDataClient()
    return {
        "signal_klines": client.fetch_klines(symbol, interval=signal_interval, limit=signal_limit),
        "benchmark_klines": client.fetch_klines(
            benchmark_symbol, interval=state_interval, limit=state_limit
        ),
        "signal_interval": signal_interval,
        "state_interval": state_interval,
    }


STRATEGY_CANDIDATES = [
    {
        "name": "breakout_only",
        "description": "Only breakout setups allowed; pullback/reclaim disabled.",
        "family": "breakout",
        "tags": ["trend", "momentum", "minimalist"],
        "overrides": {
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            }
        },
    },
    {
        "name": "breakout_only_strict",
        "description": "Breakout only with stricter breakout/momentum confirmation.",
        "family": "breakout",
        "tags": ["trend", "momentum", "strict"],
        "overrides": {
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True, "lookback_bars": 30},
                "pullback": {"enabled": False},
                "reclaim": {"enabled": False},
            },
            "signal_params": {
                "momentum_positive_min_pct": 1.2,
                "momentum_strong_min_pct": 2.5,
                "close_near_high_min": 0.8,
            },
        },
    },
    {
        "name": "breakout_pullback",
        "description": "Breakout + pullback allowed; reclaim disabled.",
        "family": "hybrid",
        "tags": ["trend", "pullback", "balanced"],
        "overrides": {
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True},
                "pullback": {"enabled": True},
                "reclaim": {"enabled": False},
            }
        },
    },
    {
        "name": "full_stack_setups",
        "description": "Breakout + pullback + reclaim all enabled.",
        "family": "hybrid",
        "tags": ["trend", "pullback", "reclaim", "broad"],
        "overrides": {
            "setup_filters": {
                "require_setup_for_buy": True,
                "breakout": {"enabled": True},
                "pullback": {"enabled": True},
                "reclaim": {"enabled": True},
            }
        },
    },
]

STATE_PROFILES = [
    {
        "name": "s1s2_only",
        "description": "Only trade constructive S1/S2 environments.",
        "style": "defensive",
        "overrides": {"allow_s3_entries": False},
    },
    {
        "name": "s3_selective",
        "description": "Allow S3 but raise B-score threshold to be more selective.",
        "style": "balanced",
        "overrides": {"allow_s3_entries": True, "b_min_score": 4},
    },
    {
        "name": "s3_very_selective",
        "description": "Allow S3 only with near-A quality threshold.",
        "style": "tight",
        "overrides": {"allow_s3_entries": True, "b_min_score": 5},
    },
    {
        "name": "global_default",
        "description": "Current default global mode.",
        "style": "looser",
        "overrides": {"allow_s3_entries": True, "b_min_score": 3},
    },
]

CANDIDATE_INDEX = {item["name"]: item for item in STRATEGY_CANDIDATES}
STATE_PROFILE_INDEX = {item["name"]: item for item in STATE_PROFILES}


def deep_merge(base: dict, updates: dict) -> dict:
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def extract_result_identity(result: dict) -> dict:
    candidate_meta = CANDIDATE_INDEX.get(result.get("candidate"), {})
    state_meta = STATE_PROFILE_INDEX.get(result.get("state_profile"), {})

    candidate_family = result.get("candidate_family") or candidate_meta.get("family", "unknown")
    candidate_tags = result.get("candidate_tags") or candidate_meta.get("tags", [])
    state_style = result.get("state_style") or state_meta.get("style", "unknown")
    archetype = result.get("archetype") or f"{candidate_family}__{state_style}"

    return {
        "candidate": result.get("candidate"),
        "candidate_family": candidate_family,
        "candidate_tags": candidate_tags,
        "state_profile": result.get("state_profile"),
        "state_style": state_style,
        "archetype": archetype,
    }


def annotate_result_identity(
    result: dict, candidate: dict | None = None, state_profile: dict | None = None
) -> dict:
    result = dict(result)
    if candidate is not None:
        result["candidate"] = candidate["name"]
        result["candidate_description"] = candidate["description"]
        result["candidate_family"] = candidate.get("family", "unknown")
        result["candidate_tags"] = candidate.get("tags", [])
    if state_profile is not None:
        result["state_profile"] = state_profile["name"]
        result["state_profile_description"] = state_profile["description"]
        result["state_style"] = state_profile.get("style", "unknown")
    result.update(extract_result_identity(result))
    return result


def compute_objective_score(result: dict) -> float:
    perf = result.get("performance", {})
    trades = int(perf.get("closed_trades", 0) or 0)
    win_rate = float(perf.get("win_rate_pct", 0.0) or 0.0)
    total_pnl_usdt = float(perf.get("total_pnl_usdt", 0.0) or 0.0)
    avg_pnl_pct = float(perf.get("avg_pnl_pct", 0.0) or 0.0)
    ending_equity = float(result.get("ending_equity_usdt", 0.0) or 0.0)

    if trades == 0:
        return round(-999.0 + (ending_equity / 1000.0), 4)

    sample_penalty = 0.0
    if trades < 2:
        sample_penalty = 10.0
    elif trades < 4:
        sample_penalty = 4.0

    trade_support_bonus = min(trades, 8) * 0.8
    score = (
        total_pnl_usdt * 100.0
        + win_rate * 0.6
        + avg_pnl_pct * 10.0
        + trade_support_bonus
        - sample_penalty
    )
    return round(score, 4)


def rank_results(results: list[dict]) -> list[dict]:
    ranked = []
    for result in results:
        item = dict(result)
        item["objective_score"] = compute_objective_score(item)
        ranked.append(item)

    return sorted(
        ranked,
        key=lambda x: (
            x["objective_score"],
            1 if x["performance"]["closed_trades"] > 0 else 0,
            x["performance"]["total_pnl_usdt"],
            x["performance"]["win_rate_pct"],
            x["ending_equity_usdt"],
            x["performance"]["closed_trades"],
        ),
        reverse=True,
    )


def summarize_group_pnl(results: list[dict], field: str) -> list[dict]:
    grouped: dict[str, dict] = {}
    for result in results:
        key = str(result.get(field, "unknown"))
        bucket = grouped.setdefault(
            key,
            {
                "key": key,
                "profiles": 0,
                "tradable_profiles": 0,
                "closed_trades": 0,
                "total_pnl_usdt": 0.0,
                "best_equity_usdt": 0.0,
                "objective_score_sum": 0.0,
                "objective_score_avg": 0.0,
            },
        )
        perf = result.get("performance", {})
        bucket["profiles"] += 1
        if perf.get("closed_trades", 0) > 0:
            bucket["tradable_profiles"] += 1
        bucket["closed_trades"] += int(perf.get("closed_trades", 0))
        bucket["total_pnl_usdt"] += float(perf.get("total_pnl_usdt", 0.0))
        bucket["best_equity_usdt"] = max(
            bucket["best_equity_usdt"], float(result.get("ending_equity_usdt", 0.0))
        )
        bucket["objective_score_sum"] += float(
            result.get("objective_score", compute_objective_score(result))
        )

    for bucket in grouped.values():
        profiles = max(1, bucket["profiles"])
        bucket["objective_score_avg"] = round(bucket["objective_score_sum"] / profiles, 4)

    return sorted(
        grouped.values(),
        key=lambda x: (
            x["objective_score_avg"],
            x["tradable_profiles"],
            x["total_pnl_usdt"],
            x["best_equity_usdt"],
        ),
        reverse=True,
    )


def detect_failure_modes(results: list[dict]) -> list[str]:
    notes: list[str] = []
    if not results:
        return notes

    zero_trade_count = sum(1 for r in results if r["performance"]["closed_trades"] == 0)
    if zero_trade_count:
        notes.append(
            f"{zero_trade_count}/{len(results)} profiles produced zero trades, so over-filtering remains a first-class risk."
        )

    s1s2_results = [r for r in results if r.get("state_profile") == "s1s2_only"]
    if s1s2_results and all(r["performance"]["closed_trades"] == 0 for r in s1s2_results):
        notes.append(
            "Pure S1/S2-only mode still collapses activity; S3 likely needs filtering rather than a hard ban."
        )

    reclaim_negative = []
    for result in results:
        reclaim_stats = result.get("analysis", {}).get("by_setup", {}).get("reclaim")
        if reclaim_stats and float(reclaim_stats.get("total_pnl_usdt", 0.0)) < 0:
            reclaim_negative.append(result)
    if reclaim_negative:
        notes.append("Reclaim still shows repeated negative contribution across tested profiles.")

    return notes


def build_learning_summary(results: list[dict]) -> dict:
    if not results:
        return {
            "winner": None,
            "keep": [],
            "deprioritize": [],
            "notes": [],
            "family_ranking": [],
            "state_style_ranking": [],
            "failure_modes": [],
        }

    ranked_results = rank_results(results)
    tradable_results = [r for r in ranked_results if r["performance"]["closed_trades"] > 0]
    winner = tradable_results[0] if tradable_results else ranked_results[0]
    notes: list[str] = []
    deprioritize: list[str] = []
    keep: list[str] = []

    if not tradable_results:
        notes.append(
            "No tradable profile produced closed trades in this run; current filters are too strict for this sample window."
        )

    setup_totals: dict[str, float] = {}
    for result in tradable_results:
        by_setup = result.get("analysis", {}).get("by_setup", {})
        for setup_name, stats in by_setup.items():
            setup_totals[setup_name] = setup_totals.get(setup_name, 0.0) + float(
                stats.get("total_pnl_usdt", 0.0)
            )

    for setup_name, total_pnl in sorted(setup_totals.items(), key=lambda x: x[1], reverse=True):
        if setup_name == "none":
            continue
        if total_pnl >= 0:
            keep.append(setup_name)
        else:
            deprioritize.append(setup_name)

    failure_modes = detect_failure_modes(ranked_results)
    notes.extend(failure_modes)

    family_ranking = summarize_group_pnl(ranked_results, "candidate")
    state_style_ranking = summarize_group_pnl(ranked_results, "state_profile")

    winner_identity = extract_result_identity(winner)
    notes.append(
        f"Current best candidate is {winner['candidate']} + {winner['state_profile']} ({winner_identity['archetype']}) with objective={winner.get('objective_score', 0.0)}, equity={winner['ending_equity_usdt']} and totalPnL={winner['performance']['total_pnl_usdt']} USDT."
    )

    return {
        "winner": {
            "candidate": winner["candidate"],
            "state_profile": winner["state_profile"],
            "ending_equity_usdt": winner["ending_equity_usdt"],
            "closed_trades": winner["performance"]["closed_trades"],
            "win_rate_pct": winner["performance"]["win_rate_pct"],
            "total_pnl_usdt": winner["performance"]["total_pnl_usdt"],
            "objective_score": winner.get("objective_score", 0.0),
            **winner_identity,
        },
        "keep": keep,
        "deprioritize": deprioritize,
        "notes": notes,
        "family_ranking": family_ranking,
        "state_style_ranking": state_style_ranking,
        "failure_modes": failure_modes,
    }


def build_attribution_experiments(results: list[dict], add_fn, base_overrides: dict) -> None:
    tradable_results = [r for r in results if r.get("performance", {}).get("closed_trades", 0) > 0]
    source_results = tradable_results or results
    if not source_results:
        return

    aggregated_hints: list[dict] = []
    for result in source_results:
        for hint in result.get("analysis", {}).get("worst_group_hints", []):
            aggregated_hints.append(hint)

    if not aggregated_hints:
        return

    seen_pairs = set()
    for hint in aggregated_hints:
        pair = (hint.get("source"), str(hint.get("key")))
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        source = hint.get("source")
        key = str(hint.get("key", ""))
        if source == "market_state" and key == "S3":
            add_fn(
                "s3_entry_throttle",
                "Failure attribution: S3 dominates the losing groups, so throttle S3 by demanding stronger quality and tighter near-high confirmation.",
                deep_merge(
                    base_overrides,
                    {
                        "allow_s3_entries": True,
                        "b_min_score": 5,
                        "signal_params": {
                            "close_near_high_min": 0.82,
                            "momentum_positive_min_pct": 1.15,
                        },
                    },
                ),
            )

        if source == "exit_reason" and "EMA20" in key:
            add_fn(
                "ema_exit_sensitivity_probe",
                "Failure attribution: repeated exits are caused by EMA20 loss, so probe a slower exit regime to avoid immediate whipsaw exits.",
                deep_merge(base_overrides, {"ema_exit_period": 34}),
            )

        if source == "setup" and key == "pullback":
            add_fn(
                "pullback_quality_gate",
                "Failure attribution: pullback setup is repeatedly losing, so constrain pullback to constructive states and demand stronger quality.",
                deep_merge(
                    base_overrides,
                    {
                        "setup_filters": {
                            "pullback": {"enabled": True, "require_market_states": ["S1", "S2"]},
                            "reclaim": {"enabled": False},
                        },
                        "allow_s3_entries": False,
                        "b_min_score": 4,
                    },
                ),
            )

        if source == "setup" and key == "breakout":
            add_fn(
                "breakout_false_start_filter",
                "Failure attribution: breakout entries are stopping out quickly, so tighten breakout lookback and confirmation before allowing entry.",
                deep_merge(
                    base_overrides,
                    {
                        "setup_filters": {
                            "breakout": {"enabled": True, "lookback_bars": 28},
                            "reclaim": {"enabled": False},
                        },
                        "signal_params": {
                            "close_near_high_min": 0.84,
                            "momentum_positive_min_pct": 1.2,
                        },
                    },
                ),
            )

        if source == "signal_bucket" and key == "A_like(score>=5)":
            add_fn(
                "a_like_false_strength_filter",
                "Failure attribution: even A-like signals are losing, so require stronger momentum and better close location before treating high-score signals as valid.",
                deep_merge(
                    base_overrides,
                    {
                        "signal_params": {
                            "momentum_positive_min_pct": 1.3,
                            "momentum_strong_min_pct": 2.8,
                            "close_near_high_min": 0.85,
                        }
                    },
                ),
            )


def build_blocked_reason_experiments(results: list[dict], add_fn, base_overrides: dict) -> None:
    if not results:
        return

    blocked_reason_counts: dict[str, int] = {}
    for result in results:
        blocked = result.get("analysis", {}).get("blocked", {})
        by_reason = blocked.get("by_reason", {}) if isinstance(blocked, dict) else {}
        for reason, stats in by_reason.items():
            blocked_reason_counts[reason] = blocked_reason_counts.get(reason, 0) + int(
                stats.get("count", 0) or 0
            )

    if not blocked_reason_counts:
        return

    top_reasons = sorted(blocked_reason_counts.items(), key=lambda x: x[1], reverse=True)[:4]
    seen = {name for name, _ in top_reasons}

    if "missing_required_setup" in seen:
        add_fn(
            "setup_requirement_relief_probe",
            "Blocked-reason relief: required setup is rejecting too many candidates, so relax setup breadth while keeping reclaim disabled.",
            deep_merge(
                base_overrides,
                {
                    "setup_filters": {
                        "require_setup_for_buy": True,
                        "breakout": {"enabled": True, "lookback_bars": 18},
                        "pullback": {"enabled": True, "require_market_states": ["S1", "S2", "S3"]},
                        "reclaim": {"enabled": False},
                    }
                },
            ),
        )

    if "score_below_B_threshold" in seen:
        add_fn(
            "score_threshold_relief_probe",
            "Blocked-reason relief: many candidates miss the B threshold, so lower the B gate slightly and ease near-high confirmation.",
            deep_merge(
                base_overrides,
                {
                    "b_min_score": 2,
                    "signal_params": {
                        "close_near_high_min": 0.66,
                        "momentum_positive_min_pct": 0.6,
                    },
                },
            ),
        )

    if "backtest_s3_disabled" in seen:
        add_fn(
            "s3_reenable_probe",
            "Blocked-reason relief: many potential entries are blocked only because S3 is disabled, so re-enable S3 with a tighter threshold instead of a full ban.",
            deep_merge(
                base_overrides,
                {
                    "allow_s3_entries": True,
                    "b_min_score": 4,
                    "signal_params": {
                        "momentum_positive_min_pct": 1.0,
                        "close_near_high_min": 0.76,
                    },
                },
            ),
        )

    if "risk_open_filter_reject" in seen:
        add_fn(
            "risk_capacity_probe",
            "Blocked-reason relief: signals are being rejected by portfolio/risk open filters, so inspect whether the issue is frequency or portfolio capacity clustering.",
            deep_merge(base_overrides, {}),
        )

def build_next_experiments(results: list[dict], learning_summary: dict) -> list[dict]:
    experiments: list[dict] = []
    seen: set[str] = set()
    source_buckets: dict[str, list[dict]] = {
        "template": [],
        "attribution": [],
        "blocked_reason": [],
        "objective": [],
        "fallback": [],
    }

    def add(name: str, thesis: str, overrides: dict, source: str = "template") -> None:
        key = json.dumps({"name": name, "overrides": overrides}, sort_keys=True, ensure_ascii=False)
        if key in seen:
            return
        seen.add(key)
        item = {"name": name, "thesis": thesis, "overrides": overrides, "source": source}
        experiments.append(item)
        source_buckets.setdefault(source, []).append(item)

    winner = learning_summary.get("winner") or {}
    winner_candidate = winner.get("candidate")
    winner_state_profile = winner.get("state_profile")
    tradable_results = [r for r in results if r["performance"]["closed_trades"] > 0]
    best_result = tradable_results[0] if tradable_results else (results[0] if results else None)
    base_overrides = deepcopy(best_result.get("overrides", {})) if best_result is not None else {}

    if winner_candidate == "breakout_only" and winner_state_profile in {
        "s3_selective",
        "s3_very_selective",
        "global_default",
    }:
        add(
            "breakout_only_tighter_s3",
            "If breakout-only is leading, the next edge is likely not more setups but tighter S3 admission.",
            {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True, "lookback_bars": 24},
                    "pullback": {"enabled": False},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": True,
                "b_min_score": 5,
                "signal_params": {
                    "momentum_positive_min_pct": 1.0,
                    "close_near_high_min": 0.78,
                },
            },
            source="template",
        )

    if winner_candidate == "breakout_only_strict":
        add(
            "breakout_strict_with_small_relief",
            "Strict breakout may be too sparse; slightly relax momentum/near-high rules without re-enabling weak setups.",
            {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True, "lookback_bars": 24},
                    "pullback": {"enabled": False},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": True,
                "b_min_score": 4,
                "signal_params": {
                    "momentum_positive_min_pct": 1.0,
                    "momentum_strong_min_pct": 2.2,
                    "close_near_high_min": 0.76,
                },
            },
            source="template",
        )

    if winner_candidate == "breakout_pullback":
        add(
            "breakout_pullback_no_reclaim_stricter_pullback",
            "If breakout+pullback wins, test whether pullback helps only in constructive states with slightly tighter quality control.",
            {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True, "lookback_bars": 20},
                    "pullback": {"enabled": True, "require_market_states": ["S1", "S2"]},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": True,
                "b_min_score": 4,
            },
            source="template",
        )

    if "reclaim" in set(learning_summary.get("deprioritize", [])):
        add(
            "reclaim_off_retest",
            "Reclaim remains suspect; keep it disabled and re-test whether equity improves from reduced false entries.",
            {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True},
                    "pullback": {"enabled": True},
                    "reclaim": {"enabled": False},
                }
            },
            source="template",
        )

    if any("S3 likely" in note or "hard ban" in note for note in learning_summary.get("notes", [])):
        add(
            "s3_micro_filter_test",
            "S3 should probably be filtered, not banned; test stricter B threshold plus stronger momentum requirement.",
            {
                "allow_s3_entries": True,
                "b_min_score": 5,
                "signal_params": {
                    "momentum_positive_min_pct": 1.2,
                    "close_near_high_min": 0.8,
                },
            },
            source="template",
        )

    zero_trade_count = sum(1 for r in results if r["performance"]["closed_trades"] == 0)
    if results and zero_trade_count >= max(1, len(results) // 2):
        add(
            "anti_overfilter_probe",
            "Many profiles have zero trades, so add one probe with slightly looser setup strictness to measure whether the engine is over-filtered.",
            {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True, "lookback_bars": 18},
                    "pullback": {"enabled": True, "require_market_states": ["S1", "S2", "S3"]},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": True,
                "b_min_score": 4,
                "signal_params": {
                    "close_near_high_min": 0.68,
                },
            },
            source="template",
        )

    build_attribution_experiments(
        results, lambda n, t, o: add(n, t, o, source="attribution"), base_overrides
    )
    build_blocked_reason_experiments(
        results, lambda n, t, o: add(n, t, o, source="blocked_reason"), base_overrides
    )

    if best_result is not None:
        perf = best_result.get("performance", {})
        trades = int(perf.get("closed_trades", 0) or 0)
        win_rate = float(perf.get("win_rate_pct", 0.0) or 0.0)
        avg_pnl = float(perf.get("avg_pnl_pct", 0.0) or 0.0)

        if trades <= 2:
            add(
                "trade_frequency_probe",
                "Objective optimization: current best relies on too few trades, so slightly relax entry strictness to test whether profit can scale without collapsing quality.",
                deep_merge(
                    base_overrides,
                    {
                        "b_min_score": 4,
                        "signal_params": {
                            "close_near_high_min": 0.76,
                            "momentum_positive_min_pct": 1.0,
                        },
                    },
                ),
                source="objective",
            )

        if win_rate >= 20.0 and avg_pnl <= 0:
            add(
                "profit_capture_probe",
                "Objective optimization: win rate is not the main problem, average trade quality is. Tighten quality to improve average profit per trade instead of just adding more trades.",
                deep_merge(
                    base_overrides,
                    {
                        "b_min_score": 5,
                        "signal_params": {
                            "close_near_high_min": 0.84,
                            "momentum_positive_min_pct": 1.2,
                        },
                    },
                ),
                source="objective",
            )

        if win_rate == 0.0 and trades > 0:
            add(
                "winrate_rescue_probe",
                "Objective optimization: the current profile trades but never wins, so reduce false starts by demanding stronger confirmation and fewer marginal entries.",
                deep_merge(
                    base_overrides,
                    {
                        "b_min_score": 5,
                        "signal_params": {
                            "close_near_high_min": 0.86,
                            "momentum_positive_min_pct": 1.25,
                            "momentum_strong_min_pct": 2.8,
                        },
                    },
                ),
                source="objective",
            )

    if best_result is not None and not experiments:
        add(
            "winner_neighbor_probe",
            "No strong directional lesson was found, so probe one neighbor around the current winner.",
            deep_merge(best_result.get("overrides", {}), {"b_min_score": 4}),
            source="fallback",
        )

    zero_trade_count = sum(1 for r in results if r["performance"]["closed_trades"] == 0)
    zero_trade_dominant = bool(results) and zero_trade_count >= max(1, (len(results) * 3) // 4)

    if zero_trade_dominant:
        selected = (
            source_buckets.get("blocked_reason", [])[:4]
            + source_buckets.get("template", [])[:2]
            + source_buckets.get("attribution", [])[:2]
            + source_buckets.get("objective", [])[:1]
            + source_buckets.get("fallback", [])[:1]
        )
    else:
        selected = (
            source_buckets.get("template", [])[:3]
            + source_buckets.get("attribution", [])[:2]
            + source_buckets.get("blocked_reason", [])[:3]
            + source_buckets.get("objective", [])[:2]
            + source_buckets.get("fallback", [])[:1]
        )
    return selected[:9]


def build_lab_meta(results: list[dict]) -> dict:
    zero_trade_profiles = sum(1 for r in results if r["performance"]["closed_trades"] == 0)
    tradable_profiles = len(results) - zero_trade_profiles
    return {
        "profile_count": len(results),
        "tradable_profiles": tradable_profiles,
        "zero_trade_profiles": zero_trade_profiles,
        "zero_trade_ratio": (
            round((zero_trade_profiles / len(results)) * 100, 2) if results else 0.0
        ),
    }


def build_recommended_patch(winner: dict) -> dict:
    overrides = deepcopy(winner.get("overrides", {})) if isinstance(winner, dict) else {}
    strategy_patch: dict = {}
    risk_patch: dict = {}

    if "setup_filters" in overrides:
        strategy_patch["setup_filters"] = deepcopy(overrides["setup_filters"])
    if "signal_params" in overrides:
        strategy_patch["signal_params"] = deepcopy(overrides["signal_params"])
    if "state_params" in overrides:
        strategy_patch["state_params"] = deepcopy(overrides["state_params"])
    if "b_min_score" in overrides:
        strategy_patch.setdefault("signal_levels", {}).setdefault("B", {})["min_score"] = int(
            overrides["b_min_score"]
        )
    if "allow_s3_entries" in overrides:
        strategy_patch.setdefault("backtest", {})["allow_s3_entries"] = bool(
            overrides["allow_s3_entries"]
        )
    if "ema_exit_period" in overrides:
        risk_patch["ema_exit_period"] = int(overrides["ema_exit_period"])

    return {
        "strategy": strategy_patch,
        "risk": risk_patch,
    }


def build_tuning_plan(results: list[dict], learning_summary: dict) -> dict:
    winner = learning_summary.get("winner") or {}
    ranked = rank_results(results) if results else []
    best_result = None
    if winner:
        for item in ranked:
            if item.get("candidate") == winner.get("candidate") and item.get("state_profile") == winner.get(
                "state_profile"
            ):
                best_result = item
                break
    if best_result is None and ranked:
        best_result = ranked[0]

    recommended_patch = build_recommended_patch(best_result or {})
    deployment_notes: list[str] = []
    validation_checks: list[str] = [
        "Run the recommended patch through backtest on the current sample window.",
        "Run one neighbor batch around the winner to confirm the edge is not a one-off.",
        "Paper-run the winner before promoting any config into live trading.",
    ]

    if winner:
        deployment_notes.append(
            f"Promote winner {winner.get('candidate')} + {winner.get('state_profile')} as the current default research baseline."
        )
    if recommended_patch.get("strategy"):
        deployment_notes.append("Apply the strategy patch in config/strategy.yaml only after backtest confirmation.")
    if recommended_patch.get("risk"):
        deployment_notes.append("Apply the risk patch in config/risk.yaml only after paper validation.")
    if learning_summary.get("deprioritize"):
        deployment_notes.append(
            "Keep deprioritized setups disabled or constrained in the next round: "
            + ", ".join(learning_summary.get("deprioritize", []))
        )
    if learning_summary.get("failure_modes"):
        deployment_notes.extend(learning_summary.get("failure_modes", [])[:3])

    return {
        "winner_baseline": {
            "candidate": winner.get("candidate"),
            "state_profile": winner.get("state_profile"),
            "archetype": winner.get("archetype"),
        },
        "recommended_patch": recommended_patch,
        "deployment_notes": deployment_notes,
        "validation_checks": validation_checks,
    }


def format_tuning_plan(plan: dict) -> str:
    winner = plan.get("winner_baseline", {})
    lines = ["=== TUNING PLAN ==="]
    if winner.get("candidate"):
        lines.append(
            f"Baseline winner: {winner.get('candidate')} + {winner.get('state_profile')} | archetype={winner.get('archetype', 'unknown')}"
        )
    patch = plan.get("recommended_patch", {})
    strategy_patch = patch.get("strategy", {})
    risk_patch = patch.get("risk", {})
    lines.append(
        f"Patch summary: strategyKeys={sorted(strategy_patch.keys()) if strategy_patch else []} | riskKeys={sorted(risk_patch.keys()) if risk_patch else []}"
    )
    for note in plan.get("deployment_notes", []):
        lines.append(f"- Deploy: {note}")
    for check in plan.get("validation_checks", []):
        lines.append(f"- Validate: {check}")
    return "\n".join(lines)


def write_tuning_plan_artifacts(payload: dict, base_dir: Path) -> dict:
    base_dir.mkdir(parents=True, exist_ok=True)
    tuning_plan = payload.get("tuning_plan", {}) if isinstance(payload, dict) else {}
    patch = tuning_plan.get("recommended_patch", {}) if isinstance(tuning_plan, dict) else {}

    strategy_path = base_dir / "recommended_strategy_patch.json"
    risk_path = base_dir / "recommended_risk_patch.json"
    tuning_plan_path = base_dir / "tuning_plan.json"

    strategy_payload = patch.get("strategy", {}) if isinstance(patch, dict) else {}
    risk_payload = patch.get("risk", {}) if isinstance(patch, dict) else {}

    strategy_path.write_text(json.dumps(strategy_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    risk_path.write_text(json.dumps(risk_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tuning_plan_path.write_text(json.dumps(tuning_plan, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "strategy_patch": str(strategy_path),
        "risk_patch": str(risk_path),
        "tuning_plan": str(tuning_plan_path),
    }


def build_lab_meta(results: list[dict]) -> dict:
    zero_trade_profiles = sum(1 for r in results if r["performance"]["closed_trades"] == 0)
    tradable_profiles = len(results) - zero_trade_profiles
    return {
        "profile_count": len(results),
        "tradable_profiles": tradable_profiles,
        "zero_trade_profiles": zero_trade_profiles,
        "zero_trade_ratio": (
            round((zero_trade_profiles / len(results)) * 100, 2) if results else 0.0
        ),
    }


def format_lab_panel(payload: dict) -> str:
    meta = payload.get("lab_meta", {})
    lines = [
        "=== STRATEGY LAB RANKING ===",
        (
            f"Symbol: {payload['symbol']} | Benchmark: {payload['benchmark_symbol']} | profiles={len(payload['ranking'])} "
            f"| tradable={meta.get('tradable_profiles', 0)} | zeroTrade={meta.get('zero_trade_profiles', 0)}"
        ),
    ]
    for idx, item in enumerate(payload["ranking"][:10], start=1):
        perf = item["performance"]
        identity = extract_result_identity(item)
        lines.append(
            f"{idx}. {item['candidate']} + {item['state_profile']} | archetype={identity['archetype']} | objective={item.get('objective_score', 0.0)} | equity={item['ending_equity_usdt']} | totalPnL={perf['total_pnl_usdt']} USDT | winRate={perf['win_rate_pct']}% | trades={perf['closed_trades']}"
        )

    learning = payload.get("learning_summary", {})
    winner = learning.get("winner")
    if winner:
        lines.append("Best candidate:")
        lines.append(
            f"- {winner['candidate']} + {winner['state_profile']} | archetype={winner['archetype']} | objective={winner.get('objective_score', 0.0)} | equity={winner['ending_equity_usdt']} | totalPnL={winner['total_pnl_usdt']} USDT | winRate={winner['win_rate_pct']}% | trades={winner['closed_trades']}"
        )
    for note in learning.get("notes", []):
        lines.append(f"- Note: {note}")
    if learning.get("keep"):
        lines.append("- Keep watching: " + ", ".join(learning["keep"]))
    if learning.get("deprioritize"):
        lines.append("- Deprioritize: " + ", ".join(learning["deprioritize"]))

    family_ranking = learning.get("family_ranking", [])
    if family_ranking:
        lines.append("Candidate family ranking:")
        for item in family_ranking[:4]:
            lines.append(
                f"- {item['key']}: pnl={round(item['total_pnl_usdt'], 4)} USDT | tradableProfiles={item['tradable_profiles']}/{item['profiles']} | trades={item['closed_trades']}"
            )

    state_style_ranking = learning.get("state_style_ranking", [])
    if state_style_ranking:
        lines.append("State profile ranking:")
        for item in state_style_ranking[:4]:
            lines.append(
                f"- {item['key']}: pnl={round(item['total_pnl_usdt'], 4)} USDT | tradableProfiles={item['tradable_profiles']}/{item['profiles']} | trades={item['closed_trades']}"
            )

    tuning_plan = payload.get("tuning_plan")
    if tuning_plan:
        lines.append(format_tuning_plan(tuning_plan))

    next_experiments = payload.get("next_experiments", [])
    if next_experiments:
        lines.append("Next experiments:")
        for idx, item in enumerate(next_experiments, start=1):
            lines.append(f"- E{idx} {item['name']}: {item['thesis']}")
    return "\n".join(lines)


def run_strategy_lab(
    symbol: str, benchmark_symbol: str, signal_limit: int, state_limit: int
) -> dict:
    _strategy = load_yaml("strategy.yaml")
    _risk = load_yaml("risk.yaml")
    _ = _strategy, _risk

    results: list[dict] = []
    for candidate in STRATEGY_CANDIDATES:
        for state_profile in STATE_PROFILES:
            overrides = deep_merge(candidate["overrides"], state_profile["overrides"])
            result = run_backtest(
                symbol=symbol,
                benchmark_symbol=benchmark_symbol,
                signal_limit=signal_limit,
                state_limit=state_limit,
                overrides=overrides,
            )
            results.append(annotate_result_identity(result, candidate, state_profile))

    ranking = rank_results(results)
    learning_summary = build_learning_summary(ranking)
    tuning_plan = build_tuning_plan(ranking, learning_summary)
    payload = {
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "signal_limit": signal_limit,
        "state_limit": state_limit,
        "candidates": STRATEGY_CANDIDATES,
        "state_profiles": STATE_PROFILES,
        "ranking": ranking,
        "lab_meta": build_lab_meta(ranking),
        "learning_summary": learning_summary,
        "tuning_plan": tuning_plan,
        "next_experiments": build_next_experiments(ranking, learning_summary),
    }
    payload["ranking_panel"] = format_lab_panel(payload)
    return payload


def run_experiment_batch(
    symbol: str,
    benchmark_symbol: str,
    signal_limit: int,
    state_limit: int,
    experiments: list[dict],
    round_name: str,
) -> dict:
    preloaded = preload_lab_market_inputs(symbol, benchmark_symbol, signal_limit, state_limit)

    results: list[dict] = []
    for experiment in experiments:
        result = run_backtest(
            symbol=symbol,
            benchmark_symbol=benchmark_symbol,
            signal_limit=signal_limit,
            state_limit=state_limit,
            overrides=experiment.get("overrides", {}),
            preloaded_signal_klines=preloaded["signal_klines"],
            preloaded_benchmark_klines=preloaded["benchmark_klines"],
        )
        result["candidate"] = experiment.get("name", "unnamed_experiment")
        result["candidate_description"] = experiment.get("thesis", "")
        result["candidate_family"] = "experiment"
        result["candidate_tags"] = ["auto-generated", round_name]
        result["state_profile"] = round_name
        result["state_profile_description"] = f"Autogenerated batch {round_name}"
        result["state_style"] = "adaptive"
        result["archetype"] = f"experiment__{round_name}"
        result["experiment_name"] = experiment.get("name")
        result["experiment_thesis"] = experiment.get("thesis")
        result["experiment_overrides"] = experiment.get("overrides", {})
        results.append(result)

    ranking = rank_results(results)
    learning_summary = build_learning_summary(ranking)
    tuning_plan = build_tuning_plan(ranking, learning_summary)
    payload = {
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "signal_limit": signal_limit,
        "state_limit": state_limit,
        "round_name": round_name,
        "source": "next_experiments_batch",
        "experiments": experiments,
        "ranking": ranking,
        "lab_meta": build_lab_meta(ranking),
        "learning_summary": learning_summary,
        "tuning_plan": tuning_plan,
        "next_experiments": build_next_experiments(ranking, learning_summary),
    }
    payload["ranking_panel"] = format_lab_panel(payload)
    return payload


def run_round4(symbol: str, benchmark_symbol: str, signal_limit: int, state_limit: int) -> dict:
    base_payload = run_strategy_lab(symbol, benchmark_symbol, signal_limit, state_limit)
    experiments = base_payload.get("next_experiments", [])
    round4_payload = run_experiment_batch(
        symbol, benchmark_symbol, signal_limit, state_limit, experiments, "round4"
    )
    return {
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "signal_limit": signal_limit,
        "state_limit": state_limit,
        "base_round": base_payload,
        "round4": round4_payload,
    }


def load_experiments_file(path: str) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, list):
        experiments = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("experiments"), list):
            experiments = payload["experiments"]
        elif isinstance(payload.get("next_experiments"), list):
            experiments = payload["next_experiments"]
        else:
            raise ValueError(f"Unsupported experiments file format: {path}")
    else:
        raise ValueError(f"Unsupported experiments file format: {path}")

    normalized: list[dict] = []
    if not experiments:
        raise ValueError(f"No experiments found in file: {path}")

    for idx, item in enumerate(experiments, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Experiment #{idx} must be an object/dict")
        name = str(item.get("name", "")).strip() or f"experiment_{idx}"
        thesis = str(item.get("thesis", "")).strip() or f"Autogenerated thesis for {name}"
        overrides = item.get("overrides", {})
        if overrides is None:
            overrides = {}
        if not isinstance(overrides, dict):
            raise ValueError(f"Experiment '{name}' has non-dict overrides")
        normalized.append(
            {
                "name": name,
                "thesis": thesis,
                "overrides": overrides,
                "source": str(item.get("source", "custom")).strip() or "custom",
            }
        )

    return normalized


def run_custom_batch(
    symbol: str,
    benchmark_symbol: str,
    signal_limit: int,
    state_limit: int,
    experiments_file: str,
    batch_name: str,
) -> dict:
    experiments = load_experiments_file(experiments_file)
    return run_experiment_batch(
        symbol=symbol,
        benchmark_symbol=benchmark_symbol,
        signal_limit=signal_limit,
        state_limit=state_limit,
        experiments=experiments,
        round_name=batch_name,
    )


def build_lineage_entry(round_name: str, experiment: dict, result: dict, parent_round: str) -> dict:
    perf = result.get("performance", {})
    return {
        "round": round_name,
        "parent_round": parent_round,
        "name": experiment.get("name", result.get("candidate", "unnamed_experiment")),
        "thesis": experiment.get("thesis", result.get("candidate_description", "")),
        "overrides": experiment.get(
            "overrides", result.get("experiment_overrides", result.get("overrides", {}))
        ),
        "ending_equity_usdt": result.get("ending_equity_usdt"),
        "closed_trades": perf.get("closed_trades", 0),
        "win_rate_pct": perf.get("win_rate_pct", 0.0),
        "total_pnl_usdt": perf.get("total_pnl_usdt", 0.0),
        "archetype": result.get("archetype", "unknown"),
    }


def build_exploration_experiments(
    lineage: list[dict], base_round: dict, recent_rounds: list[dict], limit: int = 2
) -> list[dict]:
    seen_names = {item.get("name") for item in lineage}
    candidate_pool: list[dict] = []

    candidate_pool.append(
        {
            "name": "explore_breakout_pullback_s3_relief",
            "thesis": "Force exploration: allow breakout+pullback with moderate S3 participation and relaxed near-high constraint to test whether the engine is trapped in overly narrow breakout-only thinking.",
            "overrides": {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True, "lookback_bars": 18},
                    "pullback": {"enabled": True, "require_market_states": ["S1", "S2", "S3"]},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": True,
                "b_min_score": 4,
                "signal_params": {
                    "close_near_high_min": 0.7,
                    "momentum_positive_min_pct": 0.9,
                },
            },
        }
    )
    candidate_pool.append(
        {
            "name": "explore_breakout_strict_no_s3",
            "thesis": "Force exploration: test whether strict breakout logic works better when S3 is removed entirely rather than selectively filtered.",
            "overrides": {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": True, "lookback_bars": 26},
                    "pullback": {"enabled": False},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": False,
                "signal_params": {
                    "close_near_high_min": 0.8,
                    "momentum_positive_min_pct": 1.1,
                },
            },
        }
    )
    candidate_pool.append(
        {
            "name": "explore_pullback_constructive_only",
            "thesis": "Force exploration: isolate pullback behavior in constructive states without reclaim noise.",
            "overrides": {
                "setup_filters": {
                    "require_setup_for_buy": True,
                    "breakout": {"enabled": False},
                    "pullback": {"enabled": True, "require_market_states": ["S1", "S2"]},
                    "reclaim": {"enabled": False},
                },
                "allow_s3_entries": False,
                "b_min_score": 4,
            },
        }
    )

    experiments = []
    for item in candidate_pool:
        if item["name"] not in seen_names:
            experiments.append(item)
        if len(experiments) >= limit:
            break
    return experiments


def detect_stagnation(rounds: list[dict]) -> dict:
    if len(rounds) < 2:
        return {
            "stagnating": False,
            "reason": "not_enough_rounds",
            "repeat_winner": False,
            "non_improving": False,
        }

    recent = rounds[-2:]
    winners = [r.get("learning_summary", {}).get("winner") or {} for r in recent]
    winner_names = [w.get("candidate") for w in winners]
    repeat_winner = len(set(winner_names)) == 1 and all(winner_names)

    pnl_values = [float((w.get("total_pnl_usdt") if w else 0.0) or 0.0) for w in winners]
    equity_values = [float((w.get("ending_equity_usdt") if w else 0.0) or 0.0) for w in winners]
    non_improving = pnl_values[-1] <= pnl_values[0] and equity_values[-1] <= equity_values[0]

    stagnating = repeat_winner and non_improving
    reason = "repeat_winner_non_improving" if stagnating else "healthy_or_inconclusive"
    return {
        "stagnating": stagnating,
        "reason": reason,
        "repeat_winner": repeat_winner,
        "non_improving": non_improving,
        "winner_names": winner_names,
        "pnl_values": pnl_values,
        "equity_values": equity_values,
    }


def should_stop_auto_rounds(rounds: list[dict], max_stagnation_rounds: int = 2) -> dict:
    if not rounds:
        return {"stop": False, "reason": "no_rounds"}

    latest = rounds[-1]
    next_experiments = latest.get("next_experiments", [])
    if not next_experiments:
        return {"stop": True, "reason": "no_next_experiments"}

    latest_winner = latest.get("learning_summary", {}).get("winner") or {}
    latest_pnl = float(latest_winner.get("total_pnl_usdt", 0.0) or 0.0)
    latest_trades = int(latest_winner.get("closed_trades", 0) or 0)

    if len(rounds) >= max_stagnation_rounds:
        stagnation = detect_stagnation(rounds)
        if stagnation.get("stagnating"):
            return {"stop": True, "reason": stagnation.get("reason"), "diagnostics": stagnation}

    if len(rounds) >= 3:
        recent_winners = [r.get("learning_summary", {}).get("winner") or {} for r in rounds[-3:]]
        recent_pnls = [float(w.get("total_pnl_usdt", 0.0) or 0.0) for w in recent_winners]
        if (
            latest_trades > 0
            and all(p <= 0 for p in recent_pnls)
            and max(recent_pnls) - min(recent_pnls) < 0.05
        ):
            return {
                "stop": True,
                "reason": "flat_negative_loop",
                "diagnostics": {"recent_pnls": recent_pnls, "latest_trades": latest_trades},
            }

    if len(rounds) >= 2:
        latest_round = rounds[-1]
        if latest_round.get("exploration_injected"):
            return {"stop": False, "reason": "exploration_just_injected"}
        prev_round = rounds[-2]
        if prev_round.get("exploration_injected"):
            prev_winner = prev_round.get("learning_summary", {}).get("winner") or {}
            prev_pnl = float(prev_winner.get("total_pnl_usdt", 0.0) or 0.0)
            if latest_trades > 0 and latest_pnl <= prev_pnl:
                return {
                    "stop": True,
                    "reason": "post_exploration_regression",
                    "diagnostics": {"prev_pnl": prev_pnl, "latest_pnl": latest_pnl},
                }

    return {"stop": False, "reason": "continue"}


def run_auto_rounds(
    symbol: str, benchmark_symbol: str, signal_limit: int, state_limit: int, auto_rounds: int
) -> dict:
    auto_rounds = max(1, int(auto_rounds))
    base_round = run_strategy_lab(symbol, benchmark_symbol, signal_limit, state_limit)
    rounds: list[dict] = []
    lineage: list[dict] = []
    stop_event = {"stop": False, "reason": "not_triggered"}

    current_experiments = list(base_round.get("next_experiments", []))
    parent_round = "base_round"

    for idx in range(auto_rounds):
        round_name = f"round{idx + 4}"
        batch_payload = run_experiment_batch(
            symbol=symbol,
            benchmark_symbol=benchmark_symbol,
            signal_limit=signal_limit,
            state_limit=state_limit,
            experiments=current_experiments,
            round_name=round_name,
        )
        rounds.append(batch_payload)

        ranked_results = batch_payload.get("ranking", [])
        experiment_index = {exp.get("name"): exp for exp in batch_payload.get("experiments", [])}
        for result in ranked_results:
            exp = experiment_index.get(
                result.get("experiment_name"),
                {
                    "name": result.get("experiment_name"),
                    "thesis": result.get("experiment_thesis"),
                    "overrides": result.get("experiment_overrides", {}),
                },
            )
            lineage.append(build_lineage_entry(round_name, exp, result, parent_round))

        stop_check = should_stop_auto_rounds(rounds)
        if stop_check.get("stop"):
            if stop_check.get("reason") == "repeat_winner_non_improving":
                exploration = build_exploration_experiments(lineage, base_round, rounds, limit=2)
                if exploration:
                    batch_payload["exploration_injected"] = exploration
                    batch_payload["next_experiments"] = exploration
                    current_experiments = exploration
                    parent_round = round_name
                    continue
            stop_event = stop_check
            break

        next_experiments = batch_payload.get("next_experiments", [])
        current_experiments = next_experiments
        parent_round = round_name

    summary_winner = (
        rounds[-1].get("learning_summary", {}).get("winner")
        if rounds
        else base_round.get("learning_summary", {}).get("winner")
    )
    return {
        "symbol": symbol,
        "benchmark_symbol": benchmark_symbol,
        "signal_limit": signal_limit,
        "state_limit": state_limit,
        "auto_rounds_requested": auto_rounds,
        "base_round": base_round,
        "rounds": rounds,
        "lineage": lineage,
        "stop_event": stop_event,
        "final_winner": summary_winner,
    }


def format_auto_rounds_panel(payload: dict) -> str:
    lines = [
        "=== AUTO EVOLUTION SUMMARY ===",
        f"Symbol: {payload['symbol']} | Benchmark: {payload['benchmark_symbol']} | requestedRounds={payload.get('auto_rounds_requested', 0)} | completedRounds={len(payload.get('rounds', []))}",
    ]
    base_winner = payload.get("base_round", {}).get("learning_summary", {}).get("winner")
    if base_winner:
        lines.append(
            f"Base winner: {base_winner['candidate']} + {base_winner['state_profile']} | archetype={base_winner.get('archetype', 'unknown')} | equity={base_winner['ending_equity_usdt']} | pnl={base_winner['total_pnl_usdt']} USDT"
        )

    for batch in payload.get("rounds", []):
        winner = batch.get("learning_summary", {}).get("winner")
        round_name = batch.get("round_name", "unknown_round")
        meta = batch.get("lab_meta", {})
        if winner:
            lines.append(
                f"- {round_name}: winner={winner['candidate']} | equity={winner['ending_equity_usdt']} | pnl={winner['total_pnl_usdt']} USDT | trades={winner['closed_trades']} | zeroTrade={meta.get('zero_trade_profiles', 0)}"
            )
        else:
            lines.append(f"- {round_name}: no winner")
        if batch.get("exploration_injected"):
            lines.append(
                "  exploration injected: "
                + ", ".join(x["name"] for x in batch.get("exploration_injected", []))
            )

    stop_event = payload.get("stop_event", {})
    if stop_event:
        lines.append(f"Stop event: {stop_event.get('reason', 'none')}")

    lineage = payload.get("lineage", [])
    if lineage:
        lines.append("Lineage tail:")
        for item in lineage[-8:]:
            lines.append(
                f"- {item['round']} <= {item['parent_round']} | {item['name']} | pnl={item['total_pnl_usdt']} USDT | trades={item['closed_trades']}"
            )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strategy lab / research engine v1 for trading-system"
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--benchmark-symbol", default="BTCUSDT")
    parser.add_argument("--signal-limit", type=int, default=300)
    parser.add_argument("--state-limit", type=int, default=300)
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--round4",
        action="store_true",
        help="Run base strategy lab, then auto-execute next_experiments as round4 batch",
    )
    parser.add_argument(
        "--auto-rounds",
        type=int,
        default=0,
        help="Run multi-round auto evolution starting from next_experiments; e.g. 3 => round4..round6",
    )
    parser.add_argument(
        "--experiments-file",
        default="",
        help="Run a custom experiment batch from a JSON file (list, {experiments}, or {next_experiments})",
    )
    parser.add_argument(
        "--batch-name", default="custom_batch", help="Round/batch name used with --experiments-file"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    journal = Journal(ROOT)

    if args.experiments_file:
        payload = run_custom_batch(
            symbol=args.symbol.upper(),
            benchmark_symbol=args.benchmark_symbol.upper(),
            signal_limit=args.signal_limit,
            state_limit=args.state_limit,
            experiments_file=args.experiments_file,
            batch_name=args.batch_name,
        )
        print(f"=== {args.batch_name.upper()} CUSTOM EXPERIMENT BATCH ===")
        print(payload["ranking_panel"])
        journal.log(
            "strategy_lab_custom_batch_run",
            {
                "symbol": payload["symbol"],
                "benchmark_symbol": payload["benchmark_symbol"],
                "signal_limit": payload["signal_limit"],
                "state_limit": payload["state_limit"],
                "batch_name": payload.get("round_name"),
                "winner": payload.get("learning_summary", {}).get("winner"),
                "experiments": payload.get("experiments", []),
            },
        )
    elif args.auto_rounds and args.auto_rounds > 0:
        payload = run_auto_rounds(
            symbol=args.symbol.upper(),
            benchmark_symbol=args.benchmark_symbol.upper(),
            signal_limit=args.signal_limit,
            state_limit=args.state_limit,
            auto_rounds=args.auto_rounds,
        )
        print(payload["base_round"]["ranking_panel"])
        print()
        for batch in payload.get("rounds", []):
            print(f"=== {batch.get('round_name', 'AUTO ROUND').upper()} AUTO EXPERIMENT BATCH ===")
            print(batch["ranking_panel"])
            print()
        print(format_auto_rounds_panel(payload))
        journal.log(
            "strategy_lab_auto_rounds_run",
            {
                "symbol": payload["symbol"],
                "benchmark_symbol": payload["benchmark_symbol"],
                "signal_limit": payload["signal_limit"],
                "state_limit": payload["state_limit"],
                "auto_rounds_requested": payload.get("auto_rounds_requested", 0),
                "completed_rounds": len(payload.get("rounds", [])),
                "final_winner": payload.get("final_winner"),
                "lineage_tail": payload.get("lineage", [])[-10:],
            },
        )
    elif args.round4:
        payload = run_round4(
            symbol=args.symbol.upper(),
            benchmark_symbol=args.benchmark_symbol.upper(),
            signal_limit=args.signal_limit,
            state_limit=args.state_limit,
        )
        print(payload["base_round"]["ranking_panel"])
        print()
        print("=== ROUND4 AUTO EXPERIMENT BATCH ===")
        print(payload["round4"]["ranking_panel"])
        journal.log(
            "strategy_lab_round4_run",
            {
                "symbol": payload["symbol"],
                "benchmark_symbol": payload["benchmark_symbol"],
                "signal_limit": payload["signal_limit"],
                "state_limit": payload["state_limit"],
                "base_winner": payload.get("base_round", {})
                .get("learning_summary", {})
                .get("winner"),
                "round4_winner": payload.get("round4", {})
                .get("learning_summary", {})
                .get("winner"),
                "round4_experiments": payload.get("round4", {}).get("experiments", []),
            },
        )
    else:
        payload = run_strategy_lab(
            symbol=args.symbol.upper(),
            benchmark_symbol=args.benchmark_symbol.upper(),
            signal_limit=args.signal_limit,
            state_limit=args.state_limit,
        )
        print(payload["ranking_panel"])
        journal.log(
            "strategy_lab_run",
            {
                "symbol": payload["symbol"],
                "benchmark_symbol": payload["benchmark_symbol"],
                "signal_limit": payload["signal_limit"],
                "state_limit": payload["state_limit"],
                "lab_meta": payload.get("lab_meta", {}),
                "winner": payload.get("learning_summary", {}).get("winner"),
                "next_experiments": payload.get("next_experiments", []),
            },
        )

    artifact_base_dir = Path(args.out).parent if args.out else ROOT / "logs"
    artifact_paths = write_tuning_plan_artifacts(payload, artifact_base_dir)
    payload.setdefault("artifacts", {}).update(artifact_paths)

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"Saved lab results to {out_path}")

    print(f"Saved tuning artifacts to {artifact_paths['strategy_patch']}, {artifact_paths['risk_patch']}, {artifact_paths['tuning_plan']}")
    print(text)


if __name__ == "__main__":
    main()
