import json
import unittest
from pathlib import Path
from unittest import mock

import weather_correction
import weather_scanner


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "weather_intraday_backtest.json"


def _market_event(question, yes_price="0.40", yes_token="yes-1", no_token="no-1", liquidity=1000):
    return {
        "title": "Daily high temperature market",
        "liquidity": liquidity,
        "markets": [
            {
                "id": "market-1",
                "question": question,
                "outcomePrices": f'["{yes_price}", "{1 - float(yes_price):.2f}"]',
                "clobTokenIds": f'["{yes_token}", "{no_token}"]',
            }
        ],
    }


class WeatherCorrectionTests(unittest.TestCase):
    def test_intraday_correction_applies_when_same_day_observation_is_available(self):
        observation = weather_correction.normalize_intraday_observations([
            {
                "city": "atlanta",
                "temp_f": 84.0,
                "observed_at": "2026-07-03T10:00:00-04:00",
                "previous_temp_f": 80.0,
                "previous_observed_at": "2026-07-03T08:00:00-04:00",
            }
        ])["atlanta"]
        correction = weather_correction.apply_intraday_probability_correction(
            city_key="atlanta",
            target_date="2026-07-03",
            threshold_f=90.0,
            direction="above",
            hours_ahead=8,
            market_price=0.44,
            correction_mode="corrected",
            observation=observation,
            source_details=[
                {
                    "source_id": "noaa",
                    "value_f": 88.0,
                    "low_f": 72.0,
                    "baseline_prob": 0.2146,
                    "baseline_sigma_f": 2.5,
                },
                {
                    "source_id": "open-meteo",
                    "value_f": 87.0,
                    "low_f": 71.0,
                    "baseline_prob": 0.1151,
                    "baseline_sigma_f": 2.5,
                },
            ],
        )

        self.assertEqual(correction["status"], "corrected")
        self.assertGreater(correction["corrected_prob"], correction["baseline_prob"])
        self.assertFalse(correction["compare_only"])
        self.assertGreater(correction["confidence_weight"], 0.0)

    def test_intraday_correction_falls_back_when_observation_date_differs(self):
        observation = weather_correction.normalize_intraday_observations([
            {
                "city": "atlanta",
                "temp_f": 84.0,
                "observed_at": "2026-07-02T10:00:00-04:00",
                "previous_temp_f": 80.0,
                "previous_observed_at": "2026-07-02T08:00:00-04:00",
            }
        ])["atlanta"]
        correction = weather_correction.apply_intraday_probability_correction(
            city_key="atlanta",
            target_date="2026-07-03",
            threshold_f=90.0,
            direction="above",
            hours_ahead=8,
            market_price=0.44,
            correction_mode="shadow",
            observation=observation,
            source_details=[
                {
                    "source_id": "noaa",
                    "value_f": 88.0,
                    "low_f": 72.0,
                    "baseline_prob": 0.2146,
                    "baseline_sigma_f": 2.5,
                }
            ],
        )

        self.assertEqual(correction["status"], "fallback")
        self.assertEqual(correction["selected_prob"], correction["baseline_prob"])
        self.assertTrue(correction["compare_only"])

    def test_backtest_fixture_improves_brier_and_realized_edge(self):
        with FIXTURE_PATH.open("r", encoding="utf-8") as handle:
            samples = json.load(handle)

        result = weather_correction.evaluate_intraday_correction(samples)

        self.assertGreater(result["improvement"]["brier_delta"], 0)
        self.assertGreater(result["improvement"]["log_loss_delta"], 0)
        self.assertGreaterEqual(result["improvement"]["edge_realized_delta"], 0)

    @mock.patch.object(weather_scanner.api, "get_events")
    @mock.patch.object(weather_scanner.weather_sources, "fetch_threshold_forecasts")
    def test_scan_exposes_baseline_and_corrected_outputs_without_replacing_default_selection(self, mock_fetch_sources, mock_get_events):
        mock_get_events.side_effect = [
            [_market_event("Will Atlanta temperatures reach 90°F or higher today?")],
            [],
        ]
        mock_fetch_sources.return_value = [
            {
                "source_id": "noaa",
                "attempted": True,
                "available": True,
                "value_f": 88.0,
                "low_f": 72.0,
            },
            {
                "source_id": "open-meteo",
                "attempted": True,
                "available": True,
                "value_f": 87.0,
                "low_f": 71.0,
            },
        ]

        opportunities, _ = weather_scanner.scan(
            verbose=False,
            intraday_observations=[
                {
                    "city": "atlanta",
                    "temp_f": 84.0,
                    "observed_at": "2026-04-02T10:00:00-04:00",
                    "previous_temp_f": 80.0,
                    "previous_observed_at": "2026-04-02T08:00:00-04:00",
                }
            ],
        )

        self.assertEqual(len(opportunities), 1)
        opp = opportunities[0]
        self.assertEqual(opp["correction_mode"], "shadow")
        self.assertTrue(opp["correction_compare_only"])
        self.assertEqual(opp["selected_prob"], opp["combined_prob"])
        self.assertGreater(opp["corrected_combined_prob"], opp["combined_prob"])
        self.assertEqual(opp["correction_status"], "corrected")


if __name__ == "__main__":
    unittest.main()
