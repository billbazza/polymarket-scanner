import importlib
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient


def _base_signal():
    return {
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
        "liquidity": 18000,
        "volume_24h": 9000,
        "action": "SELL Fed cuts / BUY CPI below 3%",
        "grade_label": "A+",
        "tradeable": True,
        "paper_tradeable": True,
        "token_id_a": "tok-a",
        "token_id_b": "tok-b",
        "ev": {"ev_pct": 2.1},
        "sizing": {"recommended_size": 20.0},
        "filters": {"ev_pass": True},
    }


def _weather_opp():
    return {
        "event": "Highest temperature in Atlanta on April 3?",
        "market": "Will Atlanta hit 82F?",
        "market_id": "market-atl",
        "yes_token": "yes-atl",
        "no_token": "no-atl",
        "city": "atlanta",
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


class StrategyPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        self.old_api_key = os.environ.get("SCANNER_API_KEY")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")
        os.environ["SCANNER_API_KEY"] = "test-admin-key"

        import db
        import server

        self.db = importlib.reload(db)
        self.db.init_db()
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
        import server

        importlib.reload(db)
        importlib.reload(server)

    def _seed_mixed_strategy_trades(self):
        self.db.set_paper_starting_bankroll(500)

        signal_id = self.db.save_signal(_base_signal())
        coin_trade_id = self.db.open_trade(signal_id, size_usd=100)
        self.assertIsNotNone(coin_trade_id)
        coin_pnl = self.db.close_trade(coin_trade_id, exit_price_a=0.40, exit_price_b=0.60, notes="Closed winner")

        weather_signal_id = self.db.save_weather_signal(_weather_opp())
        weather_trade_id = self.db.open_weather_trade(weather_signal_id, size_usd=40)
        self.assertIsNotNone(weather_trade_id)
        self.db.save_snapshot(weather_trade_id, 0.55, None, None, None)

        whale_trade_id = self.db.open_whale_trade({
            "trade_type": "whale",
            "opened_at": time.time(),
            "side_a": "BUY_YES",
            "side_b": "",
            "entry_price_a": 0.40,
            "entry_price_b": 0.0,
            "token_id_a": "whale-token",
            "size_usd": 60,
            "status": "open",
            "whale_alert_id": 7,
            "event": "Large account moved size",
            "market_a": "Will policy pass?",
            "notes": "Whale test trade",
        })
        self.assertIsNotNone(whale_trade_id)
        self.db.save_snapshot(whale_trade_id, 0.35, None, None, None)

        copy_trade_id = self.db.open_copy_trade(
            "0xabc1230000000000000000000000000000000001",
            "Wallet Alpha",
            {
                "conditionId": "cond-1",
                "outcome": "YES",
                "title": "Shared market",
                "curPrice": 0.61,
                "asset": "copy-asset-1",
            },
            size_usd=50,
        )
        self.assertIsNotNone(copy_trade_id)
        copy_pnl = self.db.close_trade(copy_trade_id, exit_price_a=0.50, notes="Closed loser")

        return {
            "coin_pnl": round(coin_pnl, 2),
            "copy_pnl": round(copy_pnl, 2),
        }

    def test_strategy_performance_breaks_out_realized_unrealized_and_capital_usage(self):
        pnls = self._seed_mixed_strategy_trades()

        summary = self.db.get_strategy_performance(refresh_unrealized=False)
        strategies = {row["strategy"]: row for row in summary["strategies"]}

        self.assertEqual(summary["starting_bankroll"], 500.0)
        self.assertEqual(summary["total_committed_capital"], 100.0)
        self.assertAlmostEqual(summary["total_realized_pnl"], pnls["coin_pnl"] + pnls["copy_pnl"], places=2)

        coin = strategies["cointegration"]
        self.assertEqual(coin["closed_trades"], 1)
        self.assertEqual(coin["wins"], 1)
        self.assertEqual(coin["losses"], 0)
        self.assertEqual(coin["open_trades"], 0)
        self.assertAlmostEqual(coin["realized_pnl"], pnls["coin_pnl"], places=2)
        self.assertEqual(coin["win_rate"], 100.0)
        self.assertEqual(coin["committed_capital"], 0.0)

        weather = strategies["weather"]
        self.assertEqual(weather["open_trades"], 1)
        self.assertEqual(weather["closed_trades"], 0)
        self.assertAlmostEqual(weather["unrealized_pnl"], 13.66, places=2)
        self.assertEqual(weather["committed_capital"], 40.0)
        self.assertEqual(weather["bankroll_utilization_pct"], 8.0)

        whale = strategies["whale"]
        self.assertEqual(whale["open_trades"], 1)
        self.assertAlmostEqual(whale["unrealized_pnl"], -7.5, places=2)
        self.assertEqual(whale["committed_capital"], 60.0)
        self.assertEqual(whale["bankroll_utilization_pct"], 12.0)

        copy = strategies["copy"]
        self.assertEqual(copy["closed_trades"], 1)
        self.assertEqual(copy["wins"], 0)
        self.assertEqual(copy["losses"], 1)
        self.assertAlmostEqual(copy["realized_pnl"], pnls["copy_pnl"], places=2)
        self.assertEqual(copy["win_rate"], 0.0)
        self.assertEqual(copy["committed_capital"], 0.0)

    def test_stats_api_exposes_strategy_breakdown_for_dashboard(self):
        signal_id = self.db.save_signal(_base_signal())
        trade_id = self.db.open_trade(signal_id, size_usd=25)
        self.assertIsNotNone(trade_id)
        self.db.close_trade(trade_id, exit_price_a=0.40, exit_price_b=0.60, notes="Closed winner")

        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        body = response.json()

        self.assertIn("strategy_breakdown", body)
        self.assertIn("strategy_breakdown", body["paper_account"])
        strategies = {row["strategy"]: row for row in body["strategy_breakdown"]["strategies"]}
        self.assertIn("cointegration", strategies)
        self.assertEqual(strategies["cointegration"]["closed_trades"], 1)

        paper_account_response = self.client.get("/api/paper-account")
        self.assertEqual(paper_account_response.status_code, 200)
        paper_account = paper_account_response.json()
        self.assertIn("strategy_breakdown", paper_account)
        self.assertEqual(paper_account["strategy_breakdown"]["strategies"][0]["strategy"], "cointegration")


if __name__ == "__main__":
    unittest.main()
