import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


def _base_signal():
    return {
        "event": "Rate cuts vs inflation",
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


class PaperTradeAttemptTests(unittest.TestCase):
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

    def test_attempt_summary_counts_allowed_blocked_and_errors(self):
        self.db.record_paper_trade_attempt(
            source="manual_api",
            strategy="pairs",
            outcome="allowed",
            reason_code="opened",
            reason="Paper pairs trade opened.",
            event="Allowed trade",
            signal_id=1,
            trade_id=11,
            size_usd=20,
        )
        self.db.record_paper_trade_attempt(
            source="manual_api",
            strategy="weather",
            outcome="blocked",
            reason_code="insufficient_cash",
            reason="Insufficient paper cash.",
            event="Blocked trade",
            weather_signal_id=2,
            size_usd=50,
        )
        self.db.record_paper_trade_attempt(
            source="autonomy",
            strategy="system",
            outcome="error",
            reason_code="refresh_open_trades_failed",
            reason="Open-trade refresh failed.",
            event="Autonomy",
        )

        summary = self.db.get_paper_trade_attempt_summary(limit=10)
        attempts = self.db.get_paper_trade_attempts(limit=10)

        self.assertEqual(summary["allowed"], 1)
        self.assertEqual(summary["blocked"], 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["top_blockers"][0]["reason_code"], "insufficient_cash")
        self.assertEqual(attempts[0]["reason_code"], "refresh_open_trades_failed")

    def test_blocked_pairs_endpoint_records_operator_visible_attempt(self):
        signal_id = self.db.save_signal(_base_signal())
        self.db.set_paper_starting_bankroll(10)

        response = self.client.post(
            f"/api/trades?signal_id={signal_id}&size_usd=25",
            headers={"X-API-Key": "test-admin-key"},
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["reason_code"], "insufficient_cash")

        attempts = self.db.get_paper_trade_attempts(limit=5)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["strategy"], "pairs")
        self.assertEqual(attempts[0]["outcome"], "blocked")
        self.assertEqual(attempts[0]["reason_code"], "insufficient_cash")
        self.assertEqual(attempts[0]["signal_id"], signal_id)

        feed = self.client.get("/api/paper-trade-attempts?limit=5")
        self.assertEqual(feed.status_code, 200)
        body = feed.json()
        self.assertEqual(body["summary"]["blocked"], 1)
        self.assertEqual(body["attempts"][0]["reason_code"], "insufficient_cash")

    def test_blocked_pairs_endpoint_degrades_gracefully_when_attempt_logger_missing(self):
        signal_id = self.db.save_signal(_base_signal())
        self.db.set_paper_starting_bankroll(10)

        original = getattr(self.server.db, "record_paper_trade_attempt")
        delattr(self.server.db, "record_paper_trade_attempt")
        try:
            response = self.client.post(
                f"/api/trades?signal_id={signal_id}&size_usd=25",
                headers={"X-API-Key": "test-admin-key"},
            )
        finally:
            setattr(self.server.db, "record_paper_trade_attempt", original)

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["reason_code"], "insufficient_cash")

    def test_attempt_feed_degrades_gracefully_when_attempt_helpers_missing(self):
        original_attempts = getattr(self.server.db, "get_paper_trade_attempts")
        original_summary = getattr(self.server.db, "get_paper_trade_attempt_summary")
        delattr(self.server.db, "get_paper_trade_attempts")
        delattr(self.server.db, "get_paper_trade_attempt_summary")
        try:
            response = self.client.get("/api/paper-trade-attempts?limit=5")
        finally:
            setattr(self.server.db, "get_paper_trade_attempts", original_attempts)
            setattr(self.server.db, "get_paper_trade_attempt_summary", original_summary)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["available"])
        self.assertEqual(body["degraded_reason"], "paper_trade_attempt_api_missing")
        self.assertEqual(body["attempts"], [])
        self.assertFalse(body["summary"]["available"])

    def test_autonomy_record_attempt_skips_missing_db_helper(self):
        import autonomy

        autonomy = importlib.reload(autonomy)
        original = getattr(autonomy.db, "record_paper_trade_attempt")
        delattr(autonomy.db, "record_paper_trade_attempt")
        try:
            autonomy.record_attempt(
                "paper",
                "pairs",
                "blocked",
                "insufficient_cash",
                "Insufficient paper cash.",
                event="Autonomy preflight",
            )
        finally:
            setattr(autonomy.db, "record_paper_trade_attempt", original)


if __name__ == "__main__":
    unittest.main()
