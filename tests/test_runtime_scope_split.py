import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _signal():
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


class RuntimeScopeSplitTests(unittest.TestCase):
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

    def _seed_scoped_pairs_trades(self):
        signal_id = self.db.save_signal(_signal())
        paper_trade_id = self.db.open_trade(
            signal_id,
            size_usd=25,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PAPER},
        )
        penny_trade_id = self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )
        return signal_id, paper_trade_id, penny_trade_id

    def _weather_signal(self, yes_token: str, no_token: str, market: str) -> dict:
        return {
            "event": f"{market} event",
            "market": market,
            "strategy_name": "weather_threshold",
            "market_family": "weather_threshold",
            "market_id": market.lower().replace(" ", "-"),
            "yes_token": yes_token,
            "no_token": no_token,
            "city": "new york",
            "lat": 40.7128,
            "lon": -74.0060,
            "target_date": "2026-04-06",
            "threshold_f": 80.0,
            "direction": "above",
            "resolution_source": "wunderground_history",
            "station_id": "KNYC",
            "station_label": "New York City",
            "settlement_unit": "F",
            "settlement_precision": 1.0,
            "station_timezone": "America/New_York",
            "outcome_label": "80F or higher",
            "market_price": 0.35,
            "hours_ahead": 72,
            "timestamp": 1710000000,
            "liquidity": 8000,
            "ev_pct": 8.0,
            "kelly_fraction": 0.08,
            "action": "BUY_YES",
            "tradeable": True,
            "noaa_forecast_f": 81.0,
            "noaa_prob": 0.52,
            "noaa_sigma_f": 2.1,
            "om_forecast_f": 80.0,
            "om_prob": 0.54,
            "combined_prob": 0.53,
            "combined_edge": 0.18,
            "combined_edge_pct": 18.0,
            "selected_prob": 0.53,
            "selected_edge": 0.18,
            "selected_edge_pct": 18.0,
            "sources_agree": True,
            "sources_available": 2,
        }

    def test_scoped_trade_open_checks_and_accounting_are_isolated(self):
        signal_id, paper_trade_id, penny_trade_id = self._seed_scoped_pairs_trades()

        self.assertIsNotNone(paper_trade_id)
        self.assertIsNotNone(penny_trade_id)
        self.assertNotEqual(paper_trade_id, penny_trade_id)

        paper_decision = self.db.inspect_pairs_trade_open(
            signal_id,
            size_usd=10,
            runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
        )
        penny_decision = self.db.inspect_pairs_trade_open(
            signal_id,
            size_usd=1,
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )
        self.assertFalse(paper_decision["ok"])
        self.assertEqual(paper_decision["reason_code"], "signal_already_open")
        self.assertFalse(penny_decision["ok"])
        self.assertEqual(penny_decision["reason_code"], "signal_already_open")

        self.assertEqual(self.db.count_open_trades(runtime_scope=self.db.RUNTIME_SCOPE_PAPER), 1)
        self.assertEqual(self.db.count_open_trades(runtime_scope=self.db.RUNTIME_SCOPE_PENNY), 1)
        self.assertEqual(self.db.count_open_trades(), 2)

        paper_account = self.db.get_paper_account_state(runtime_scope=self.db.RUNTIME_SCOPE_PAPER)
        penny_account = self.db.get_paper_account_state(runtime_scope=self.db.RUNTIME_SCOPE_PENNY)
        self.assertEqual(paper_account["open_trades"], 1)
        self.assertEqual(paper_account["committed_capital"], 25.0)
        self.assertEqual(paper_account["runtime_scope"], self.db.RUNTIME_SCOPE_PAPER)
        self.assertEqual(penny_account["open_trades"], 1)
        self.assertEqual(penny_account["committed_capital"], 3.0)
        self.assertEqual(penny_account["runtime_scope"], self.db.RUNTIME_SCOPE_PENNY)

    def test_stats_and_trades_api_are_runtime_scoped(self):
        _, paper_trade_id, penny_trade_id = self._seed_scoped_pairs_trades()

        paper_trades = self.client.get("/api/trades?status=open&runtime_scope=paper").json()
        penny_trades = self.client.get("/api/trades?status=open&runtime_scope=penny").json()
        self.assertEqual([row["id"] for row in paper_trades], [paper_trade_id])
        self.assertEqual([row["id"] for row in penny_trades], [penny_trade_id])

        with patch.object(self.db, "_get_live_wallet_snapshot", return_value={
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "wallet_connected": True,
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "available_balance_usd": 17.25,
            "balance_source": "polygon_wallet",
            "wallet_error": None,
            "verification_error": None,
            "block_number": 123,
            "block_hash": "0xabc",
            "block_timestamp": 1710000000,
            "block_age_seconds": 12,
            "max_block_age_seconds": 180,
            "chain_id": 137,
            "expected_chain_id": 137,
            "chain_parity_ok": True,
            "verified_at": 1710000012,
        }):
            paper_stats = self.client.get("/api/stats?runtime_scope=paper").json()
            penny_stats = self.client.get("/api/stats?runtime_scope=penny").json()
            penny_account = self.client.get("/api/runtime/account?runtime_scope=penny").json()
        self.assertEqual(paper_stats["runtime_scope"], "paper")
        self.assertEqual(paper_stats["open_trades"], 1)
        self.assertEqual(paper_stats["runtime_account"]["runtime_scope"], "paper")
        self.assertEqual(paper_stats["runtime_account"]["account_mode"], "paper_bankroll")
        self.assertEqual(penny_stats["runtime_scope"], "penny")
        self.assertEqual(penny_stats["open_trades"], 1)
        self.assertEqual(penny_stats["runtime_account"]["runtime_scope"], "penny")
        self.assertEqual(penny_stats["runtime_account"]["account_mode"], "live_wallet")
        self.assertTrue(penny_stats["runtime_account"]["verified_live_ledger"])
        self.assertEqual(penny_stats["runtime_account"]["deployed_capital_usd"], 3.0)
        self.assertEqual(penny_stats["runtime_account"]["available_balance_usd"], 17.25)
        self.assertTrue(penny_stats["acceptance_checks"]["all_passed"])
        self.assertEqual(penny_stats["trade_reconciliation"]["runtime_scope"], "penny")
        self.assertEqual(penny_stats["trade_reconciliation"]["total_trades"], 1)
        self.assertEqual(penny_stats["trade_reconciliation"]["committed_capital"], 3.0)
        self.assertEqual(
            penny_stats["runtime_account"]["trade_reconciliation"]["committed_capital"],
            3.0,
        )
        self.assertEqual(penny_account["account_mode"], "live_wallet")
        self.assertTrue(penny_account["verified_live_ledger"])
        self.assertEqual(penny_account["available_balance_usd"], 17.25)
        self.assertEqual(penny_account["trade_reconciliation"]["total_trades"], 1)
        self.assertNotIn("paper_account", penny_account)

    def test_penny_runtime_account_fails_closed_without_verified_live_wallet(self):
        self._seed_scoped_pairs_trades()

        with patch.object(self.db, "_get_live_wallet_snapshot", return_value={
            "ok": False,
            "verified": False,
            "verification_status": "stale",
            "wallet_connected": False,
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "available_balance_usd": 0.0,
            "balance_source": "polygon_wallet",
            "wallet_error": "Latest Polygon block is stale",
            "verification_error": "Latest Polygon block is stale",
            "block_number": 123,
            "block_hash": "0xabc",
            "block_timestamp": 1710000000,
            "block_age_seconds": 999,
            "max_block_age_seconds": 180,
            "chain_id": 137,
            "expected_chain_id": 137,
            "chain_parity_ok": True,
            "verified_at": 1710000999,
        }):
            response = self.client.get("/api/runtime/account?runtime_scope=penny")
            payload = response.json()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["account_mode"], "live_wallet")
        self.assertFalse(payload["verified_live_ledger"])
        self.assertEqual(payload["verification_status"], "stale")
        self.assertIn("blocked", payload["message"].lower())
        self.assertIsNone(payload["available_balance_usd"])

    def test_penny_closed_trade_stats_reconcile_only_to_penny_history(self):
        signal_id = self.db.save_signal(_signal())
        paper_trade_id = self.db.open_trade(
            signal_id,
            size_usd=25,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PAPER},
        )
        penny_trade_id = self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )
        self.db.close_trade(paper_trade_id, 0.55, 0.45, "paper close")
        self.db.close_trade(penny_trade_id, 0.60, 0.40, "penny close")

        with patch.object(self.db, "_get_live_wallet_snapshot", return_value={
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "wallet_connected": True,
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "available_balance_usd": 12.0,
            "balance_source": "polygon_wallet",
            "wallet_error": None,
            "verification_error": None,
        }):
            penny_stats = self.client.get("/api/stats?runtime_scope=penny").json()
            penny_account = self.client.get("/api/runtime/account?runtime_scope=penny").json()

        self.assertEqual(penny_stats["closed_trades"], 1)
        self.assertEqual(penny_stats["trade_reconciliation"]["closed_trades"], 1)
        self.assertEqual(penny_stats["trade_reconciliation"]["total_trades"], 1)
        self.assertEqual(
            penny_stats["total_pnl"],
            penny_stats["trade_reconciliation"]["total_pnl"],
        )
        self.assertEqual(
            penny_account["realized_pnl_usd"],
            penny_stats["trade_reconciliation"]["realized_pnl"],
        )

    def test_penny_strategy_breakdown_reports_live_exposure_from_penny_ledger(self):
        signal_id = self.db.save_signal(_signal())
        self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )

        with patch.object(self.db, "_get_live_wallet_snapshot", return_value={
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "wallet_connected": True,
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "available_balance_usd": 11.0,
            "balance_source": "polygon_wallet",
            "wallet_error": None,
            "verification_error": None,
        }):
            penny_stats = self.client.get("/api/stats?runtime_scope=penny").json()

        strategies = {row["strategy"]: row for row in penny_stats["strategy_breakdown"]["strategies"]}
        coin = strategies["cointegration"]
        self.assertEqual(coin["open_trades"], 1)
        self.assertEqual(coin["committed_capital"], 0.0)
        self.assertEqual(coin["external_capital"], 3.0)
        self.assertEqual(coin["reporting_capital"], 3.0)
        self.assertEqual(coin["reporting_capital_basis"], "external_capital")
        self.assertEqual(penny_stats["strategy_breakdown"]["total_reporting_capital"], 3.0)
        self.assertEqual(penny_stats["runtime_account"]["deployed_capital_usd"], 3.0)
        self.assertTrue(penny_stats["acceptance_checks"]["all_passed"])

    def test_penny_scope_excludes_paper_state_rows_from_live_ledger_views(self):
        signal_id = self.db.save_signal(_signal())
        stray_trade_id = self.db.open_trade(
            signal_id,
            size_usd=9,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_PAPER,
                "reconciliation_mode": self.db.RECONCILIATION_INTERNAL,
            },
        )
        live_trade_id = self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )

        self.assertIsNotNone(stray_trade_id)
        self.assertIsNotNone(live_trade_id)

        with patch.object(self.db, "_get_live_wallet_snapshot", return_value={
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "wallet_connected": True,
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "available_balance_usd": 20.0,
            "balance_source": "polygon_wallet",
            "wallet_error": None,
            "verification_error": None,
            "block_number": 123,
            "block_hash": "0xabc",
            "block_timestamp": 1710000000,
            "block_age_seconds": 12,
            "max_block_age_seconds": 180,
            "chain_id": 137,
            "expected_chain_id": 137,
            "chain_parity_ok": True,
            "verified_at": 1710000012,
        }):
            penny_trades = self.client.get("/api/trades?status=open&runtime_scope=penny").json()
            penny_stats = self.client.get("/api/stats?runtime_scope=penny").json()
            penny_account = self.client.get("/api/runtime/account?runtime_scope=penny").json()
            penny_runtime = self.client.get("/api/autonomy/runtime?runtime_scope=penny").json()

        self.assertEqual([row["id"] for row in penny_trades], [live_trade_id])
        self.assertEqual(penny_stats["open_trades"], 1)
        self.assertEqual(penny_stats["total_trades"], 1)
        self.assertEqual(penny_stats["trade_reconciliation"]["excluded_non_ledger_trades"], 1)
        self.assertEqual(penny_account["open_positions"], 1)
        self.assertEqual(penny_account["deployed_capital_usd"], 3.0)
        self.assertEqual(penny_account["trade_reconciliation"]["excluded_non_ledger_trades"], 1)
        self.assertEqual(penny_runtime["open_positions"], 1)
        self.assertEqual(penny_runtime["max_open_usage"], "1/3")

    def test_closed_trade_history_api_is_runtime_scoped(self):
        signal_id = self.db.save_signal(_signal())
        paper_trade_id = self.db.open_trade(
            signal_id,
            size_usd=25,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PAPER},
        )
        penny_trade_id = self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )
        self.db.close_trade(paper_trade_id, 0.55, 0.45, "paper close")
        self.db.close_trade(penny_trade_id, 0.60, 0.40, "penny close")

        paper_history = self.client.get("/api/trades?status=closed&runtime_scope=paper").json()
        penny_history = self.client.get("/api/trades?status=closed&runtime_scope=penny").json()
        self.assertEqual([row["id"] for row in paper_history], [paper_trade_id])
        self.assertEqual([row["id"] for row in penny_history], [penny_trade_id])
        self.assertEqual(paper_history[0]["runtime_scope"], "paper")
        self.assertEqual(penny_history[0]["runtime_scope"], "penny")

    def test_autonomy_state_is_split_per_runtime_scope(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        tmp_path = Path(self.tmpdir.name)
        with patch.object(autonomy, "STATE_DIR", tmp_path / "logs"), \
             patch.object(autonomy, "STATE_FILE", tmp_path / "logs" / "autonomy_state.json"), \
             patch.object(autonomy, "LEGACY_STATE_FILE", tmp_path / "autonomy_state.json"):
            paper_state = autonomy.default_state("paper")
            penny_state = autonomy.default_state("penny")
            paper_state["pnl_at_level"] = 12.5
            penny_state["pnl_at_level"] = -1.25

            autonomy.save_state(paper_state, runtime_scope="paper")
            autonomy.save_state(penny_state, runtime_scope="penny")

            self.assertTrue((tmp_path / "logs" / "autonomy_state.paper.json").exists())
            self.assertTrue((tmp_path / "logs" / "autonomy_state.penny.json").exists())
            self.assertEqual(autonomy.load_state("paper")["pnl_at_level"], 12.5)
            self.assertEqual(autonomy.load_state("penny")["pnl_at_level"], -1.25)

            legacy_payload = autonomy.default_state("penny")
            legacy_payload["level"] = "penny"
            (tmp_path / "logs" / "autonomy_state.penny.json").unlink()
            (tmp_path / "logs" / "autonomy_state.json").write_text(json.dumps(legacy_payload))
            migrated = autonomy.load_state("penny")
            self.assertEqual(migrated["runtime_scope"], "penny")
            self.assertTrue((tmp_path / "logs" / "autonomy_state.penny.json").exists())

    def test_autonomy_background_scopes_require_explicit_configuration_for_concurrency(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        self.assertEqual(autonomy.background_runtime_scopes(None), ["paper"])
        self.assertEqual(autonomy.background_runtime_scopes("penny"), ["penny"])
        self.assertEqual(autonomy.background_runtime_scopes("paper,penny"), ["paper", "penny"])
        self.assertEqual(autonomy.background_runtime_scopes("disabled"), [])

    def test_autonomy_journal_labels_runtime_scope_and_runtime_label(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        captured = []
        with patch.object(autonomy.journal_writer, "append_entry", side_effect=lambda entry: captured.append(entry) or entry):
            autonomy.journal({"action": "unit_test", "level": "penny", "reason": "check scope defaults"})

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["runtime_scope"], "penny")
        self.assertEqual(captured[0]["runtime_label"], "autonomy:penny")

    def test_penny_runtime_scans_weather_but_keeps_live_autotrading_explicit(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        state = autonomy.default_state("penny")
        state["level"] = "penny"

        journal_entries = []
        scanned_weather = []
        fake_weather_strategy = types.SimpleNamespace(
            scan_weather_opportunities=lambda **kwargs: (
                scanned_weather.append(kwargs) or ([{
                    "event": "London temperature",
                    "market": "Will London hit 70F?",
                    "strategy_name": "weather_threshold",
                    "market_family": "weather_threshold",
                    "yes_token": "weather-yes",
                    "no_token": "weather-no",
                    "market_price": 0.42,
                    "combined_edge_pct": 9.0,
                    "hours_ahead": 72,
                    "timestamp": 1710000000,
                    "liquidity": 9000,
                    "action": "BUY_YES",
                    "tradeable": True,
                }], {"markets_checked": 1})
            )
        )
        fake_weather_exact = types.SimpleNamespace(
            exact_temp_enabled=lambda: False,
            exact_temp_autotrade_enabled=lambda: False,
        )
        fake_copy_scanner = types.SimpleNamespace(
            get_positions=lambda address: (_ for _ in ()).throw(
                AssertionError("copy trader should be skipped for penny runtime")
            )
        )
        fake_wallet_discovery = types.SimpleNamespace(
            run_discovery=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("wallet discovery should be skipped for penny runtime")
            )
        )
        fake_longshot = types.SimpleNamespace(scan=lambda **kwargs: ([], {"markets_checked": 0}))
        fake_near_certainty = types.SimpleNamespace(scan=lambda **kwargs: ([], {"markets_checked": 0}))
        fake_whale = types.SimpleNamespace(scan=lambda **kwargs: ([], {"markets_checked": 0}))
        sizing_decision = {
            "selected_size_usd": 3.0,
            "selected_policy": "baseline",
            "rollout_state": "inactive",
            "applied": False,
            "activation_status": {"blocker_codes": [], "blockers": [], "can_apply_confidence": False},
            "compare_only": False,
            "confidence_size_usd": None,
        }

        with patch.object(autonomy.async_scanner, "scan", return_value={
            "opportunities": [],
            "pairs_tested": 0,
            "pairs_cointegrated": 0,
        }), \
             patch.object(autonomy, "get_level_config", return_value={
                 "name": "Penny Trader",
                 "can_trade": True,
                 "auto_trade_enabled": True,
                 "max_open": 3,
                 "size_usd": 3,
                 "runtime_controls": {
                     "auto_trade_enabled": True,
                     "weather_auto_trade_enabled": False,
                 },
             }), \
             patch.object(autonomy.db, "save_scan_run"), \
             patch.object(autonomy.tracker, "refresh_open_trades", return_value=[]), \
             patch.object(autonomy.execution, "manage_open_orders", return_value={"filled": 0, "cancelled": 0}), \
             patch.object(autonomy.trade_monitor, "reconcile_open_trades", return_value={"counts": {}, "results": [], "auto_closed_trade_ids": []}), \
             patch.object(autonomy.tracker, "auto_close_trades", return_value=[]), \
             patch.object(autonomy.db, "get_trades", return_value=[]), \
             patch.object(autonomy.db, "get_runtime_account_overview", return_value={"available_balance": 100.0}), \
             patch.object(autonomy.db, "save_weather_signal", return_value=17), \
             patch.object(autonomy.db, "inspect_weather_trade_open", return_value={"ok": True, "entry_token": "weather-yes", "entry_price": 0.42, "action": "BUY_YES"}), \
             patch.object(autonomy.paper_sizing, "build_paper_sizing_decision", return_value=sizing_decision), \
             patch.object(autonomy.paper_sizing, "record_sizing_decision"), \
             patch.object(autonomy.execution, "execute_weather_trade", return_value={"ok": True, "trade_id": 33}), \
             patch.object(autonomy, "save_state"), \
             patch.object(autonomy, "journal", side_effect=lambda entry: journal_entries.append(entry)), \
             patch.dict(sys.modules, {
                 "weather_strategy": fake_weather_strategy,
                 "weather_exact_temp_scanner": fake_weather_exact,
                 "copy_scanner": fake_copy_scanner,
                 "wallet_discovery": fake_wallet_discovery,
                 "longshot_scanner": fake_longshot,
                 "near_certainty_scanner": fake_near_certainty,
                 "whale_detector": fake_whale,
             }, clear=False):
            result = autonomy.run_cycle(state)

        skipped = [entry for entry in journal_entries if entry.get("action") == "paper_only_step_skipped"]
        self.assertEqual(
            {entry.get("strategy") for entry in skipped},
            {"copy", "wallet_discovery"},
        )
        self.assertTrue(all(entry.get("runtime_scope") == "penny" for entry in skipped))
        self.assertEqual(len(scanned_weather), 1)
        scan_only_entries = [entry for entry in journal_entries if entry.get("action") == "weather_scan_only"]
        self.assertEqual(len(scan_only_entries), 0)
        weather_phase = (result or {}).get("cycle_summary", {}).get("weather_phase", {})
        self.assertEqual(weather_phase.get("status"), "completed")
        self.assertEqual(weather_phase.get("execution_mode"), "live-auto-trade")
        self.assertIsNone(weather_phase.get("reason_code"))
        self.assertEqual(weather_phase.get("result_counts", {}).get("tradeable"), 1)
        self.assertEqual(weather_phase.get("result_counts", {}).get("saved"), 1)
        self.assertEqual(weather_phase.get("result_counts", {}).get("traded"), 1)
        self.assertEqual(weather_phase.get("result_counts", {}).get("live_vetoed"), 0)

    def test_autonomy_background_status_includes_weather_phase_summary(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        with patch.object(self.server.db, "get_stats", side_effect=[
            {"total_signals": 5, "open_trades": 2, "closed_trades": 7},
            {"total_signals": 8, "open_trades": 3, "closed_trades": 9},
        ]), \
             patch.object(autonomy, "load_state", return_value={"level": "penny", "runtime_scope": "penny"}), \
             patch.object(autonomy, "run_cycle", return_value={
                 "state": {"level": "penny", "runtime_scope": "penny"},
                "cycle_summary": {
                    "pairs_phase": {
                        "status": "blocked",
                        "reason_code": "max_open_reached",
                        "reason": "No cointegration slots remain for scope=penny.",
                        "trade_execution_status": "slots_full",
                    },
                    "weather_phase": {
                        "status": "completed",
                        "reason_code": None,
                        "reason": None,
                        "duration_secs": 0.1,
                         "execution_mode": "live-auto-trade",
                         "result_counts": {"opportunities": 1, "tradeable": 1, "traded": 1},
                     },
                     "phases": [{"name": "weather_scan", "status": "completed"}],
                 },
             }):
            self.server._run_autonomy_background("penny")

        last_result = self.server._autonomy_status["penny"]["last_result"]
        self.assertTrue(last_result["ok"])
        self.assertEqual(last_result["execution_mode"], "background")
        self.assertTrue(last_result["all_enabled_phases_completed"])
        self.assertEqual(last_result["signals_found"], 3)
        self.assertEqual(last_result["trades_opened"], 1)
        self.assertEqual(last_result["trades_closed"], 2)
        self.assertEqual(last_result["pairs_phase"]["reason_code"], "max_open_reached")
        self.assertEqual(last_result["pairs_phase"]["trade_execution_status"], "slots_full")
        self.assertEqual(last_result["weather_phase"]["status"], "completed")
        self.assertIsNone(last_result["weather_phase"]["reason_code"])
        self.assertEqual(last_result["weather_phase"]["execution_mode"], "live-auto-trade")

    def test_penny_a_grade_trial_is_not_filtered_by_perplexity_metadata(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        journal_entries = []
        a_grade_signal = _signal()
        a_grade_signal.update({
            "event": "A-grade parity candidate",
            "grade_label": "A",
            "grade": 7,
            "tradeable": False,
            "paper_tradeable": False,
            "ev": {"ev_pct": 1.2},
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
        })
        fake_weather_strategy = types.SimpleNamespace(scan_weather_opportunities=lambda **kwargs: ([], {}))
        fake_weather_exact = types.SimpleNamespace(
            exact_temp_enabled=lambda: False,
            exact_temp_autotrade_enabled=lambda: False,
        )
        fake_copy_scanner = types.SimpleNamespace(run_copy_trader=lambda **kwargs: {"executed": 0})
        fake_wallet_discovery = types.SimpleNamespace(run=lambda **kwargs: {"found": 0})
        fake_longshot = types.SimpleNamespace(scan=lambda **kwargs: ([], {"markets_checked": 0}))
        fake_near_certainty = types.SimpleNamespace(scan=lambda **kwargs: ([], {"markets_checked": 0}))
        fake_whale = types.SimpleNamespace(scan=lambda **kwargs: ([], {"markets_checked": 0}))
        fake_brain = types.SimpleNamespace(validate_signal=lambda opp: (True, "brain ok"))

        with patch.object(autonomy.async_scanner, "scan", return_value={
            "opportunities": [a_grade_signal],
            "pairs_tested": 1,
            "pairs_cointegrated": 1,
        }), \
             patch.object(autonomy, "get_level_config", return_value={
                 "name": "Penny Trader",
                 "can_trade": True,
                 "auto_trade_enabled": True,
                 "max_open": 3,
                 "size_usd": 3,
                 "runtime_controls": {
                     "auto_trade_enabled": True,
                     "weather_auto_trade_enabled": True,
                 },
             }), \
             patch.object(autonomy.db, "save_scan_run"), \
             patch.object(autonomy.tracker, "refresh_open_trades", return_value=[]), \
             patch.object(autonomy.execution, "manage_open_orders", return_value={"filled": 0, "cancelled": 0}), \
             patch.object(autonomy.trade_monitor, "reconcile_open_trades", return_value={"counts": {}, "results": [], "auto_closed_trade_ids": []}), \
             patch.object(autonomy.tracker, "auto_close_trades", return_value=[]), \
             patch.object(autonomy.db, "get_runtime_slot_usage", return_value={
                 "open_positions": 0,
                 "slots_remaining": 3,
                 "max_open_usage": "0/3",
                 "consuming_trade_ids": [],
                 "consuming_trades": [],
             }), \
             patch.object(autonomy.cointegration_trial.math_engine, "check_slippage", side_effect=[
                 {"ok": True, "slippage_pct": 0.4, "reason": None},
                 {"ok": True, "slippage_pct": 0.5, "reason": None},
             ]), \
             patch.object(autonomy.perplexity, "annotate_profitable_candidate", side_effect=lambda opp: opp.update({
                 "perplexity": {"status": "complete", "profitable_candidate": False, "reason": "observability only", "confidence": 0.2},
                 "profitable_candidate_feature": False,
                 "profitable_candidate_reason": "observability only",
             })), \
             patch.object(autonomy.execution, "execute_trade", return_value={
                 "ok": False,
                 "reason_code": "balance_check_failed",
                 "error": "Balance check failed: test live veto",
             }) as execute_trade_mock, \
             patch.object(autonomy, "save_state"), \
             patch.object(autonomy, "journal", side_effect=lambda entry: journal_entries.append(entry)), \
             patch.dict(sys.modules, {
                 "weather_strategy": fake_weather_strategy,
                 "weather_exact_temp_scanner": fake_weather_exact,
                 "copy_scanner": fake_copy_scanner,
                 "wallet_discovery": fake_wallet_discovery,
                 "longshot_scanner": fake_longshot,
                 "near_certainty_scanner": fake_near_certainty,
                 "whale_detector": fake_whale,
                 "brain": fake_brain,
             }, clear=False):
            result = autonomy.run_cycle({"level": "penny", "runtime_scope": "penny"})

        self.assertEqual(execute_trade_mock.call_count, 1)
        self.assertEqual(result["cycle_summary"]["pairs_phase"]["result_counts"]["admitted"], 1)
        self.assertTrue(any(entry.get("action") == "perplexity_observability" for entry in journal_entries))
        self.assertFalse(any(entry.get("action") == "stage3_perplexity_gate" for entry in journal_entries))

    def test_autonomy_runtime_api_returns_scoped_state_and_limits(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        tmp_path = Path(self.tmpdir.name)
        with patch.object(autonomy, "STATE_DIR", tmp_path / "logs"), \
             patch.object(autonomy, "STATE_FILE", tmp_path / "logs" / "autonomy_state.json"), \
             patch.object(autonomy, "LEGACY_STATE_FILE", tmp_path / "autonomy_state.json"), \
             patch.object(self.server, "_autonomy_status", {
                 self.db.RUNTIME_SCOPE_PAPER: {"running": False, "last_result": {"ok": True, "runtime_scope": "paper", "trades_opened": 2, "trades_closed": 1, "duration_secs": 10.5}},
                 self.db.RUNTIME_SCOPE_PENNY: {"running": True, "last_result": {"ok": True, "runtime_scope": "penny", "trades_opened": 1, "trades_closed": 0, "duration_secs": 4.2}},
             }):
            paper_state = autonomy.default_state("paper")
            penny_state = autonomy.default_state("penny")
            penny_state["level"] = "penny"
            autonomy.save_state(paper_state, runtime_scope="paper")
            autonomy.save_state(penny_state, runtime_scope="penny")

            paper_runtime = self.client.get("/api/autonomy/runtime?runtime_scope=paper").json()
            penny_runtime = self.client.get("/api/autonomy/runtime?runtime_scope=penny").json()

        self.assertEqual(paper_runtime["runtime_scope"], "paper")
        self.assertEqual(paper_runtime["state"]["runtime_scope"], "paper")
        self.assertEqual(paper_runtime["state"]["level"], "paper")
        self.assertEqual(paper_runtime["max_open"], None)
        self.assertEqual(paper_runtime["max_open_label"], "No hard cap (cash-limited)")
        self.assertTrue(paper_runtime["state_file"].endswith("autonomy_state.paper.json"))

        self.assertEqual(penny_runtime["runtime_scope"], "penny")
        self.assertEqual(penny_runtime["state"]["runtime_scope"], "penny")
        self.assertEqual(penny_runtime["state"]["level"], "penny")
        self.assertEqual(penny_runtime["level_config"]["max_open"], 3)
        self.assertEqual(penny_runtime["max_open"], 3)
        self.assertEqual(penny_runtime["max_open_label"], "3")
        self.assertEqual(penny_runtime["open_positions"], 0)
        self.assertEqual(penny_runtime["max_open_usage"], "0/3")
        self.assertEqual(penny_runtime["slots_remaining"], 3)
        self.assertEqual(penny_runtime["slot_usage"]["open_positions"], 0)
        self.assertEqual(penny_runtime["slot_usage"]["consuming_trades"], [])
        self.assertEqual(penny_runtime["slot_limit_state"]["status"], "available")
        self.assertEqual(penny_runtime["slot_limit_state"]["active_max_open"], 3)
        self.assertEqual(penny_runtime["slot_limit_state"]["strategies"]["cointegration"]["status"], "available")
        self.assertEqual(penny_runtime["slot_limit_state"]["strategies"]["weather"]["status"], "available")
        self.assertTrue(penny_runtime["running"])
        self.assertTrue(penny_runtime["state_file"].endswith("autonomy_state.penny.json"))

    def test_penny_runtime_slot_usage_excludes_non_live_rows_and_lists_consumers(self):
        signal_id = self.db.save_signal(_signal())
        stray_trade_id = self.db.open_trade(
            signal_id,
            size_usd=8,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PENNY},
        )
        live_pairs_id = self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
                "strategy_name": "cointegration",
            },
        )
        weather_signal_id = self.db.save_weather_signal(self._weather_signal("wx-yes", "wx-no", "Will NYC hit 80F?"))
        live_weather_id = self.db.open_weather_trade(
            weather_signal_id,
            size_usd=4,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
            metadata={
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )

        self.assertIsNotNone(stray_trade_id)
        self.assertIsNotNone(live_pairs_id)
        self.assertIsNotNone(live_weather_id)

        response = self.client.get("/api/autonomy/runtime?runtime_scope=penny")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["open_positions"], 2)
        self.assertEqual(payload["max_open_usage"], "2/3")
        self.assertEqual(payload["slots_remaining"], 1)
        self.assertEqual(payload["slot_limit_state"]["status"], "available")
        self.assertEqual(payload["slot_limit_state"]["open_positions"], 2)
        self.assertEqual(payload["slot_limit_state"]["slots_remaining"], 1)
        self.assertEqual(payload["slot_usage"]["consuming_trade_ids"], [live_pairs_id, live_weather_id])
        self.assertEqual(
            [row["trade_id"] for row in payload["slot_usage"]["consuming_trades"]],
            [live_pairs_id, live_weather_id],
        )
        self.assertEqual(
            {row["trade_type"] for row in payload["slot_usage"]["consuming_trades"]},
            {"pairs", "weather"},
        )

    def test_penny_runtime_account_includes_live_slot_usage_only(self):
        signal_id = self.db.save_signal(_signal())
        self.db.open_trade(
            signal_id,
            size_usd=8,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PENNY},
        )
        live_pairs_id = self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
                "strategy_name": "cointegration",
            },
        )
        weather_signal_id = self.db.save_weather_signal(self._weather_signal("live-slot-yes", "live-slot-no", "Live slot weather"))
        live_weather_id = self.db.open_weather_trade(
            weather_signal_id,
            size_usd=4,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
            metadata={
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )

        with patch.object(self.db, "_get_live_wallet_snapshot", return_value={
            "ok": True,
            "verified": True,
            "verification_status": "verified",
            "wallet_connected": True,
            "wallet_address": "0x1234567890abcdef1234567890abcdef12345678",
            "available_balance_usd": 21.5,
            "balance_source": "polygon_wallet",
            "wallet_error": None,
            "verification_error": None,
        }):
            response = self.client.get("/api/runtime/account?runtime_scope=penny")

        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["max_open"], 3)
        self.assertEqual(payload["max_open_usage"], "2/3")
        self.assertEqual(payload["open_positions"], 2)
        self.assertEqual(payload["slots_remaining"], 1)
        self.assertEqual(payload["slot_limit_state"]["status"], "available")
        self.assertEqual(payload["slot_usage"]["consuming_trade_ids"], [live_pairs_id, live_weather_id])
        self.assertEqual(
            [row["trade_id"] for row in payload["slot_usage"]["consuming_trades"]],
            [live_pairs_id, live_weather_id],
        )

    def test_penny_runtime_settings_update_is_scoped_and_audit_logged(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        captured = []
        with patch.object(autonomy.journal_writer, "append_entry", side_effect=lambda entry: captured.append(entry) or entry):
            response = self.client.post(
                "/api/autonomy/settings?runtime_scope=penny&auto_trade_enabled=true&weather_auto_trade_enabled=false&max_open_override=5",
                headers={"X-API-Key": "test-admin-key"},
            )

        payload = response.json()
        paper_settings = self.db.get_autonomy_runtime_settings(self.db.RUNTIME_SCOPE_PAPER)
        penny_settings = self.db.get_autonomy_runtime_settings(self.db.RUNTIME_SCOPE_PENNY)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtime_scope"], "penny")
        self.assertEqual(payload["settings"]["runtime_scope"], "penny")
        self.assertTrue(payload["settings"]["auto_trade_enabled"])
        self.assertTrue(payload["settings"]["weather_auto_trade_enabled"])
        self.assertEqual(payload["settings"]["max_open_override"], 5)
        self.assertEqual(payload["runtime"]["max_open"], 5)
        self.assertEqual(payload["runtime"]["max_open_usage"], "0/5")
        self.assertEqual(paper_settings["runtime_scope"], "paper")
        self.assertTrue(paper_settings["auto_trade_enabled"])
        self.assertIsNone(paper_settings["max_open_override"])
        self.assertTrue(penny_settings["auto_trade_enabled"])
        self.assertEqual(penny_settings["max_open_override"], 5)
        self.assertEqual(payload["audit_entry"]["action"], "runtime_controls_updated")
        self.assertEqual(payload["audit_entry"]["runtime_scope"], "penny")
        self.assertEqual(payload["audit_entry"]["changed_fields"]["max_open_override"]["old"], None)
        self.assertEqual(payload["audit_entry"]["changed_fields"]["max_open_override"]["new"], 5)
        self.assertNotIn("auto_trade_enabled", payload["audit_entry"]["changed_fields"])
        self.assertNotIn("weather_auto_trade_enabled", payload["audit_entry"]["changed_fields"])
        self.assertEqual(captured[0]["runtime_scope"], "penny")
        self.assertEqual(captured[0]["runtime_label"], "autonomy:penny")
        self.assertEqual(captured[0]["action"], "runtime_controls_updated")

    def test_manual_penny_weather_trade_uses_live_execution_mode(self):
        signal_id = self.db.save_weather_signal({
            "event": "NYC temp",
            "market": "Will NYC hit 80F?",
            "strategy_name": "weather_threshold",
            "market_family": "weather_threshold",
            "market_id": "nyc-temp-80",
            "yes_token": "nyc-yes",
            "no_token": "nyc-no",
            "city": "new york",
            "lat": 40.7128,
            "lon": -74.0060,
            "target_date": "2026-04-06",
            "threshold_f": 80.0,
            "direction": "above",
            "resolution_source": "wunderground_history",
            "station_id": "KNYC",
            "station_label": "New York City",
            "settlement_unit": "F",
            "settlement_precision": 1.0,
            "station_timezone": "America/New_York",
            "outcome_label": "80F or higher",
            "market_price": 0.35,
            "hours_ahead": 72,
            "timestamp": 1710000000,
            "liquidity": 8000,
            "ev_pct": 8.0,
            "kelly_fraction": 0.08,
            "action": "BUY_YES",
            "tradeable": True,
            "noaa_forecast_f": 81.0,
            "noaa_prob": 0.52,
            "noaa_sigma_f": 2.1,
            "om_forecast_f": 80.0,
            "om_prob": 0.54,
            "combined_prob": 0.53,
            "combined_edge": 0.18,
            "combined_edge_pct": 18.0,
            "selected_prob": 0.53,
            "selected_edge": 0.18,
            "selected_edge_pct": 18.0,
            "sources_agree": True,
            "sources_available": 2,
        })

        with patch.object(self.server.execution, "execute_weather_trade", return_value={
            "ok": True,
            "trade_id": 88,
            "trade_state_mode": self.db.TRADE_STATE_LIVE,
            "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
        }) as execute_weather_trade:
            response = self.client.post(
                f"/api/weather/{signal_id}/trade?runtime_scope=penny&size_usd=3",
                headers={"X-API-Key": "test-admin-key"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["runtime_scope"], "penny")
        self.assertEqual(response.json()["trade_state_mode"], self.db.TRADE_STATE_LIVE)
        execute_weather_trade.assert_called_once()
        self.assertEqual(execute_weather_trade.call_args.kwargs["mode"], "live")

    def test_dashboard_uses_runtime_scoped_history_and_runtime_fetches(self):
        html = Path(self.server.DASHBOARD_PATH).read_text()
        self.assertIn("runtimeScopedUrl('/api/trades', { status: 'closed', limit: 500 }, scope)", html)
        self.assertIn("fetch(API + runtimeScopedUrl('/api/autonomy/runtime', {}, scope))", html)
        self.assertIn("fetch(API + runtimeScopedUrl('/api/runtime/account', {}, scope))", html)
        self.assertIn("fetch(API + runtimeScopedUrl('/api/weather', { limit: 100, tradeable_only: tradeable }, ACTIVE_RUNTIME_SCOPE))", html)
        self.assertIn("if (requestId !== SCOPE_REQUEST_SEQ.history || scope !== ACTIVE_RUNTIME_SCOPE) return;", html)
        self.assertIn("if (requestId !== SCOPE_REQUEST_SEQ.stats || scope !== ACTIVE_RUNTIME_SCOPE) return;", html)
        self.assertIn("LIVE / POLYGON WALLET VERIFIED", html)
        self.assertIn("LIVE / POLYGON WALLET BLOCKED", html)
        self.assertIn("Penny mode fails closed", html)
        self.assertIn("runtimeData?.max_open_label || '—'", html)
        self.assertIn("runtimeData?.slot_limit_state || {}", html)
        self.assertIn("Open Penny Trades", html)
        self.assertIn("Remaining Slots", html)
        self.assertIn("runtimeSlotBlockedReasonMeta", html)
        self.assertIn("Blocked reason: none.", html)
        self.assertIn("Slot Limit State", html)
        self.assertIn("const strategySummary = ['cointegration', 'weather']", html)
        self.assertIn("weather.live_safeguard_reason_counts", html)
        self.assertIn("Live safeguard vetoes", html)
        self.assertIn("acceptance && acceptance.all_passed === false", html)
        self.assertIn("Weather phase: skipped", html)
        self.assertIn("cycle started in background", html)
        self.assertIn("Penny Max Open Trades", html)
        self.assertIn("Changes save immediately to the live penny scope and are appended to the audit journal.", html)
        self.assertIn("const auditAction = data?.audit_entry?.action === 'runtime_controls_update_noop'", html)

    def test_penny_runtime_slot_limit_state_surfaces_weather_live_vetoes(self):
        self.server._autonomy_status["penny"]["last_result"] = {
            "ok": True,
            "weather_phase": {
                "status": "completed",
                "trade_execution_status": "live_safeguard_vetoed",
                "reason_code": "live_safeguard_vetoed",
                "reason": "Weather live safeguards vetoed 2 trade attempt(s): slippage_block x1, balance_check_failed x1.",
                "live_safeguard_veto_count": 2,
                "live_safeguard_reason_counts": [
                    {"reason_code": "balance_check_failed", "count": 1},
                    {"reason_code": "slippage_block", "count": 1},
                ],
                "result_counts": {
                    "tradeable": 2,
                    "traded": 0,
                    "live_vetoed": 2,
                },
            },
        }

        response = self.client.get(
            "/api/autonomy/runtime?runtime_scope=penny",
            headers={"X-API-Key": "test-admin-key"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["slot_limit_state"]["status"], "attention")
        self.assertEqual(payload["slot_limit_state"]["strategies"]["weather"]["status"], "blocked")
        self.assertEqual(payload["slot_limit_state"]["strategies"]["weather"]["reason_code"], "live_safeguard_vetoed")
        self.assertIn("slippage_block x1", payload["slot_limit_state"]["strategies"]["weather"]["reason"])

    def test_penny_runtime_slot_limit_state_marks_full_budget_as_blocked(self):
        signal_id = self.db.save_signal(_signal())
        self.db.open_trade(
            signal_id,
            size_usd=3,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
                "strategy_name": "cointegration",
            },
        )
        weather_signal_id = self.db.save_weather_signal(self._weather_signal("blocked-yes", "blocked-no", "Blocked weather"))
        self.db.open_weather_trade(
            weather_signal_id,
            size_usd=4,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
            metadata={
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
            },
        )
        second_signal = _signal()
        second_signal["event"] = "Second penny slot"
        second_signal_id = self.db.save_signal(second_signal)
        self.db.open_trade(
            second_signal_id,
            size_usd=2,
            metadata={
                "runtime_scope": self.db.RUNTIME_SCOPE_PENNY,
                "trade_state_mode": self.db.TRADE_STATE_LIVE,
                "reconciliation_mode": self.db.RECONCILIATION_ORDERS,
                "strategy_name": "cointegration",
            },
        )

        response = self.client.get("/api/autonomy/runtime?runtime_scope=penny")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["open_positions"], 3)
        self.assertEqual(payload["slots_remaining"], 0)
        self.assertEqual(payload["slot_limit_state"]["status"], "blocked")
        self.assertEqual(payload["slot_limit_state"]["reason_code"], "max_open_reached")
        self.assertIn("New cointegration and weather trades are blocked", payload["slot_limit_state"]["reason"])
        self.assertEqual(payload["slot_limit_state"]["strategies"]["cointegration"]["status"], "blocked")
        self.assertEqual(payload["slot_limit_state"]["strategies"]["weather"]["status"], "blocked")

    def test_weather_api_annotations_are_runtime_scoped(self):
        paper_signal_id = self.db.save_weather_signal(self._weather_signal("scope-yes", "scope-no", "Paper weather"))
        paper_trade_id = self.db.open_weather_trade(
            paper_signal_id,
            size_usd=20,
            runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
        )
        self.assertIsNotNone(paper_trade_id)
        self.db.close_trade(paper_trade_id, exit_price_a=1.0, notes="Paper weather close")

        penny_signal_id = self.db.save_weather_signal(self._weather_signal("scope-yes", "scope-no", "Penny weather"))
        paper_rows = self.client.get("/api/weather?runtime_scope=paper").json()
        penny_rows = self.client.get("/api/weather?runtime_scope=penny").json()

        paper_row = next(row for row in paper_rows if row["id"] == penny_signal_id)
        penny_row = next(row for row in penny_rows if row["id"] == penny_signal_id)

        self.assertEqual(paper_row["blocking_reason_code"], "token_already_closed")
        self.assertEqual(paper_row["blocking_source"], "paper-weather")
        self.assertIsNone(penny_row["blocking_reason_code"])
        self.assertIsNone(penny_row["blocking_source"])


if __name__ == "__main__":
    unittest.main()
