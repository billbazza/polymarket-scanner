import json
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _weather_opp(
    city="Atlanta",
    market="Will Atlanta hit 82F?",
    yes_token="yes-atl",
    no_token="no-atl",
    target_date="2026-04-03",
    hours_ahead=72,
):
    return {
        "event": f"Highest temperature in {city} on {target_date}?",
        "market": market,
        "market_id": f"market-{yes_token}",
        "yes_token": yes_token,
        "no_token": no_token,
        "city": city.lower(),
        "lat": 33.7490,
        "lon": -84.3880,
        "target_date": target_date,
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
        "hours_ahead": hours_ahead,
        "source_disagreement": 0.02,
        "ev_pct": 8.5,
        "kelly_fraction": 0.12,
        "action": "BUY_YES",
        "tradeable": True,
        "liquidity": 10000,
        "strategy_name": "weather_threshold",
        "source_meta": {
            "threshold_admission": {
                "tradeable": True,
                "hours_ahead": hours_ahead,
                "hours_ahead_cmp": round(float(hours_ahead), 1),
                "source_disagreement": 0.02,
                "guard_thresholds": {
                    "min_liquidity": 0.0,
                    "min_hours_ahead": 0.0,
                    "max_disagreement": 1.0,
                    "guard_name": "scan-fixture",
                    "guard_tier": 0,
                },
            }
        },
    }


class WeatherSignalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.old_db_path = os.environ.get("SCANNER_DB_PATH")
        self.old_diag_dir = os.environ.get("SCANNER_DIAGNOSTICS_DIR")
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
        if self.old_diag_dir is None:
            os.environ.pop("SCANNER_DIAGNOSTICS_DIR", None)
        else:
            os.environ["SCANNER_DIAGNOSTICS_DIR"] = self.old_diag_dir

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

    def test_closed_token_blocks_reentry_from_fresh_signal_row(self):
        original_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Los Angeles", market="Will LA hit 80F?", yes_token="yes-la", no_token="no-la")
        )
        original_trade_id = self.db.open_weather_trade(original_signal_id, size_usd=20)
        self.assertIsNotNone(original_trade_id)
        pnl = self.db.close_trade(original_trade_id, exit_price_a=0.28, notes="Auto-closed: stop-loss hit (0.280 <= 0.315)")
        self.assertIsNotNone(pnl)

        duplicate_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Los Angeles", market="Will LA hit 80F again?", yes_token="yes-la", no_token="no-la")
        )

        decision = self.db.inspect_weather_trade_open(duplicate_signal_id, size_usd=20)
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason_code"], "token_already_closed")
        self.assertEqual(decision["existing_trade_id"], original_trade_id)
        self.assertIn("do not reopen", decision["reason"])

        duplicate_row = next(row for row in self.db.get_weather_signals(limit=None) if row["id"] == duplicate_signal_id)
        self.assertEqual(duplicate_row["status"], "blocked")
        self.assertEqual(duplicate_row["blocking_reason_code"], "token_already_closed")
        self.assertEqual(duplicate_row["blocked_by_trade_id"], original_trade_id)
        self.assertIsNone(duplicate_row["latest_trade_exit_reason"])
        self.assertIn("do not reopen", duplicate_row["status_detail"])

    def test_weather_reopen_probation_respected_for_approved_tokens(self):
        base_token = "yes-la"
        no_token = "no-la"
        first_signal_id = self.db.save_weather_signal(
            _weather_opp(
                city="Los Angeles",
                market="Will Los Angeles hit 80F on April 4?",
                yes_token=base_token,
                no_token=no_token,
                target_date="2026-04-04",
            )
        )
        trade_id = self.db.open_weather_trade(first_signal_id, size_usd=20, mode="paper")
        self.assertIsNotNone(trade_id)
        self.db.close_trade(trade_id, exit_price_a=0.50, notes="Initial close")

        for attempt in (1, 2):
            signal_id = self.db.save_weather_signal(
                _weather_opp(
                    city="Los Angeles",
                    market=f"Will Los Angeles hit 80F on April 4 (reopen {attempt})?",
                    yes_token=base_token,
                    no_token=no_token,
                    target_date="2026-04-04",
                )
            )
            decision = self.db.inspect_weather_trade_open(signal_id, size_usd=20, mode="paper")
            self.assertTrue(decision["ok"], msg=f"Reopen {attempt} should be allowed")
            reopen_context = decision.get("reopen_context")
            self.assertIsNotNone(reopen_context)
            self.assertEqual(reopen_context.get("reopen_count"), attempt - 1)
            reopened_trade_id = self.db.open_weather_trade(signal_id, size_usd=20, mode="paper")
            self.assertIsNotNone(reopened_trade_id)
            self.db.close_trade(reopened_trade_id, exit_price_a=0.45, notes=f"Reopen {attempt} close")

        final_signal_id = self.db.save_weather_signal(
            _weather_opp(
                city="Los Angeles",
                market="Will Los Angeles hit 80F on April 4 (reopen 3)?",
                yes_token=base_token,
                no_token=no_token,
                target_date="2026-04-04",
            )
        )
        final_decision = self.db.inspect_weather_trade_open(final_signal_id, size_usd=20, mode="paper")
        self.assertFalse(final_decision["ok"])
        self.assertEqual(final_decision["reason_code"], "token_probation_blocked")

    def test_penny_weather_history_is_isolated_from_paper_weather_history(self):
        paper_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Chicago", market="Will Chicago hit 71F?", yes_token="yes-chi", no_token="no-chi")
        )
        paper_trade_id = self.db.open_weather_trade(
            paper_signal_id,
            size_usd=20,
            runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
        )
        self.assertIsNotNone(paper_trade_id)
        self.db.close_trade(paper_trade_id, exit_price_a=1.0, notes="Paper close")

        penny_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Chicago", market="Will Chicago hit 71F live?", yes_token="yes-chi", no_token="no-chi")
        )
        penny_decision = self.db.inspect_weather_trade_open(
            penny_signal_id,
            size_usd=3,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )

        self.assertTrue(penny_decision["ok"])
        self.assertEqual(penny_decision["runtime_scope"], self.db.RUNTIME_SCOPE_PENNY)
        self.assertEqual(penny_decision["decision_source"], "penny-weather")
        self.assertEqual(penny_decision["history_source"], "penny-weather")

        paper_duplicate_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Chicago", market="Will Chicago hit 71F paper again?", yes_token="yes-chi", no_token="no-chi")
        )
        paper_decision = self.db.inspect_weather_trade_open(
            paper_duplicate_signal_id,
            size_usd=20,
            mode="paper",
            runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
        )

        self.assertFalse(paper_decision["ok"])
        self.assertEqual(paper_decision["reason_code"], "token_already_closed")
        self.assertEqual(paper_decision["history_source"], "paper-weather")
        self.assertEqual(paper_decision["existing_trade_id"], paper_trade_id)

        penny_rows = {
            row["id"]: row
            for row in self.db.get_weather_signals(limit=None, runtime_scope=self.db.RUNTIME_SCOPE_PENNY)
            if row["id"] in {paper_signal_id, penny_signal_id}
        }
        self.assertIsNone(penny_rows[penny_signal_id]["blocking_reason_code"])
        self.assertIsNone(penny_rows[penny_signal_id]["blocked_by_trade_id"])

    def test_weather_probation_isolated_per_runtime_scope(self):
        token = "yes-la"
        no_token = "no-la"
        for attempt in (1, 2):
            signal_id = self.db.save_weather_signal(
                _weather_opp(
                    city="Los Angeles",
                    market=f"Will Los Angeles hit 80F on April 4 (paper reopen {attempt})?",
                    yes_token=token,
                    no_token=no_token,
                    target_date="2026-04-04",
                )
            )
            if attempt == 1:
                trade_id = self.db.open_weather_trade(
                    signal_id,
                    size_usd=20,
                    mode="paper",
                    runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
                )
                self.assertIsNotNone(trade_id)
            else:
                decision = self.db.inspect_weather_trade_open(
                    signal_id,
                    size_usd=20,
                    mode="paper",
                    runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
                )
                self.assertTrue(decision["ok"])
                trade_id = self.db.open_weather_trade(
                    signal_id,
                    size_usd=20,
                    mode="paper",
                    runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
                )
                self.assertIsNotNone(trade_id)
            self.db.close_trade(trade_id, exit_price_a=0.45, notes=f"Paper close {attempt}")

        paper_probation = self.db.get_weather_token_probation(token, runtime_scope=self.db.RUNTIME_SCOPE_PAPER)
        self.assertEqual(paper_probation["reopen_count"], 1)

        penny_signal_id = self.db.save_weather_signal(
            _weather_opp(
                city="Los Angeles",
                market="Will Los Angeles hit 80F on April 4 (penny baseline close)?",
                yes_token=token,
                no_token=no_token,
                target_date="2026-04-04",
            )
        )
        penny_trade_id = self.db.open_weather_trade(
            penny_signal_id,
            size_usd=3,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )
        self.assertIsNotNone(penny_trade_id)
        self.db.close_trade(penny_trade_id, exit_price_a=0.45, notes="Penny close")

        penny_reopen_signal_id = self.db.save_weather_signal(
            _weather_opp(
                city="Los Angeles",
                market="Will Los Angeles hit 80F on April 4 (penny reopen)?",
                yes_token=token,
                no_token=no_token,
                target_date="2026-04-04",
            )
        )
        penny_decision = self.db.inspect_weather_trade_open(
            penny_reopen_signal_id,
            size_usd=3,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )
        self.assertTrue(penny_decision["ok"])
        penny_probation = self.db.get_weather_token_probation(token, runtime_scope=self.db.RUNTIME_SCOPE_PENNY)
        self.assertEqual(penny_probation["reopen_count"], 0)

    def test_weather_history_ignores_cointegration_trades(self):
        pairs_signal_id = self.db.save_signal({
            "event": "Token overlap",
            "market_a": "Market A",
            "market_b": "Market B",
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
            "action": "SELL A / BUY B",
            "grade_label": "A+",
            "tradeable": True,
            "paper_tradeable": True,
            "token_id_a": "yes-overlap",
            "token_id_b": "tok-b",
            "ev": {"ev_pct": 2.1},
            "sizing": {"recommended_size": 20.0},
            "filters": {"ev_pass": True},
        })
        pairs_trade_id = self.db.open_trade(
            pairs_signal_id,
            size_usd=3,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PENNY},
        )
        self.assertIsNotNone(pairs_trade_id)

        weather_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Boston", market="Will Boston hit 68F?", yes_token="yes-overlap", no_token="no-overlap")
        )
        decision = self.db.inspect_weather_trade_open(
            weather_signal_id,
            size_usd=3,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )

        self.assertTrue(decision["ok"])
        self.assertEqual(decision["decision_source"], "penny-weather")
        self.assertEqual(decision["history_source"], "penny-weather")

    def test_penny_weather_history_prefers_penny_weather_over_paper_and_cointegration_overlap(self):
        token = "yes-overlap-2"
        no_token = "no-overlap-2"

        paper_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Seattle", market="Will Seattle hit 63F paper?", yes_token=token, no_token=no_token)
        )
        paper_trade_id = self.db.open_weather_trade(
            paper_signal_id,
            size_usd=20,
            mode="paper",
            runtime_scope=self.db.RUNTIME_SCOPE_PAPER,
        )
        self.assertIsNotNone(paper_trade_id)
        self.db.close_trade(paper_trade_id, exit_price_a=0.5, notes="Paper weather close")

        pairs_signal_id = self.db.save_signal({
            "event": "Overlap cointegration",
            "market_a": "Overlap A",
            "market_b": "Overlap B",
            "price_a": 0.47,
            "price_b": 0.55,
            "z_score": 1.6,
            "coint_pvalue": 0.03,
            "beta": 1.0,
            "half_life": 6.0,
            "spread_mean": 0.0,
            "spread_std": 0.05,
            "current_spread": 0.09,
            "liquidity": 20000,
            "volume_24h": 11000,
            "action": "SELL A / BUY B",
            "grade_label": "A+",
            "tradeable": True,
            "paper_tradeable": True,
            "token_id_a": token,
            "token_id_b": "tok-overlap-b",
            "ev": {"ev_pct": 2.0},
            "sizing": {"recommended_size": 20.0},
            "filters": {"ev_pass": True},
        })
        pairs_trade_id = self.db.open_trade(
            pairs_signal_id,
            size_usd=3,
            metadata={"runtime_scope": self.db.RUNTIME_SCOPE_PENNY},
        )
        self.assertIsNotNone(pairs_trade_id)

        penny_first_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Seattle", market="Will Seattle hit 63F penny base?", yes_token=token, no_token=no_token)
        )
        penny_trade_id = self.db.open_weather_trade(
            penny_first_signal_id,
            size_usd=3,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )
        self.assertIsNotNone(penny_trade_id)

        penny_repeat_signal_id = self.db.save_weather_signal(
            _weather_opp(city="Seattle", market="Will Seattle hit 63F penny repeat?", yes_token=token, no_token=no_token)
        )
        decision = self.db.inspect_weather_trade_open(
            penny_repeat_signal_id,
            size_usd=3,
            mode="live",
            runtime_scope=self.db.RUNTIME_SCOPE_PENNY,
        )

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason_code"], "token_already_open")
        self.assertEqual(decision["runtime_scope"], self.db.RUNTIME_SCOPE_PENNY)
        self.assertEqual(decision["decision_source"], "penny-weather")
        self.assertEqual(decision["history_runtime_scope"], self.db.RUNTIME_SCOPE_PENNY)
        self.assertEqual(decision["history_strategy"], "weather")
        self.assertEqual(decision["history_source"], "penny-weather")
        self.assertEqual(decision["existing_trade_id"], penny_trade_id)

    def test_weather_preflight_horizon_block_uses_decision_source_not_history_source(self):
        signal_id = self.db.save_weather_signal(
            _weather_opp(city="Leeds", market="Will Leeds hit 75F?", yes_token="yes-leeds", no_token="no-leeds")
        )

        with mock.patch.object(
            self.db.weather_guard_state,
            "current_guard",
            return_value={"min_hours_ahead": 80, "name": "legacy", "tier_index": 2, "max_disagreement": 0.12, "min_liquidity": 10000},
        ):
            decision = self.db.inspect_weather_trade_open(signal_id, size_usd=20, mode="paper")
            rows = {row["id"]: row for row in self.db.get_weather_signals(limit=None) if row["id"] == signal_id}

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason_code"], "horizon_too_short")
        self.assertEqual(decision["blocker_source"], "paper-weather")
        row = rows[signal_id]
        self.assertEqual(row["blocking_reason_code"], "horizon_too_short")
        self.assertEqual(row["blocking_source"], "paper-weather")
        self.assertEqual(row["status"], "blocked")

    def test_weather_tradeable_boundary_rounds_consistently_at_48h(self):
        opp = _weather_opp(
            city="Bristol",
            market="Will Bristol hit 72F?",
            yes_token="yes-bristol",
            no_token="no-bristol",
            hours_ahead=48.0,
        )
        opp["source_meta"]["threshold_admission"]["guard_thresholds"] = {
            "min_liquidity": 5000.0,
            "min_hours_ahead": 48.0,
            "max_disagreement": 0.18,
            "guard_name": "relaxed",
            "guard_tier": 0,
        }
        signal_id = self.db.save_weather_signal(opp)
        conn = self.db.get_conn()
        conn.execute("UPDATE weather_signals SET timestamp=? WHERE id=?", (1000.0, signal_id))
        conn.commit()
        conn.close()
        with mock.patch.object(self.db.time, "time", return_value=1060.0):
            with mock.patch.object(
                self.db.weather_guard_state,
                "current_guard",
                return_value={"min_hours_ahead": 48, "name": "relaxed", "tier_index": 0, "max_disagreement": 0.18, "min_liquidity": 5000},
            ):
                decision = self.db.inspect_weather_trade_open(signal_id, size_usd=20, mode="paper")

        self.assertTrue(decision["ok"])
        self.assertEqual(decision["remaining_hours_cmp"], 48.0)
        self.assertEqual(decision["stored_hours_ahead_cmp"], 48.0)

    def test_weather_tradeable_horizon_block_requires_material_state_change(self):
        opp = _weather_opp(
            city="Cardiff",
            market="Will Cardiff hit 70F?",
            yes_token="yes-cardiff",
            no_token="no-cardiff",
            hours_ahead=48.0,
        )
        opp["source_meta"]["threshold_admission"]["guard_thresholds"] = {
            "min_liquidity": 5000.0,
            "min_hours_ahead": 48.0,
            "max_disagreement": 0.18,
            "guard_name": "relaxed",
            "guard_tier": 0,
        }
        signal_id = self.db.save_weather_signal(opp)
        conn = self.db.get_conn()
        conn.execute("UPDATE weather_signals SET timestamp=? WHERE id=?", (1000.0, signal_id))
        conn.commit()
        conn.close()
        with mock.patch.object(self.db.time, "time", return_value=2200.0):
            with mock.patch.object(
                self.db.weather_guard_state,
                "current_guard",
                return_value={"min_hours_ahead": 48, "name": "relaxed", "tier_index": 0, "max_disagreement": 0.18, "min_liquidity": 5000},
            ):
                decision = self.db.inspect_weather_trade_open(signal_id, size_usd=20, mode="paper")

        self.assertFalse(decision["ok"])
        self.assertEqual(decision["reason_code"], "horizon_too_short")
        self.assertTrue(decision["material_state_change"])
        self.assertEqual(decision["state_change_reason_code"], "horizon_aged_below_threshold")
        self.assertEqual(decision["stored_hours_ahead_cmp"], 48.0)
        self.assertEqual(decision["remaining_hours_cmp"], 47.7)

    def test_weather_close_trade_uses_single_leg_pnl_and_closes_signal(self):
        signal_id = self.db.save_weather_signal(
            _weather_opp(city="Miami", market="Will Miami hit 87F?", yes_token="yes-mia", no_token="no-mia")
        )
        trade_id = self.db.open_weather_trade(signal_id, size_usd=20)
        self.assertIsNotNone(trade_id)

        pnl = self.db.close_trade(trade_id, exit_price_a=0.50, notes="Manual close for audit")
        trade = self.db.get_trade(trade_id)
        signal = self.db.get_weather_signal_by_id(signal_id)

        expected_pnl = round((20 / 0.41) * 0.50 - 20, 2)
        self.assertAlmostEqual(pnl, expected_pnl, places=2)
        self.assertAlmostEqual(trade["pnl"], expected_pnl, places=2)
        self.assertEqual(trade["status"], "closed")
        self.assertEqual(trade["exit_price_a"], 0.50)
        self.assertEqual(trade["exit_price_b"], 0.50)
        self.assertEqual(trade["exit_reason"], "Manual close for audit")
        self.assertEqual(signal["status"], "closed")

    def test_weather_stop_loss_uses_configured_floor(self):
        signal_id = self.db.save_weather_signal(_weather_opp(city="Dallas", market="Will Dallas hit 84F?", yes_token="yes-dal", no_token="no-dal"))
        trade_id = self.db.open_weather_trade(signal_id, size_usd=20)
        trade = self.db.get_trade(trade_id)

        import tracker

        tracker = importlib.reload(tracker)
        stop_floor = trade["entry_price_a"] * (1 - tracker.WEATHER_STOP_LOSS_PCT)
        with mock.patch.object(tracker, "_resolve_single_leg_price", return_value={"price": stop_floor + 0.01, "source": "midpoint", "resolved": False}):
            result = tracker.auto_close_trades()
        self.assertEqual(result, [])
        self.assertEqual(self.db.get_trade(trade_id)["status"], "open")

        with mock.patch.object(
            tracker,
            "_resolve_single_leg_price",
            return_value={"price": stop_floor - 0.002, "source": "midpoint", "resolved": False},
        ), mock.patch.object(tracker.weather_guard_state, "register_failure"):
            result = tracker.auto_close_trades()

        self.assertEqual(len(result), 1)
        close = result[0]
        self.assertEqual(close["trade_id"], trade_id)
        self.assertIn("stop-loss hit", close["reason"])
        self.assertEqual(self.db.get_trade(trade_id)["status"], "closed")
        self.assertAlmostEqual(tracker.WEATHER_STOP_LOSS_PCT, 0.15, places=4)
        self.assertAlmostEqual(stop_floor, trade["entry_price_a"] * (1 - tracker.WEATHER_STOP_LOSS_PCT), places=4)

    def test_weather_stop_loss_persists_diagnostics_in_test_scoped_path(self):
        signal_id = self.db.save_weather_signal(
            _weather_opp(city="Denver", market="Will Denver hit 62F?", yes_token="yes-den", no_token="no-den")
        )
        trade_id = self.db.open_weather_trade(signal_id, size_usd=20)

        import tracker

        tracker = importlib.reload(tracker)
        diagnostics_path = tracker._stop_contexts_file().resolve()
        expected_path = (Path(self.tmpdir.name) / "reports" / "diagnostics" / "weather-stop-contexts.jsonl").resolve()
        self.assertEqual(diagnostics_path, expected_path)

        stop_floor = self.db.get_trade(trade_id)["entry_price_a"] * (1 - tracker.WEATHER_STOP_LOSS_PCT)
        with mock.patch.object(
            tracker,
            "_resolve_single_leg_price",
            return_value={"price": stop_floor - 0.01, "source": "midpoint", "resolved": False},
        ), mock.patch.object(tracker.journal_writer, "append_entry") as append_entry, mock.patch.object(
            tracker.weather_guard_state,
            "register_failure",
        ):
            result = tracker.auto_close_trades()

        self.assertEqual(len(result), 1)
        self.assertTrue(diagnostics_path.exists())
        rows = [json.loads(line) for line in diagnostics_path.read_text().splitlines() if line.strip()]
        self.assertEqual(len(rows), 1)
        payload = rows[0]
        context = payload["context"]

        self.assertEqual(payload["trade_id"], trade_id)
        self.assertEqual(payload["signal_id"], signal_id)
        self.assertEqual(context["city"], "denver")
        self.assertEqual(context["target_date"], "2026-04-03")
        self.assertEqual(context["strategy_name"], "weather_threshold")
        self.assertTrue(context["sources_agree"])
        self.assertTrue(context["gap_through_stop"])
        self.assertEqual(context["trigger_type"], "gap_through")
        self.assertIsNotNone(context["entry_age_hours"])
        self.assertIsNotNone(context["hold_hours"])
        append_entry.assert_called_once()


if __name__ == "__main__":
    unittest.main()
