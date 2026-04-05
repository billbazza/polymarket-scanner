import importlib
import json
import os
import tempfile
import unittest
from datetime import date
from unittest import mock


def _today_label():
    today = date.today()
    return f"{today:%B} {today.day}"


def _exact_temp_event():
    day_label = _today_label()
    return [{
        "title": f"Highest temperature in Shanghai on {day_label}?",
        "liquidity": 2400,
        "markets": [{
            "id": "shanghai-exact-1",
            "question": f"What will be the highest temperature in Shanghai on {day_label}?",
            "outcomes": json.dumps(["19C or lower", "20C", "21C or higher"]),
            "outcomePrices": json.dumps(["0.08", "0.12", "0.41"]),
            "clobTokenIds": json.dumps(["tok-low", "tok-mid", "tok-high"]),
        }],
    }]


def _exact_temp_opp():
    return {
        "event": "Highest temperature in Shanghai on April 2?",
        "market": "What will be the highest temperature in Shanghai on April 2? [21C or higher]",
        "strategy_name": "weather_exact_temp",
        "market_family": "weather_exact_temp",
        "market_id": "shanghai-exact-1:2",
        "yes_token": "tok-high",
        "no_token": None,
        "city": "shanghai",
        "lat": 31.1434,
        "lon": 121.8052,
        "target_date": date.today().isoformat(),
        "threshold_f": None,
        "direction": "exact",
        "resolution_source": "wunderground_history",
        "station_id": "ZSPD",
        "station_label": "Shanghai Pudong International Airport",
        "settlement_unit": "C",
        "settlement_precision": 1.0,
        "station_timezone": "Asia/Shanghai",
        "outcome_label": "21C or higher",
        "market_price": 0.41,
        "noaa_forecast_f": 71.6,
        "noaa_prob": 0.63,
        "noaa_sigma_f": 2.5,
        "om_forecast_f": 71.6,
        "om_prob": 0.61,
        "combined_prob": 0.62,
        "combined_edge": 0.21,
        "combined_edge_pct": 21.0,
        "selected_prob": 0.62,
        "selected_edge": 0.21,
        "selected_edge_pct": 21.0,
        "sources_agree": True,
        "sources_available": 2,
        "hours_ahead": 0,
        "ev_pct": 21.0,
        "kelly_fraction": 0.22,
        "action": "BUY_YES",
        "tradeable": True,
        "liquidity": 2400,
        "source_meta": {"strategy_name": "weather_exact_temp"},
    }


class WeatherExactTempTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        self.old_enabled = os.environ.get("WEATHER_EXACT_TEMP_ENABLED")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")

        import db
        import execution
        import weather_exact_temp_scanner
        import weather_strategy

        self.db = importlib.reload(db)
        self.db.init_db()
        self.execution = importlib.reload(execution)
        self.weather_exact_temp_scanner = importlib.reload(weather_exact_temp_scanner)
        self.weather_strategy = importlib.reload(weather_strategy)

    def tearDown(self):
        self.tmpdir.cleanup()
        if self.old_db_path is None:
            os.environ.pop("SCANNER_DB_PATH", None)
        else:
            os.environ["SCANNER_DB_PATH"] = self.old_db_path
        if self.old_enabled is None:
            os.environ.pop("WEATHER_EXACT_TEMP_ENABLED", None)
        else:
            os.environ["WEATHER_EXACT_TEMP_ENABLED"] = self.old_enabled

        import db
        import execution
        import weather_exact_temp_scanner
        import weather_strategy

        importlib.reload(db)
        importlib.reload(execution)
        importlib.reload(weather_exact_temp_scanner)
        importlib.reload(weather_strategy)

    def test_exact_temp_scan_is_disabled_by_default(self):
        os.environ.pop("WEATHER_EXACT_TEMP_ENABLED", None)
        scanner = importlib.reload(self.weather_exact_temp_scanner)

        opportunities, meta = scanner.scan(verbose=False)

        self.assertEqual(opportunities, [])
        self.assertFalse(meta["enabled"])

    @mock.patch("weather_exact_temp_scanner.weather_sources.fetch_threshold_forecasts")
    @mock.patch("weather_exact_temp_scanner.api.get_events")
    def test_exact_temp_scan_emits_station_metadata_when_enabled(self, mock_get_events, mock_fetch_forecasts):
        os.environ["WEATHER_EXACT_TEMP_ENABLED"] = "1"
        scanner = importlib.reload(self.weather_exact_temp_scanner)
        mock_get_events.return_value = _exact_temp_event()
        mock_fetch_forecasts.return_value = [
            {"source_id": "noaa", "source_name": "NOAA NWS", "value_f": 71.6},
            {"source_id": "open-meteo", "source_name": "Open-Meteo", "value_f": 71.6},
        ]

        opportunities, meta = scanner.scan(verbose=False)

        self.assertTrue(meta["enabled"])
        self.assertGreaterEqual(len(opportunities), 1)
        best = opportunities[0]
        self.assertEqual(best["strategy_name"], "weather_exact_temp")
        self.assertEqual(best["station_id"], "ZSPD")
        self.assertEqual(best["settlement_unit"], "C")
        self.assertEqual(best["outcome_label"], "21C or higher")
        self.assertTrue(best["tradeable"])
        self.assertEqual(best["action"], "BUY_YES")

    def test_weather_strategy_runner_keeps_exact_temp_opt_in(self):
        with mock.patch("weather_strategy.weather_scanner.scan", return_value=([{"market": "threshold", "tradeable": False, "combined_edge": 0.1}], {"markets_checked": 1, "weather_found": 1, "fetch_errors": {}})), \
             mock.patch("weather_strategy.weather_exact_temp_scanner.scan", return_value=([{"market": "exact", "tradeable": True, "combined_edge": 0.2}], {"enabled": True, "markets_checked": 1, "tradeable": 1})):
            opportunities, meta = self.weather_strategy.scan_weather_opportunities(verbose=False, include_exact_temp=False)
            self.assertEqual(len(opportunities), 1)
            self.assertFalse(meta["exact_temp_enabled"])

            opportunities, meta = self.weather_strategy.scan_weather_opportunities(verbose=False, include_exact_temp=True)
            self.assertEqual(len(opportunities), 2)
            self.assertTrue(meta["exact_temp_enabled"])

    def test_execute_weather_trade_keeps_exact_temp_live_rollout_blocked(self):
        signal_id = self.db.save_weather_signal(_exact_temp_opp())
        signal = self.db.get_weather_signal_by_id(signal_id)

        blocked = self.execution.execute_weather_trade(signal, size_usd=20, mode="live")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason_code"], "exact_temp_paper_only")

        opened = self.execution.execute_weather_trade(signal, size_usd=20, mode="paper")
        self.assertTrue(opened["ok"])
        trade = self.db.get_trade(opened["trade_id"])
        self.assertEqual(trade["trade_type"], "weather")
        self.assertEqual(trade["strategy_name"], "weather_exact_temp")


if __name__ == "__main__":
    unittest.main()
