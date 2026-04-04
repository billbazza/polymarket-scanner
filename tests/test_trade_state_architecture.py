import importlib
import os
import tempfile
import unittest


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
        "admission_path": "standard_a_plus",
        "experiment_name": "cointegration_a_grade_paper_trial",
        "experiment_status": "control",
    }


class TradeStateArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")

        import db
        import execution
        import trade_monitor

        self.db = importlib.reload(db)
        self.db.init_db()
        self.execution = importlib.reload(execution)
        self.trade_monitor = importlib.reload(trade_monitor)

    def tearDown(self):
        self.tmpdir.cleanup()
        if self.old_db_path is None:
            os.environ.pop("SCANNER_DB_PATH", None)
        else:
            os.environ["SCANNER_DB_PATH"] = self.old_db_path

        import db
        import execution
        import trade_monitor

        importlib.reload(db)
        importlib.reload(execution)
        importlib.reload(trade_monitor)

    def test_paper_pairs_trade_stays_internal_and_creates_no_open_orders(self):
        signal_id = self.db.save_signal(_base_signal())
        signal = self.db.get_signal_by_id(signal_id)

        result = self.execution._execute_paper(
            signal,
            size_usd=20,
            price_a=0.43,
            price_b=0.57,
            side_a="SELL",
            side_b="BUY",
            exec_mode="maker",
        )

        self.assertTrue(result["ok"])
        trade = self.db.get_trade(result["trade_id"])
        self.assertEqual(trade["trade_state_mode"], self.db.TRADE_STATE_PAPER)
        self.assertEqual(trade["reconciliation_mode"], self.db.RECONCILIATION_INTERNAL)
        self.assertIsNone(trade["canonical_ref"])
        self.assertEqual(trade["token_id_a"], "tok-a")
        self.assertEqual(trade["token_id_b"], "tok-b")

        conn = self.db.get_conn()
        try:
            open_order_count = conn.execute("SELECT COUNT(*) FROM open_orders").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(open_order_count, 0)

    def test_copy_trade_persists_wallet_identity_and_allows_distinct_outcomes(self):
        wallet = "0xabc1230000000000000000000000000000000001"
        yes_position = {
            "conditionId": "cond-1",
            "outcome": "YES",
            "title": "Shared market",
            "curPrice": 0.61,
            "asset": "asset-yes",
        }
        no_position = {
            "conditionId": "cond-1",
            "outcome": "NO",
            "title": "Shared market",
            "curPrice": 0.39,
            "asset": "asset-no",
        }

        yes_trade_id = self.db.open_copy_trade(wallet, "Wallet", yes_position, size_usd=20)
        self.assertIsNotNone(yes_trade_id)

        trade = self.db.get_trade(yes_trade_id)
        self.assertEqual(trade["trade_state_mode"], self.db.TRADE_STATE_WALLET)
        self.assertEqual(trade["reconciliation_mode"], self.db.RECONCILIATION_WALLET)
        self.assertEqual(trade["external_position_id"], "asset-yes")
        self.assertEqual(
            trade["canonical_ref"],
            f"wallet:{wallet.lower()}:condition:cond-1:outcome:yes",
        )

        second_decision = self.db.inspect_copy_trade_open(wallet, no_position, size_usd=20)
        self.assertTrue(second_decision["ok"])
        self.assertEqual(second_decision["external_position_id"], "asset-no")
        self.assertNotEqual(second_decision["canonical_ref"], trade["canonical_ref"])

    def test_copy_trade_blocks_extreme_entry_prices(self):
        wallet = "0xabc1230000000000000000000000000000000002"
        high_price_position = {
            "conditionId": "cond-extreme",
            "outcome": "NO",
            "title": "Extreme high price",
            "curPrice": 0.96,
            "asset": "asset-extreme-high",
        }
        low_price_position = {
            "conditionId": "cond-extreme",
            "outcome": "YES",
            "title": "Extreme low price",
            "curPrice": 0.04,
            "asset": "asset-extreme-low",
        }
        for position in (high_price_position, low_price_position):
            decision = self.db.inspect_copy_trade_open(wallet, position, size_usd=20)
            self.assertFalse(decision["ok"], msg=f"{position}")
            self.assertEqual(decision["reason_code"], "entry_price_range_violation")

        balanced_position = {
            "conditionId": "cond-extreme",
            "outcome": "YES",
            "title": "Balanced price",
            "curPrice": 0.45,
            "asset": "asset-balanced",
        }
        balanced_decision = self.db.inspect_copy_trade_open(wallet, balanced_position, size_usd=20)
        self.assertTrue(balanced_decision["ok"])

    def test_trade_monitor_flags_attached_trade_missing_canonical_identity(self):
        conn = self.db.get_conn()
        try:
            now = 1_700_000_000
            conn.execute(
                """
                INSERT INTO trades (
                    trade_type, opened_at, side_a, side_b, entry_price_a, entry_price_b,
                    size_usd, status, copy_wallet, copy_label, copy_condition_id, copy_outcome,
                    trade_state_mode, reconciliation_mode, token_id_a, event, market_a
                ) VALUES ('copy', ?, 'BUY_YES', '', 0.6, 0, 20, 'open', ?, ?, ?, ?, ?, ?, '', ?, ?)
                """,
                (
                    now,
                    "0xfeed000000000000000000000000000000000001",
                    "Feed Wallet",
                    "cond-feed",
                    "YES",
                    self.db.TRADE_STATE_WALLET,
                    self.db.RECONCILIATION_WALLET,
                    "Validation market",
                    "YES",
                ),
            )
            trade_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        trade = self.db.get_trade(trade_id)
        result = self.trade_monitor.classify_trade(trade, wallet_positions_cache={})

        self.assertEqual(result["classification"], "unpriceable-but-identifiable")
        self.assertEqual(result["reason_code"], "missing_canonical_identity")


if __name__ == "__main__":
    unittest.main()
