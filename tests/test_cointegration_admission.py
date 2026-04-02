import importlib
import os
import tempfile
import unittest
from unittest import mock


def _base_signal():
    return {
        "event": "Rates vs inflation",
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
        "tradeable": False,
        "paper_tradeable": False,
        "token_id_a": "tok-a",
        "token_id_b": "tok-b",
        "ev": {"ev_pct": 1.2},
        "sizing": {"recommended_size": 12.5, "kelly_fraction": 0.04},
        "filters": {
            "ev_pass": False,
            "kelly_pass": True,
            "z_pass": True,
            "coint_pass": True,
            "hl_pass": True,
            "momentum_pass": True,
            "price_pass": True,
            "spread_std_pass": True,
        },
    }


class CointegrationAdmissionMathTests(unittest.TestCase):
    def test_score_opportunity_uses_run_thresholds_in_diagnostics(self):
        import math_engine

        opp = {
            "event": "Test Event",
            "price_a": 0.44,
            "price_b": 0.53,
            "z_score": 1.6,
            "coint_pvalue": 0.07,
            "half_life": 5.0,
            "spread_std": 0.08,
            "spread_retreating": True,
        }

        scored = math_engine.score_opportunity(
            opp,
            min_z_abs=2.0,
            max_coint_pvalue=0.05,
            correlated_legs=True,
        )

        self.assertFalse(scored["filters"]["z_pass"])
        self.assertFalse(scored["filters"]["coint_pass"])
        self.assertEqual(scored["admission"]["primary_reason_code"], "cointegration_too_weak")
        self.assertIn("z_pass", scored["admission"]["failed_filters"])
        self.assertEqual(scored["admission"]["thresholds"]["min_z_abs"], 2.0)
        self.assertEqual(scored["admission"]["thresholds"]["max_coint_pvalue"], 0.05)


class CointegrationAdmissionDbTests(unittest.TestCase):
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

    def test_get_signals_can_hide_low_quality_rejections(self):
        ev_near_miss = _base_signal()
        ev_near_miss.update({
            "admission": {
                "status": "monitor",
                "accepted": False,
                "monitorable_signal": True,
                "ev_only_near_miss": True,
                "primary_reason_code": "ev_below_hurdle",
                "primary_reason": "EV 1.20% is below the hurdle 2.00%.",
                "failed_filters": ["ev_pass"],
                "failed_filter_count": 1,
                "primary_failed_filter": "ev_pass",
                "thresholds": {"min_ev_pct": 2.0},
                "observed": {"ev_pct": 1.2},
            },
            "experiment_reason_code": "trial_eligible",
            "experiment_reason": "Eligible for paper trial.",
        })
        self.db.save_signal(ev_near_miss)

        rejected = _base_signal()
        rejected.update({
            "grade_label": "B",
            "admission": {
                "status": "rejected",
                "accepted": False,
                "monitorable_signal": False,
                "ev_only_near_miss": False,
                "primary_reason_code": "spread_too_tight",
                "primary_reason": "Spread std 0.0100 is below the minimum 0.0200.",
                "failed_filters": ["spread_std_pass", "ev_pass"],
                "failed_filter_count": 2,
                "primary_failed_filter": "spread_std_pass",
                "thresholds": {"min_spread_std": 0.02},
                "observed": {"spread_std": 0.01},
            },
        })
        self.db.save_signal(rejected)

        visible = self.db.get_signals(limit=10, include_rejected=False)
        all_rows = self.db.get_signals(limit=10, include_rejected=True)

        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["admission_reason_code"], "trial_eligible")
        self.assertTrue(visible[0]["monitorable_signal"])
        self.assertEqual(len(all_rows), 2)
        self.assertEqual(all_rows[0]["failed_filters"], ["spread_std_pass", "ev_pass"])


class CointegrationScannerAdmissionTests(unittest.TestCase):
    def test_scan_skips_pairs_outside_operating_price_band(self):
        import scanner

        mock_events = [{
            "title": "Mock Event",
            "liquidity": 10000,
            "volume_24h": 500,
            "markets": [
                {"question": "A", "yes_token": "tok_a", "yes_price": 0.03, "end_date": "2099-01-01T00:00:00Z"},
                {"question": "B", "yes_token": "tok_b", "yes_price": 0.55, "end_date": "2099-01-01T00:00:00Z"},
            ],
        }]

        with mock.patch("scanner.find_multi_market_events", return_value=mock_events):
            stats = scanner.scan(include_stats=True, verbose=False)

        self.assertEqual(stats["pairs_tested"], 0)
        self.assertEqual(stats["raw_diverged_pairs"], 0)
        self.assertEqual(stats["skip_counts"]["price_outside_operating_band"], 1)


if __name__ == "__main__":
    unittest.main()
