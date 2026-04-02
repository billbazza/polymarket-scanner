import unittest
from unittest import mock

import weather_scanner
import weather_sources


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


class WeatherSourceLayerTests(unittest.TestCase):
    def test_threshold_source_plan_preserves_default_order(self):
        atlanta_plan = weather_sources.get_threshold_source_plan(city_key="atlanta")
        self.assertEqual([item["id"] for item in atlanta_plan], ["noaa", "open-meteo"])
        self.assertTrue(atlanta_plan[0]["applicable"])
        self.assertTrue(atlanta_plan[1]["applicable"])

        london_plan = weather_sources.get_threshold_source_plan(city_key="london")
        self.assertFalse(london_plan[0]["applicable"])
        self.assertEqual(london_plan[0]["skip_reason"], "city_not_supported")
        self.assertTrue(london_plan[1]["applicable"])

    @mock.patch.object(weather_sources, "_om_daily_high", return_value=79.0)
    @mock.patch.object(weather_sources, "_nws_daily_high", return_value=None)
    def test_fetch_threshold_forecasts_gracefully_degrades(self, mock_noaa, mock_om):
        results = weather_sources.fetch_threshold_forecasts(
            33.7490,
            -84.3880,
            "2026-04-03",
            city_key="atlanta",
        )
        by_id = {item["source_id"]: item for item in results}

        self.assertTrue(by_id["noaa"]["attempted"])
        self.assertFalse(by_id["noaa"]["available"])
        self.assertEqual(by_id["noaa"]["meta"]["failure_reason"], "no_target_value")

        self.assertTrue(by_id["open-meteo"]["attempted"])
        self.assertTrue(by_id["open-meteo"]["available"])
        self.assertEqual(by_id["open-meteo"]["value_f"], 79.0)
        mock_noaa.assert_called_once()
        mock_om.assert_called_once()

    @mock.patch.object(weather_sources, "fetch_threshold_forecasts")
    @mock.patch.object(weather_scanner.api, "get_events")
    def test_scan_uses_shared_provider_layer_for_us_markets(self, mock_get_events, mock_fetch_sources):
        mock_get_events.side_effect = [
            [_market_event("Will Atlanta temperatures reach 82°F or higher tomorrow?")],
            [],
        ]
        mock_fetch_sources.return_value = [
            {
                "source_id": "noaa",
                "attempted": True,
                "available": True,
                "value_f": 85.0,
            },
            {
                "source_id": "open-meteo",
                "attempted": True,
                "available": True,
                "value_f": 84.0,
            },
        ]

        opportunities, meta = weather_scanner.scan(verbose=False)

        self.assertEqual(meta["fetch_errors"], {"noaa": 0, "om": 0})
        self.assertEqual(len(opportunities), 1)
        opp = opportunities[0]
        self.assertEqual(opp["noaa_forecast_f"], 85.0)
        self.assertEqual(opp["om_forecast_f"], 84.0)
        self.assertTrue(opp["sources_agree"])
        self.assertEqual(opp["sources_available"], 2)
        self.assertTrue(opp["tradeable"])
        self.assertEqual([item["source_id"] for item in opp["source_details"]], ["noaa", "open-meteo"])
        mock_fetch_sources.assert_called_once()

    @mock.patch.object(weather_sources, "fetch_threshold_forecasts")
    @mock.patch.object(weather_scanner.api, "get_events")
    def test_scan_keeps_single_source_international_markets_non_tradeable(self, mock_get_events, mock_fetch_sources):
        mock_get_events.side_effect = [
            [_market_event("Will London temperatures exceed 70°F tomorrow?")],
            [],
        ]
        mock_fetch_sources.return_value = [
            {
                "source_id": "noaa",
                "attempted": False,
                "available": False,
                "value_f": None,
            },
            {
                "source_id": "open-meteo",
                "attempted": True,
                "available": True,
                "value_f": 78.0,
            },
        ]

        opportunities, meta = weather_scanner.scan(verbose=False)

        self.assertEqual(meta["fetch_errors"], {"noaa": 0, "om": 0})
        self.assertEqual(len(opportunities), 1)
        opp = opportunities[0]
        self.assertEqual(opp["sources_available"], 1)
        self.assertFalse(opp["sources_agree"])
        self.assertFalse(opp["tradeable"])
        self.assertEqual(opp["action"], "BUY_YES")


if __name__ == "__main__":
    unittest.main()
