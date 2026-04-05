import importlib
import os
import tempfile
import unittest
from unittest.mock import patch


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
        "experiment_name": "cointegration_a_grade_parity_trial",
        "experiment_status": "control",
    }


class _RecordingLiveClient:
    def __init__(self):
        self.calls = []

    def create_and_post_order(self, order_args):
        self.calls.append(order_args)
        return {"orderID": f"ord-{len(self.calls)}"}


class _FailSecondLegClient:
    def __init__(self):
        self.calls = []

    def create_and_post_order(self, order_args):
        self.calls.append(order_args)
        if len(self.calls) == 2:
            raise RuntimeError("mock leg-b failure")
        return {"orderID": "ord-a"}


class LiveCointegrationExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        os.environ["SCANNER_DB_PATH"] = os.path.join(self.tmpdir.name, "scanner-test.db")

        import db
        import execution

        self.db = importlib.reload(db)
        self.db.init_db()
        self.execution = importlib.reload(execution)

    def tearDown(self):
        self.tmpdir.cleanup()
        if self.old_db_path is None:
            os.environ.pop("SCANNER_DB_PATH", None)
        else:
            os.environ["SCANNER_DB_PATH"] = self.old_db_path

        import db
        import execution

        importlib.reload(db)
        importlib.reload(execution)

    def test_live_cointegration_uses_orderargs_with_token_ids_not_market_questions(self):
        from py_clob_client.clob_types import OrderArgs

        signal_id = self.db.save_signal(_base_signal())
        signal = self.db.get_signal_by_id(signal_id)
        client = _RecordingLiveClient()

        with patch.object(self.execution, "_create_live_clob_client", return_value=(client, None)):
            result = self.execution._execute_live(
                signal,
                size_usd=20,
                price_a=0.43,
                price_b=0.57,
                side_a="SELL",
                side_b="BUY",
                exec_mode="maker",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(client.calls), 2)
        self.assertIsInstance(client.calls[0], OrderArgs)
        self.assertIsInstance(client.calls[1], OrderArgs)
        self.assertEqual(client.calls[0].token_id, "tok-a")
        self.assertEqual(client.calls[1].token_id, "tok-b")
        self.assertEqual(client.calls[0].side, "SELL")
        self.assertEqual(client.calls[1].side, "BUY")
        self.assertEqual(result["entry_execution"]["orders"]["a"]["order_input"]["market"], "Fed cuts by June")
        self.assertEqual(result["entry_execution"]["orders"]["b"]["order_input"]["market"], "CPI below 3%")

    def test_live_cointegration_failure_surfaces_failing_leg_and_order_input(self):
        signal_id = self.db.save_signal(_base_signal())
        signal = self.db.get_signal_by_id(signal_id)
        client = _FailSecondLegClient()

        with patch.object(self.execution, "_create_live_clob_client", return_value=(client, None)):
            result = self.execution._execute_live(
                signal,
                size_usd=20,
                price_a=0.43,
                price_b=0.57,
                side_a="SELL",
                side_b="BUY",
                exec_mode="maker",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason_code"], "exchange_order_failed")
        self.assertEqual(result["order_leg"], "b")
        self.assertEqual(result["order_input"]["token_id"], "tok-b")
        self.assertEqual(result["order_input"]["market"], "CPI below 3%")
        self.assertIn("mock leg-b failure", result["error"])


if __name__ == "__main__":
    unittest.main()
