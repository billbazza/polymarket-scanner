import importlib
import json
import os
import tempfile
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
        self.assertEqual(penny_account["account_mode"], "live_wallet")
        self.assertTrue(penny_account["verified_live_ledger"])
        self.assertEqual(penny_account["available_balance_usd"], 17.25)
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
        self.assertTrue(penny_runtime["running"])
        self.assertTrue(penny_runtime["state_file"].endswith("autonomy_state.penny.json"))

    def test_dashboard_uses_runtime_scoped_history_and_runtime_fetches(self):
        html = Path(self.server.DASHBOARD_PATH).read_text()
        self.assertIn("runtimeScopedUrl('/api/trades', { status: 'closed', limit: 500 }, scope)", html)
        self.assertIn("fetch(API + runtimeScopedUrl('/api/autonomy/runtime', {}, scope))", html)
        self.assertIn("fetch(API + runtimeScopedUrl('/api/runtime/account', {}, scope))", html)
        self.assertIn("if (requestId !== SCOPE_REQUEST_SEQ.history || scope !== ACTIVE_RUNTIME_SCOPE) return;", html)
        self.assertIn("if (requestId !== SCOPE_REQUEST_SEQ.stats || scope !== ACTIVE_RUNTIME_SCOPE) return;", html)
        self.assertIn("LIVE / POLYGON WALLET VERIFIED", html)
        self.assertIn("LIVE / POLYGON WALLET BLOCKED", html)
        self.assertIn("Penny mode fails closed", html)


if __name__ == "__main__":
    unittest.main()
