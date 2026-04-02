import importlib
import os
import unittest
from unittest import mock


class BrainProviderMigrationTests(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "BRAIN_PROVIDER": os.environ.get("BRAIN_PROVIDER"),
            "BRAIN_OPENAI_MODEL": os.environ.get("BRAIN_OPENAI_MODEL"),
            "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
        }

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

    def test_auto_provider_prefers_anthropic_then_openai(self):
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["OPENAI_API_KEY"] = "openai-test"
        os.environ["BRAIN_PROVIDER"] = "auto"

        brain = importlib.reload(self.brain)

        self.assertEqual(
            brain._available_provider_order(),
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI],
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
            return_value=('{"trade": true, "reasoning": "fallback worked"}', "gpt-5-mini"),
        ):
            response = brain._brain_request("prompt", model=brain.DEFAULT_MODEL, max_tokens=200)

        self.assertEqual(response["provider"], brain.PROVIDER_OPENAI)
        self.assertEqual(response["model"], "gpt-5-mini")

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
        os.environ["BRAIN_PROVIDER"] = "auto"
        os.environ["OPENAI_BASE_URL"] = "https://api.openai.com/v1"

        brain = importlib.reload(self.brain)

        with mock.patch.object(brain, "_get_provider_client", side_effect=lambda provider: object()):
            status = brain.get_runtime_status()

        self.assertEqual(status["mode"], brain.PROVIDER_AUTO)
        self.assertEqual(
            status["configured_order"],
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI],
        )
        self.assertEqual(
            status["client_ready_order"],
            [brain.PROVIDER_ANTHROPIC, brain.PROVIDER_OPENAI],
        )
        self.assertTrue(status["fallback_enabled"])
        self.assertEqual(
            status["providers"][brain.PROVIDER_OPENAI]["base_url"],
            "https://api.openai.com/v1",
        )

    def test_pinned_provider_with_missing_key_keeps_graceful_degradation(self):
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["ANTHROPIC_API_KEY"] = "ant-test"
        os.environ["BRAIN_PROVIDER"] = "openai"

        brain = importlib.reload(self.brain)

        self.assertEqual(brain._available_provider_order(), [])
        should_trade, reasoning = brain.validate_signal({"event": "Pinned provider missing"})
        self.assertTrue(should_trade)
        self.assertIn("defaulting to statistical signal", reasoning)


if __name__ == "__main__":
    unittest.main()
