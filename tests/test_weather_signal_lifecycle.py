import importlib
import os
import tempfile
import unittest


def _weather_opp(city="Atlanta", market="Will Atlanta hit 82F?", yes_token="yes-atl", no_token="no-atl"):
    return {
        "event": f"Highest temperature in {city} on April 3?",
        "market": market,
        "market_id": f"market-{yes_token}",
        "yes_token": yes_token,
        "no_token": no_token,
        "city": city.lower(),
        "lat": 33.7490,
        "lon": -84.3880,
        "target_date": "2026-04-03",
        "threshold_f": 82.0,
        "direction": "above",
        "market_price": 0.41,
        "noaa_forecast_f": 84.0,
        "noaa_prob": 0.66,
        "noaa_sigma_f": 3.5,
        "om_forecast_f": 83.0,
        "om_prob": 0.64,
        "combined_prob": 0.65,
        "combined_edge": 0.24,
        "combined_edge_pct": 24.0,
        "sources_agree": True,
        "sources_available": 2,
        "hours_ahead": 48,
        "ev_pct": 8.5,
        "kelly_fraction": 0.12,
        "action": "BUY_YES",
        "tradeable": True,
        "liquidity": 1000,
    }


class WeatherSignalLifecycleTests(unittest.TestCase):
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

    def test_duplicate_weather_signal_is_blocked_but_not_marked_open(self):
        original_signal_id = self.db.save_weather_signal(_weather_opp())
        original_trade_id = self.db.open_weather_trade(original_signal_id, size_usd=20)
        self.assertIsNotNone(original_trade_id)

        duplicate_signal_id = self.db.save_weather_signal(
            _weather_opp(market="Will Atlanta hit 82F again?", yes_token="yes-atl", no_token="no-atl")
        )

        rows = {
            row["id"]: row
            for row in self.db.get_weather_signals(limit=None)
            if row["id"] in {original_signal_id, duplicate_signal_id}
        }

        original = rows[original_signal_id]
        duplicate = rows[duplicate_signal_id]

        self.assertEqual(original["open_trade_id"], original_trade_id)
        self.assertTrue(original["has_open_trade"])
        self.assertEqual(original["status"], "open")

        self.assertIsNone(duplicate["open_trade_id"])
        self.assertFalse(duplicate["has_open_trade"])
        self.assertEqual(duplicate["blocked_by_trade_id"], original_trade_id)
        self.assertEqual(duplicate["blocking_reason_code"], "token_already_open")
        self.assertEqual(duplicate["status"], "blocked")

    def test_closed_weather_trade_updates_signal_lifecycle_and_blocks_reopen(self):
        signal_id = self.db.save_weather_signal(_weather_opp(city="Denver", market="Will Denver hit 62F?", yes_token="yes-den", no_token="no-den"))
        trade_id = self.db.open_weather_trade(signal_id, size_usd=20)
        self.assertIsNotNone(trade_id)

        pnl = self.db.close_trade(trade_id, exit_price_a=1.0, notes="Auto-closed: resolved (WIN)")
        self.assertIsNotNone(pnl)

        signal = self.db.get_weather_signal_by_id(signal_id)
        self.assertEqual(signal["status"], "closed")

        row = next(row for row in self.db.get_weather_signals(limit=None) if row["id"] == signal_id)
        self.assertEqual(row["status"], "closed")
        self.assertIsNone(row["open_trade_id"])
        self.assertEqual(row["latest_trade_status"], "closed")
        self.assertIn("resolved", row["status_detail"])

        decision = self.db.inspect_weather_trade_open(signal_id, size_usd=20)
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason_code"], "signal_already_closed")
        self.assertEqual(decision["existing_trade_id"], trade_id)


if __name__ == "__main__":
    unittest.main()
