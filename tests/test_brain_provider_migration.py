import importlib
import os
import unittest
from unittest import mock

import runtime_config


class BrainProviderMigrationTests(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "XAI_API_KEY": os.environ.get("XAI_API_KEY"),
            "BRAIN_PROVIDER": os.environ.get("BRAIN_PROVIDER"),
            "BRAIN_ANTHROPIC_MODEL": os.environ.get("BRAIN_ANTHROPIC_MODEL"),
            "BRAIN_ANTHROPIC_COMPLEX_MODEL": os.environ.get("BRAIN_ANTHROPIC_COMPLEX_MODEL"),
            "BRAIN_OPENAI_MODEL": os.environ.get("BRAIN_OPENAI_MODEL"),
            "BRAIN_OPENAI_COMPLEX_MODEL": os.environ.get("BRAIN_OPENAI_COMPLEX_MODEL"),
            "BRAIN_XAI_MODEL": os.environ.get("BRAIN_XAI_MODEL"),
            "BRAIN_XAI_COMPLEX_MODEL": os.environ.get("BRAIN_XAI_COMPLEX_MODEL"),
            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
            "XAI_BASE_URL": os.environ.get("XAI_BASE_URL"),
            "SCANNER_KEYCHAIN_SERVICE": os.environ.get("SCANNER_KEYCHAIN_SERVICE"),
        }
        for key in self.original_env:
            os.environ.pop(key, None)
        os.environ["SCANNER_KEYCHAIN_SERVICE"] = "__brain_provider_migration_tests__"
        runtime_config.clear_cache()

        import brain

        self.brain = importlib.reload(brain)

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        import brain

        importlib.reload(brain)
        runtime_config.clear_cache()

    def test_auto_provider_prefers_anthropic_then_openai(self):
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        self.assertEqual(
            brain._available_provider_order(),
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI],
        )

    def test_auto_provider_appends_xai_fallback(self):
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["XAI_API_KEY"] = "xai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        self.assertEqual(
            brain._available_provider_order(),
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI, brain.PROVIDER_XAI],
        )

    def test_auto_provider_uses_openai_when_anthropic_key_missing(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        self.assertEqual(brain._available_provider_order(), [brain.PROVIDER_OPENAI])

    def test_validate_signal_defaults_to_trade_when_no_provider_available(self):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        should_trade, reasoning = brain.validate_signal({"event": "No provider"})

        self.assertTrue(should_trade)
        self.assertIn("defaulting to statistical signal", reasoning)

    def test_brain_request_falls_back_from_anthropic_to_openai_on_credit_error(self):
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        with mock.patch.object(
            brain,
            "_get_client_candidates",
            return_value=[
                {"provider": brain.PROVIDER_ANTHROPIC, "client": object()},
                {"provider": brain.PROVIDER_OPENAI, "client": object()},
            ],
        ), mock.patch.object(
            brain,
            "_anthropic_message_text",
            side_effect=RuntimeError("Anthropic credit balance is too low"),
        ), mock.patch.object(
            brain,
            "_openai_message_text",
            return_value=('{"trade": true, "reasoning": "fallback worked"}', "gpt-5-mini", None),
        ):
            response = brain._brain_request("prompt", model=brain.DEFAULT_MODEL, max_tokens=200)

        self.assertEqual(response["provider"], brain.PROVIDER_OPENAI)
        self.assertEqual(response["model"], "gpt-5-mini")
        with mock.patch.object(brain, "_get_provider_client", side_effect=lambda provider: object()):
            status = brain.get_runtime_status()
        self.assertEqual(status["active_provider"], brain.PROVIDER_OPENAI)
        self.assertEqual(status["active_provider_source"], "last_success")
        self.assertEqual(
            status["providers"][brain.PROVIDER_ANTHROPIC]["availability"],
            "credits_exhausted",
        )
        self.assertEqual(
            status["last_fallback"]["from_provider"],
            brain.PROVIDER_ANTHROPIC,
        )
        self.assertEqual(
            status["last_fallback"]["to_provider"],
            brain.PROVIDER_OPENAI,
        )
        self.assertEqual(
            status["last_fallback"]["reason_kind"],
            "credits_exhausted",
        )

    def test_brain_request_falls_back_from_openai_to_xai_on_credit_error(self):
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["XAI_API_KEY"] = "xai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        with mock.patch.object(
            brain,
            "_get_client_candidates",
            return_value=[
                {"provider": brain.PROVIDER_OPENAI, "client": object()},
                {"provider": brain.PROVIDER_XAI, "client": object()},
            ],
        ), mock.patch.object(
            brain,
            "_openai_message_text",
            side_effect=RuntimeError("OpenAI credit balance is too low"),
        ), mock.patch.object(
            brain,
            "_xai_message_text",
            return_value=('{"trade": true, "reasoning": "grok fallback"}', "grok-4", None),
        ):
            response = brain._brain_request("prompt", model=brain.DEFAULT_MODEL, max_tokens=200)

        self.assertEqual(response["provider"], brain.PROVIDER_XAI)
        self.assertEqual(response["model"], "grok-4")

    def test_model_aliases_are_resolved_from_runtime_env(self):
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "openai"
        os.environ["BRAIN_OPENAI_MODEL"] = "gpt-5-custom-cutover"

        brain = importlib.reload(self.brain)

        self.assertEqual(
            brain._resolve_model_candidates(brain.PROVIDER_OPENAI, brain.DEFAULT_MODEL),
            ["gpt-5-custom-cutover"],
        )

    def test_runtime_status_reports_cutover_readiness(self):
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["XAI_API_KEY"] = "xai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"
        os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"
        os.environ["XAI_BASE_URL"] = "https://api.x.ai/v1"

        brain = importlib.reload(self.brain)

        with mock.patch.object(brain, "_get_provider_client", side_effect=lambda provider: object()):
            status = brain.get_runtime_status()

        self.assertEqual(status["mode"], brain.PROVIDER_AUTO)
        self.assertEqual(
            status["configured_order"],
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI, brain.PROVIDER_XAI],
        )
        self.assertEqual(
            status["client_ready_order"],
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI, brain.PROVIDER_XAI],
        )
        self.assertTrue(status["fallback_enabled"])
        self.assertEqual(status["active_provider"], brain.PROVIDER_ANTHROPIC)
        self.assertEqual(status["active_provider_source"], "configured_preference")
        self.assertEqual(
            status["providers"][brain.PROVIDER_OPENAI]["base_url"],
            "https://api.openai.com/v1",
        )
        self.assertEqual(
            status["providers"][brain.PROVIDER_XAI]["base_url"],
            "https://api.x.ai/v1",
        )
        self.assertEqual(
            status["providers"][brain.PROVIDER_ANTHROPIC]["availability"],
            "active",
        )
        self.assertEqual(
            status["providers"][brain.PROVIDER_OPENAI]["availability"],
            "available",
        )

    def test_runtime_status_tracks_observed_quota_headers(self):
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "openai"

        brain = importlib.reload(self.brain)

        with mock.patch.object(
            brain,
            "_get_client_candidates",
            return_value=[{"provider": brain.PROVIDER_OPENAI, "client": object()}],
        ), mock.patch.object(
            brain,
            "_openai_message_text",
            return_value=(
                '{"trade": true, "reasoning": "ok"}',
                "gpt-5-mini",
                {
                    "x-ratelimit-remaining-requests": "17",
                    "x-ratelimit-remaining-tokens": "9100",
                    "x-ratelimit-limit-requests": "100",
                    "x-ratelimit-reset-requests": "42s",
                    "x-request-id": "req_openai_123",
                },
            ),
        ):
            brain._brain_request("prompt", model=brain.DEFAULT_MODEL, max_tokens=200)

        with mock.patch.object(brain, "_get_provider_client", side_effect=lambda provider: object()):
            status = brain.get_runtime_status()

        provider_status = status["providers"][brain.PROVIDER_OPENAI]
        self.assertEqual(provider_status["availability"], "active")
        self.assertEqual(provider_status["last_request_id"], "req_openai_123")
        self.assertEqual(provider_status["quota_observation"]["requests_remaining"], 17)
        self.assertEqual(provider_status["quota_observation"]["tokens_remaining"], 9100)
        self.assertEqual(provider_status["quota_observation"]["requests_limit"], 100)
        self.assertEqual(provider_status["quota_observation"]["requests_reset"], "42s")
        self.assertIsNone(provider_status["credit_observation"])

    def test_pinned_provider_with_missing_key_keeps_graceful_degradation(self):
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["BRAIN_PROVIDER"] = "openai"

        brain = importlib.reload(self.brain)

        self.assertEqual(brain._available_provider_order(), [])
        should_trade, reasoning = brain.validate_signal({"event": "Pinned provider missing"})
        self.assertTrue(should_trade)
        self.assertIn("defaulting to statistical signal", reasoning)

    def test_validate_signal_extracts_json_from_wrapped_response(self):
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "openai"

        brain = importlib.reload(self.brain)

        with mock.patch.object(
            brain,
            "_brain_request",
            return_value={
                "provider": brain.PROVIDER_OPENAI,
                "model": "gpt-5-mini",
                "text": 'Here is the decision:\n{"trade": false, "reasoning": "wrapped json", "risk_flags": []}',
            },
        ):
            should_trade, reasoning = brain.validate_signal({"event": "Wrapped response"})

        self.assertFalse(should_trade)
        self.assertEqual(reasoning, "wrapped json")

    def test_validate_signal_handles_malformed_json_with_explicit_parity_fallback(self):
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "openai"

        brain = importlib.reload(self.brain)

        with mock.patch.object(
            brain,
            "_brain_request",
            return_value={
                "provider": brain.PROVIDER_OPENAI,
                "model": "gpt-5-mini",
                "text": '{"trade": true, "reasoning": "unterminated}',
            },
        ), mock.patch.object(brain.log, "warning") as warning_log:
            should_trade, reasoning = brain.validate_signal({"event": "Malformed response"})

        self.assertTrue(should_trade)
        self.assertIn("Brain advisory unavailable", reasoning)
        self.assertIn("malformed JSON", reasoning)
        self.assertIn("parity policy keeps the math-approved trade eligible", reasoning)
        self.assertTrue(warning_log.called)
        warning_message = warning_log.call_args[0][0] % warning_log.call_args[0][1:]
        self.assertIn("Brain validation advisory malformed", warning_message)
        self.assertIn("Unterminated string", warning_message)

    def test_blank_keychain_service_override_still_detects_default_service_provider_keys(self):
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "XAI_API_KEY", "BRAIN_PROVIDER"):
            os.environ.pop(name, None)
        os.environ["SCANNER_KEYCHAIN_SERVICE"] = "   "

        def fake_find(service, name):
            mapping = {
                (runtime_config.DEFAULT_KEYCHAIN_SERVICE, "ANTHROPIC_API_KEY"): "ant-key",
                (runtime_config.DEFAULT_KEYCHAIN_SERVICE, "OPENAI_API_KEY"): "openai-key",
                (runtime_config.DEFAULT_KEYCHAIN_SERVICE, "XAI_API_KEY"): "xai-key",
            }
            return mapping.get((service, name))

        with mock.patch.object(runtime_config, "_find_keychain_value", side_effect=fake_find):
            brain = importlib.reload(self.brain)

            self.assertEqual(
                brain._available_provider_order(),
                [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI, brain.PROVIDER_XAI],
            )

            with mock.patch.object(brain, "_get_provider_client", return_value=None), \
                 mock.patch.object(brain.log, "warning") as warning_log:
                brain._get_client_candidates()

            warning_messages = [
                call.args[0] % call.args[1:] if call.args else ""
                for call in warning_log.call_args_list
            ]
            self.assertNotIn("No brain provider configured — brain disabled", warning_messages)
            self.assertIn(
                "Brain provider clients unavailable for configured providers anthropic,openai,xai — brain disabled",
                warning_messages,
            )


if __name__ == "__main__":
    unittest.main()
