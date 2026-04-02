import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


def _cointegration_signal():
    return {
        "id": 11,
        "event": "Rates vs CPI",
        "market_a": "Fed cuts by June",
        "market_b": "CPI below 3%",
        "price_a": 0.44,
        "price_b": 0.58,
        "z_score": 1.8,
        "coint_pvalue": 0.04,
        "beta": 1.0,
        "half_life": 7.0,
        "spread_mean": 0.0,
        "spread_std": 0.07,
        "current_spread": 0.11,
        "liquidity": 28000,
        "volume_24h": 9000,
        "action": "SELL Fed cuts / BUY CPI below 3%",
        "grade_label": "A+",
        "tradeable": True,
        "paper_tradeable": True,
        "token_id_a": "tok-a",
        "token_id_b": "tok-b",
        "ev": {"ev_pct": 4.2},
        "sizing": {"recommended_size": 26.0, "kelly_fraction": 0.11},
        "filters": {"ev_pass": True},
        "admission_path": "standard_a_plus",
    }


def _weather_signal():
    return {
        "id": 21,
        "event": "Highest temperature in Atlanta on April 3?",
        "market": "Will Atlanta hit 82F?",
        "combined_edge_pct": 27.0,
        "kelly_fraction": 0.14,
        "sources_agree": True,
        "sources_available": 2,
        "liquidity": 1600,
        "action": "BUY_YES",
    }


class PaperSizingTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        self.old_api_key = os.environ.get("SCANNER_API_KEY")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")
        os.environ["SCANNER_API_KEY"] = "test-admin-key"

        import db
        import paper_sizing
        import server

        self.db = importlib.reload(db)
        self.db.init_db()
        self.paper_sizing = importlib.reload(paper_sizing)
        self.server = importlib.reload(server)
        self.client = TestClient(self.server.app)

    def tearDown(self):
        self.tmpdir.cleanup()
        if self.old_db_path is None:
            os.environ.pop("SCANNER_DB_PATH", None)
        else:
            os.environ["SCANNER_DB_PATH"] = self.old_db_path
        if self.old_api_key is None:
            os.environ.pop("SCANNER_API_KEY", None)
        else:
            os.environ["SCANNER_API_KEY"] = self.old_api_key

        import db
        import paper_sizing
        import server

        importlib.reload(db)
        importlib.reload(paper_sizing)
        importlib.reload(server)

    def test_shadow_mode_keeps_fixed_size_but_records_confidence_recommendation(self):
        self.db.set_paper_starting_bankroll(2000)

        decision = self.paper_sizing.build_paper_sizing_decision(
            "cointegration",
            _cointegration_signal(),
            baseline_size_usd=20,
            mode="paper",
            source="autonomy",
            signal_id=11,
        )

        self.assertTrue(decision["compare_only"])
        self.assertEqual(decision["selected_policy"], "fixed")
        self.assertEqual(decision["selected_size_usd"], 20.0)
        self.assertGreater(decision["confidence_size_usd"], 20.0)
        self.assertEqual(decision["review_note_path"], "reviews/2026-04-02-paper-sizing-rollout-review.md")

        row_id = self.paper_sizing.record_sizing_decision(decision)
        self.assertGreater(row_id, 0)

        summary = self.db.get_paper_sizing_summary(limit=10)
        self.assertEqual(summary["recent_count"], 1)
        self.assertEqual(summary["shadow_decisions"], 1)
        self.assertEqual(summary["applied_decisions"], 0)
        self.assertEqual(summary["strategies"][0]["strategy"], "cointegration")
        self.assertEqual(summary["strategies"][0]["avg_selected_size_usd"], 20.0)

    def test_live_mode_rolls_back_to_fixed_and_api_exposes_recent_sizing(self):
        self.db.set_paper_starting_bankroll(500)
        self.paper_sizing.set_sizing_settings({
            "rollout_state": "active",
            "active_policy": "confidence_aware",
        })

        live_decision = self.paper_sizing.build_paper_sizing_decision(
            "weather",
            _weather_signal(),
            baseline_size_usd=20,
            mode="live",
            source="autonomy",
            weather_signal_id=21,
        )
        self.assertEqual(live_decision["selected_policy"], "fixed")
        self.assertFalse(live_decision["applied"])

        paper_decision = self.paper_sizing.build_paper_sizing_decision(
            "weather",
            _weather_signal(),
            baseline_size_usd=20,
            mode="paper",
            source="autonomy",
            weather_signal_id=21,
        )
        self.assertEqual(paper_decision["selected_policy"], "confidence_aware")
        self.assertTrue(paper_decision["applied"])
        self.paper_sizing.record_sizing_decision(paper_decision)

        response = self.client.get("/api/paper-sizing?limit=5")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertEqual(body["settings"]["active_policy"], "confidence_aware")
        self.assertEqual(body["summary"]["recent_count"], 1)
        self.assertEqual(body["summary"]["applied_decisions"], 1)
        self.assertEqual(body["decisions"][0]["strategy"], "weather")


if __name__ == "__main__":
    unittest.main()
