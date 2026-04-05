import os
import unittest
from unittest import mock

import runtime_config


class RuntimeConfigTests(unittest.TestCase):
    def setUp(self):
        self.original_env = {
            "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
            "FEATURE_FLAG": os.environ.get("FEATURE_FLAG"),
            "SCANNER_KEYCHAIN_SERVICE": os.environ.get("SCANNER_KEYCHAIN_SERVICE"),
        }
        runtime_config.clear_cache()

    def tearDown(self):
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        runtime_config.clear_cache()

    def test_env_override_beats_keychain(self):
        os.environ["OPENAI_API_KEY"] = "env-openai"
        with mock.patch.object(runtime_config, "_find_keychain_value", return_value="keychain-openai"):
            self.assertEqual(runtime_config.get("OPENAI_API_KEY"), "env-openai")

    def test_keychain_value_used_when_env_missing(self):
        os.environ.pop("OPENAI_API_KEY", None)
        with mock.patch.object(runtime_config, "_find_keychain_value", return_value="keychain-openai"):
            self.assertEqual(runtime_config.get("OPENAI_API_KEY"), "keychain-openai")

    def test_blank_keychain_service_override_falls_back_to_default(self):
        os.environ["SCANNER_KEYCHAIN_SERVICE"] = "   "

        self.assertEqual(
            runtime_config.keychain_service_name(),
            runtime_config.DEFAULT_KEYCHAIN_SERVICE,
        )

    def test_get_uses_default_service_when_override_blank(self):
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ["SCANNER_KEYCHAIN_SERVICE"] = "   "

        def fake_find(service, name):
            if service == runtime_config.DEFAULT_KEYCHAIN_SERVICE and name == "OPENAI_API_KEY":
                return "keychain-openai"
            return None

        with mock.patch.object(runtime_config, "_find_keychain_value", side_effect=fake_find):
            self.assertEqual(runtime_config.get("OPENAI_API_KEY"), "keychain-openai")

    def test_bool_parser_defaults_when_missing(self):
        os.environ.pop("FEATURE_FLAG", None)
        with mock.patch.object(runtime_config, "_find_keychain_value", return_value=None):
            self.assertTrue(runtime_config.get_bool("FEATURE_FLAG", default=True))
            self.assertFalse(runtime_config.get_bool("FEATURE_FLAG", default=False))


if __name__ == "__main__":
    unittest.main()
