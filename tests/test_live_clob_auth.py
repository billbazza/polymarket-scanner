import importlib
import unittest
from unittest.mock import patch

from py_clob_client.clob_types import ApiCreds


class _FakeClobClient:
    def __init__(self, host, key, chain_id):
        self.host = host
        self.key = key
        self.chain_id = chain_id
        self.creds = None
        self.derive_calls = 0
        self.create_calls = 0

    def derive_api_key(self):
        self.derive_calls += 1
        return ApiCreds(
            api_key="derived-key",
            api_secret="derived-secret",
            api_passphrase="derived-passphrase",
        )

    def create_api_key(self):
        self.create_calls += 1
        return ApiCreds(
            api_key="created-key",
            api_secret="created-secret",
            api_passphrase="created-passphrase",
        )

    def set_api_creds(self, creds):
        self.creds = creds


class _ExplodingDeriveClient(_FakeClobClient):
    def derive_api_key(self):
        self.derive_calls += 1
        raise RuntimeError("derive failed")

    def create_api_key(self):
        self.create_calls += 1
        raise RuntimeError("create failed")


class LiveClobAuthTests(unittest.TestCase):
    def setUp(self):
        import execution

        self.execution = importlib.reload(execution)

    def _runtime_get(self, values):
        def fake_get(name, default=""):
            if name in values:
                return values[name]
            return default
        return fake_get

    def test_create_live_clob_client_uses_explicit_api_credentials_when_configured(self):
        values = {
            "POLYMARKET_PRIVATE_KEY": "0xabc",
            "POLYMARKET_CLOB_API_KEY": "api-key",
            "POLYMARKET_CLOB_API_SECRET": "api-secret",
            "POLYMARKET_CLOB_API_PASSPHRASE": "api-passphrase",
        }

        with patch.object(self.execution.runtime_config, "get", side_effect=self._runtime_get(values)), \
             patch("py_clob_client.client.ClobClient", _FakeClobClient):
            client, error = self.execution._create_live_clob_client("penny", blocker_source="penny-execution")

        self.assertIsNone(error)
        self.assertIsNotNone(client)
        self.assertEqual(client.derive_calls, 0)
        self.assertEqual(client.create_calls, 0)
        self.assertEqual(client.creds.api_key, "api-key")
        self.assertEqual(client.creds.api_secret, "api-secret")
        self.assertEqual(client.creds.api_passphrase, "api-passphrase")

    def test_create_live_clob_client_derives_api_credentials_when_only_private_key_present(self):
        values = {
            "POLYMARKET_PRIVATE_KEY": "0xabc",
        }

        with patch.object(self.execution.runtime_config, "get", side_effect=self._runtime_get(values)), \
             patch("py_clob_client.client.ClobClient", _FakeClobClient):
            client, error = self.execution._create_live_clob_client("penny", blocker_source="penny-execution")

        self.assertIsNone(error)
        self.assertIsNotNone(client)
        self.assertEqual(client.derive_calls, 1)
        self.assertEqual(client.create_calls, 0)
        self.assertEqual(client.creds.api_key, "derived-key")

    def test_create_live_clob_client_reports_partial_explicit_credentials(self):
        values = {
            "POLYMARKET_PRIVATE_KEY": "0xabc",
            "POLYMARKET_CLOB_API_KEY": "api-key",
        }

        with patch.object(self.execution.runtime_config, "get", side_effect=self._runtime_get(values)), \
             patch("py_clob_client.client.ClobClient", _FakeClobClient):
            client, error = self.execution._create_live_clob_client("penny", blocker_source="penny-execution")

        self.assertIsNone(client)
        self.assertEqual(error["reason_code"], "clob_api_auth_unavailable")
        self.assertIn("Incomplete explicit Polymarket CLOB API credentials", error["error"])
        self.assertTrue(error["live_execution"]["auth"]["explicit_api_credentials_partial"])
        self.assertEqual(
            error["live_execution"]["auth"]["explicit_missing_fields"],
            ["api_secret", "api_passphrase"],
        )

    def test_create_live_clob_client_reports_derivation_failure(self):
        values = {
            "POLYMARKET_PRIVATE_KEY": "0xabc",
        }

        with patch.object(self.execution.runtime_config, "get", side_effect=self._runtime_get(values)), \
             patch("py_clob_client.client.ClobClient", _ExplodingDeriveClient):
            client, error = self.execution._create_live_clob_client("penny", blocker_source="penny-execution")

        self.assertIsNone(client)
        self.assertEqual(error["reason_code"], "clob_api_auth_unavailable")
        self.assertIn("could not derive an existing CLOB API key or create a new one", error["error"])
        self.assertEqual(error["live_execution"]["auth"]["credential_source"], "derivation_failed")
        self.assertEqual(error["live_execution"]["auth"]["derivation_error"], "derive failed")


if __name__ == "__main__":
    unittest.main()
