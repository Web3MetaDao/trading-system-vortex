import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import strategy_lab  # noqa: E402


class StrategyLabSmokeTests(unittest.TestCase):
    def test_extract_result_identity_preserves_explicit_fields(self):
        result = {
            "candidate": "ema_exit_sensitivity_probe",
            "candidate_family": "experiment",
            "candidate_tags": ["auto-generated", "round4"],
            "state_profile": "round4",
            "state_style": "adaptive",
            "archetype": "experiment__round4",
        }
        identity = strategy_lab.extract_result_identity(result)
        self.assertEqual(identity["candidate_family"], "experiment")
        self.assertEqual(identity["state_style"], "adaptive")
        self.assertEqual(identity["archetype"], "experiment__round4")

    def test_load_experiments_file_normalizes_entries(self):
        tmp = ROOT / "logs" / "test_experiments_input.json"
        tmp.write_text(json.dumps([{"name": "x", "overrides": {"a": 1}}]), encoding="utf-8")
        try:
            experiments = strategy_lab.load_experiments_file(str(tmp))
            self.assertEqual(len(experiments), 1)
            self.assertEqual(experiments[0]["name"], "x")
            self.assertIsInstance(experiments[0]["overrides"], dict)
            self.assertIn("thesis", experiments[0])
            self.assertIn("source", experiments[0])
        finally:
            if tmp.exists():
                tmp.unlink()

    def test_build_next_experiments_respects_source_caps(self):
        results = [
            {
                "candidate": "breakout_only_strict",
                "state_profile": "s3_selective",
                "candidate_family": "breakout",
                "candidate_tags": ["trend"],
                "state_style": "balanced",
                "archetype": "breakout__balanced",
                "overrides": {},
                "ending_equity_usdt": 99.5,
                "performance": {
                    "closed_trades": 2,
                    "win_rate_pct": 0.0,
                    "total_pnl_usdt": -0.3,
                    "avg_pnl_pct": -1.0,
                },
                "analysis": {
                    "by_setup": {"breakout": {"total_pnl_usdt": -0.3}},
                    "worst_group_hints": [
                        {"source": "market_state", "key": "S3"},
                        {"source": "exit_reason", "key": "price lost EMA20"},
                        {"source": "signal_bucket", "key": "A_like(score>=5)"},
                    ],
                },
            }
        ]
        ranked = strategy_lab.rank_results(results)
        summary = strategy_lab.build_learning_summary(ranked)
        experiments = strategy_lab.build_next_experiments(ranked, summary)
        self.assertLessEqual(len(experiments), 9)
        counts = {}
        for item in experiments:
            counts[item.get("source")] = counts.get(item.get("source"), 0) + 1
        self.assertLessEqual(counts.get("template", 0), 3)
        self.assertLessEqual(counts.get("attribution", 0), 3)
        self.assertLessEqual(counts.get("objective", 0), 2)

    def test_build_tuning_plan_emits_patch_and_validation_steps(self):
        results = [
            {
                "candidate": "breakout_only_strict",
                "state_profile": "s3_selective",
                "candidate_family": "breakout",
                "candidate_tags": ["trend"],
                "state_style": "balanced",
                "archetype": "breakout__balanced",
                "overrides": {
                    "b_min_score": 5,
                    "allow_s3_entries": True,
                    "ema_exit_period": 34,
                    "signal_params": {"close_near_high_min": 0.84},
                    "setup_filters": {"reclaim": {"enabled": False}},
                },
                "ending_equity_usdt": 101.2,
                "performance": {
                    "closed_trades": 4,
                    "win_rate_pct": 50.0,
                    "total_pnl_usdt": 1.2,
                    "avg_pnl_pct": 0.5,
                },
                "analysis": {"by_setup": {"breakout": {"total_pnl_usdt": 1.2}}},
            }
        ]
        ranked = strategy_lab.rank_results(results)
        summary = strategy_lab.build_learning_summary(ranked)
        plan = strategy_lab.build_tuning_plan(ranked, summary)
        self.assertEqual(plan["winner_baseline"]["candidate"], "breakout_only_strict")
        self.assertEqual(
            plan["recommended_patch"]["strategy"]["signal_levels"]["B"]["min_score"], 5
        )
        self.assertTrue(plan["recommended_patch"]["strategy"]["backtest"]["allow_s3_entries"])
        self.assertEqual(plan["recommended_patch"]["risk"]["ema_exit_period"], 34)
        self.assertGreaterEqual(len(plan["validation_checks"]), 3)

    def test_write_tuning_plan_artifacts_writes_files(self):
        out_dir = ROOT / "logs" / "test_tuning_artifacts"
        if out_dir.exists():
            for child in out_dir.iterdir():
                child.unlink()
            out_dir.rmdir()
        payload = {
            "tuning_plan": {
                "recommended_patch": {
                    "strategy": {"signal_levels": {"B": {"min_score": 5}}},
                    "risk": {"ema_exit_period": 34},
                },
                "winner_baseline": {"candidate": "x", "state_profile": "y"},
            }
        }
        paths = strategy_lab.write_tuning_plan_artifacts(payload, out_dir)
        try:
            self.assertTrue(Path(paths["strategy_patch"]).exists())
            self.assertTrue(Path(paths["risk_patch"]).exists())
            self.assertTrue(Path(paths["tuning_plan"]).exists())
            strategy_data = json.loads(Path(paths["strategy_patch"]).read_text(encoding="utf-8"))
            risk_data = json.loads(Path(paths["risk_patch"]).read_text(encoding="utf-8"))
            self.assertEqual(strategy_data["signal_levels"]["B"]["min_score"], 5)
            self.assertEqual(risk_data["ema_exit_period"], 34)
        finally:
            if out_dir.exists():
                for child in out_dir.iterdir():
                    child.unlink()
                out_dir.rmdir()

    def test_build_next_experiments_uses_blocked_reason_relief(self):
        results = [
            {
                "candidate": "full_stack_setups",
                "state_profile": "s3_very_selective",
                "candidate_family": "hybrid",
                "candidate_tags": ["trend", "pullback"],
                "state_style": "tight",
                "archetype": "hybrid__tight",
                "overrides": {"b_min_score": 5},
                "ending_equity_usdt": 99.9,
                "performance": {
                    "closed_trades": 0,
                    "win_rate_pct": 0.0,
                    "total_pnl_usdt": 0.0,
                    "avg_pnl_pct": 0.0,
                },
                "analysis": {
                    "by_setup": {},
                    "blocked": {
                        "by_reason": {
                            "missing_required_setup": {"count": 30, "avg_score": 1.2},
                            "score_below_B_threshold": {"count": 18, "avg_score": 2.1},
                        }
                    },
                },
            }
        ]
        ranked = strategy_lab.rank_results(results)
        summary = strategy_lab.build_learning_summary(ranked)
        experiments = strategy_lab.build_next_experiments(ranked, summary)
        names = {item["name"] for item in experiments}
        self.assertIn("setup_requirement_relief_probe", names)
        self.assertIn("score_threshold_relief_probe", names)

    def test_build_next_experiments_prioritizes_blocked_reason_when_zero_trade_dominates(self):
        results = [
            {
                "candidate": "full_stack_setups",
                "state_profile": "s3_very_selective",
                "candidate_family": "hybrid",
                "candidate_tags": ["trend", "pullback"],
                "state_style": "tight",
                "archetype": "hybrid__tight",
                "overrides": {"b_min_score": 5},
                "ending_equity_usdt": 99.9,
                "performance": {
                    "closed_trades": 0,
                    "win_rate_pct": 0.0,
                    "total_pnl_usdt": 0.0,
                    "avg_pnl_pct": 0.0,
                },
                "analysis": {
                    "by_setup": {},
                    "blocked": {
                        "by_reason": {
                            "missing_required_setup": {"count": 30, "avg_score": 1.2},
                            "score_below_B_threshold": {"count": 18, "avg_score": 2.1},
                            "backtest_s3_disabled": {"count": 12, "avg_score": 2.4},
                        }
                    },
                },
            }
            for _ in range(4)
        ]
        ranked = strategy_lab.rank_results(results)
        summary = strategy_lab.build_learning_summary(ranked)
        experiments = strategy_lab.build_next_experiments(ranked, summary)
        self.assertGreaterEqual(len(experiments), 3)
        self.assertEqual(experiments[0]["source"], "blocked_reason")


if __name__ == "__main__":
    unittest.main()
