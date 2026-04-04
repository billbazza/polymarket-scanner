import importlib
import os
import tempfile
import unittest
from unittest import mock


def _base_signal():
    return {
        "event": "Fed vs CPI spread",
        "market_a": "Fed cuts by June",
        "market_b": "CPI below 3%",
        "price_a": 0.42,
        "price_b": 0.61,
        "z_score": 1.92,
        "coint_pvalue": 0.03,
        "beta": 1.0,
        "half_life": 6.0,
        "spread_mean": 0.0,
        "spread_std": 0.08,
        "current_spread": 0.12,
        "liquidity": 25000,
        "volume_24h": 15000,
        "action": "SELL Fed cuts / BUY CPI below 3%",
        "grade_label": "A",
        "grade": 7,
        "tradeable": False,
        "paper_tradeable": False,
        "token_id_a": "tok-a",
        "token_id_b": "tok-b",
        "ev": {"ev_pct": 1.2},
        "sizing": {"recommended_size": 12.5},
        "filters": {
            "ev_pass": True,
            "kelly_pass": True,
            "z_pass": True,
            "coint_pass": True,
            "hl_pass": True,
            "momentum_pass": False,
            "price_pass": True,
            "spread_std_pass": True,
        },
    }


class CointegrationTrialLogicTests(unittest.TestCase):
    def test_a_grade_signal_can_enter_paper_trial(self):
        import cointegration_trial

        signal = _base_signal()
        with mock.patch.object(
            cointegration_trial.math_engine,
            "check_slippage",
            side_effect=[
                {"ok": True, "slippage_pct": 0.4, "reason": None},
                {"ok": True, "slippage_pct": 0.6, "reason": None},
            ],
        ):
            result = cointegration_trial.evaluate_signal(signal, mode="paper")

        self.assertTrue(result["admit_trade"])
        self.assertEqual(result["admission_path"], "paper_a_trial")
        self.assertEqual(result["experiment_status"], "eligible")
        self.assertEqual(result["recommended_size_usd"], 6.5)
        self.assertEqual(result["grade_weight"], 0.65)
        self.assertAlmostEqual(result["guardrails"]["grade_weight"], 0.65)
        self.assertAlmostEqual(result["guardrails"]["weighted_entry_size_usd"], 6.5)
        self.assertAlmostEqual(result["guardrails"]["reversion_exit_z"], 0.35)
        self.assertGreater(result["guardrails"]["stop_z_threshold"], abs(signal["z_score"]))

    def test_a_grade_signal_is_rejected_outside_paper_or_allowed_filters(self):
        import cointegration_trial

        live_result = cointegration_trial.evaluate_signal(_base_signal(), mode="live")
        self.assertFalse(live_result["admit_trade"])
        self.assertEqual(live_result["reason_code"], "paper_only")

        bad_signal = _base_signal()
        bad_signal["filters"]["momentum_pass"] = True
        bad_signal["filters"]["hl_pass"] = False
        with mock.patch.object(
            cointegration_trial.math_engine,
            "check_slippage",
            return_value={"ok": True, "slippage_pct": 0.2, "reason": None},
        ):
            result = cointegration_trial.evaluate_signal(bad_signal, mode="paper")
        self.assertFalse(result["admit_trade"])
        self.assertEqual(result["reason_code"], "filter_failure_outside_trial")

    def test_allowed_filter_failure_remains_eligible(self):
        import cointegration_trial

        signal = _base_signal()
        signal["filters"]["momentum_pass"] = True
        signal["filters"]["spread_std_pass"] = False
        with mock.patch.object(
            cointegration_trial.math_engine,
            "check_slippage",
            side_effect=[
                {"ok": True, "slippage_pct": 0.4, "reason": None},
                {"ok": True, "slippage_pct": 0.6, "reason": None},
            ],
        ):
            result = cointegration_trial.evaluate_signal(signal, mode="paper")

        self.assertTrue(result["admit_trade"])
        self.assertEqual(result["failed_filter_count"], 1)
        self.assertEqual(set(result["filters_failed"]), {"spread_std_pass"})

    def test_blocker_context_reports_disallowed_filters(self):
        import cointegration_trial

        signal = _base_signal()
        signal["filters"]["momentum_pass"] = True
        signal["filters"]["price_pass"] = False
        with mock.patch.object(
            cointegration_trial.math_engine,
            "check_slippage",
            return_value={"ok": True, "slippage_pct": 0.2, "reason": None},
        ):
            result = cointegration_trial.evaluate_signal(signal, mode="paper")

        self.assertFalse(result["admit_trade"])
        self.assertEqual(result["reason_code"], "filter_failure_outside_trial")
        self.assertEqual(result["blocker_context"]["type"], "filter")
        self.assertEqual(result["blocker_context"]["disallowed_filters"], ["price_pass"])


class CointegrationTrialSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")
        import db
        self.db = importlib.reload(db)
        self.db.init_db()

    def tearDown(self):
        self.tmpdir.cleanup()
        if self.old_db_path is None:
            os.environ.pop("SCANNER_DB_PATH", None)
        else:
            os.environ["SCANNER_DB_PATH"] = self.old_db_path
        import db
        importlib.reload(db)

    def test_summary_compares_a_trial_to_a_plus(self):
        a_plus = _base_signal()
        a_plus.update({
            "grade_label": "A+",
            "tradeable": True,
            "paper_tradeable": True,
            "filters": {k: True for k in a_plus["filters"]},
            "admission_path": "standard_a_plus",
            "experiment_name": "cointegration_a_grade_paper_trial",
            "experiment_status": "control",
        })
        a_plus_id = self.db.save_signal(a_plus)
        a_plus_trade = self.db.open_trade(
            a_plus_id,
            size_usd=20,
            metadata={
                "strategy_name": "cointegration",
                "entry_grade_label": "A+",
                "admission_path": "standard_a_plus",
                "experiment_name": "cointegration_a_grade_paper_trial",
                "experiment_status": "control",
            },
        )
        self.db.save_snapshot(a_plus_trade, 0.38, 0.56, -0.18, 0.3)
        self.db.update_pairs_trade_metrics(a_plus_trade, current_pnl=6.5, current_z_score=0.3)
        realized_pnl = self.db.close_trade(a_plus_trade, 0.38, 0.56, notes="Auto-closed: reverted")

        a_trial = _base_signal()
        a_trial.update({
            "admission_path": "paper_a_trial",
            "experiment_name": "cointegration_a_grade_paper_trial",
            "experiment_status": "eligible",
            "paper_tradeable": True,
            "experiment_reason_code": "trial_eligible",
            "experiment_reason": "Eligible for paper trial.",
            "experiment_guardrails": {
                "reversion_exit_z": 0.35,
                "stop_z_threshold": 2.67,
                "max_hold_hours": 36.0,
                "regime_break_threshold": 2.92,
            },
        })
        a_trial_id = self.db.save_signal(a_trial)
        a_trial_trade = self.db.open_trade(
            a_trial_id,
            size_usd=10,
            metadata={
                "strategy_name": "cointegration",
                "entry_grade_label": "A",
                "admission_path": "paper_a_trial",
                "experiment_name": "cointegration_a_grade_paper_trial",
                "experiment_status": "eligible",
                "guardrails": a_trial["experiment_guardrails"],
            },
        )
        self.db.save_snapshot(a_trial_trade, 0.46, 0.65, -0.19, 2.95)
        self.db.update_pairs_trade_metrics(
            a_trial_trade,
            current_pnl=-3.25,
            current_z_score=2.95,
            regime_break=True,
            regime_break_note="|z| reached 2.95 against threshold 2.92",
        )

        rejected = _base_signal()
        rejected.update({
            "liquidity": 5000,
            "admission_path": "a_grade_rejected",
            "experiment_name": "cointegration_a_grade_paper_trial",
            "experiment_status": "rejected",
            "experiment_reason_code": "liquidity_too_low",
            "experiment_reason": "Liquidity too low.",
        })
        self.db.save_signal(rejected)

        summary = self.db.get_cointegration_trial_summary()

        self.assertEqual(summary["signals_seen"]["a_plus"], 1)
        self.assertEqual(summary["signals_seen"]["a"], 2)
        self.assertEqual(summary["signals_seen"]["a_trial_eligible"], 1)
        self.assertEqual(summary["signals_seen"]["a_trial_rejected"], 1)
        self.assertEqual(summary["cohorts"]["a_plus"]["closed_trades"], 1)
        self.assertAlmostEqual(summary["cohorts"]["a_plus"]["realized_pnl"], round(realized_pnl, 2), places=2)
        self.assertEqual(summary["cohorts"]["a_trial"]["open_trades"], 1)
        self.assertAlmostEqual(summary["cohorts"]["a_trial"]["avg_mae_usd"], -3.25, places=2)
        self.assertEqual(summary["cohorts"]["a_trial"]["regime_break_trades"], 1)
        self.assertEqual(summary["rejection_reasons"][0]["reason_code"], "liquidity_too_low")


if __name__ == "__main__":
    unittest.main()
