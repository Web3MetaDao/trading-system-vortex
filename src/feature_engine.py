from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BreakoutQualityResult:
    blocked: bool
    bonus: int = 0
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


class FeatureEngine:
    """External-feature adapter layer.

    This module is intentionally lightweight for phase 1 integration:
    - accepts precomputed feature payloads from context
    - evaluates breakout quality / false-start risk
    - returns structured reasons so backtest + realtime can reuse the same gate

    It does not fetch third-party data by itself yet.
    """

    def assess_breakout_quality(
        self, features: dict | None, config: dict | None
    ) -> BreakoutQualityResult:
        features = features or {}
        config = config or {}
        if not config.get("enabled", False):
            return BreakoutQualityResult(
                blocked=False, metrics={"enabled": False, "present": bool(features)}
            )

        bonus = 0
        reasons: list[str] = []
        blockers: list[str] = []

        cvd_state = str(features.get("cvd_divergence", "unknown")).lower()
        oi_change_pct = self._to_float(features.get("oi_change_pct"))
        funding_state = str(features.get("funding_regime", "unknown")).lower()
        vwap_distance_sigma = self._to_float(features.get("vwap_distance_sigma"))
        heatmap_bias = str(features.get("liquidity_heatmap_bias", "unknown")).lower()
        obi_ratio = self._to_float(features.get("order_book_imbalance_ratio"))
        tape_state = str(features.get("tape_aggression", "unknown")).lower()

        if config.get("block_on_bearish_cvd", True) and cvd_state == "bearish":
            blockers.append("bearish_cvd_divergence")

        min_oi_change_pct = self._to_float(
            config.get("min_oi_change_pct_for_breakout_confirmation"), default=0.0
        )
        if oi_change_pct is not None:
            if oi_change_pct >= min_oi_change_pct:
                bonus += int(config.get("oi_confirmation_bonus", 1))
                reasons.append(f"oi expansion confirms breakout ({oi_change_pct:.2f}%)")
            elif config.get("block_on_negative_oi", False) and oi_change_pct < 0:
                blockers.append("negative_oi_change")

        if config.get("block_on_overheated_funding", True) and funding_state in {
            "overheated",
            "crowded_long",
        }:
            blockers.append("overheated_funding")
        elif funding_state in {"supportive", "healthy"}:
            bonus += int(config.get("funding_support_bonus", 1))
            reasons.append(f"funding regime supportive ({funding_state})")

        max_vwap_extension_sigma = self._to_float(
            config.get("max_vwap_extension_sigma"), default=2.5
        )
        if vwap_distance_sigma is not None:
            if vwap_distance_sigma > max_vwap_extension_sigma:
                blockers.append(f"vwap_extension_too_far({vwap_distance_sigma:.2f}σ)")
            elif vwap_distance_sigma >= 0:
                reasons.append(f"vwap extension acceptable ({vwap_distance_sigma:.2f}σ)")

        if config.get("block_on_overhead_liquidity", False) and heatmap_bias in {
            "overhead_liquidity",
            "sell_wall",
        }:
            blockers.append("overhead_liquidity_pressure")
        elif heatmap_bias in {"upside_magnet", "supportive"}:
            bonus += int(config.get("heatmap_support_bonus", 1))
            reasons.append(f"liquidity map supportive ({heatmap_bias})")

        min_obi_ratio = self._to_float(config.get("min_obi_ratio_for_bonus"), default=1.2)
        if obi_ratio is not None:
            if obi_ratio >= min_obi_ratio:
                bonus += int(config.get("obi_bonus", 1))
                reasons.append(f"order book imbalance supports breakout ({obi_ratio:.2f})")
            elif config.get("block_on_weak_obi", False) and obi_ratio < 1.0:
                blockers.append("weak_order_book_imbalance")

        if tape_state in {"aggressive_buy", "buy_sweep"}:
            bonus += int(config.get("tape_bonus", 1))
            reasons.append(f"tape confirms aggressive buy flow ({tape_state})")
        elif config.get("block_on_sell_tape", False) and tape_state in {
            "aggressive_sell",
            "sell_sweep",
        }:
            blockers.append("aggressive_sell_tape")

        return BreakoutQualityResult(
            blocked=bool(blockers),
            bonus=bonus,
            reasons=reasons,
            blockers=blockers,
            metrics={
                "enabled": True,
                "feature_count": len(features),
                "cvd_divergence": cvd_state,
                "oi_change_pct": oi_change_pct,
                "funding_regime": funding_state,
                "vwap_distance_sigma": vwap_distance_sigma,
                "liquidity_heatmap_bias": heatmap_bias,
                "order_book_imbalance_ratio": obi_ratio,
                "tape_aggression": tape_state,
            },
        )

    @staticmethod
    def _to_float(value, default: float | None = None) -> float | None:
        if value in (None, ""):
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
