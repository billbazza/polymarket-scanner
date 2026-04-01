import importlib
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class WatchedWalletMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        self.old_api_key = os.environ.get("SCANNER_API_KEY")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")
        os.environ["SCANNER_API_KEY"] = "test-admin-key"

        import db
        import server
        import wallet_monitor

        self.db = importlib.reload(db)
        self.db.init_db()
        self.server = importlib.reload(server)
        self.wallet_monitor = importlib.reload(wallet_monitor)
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
        import wallet_monitor

        importlib.reload(db)
        importlib.reload(server)
        importlib.reload(wallet_monitor)

    def test_wallet_monitor_detects_and_records_new_position(self):
        address = "0xabc1230000000000000000000000000000000001"
        self.db.add_watched_wallet(address, "Validator")
        self.db.set_wallet_baseline(address, [])
        self.wallet_monitor._known_positions[address] = set()

        position = {
            "conditionId": "cond-1",
            "outcome": "YES",
            "title": "Will validation trader open a new position?",
            "curPrice": 0.61,
            "avgPrice": 0.59,
            "currentValue": 125.0,
            "asset": "asset-1",
        }

        with patch.object(self.wallet_monitor, "get_positions", return_value=[position]):
            opened, closed = self.wallet_monitor._check_wallet(address, "Validator", True)

        self.assertEqual(opened, 1)
        self.assertEqual(closed, 0)
        self.assertEqual(self.db.count_open_copy_trades(address), 1)

        wallet = next(w for w in self.db.get_watched_wallets(active_only=True) if w["address"] == address)
        self.assertEqual(wallet["last_event_status"], "changes_seen")
        self.assertEqual(wallet["last_positions_count"], 1)

        events = self.db.get_wallet_monitor_events(limit=5, wallet=address)
        statuses = [event["status"] for event in events]
        self.assertIn("mirrored", statuses)
        self.assertIn("changes_seen", statuses)

    def test_copy_positions_marks_mirrors_by_wallet_and_condition(self):
        wallet_a = "0xaaa0000000000000000000000000000000000001"
        wallet_b = "0xbbb0000000000000000000000000000000000002"
        condition_id = "shared-market"

        self.db.add_watched_wallet(wallet_a, "Wallet A")
        self.db.add_watched_wallet(wallet_b, "Wallet B")
        self.db.open_copy_trade(
            wallet_a,
            "Wallet A",
            {
                "conditionId": condition_id,
                "outcome": "YES",
                "title": "Shared market",
                "curPrice": 0.55,
                "asset": "asset-shared",
            },
            size_usd=20,
        )

        positions_by_wallet = {
            wallet_a: [{
                "conditionId": condition_id,
                "outcome": "YES",
                "title": "Shared market",
                "curPrice": 0.55,
                "currentValue": 50.0,
            }],
            wallet_b: [{
                "conditionId": condition_id,
                "outcome": "YES",
                "title": "Shared market",
                "curPrice": 0.55,
                "currentValue": 75.0,
            }],
        }

        import copy_scanner

        with patch.object(copy_scanner, "get_positions", side_effect=lambda wallet: positions_by_wallet[wallet]), \
             patch.object(copy_scanner, "get_portfolio_value", return_value=1000.0):
            response = self.client.get("/api/copy/positions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        by_address = {item["address"]: item for item in payload}
        self.assertTrue(by_address[wallet_a]["positions"][0]["mirrored"])
        self.assertFalse(by_address[wallet_b]["positions"][0]["mirrored"])

    def test_copy_wallet_events_endpoint_returns_recent_outcomes(self):
        address = "0xfeed000000000000000000000000000000000001"
        self.db.add_watched_wallet(address, "Feed Wallet")
        self.db.record_wallet_monitor_event(
            source="wallet_monitor",
            wallet=address,
            label="Feed Wallet",
            event_type="new_position",
            status="blocked",
            reason_code="wallet_cap_reached",
            reason="Copy wallet cap reached (1/1).",
            condition_id="cond-feed",
            market_title="Validation market",
        )

        response = self.client.get("/api/copy/events?limit=5")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["available"])
        self.assertEqual(payload["events"][0]["status"], "blocked")
        self.assertEqual(payload["summary"]["status_counts"]["blocked"], 1)


if __name__ == "__main__":
    unittest.main()
