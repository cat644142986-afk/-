from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["PRODUCT_ATELIER_DATA_DIR"] = MODULE_DATA_DIR.name
os.environ["PRODUCT_ATELIER_CREDENTIAL_NAMESPACE"] = "ProductAtelier-Migration-Test"
os.environ["PRODUCT_ATELIER_LEGACY_CONFIG"] = str(
    Path(MODULE_DATA_DIR.name) / "missing-legacy.json"
)
os.environ["PRODUCT_ATELIER_KNOWLEDGE_BASE"] = str(
    Path(MODULE_DATA_DIR.name) / "missing-vault"
)

from python import server  # noqa: E402
from python.credential_store import CredentialStoreError, StoredCredential, credential_fingerprint  # noqa: E402
from python.provider_catalog import (  # noqa: E402
    LK_API_BASE,
    LK_CONNECTION_ID,
    LK_PROVIDER,
    ProviderCatalogStore,
)


class _MemoryCredentialStore:
    def __init__(self, *, fail_verify: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.fail_verify = fail_verify

    def secret_ref(self, provider: str, connection_id: str) -> str:
        return f"test/{provider}/{connection_id}"

    def write_verified(self, secret_ref: str, secret: str, *, username: str):
        if self.fail_verify:
            raise CredentialStoreError(
                "CREDENTIAL_VERIFY_FAILED", "credential verification failed"
            )
        self.values[secret_ref] = secret
        return StoredCredential(secret_ref, credential_fingerprint(secret))

    def read(self, secret_ref: str):
        return self.values.get(secret_ref)

    def delete(self, secret_ref: str) -> bool:
        return self.values.pop(secret_ref, None) is not None


class ProviderCredentialMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config_path = root / "config.json"
        self.legacy_path = root / "legacy.json"
        self.catalog_path = root / "provider-catalog.sqlite3"
        self.store = ProviderCatalogStore(self.catalog_path)
        self.credentials = _MemoryCredentialStore()
        secret_ref = self.credentials.secret_ref(LK_PROVIDER, LK_CONNECTION_ID)
        self.store.ensure_connection(
            connection_id=LK_CONNECTION_ID,
            provider=LK_PROVIDER,
            display_name="LK",
            auth_kind="api_key",
            secret_ref=secret_ref,
            api_base=LK_API_BASE,
        )
        self.patches = [
            mock.patch.object(server, "CONFIG_PATH", self.config_path),
            mock.patch.object(server, "LEGACY_CONFIG", self.legacy_path),
            mock.patch.object(server, "PROVIDER_CATALOG_STORE", self.store),
            mock.patch.object(server, "CREDENTIAL_STORE", self.credentials),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self) -> None:
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temp.cleanup()

    def _complete_handshake(self) -> None:
        self.store.record_snapshot(
            LK_CONNECTION_ID,
            raw_catalog={"models": []},
            normalized_catalog={"counts": {"catalog_models": 0}},
            endpoint_status={"status": "complete", "generation_calls": 0},
        )

    def test_plaintext_is_retained_until_verified_handshake(self) -> None:
        secret = "fixture-migration-secret"
        self.config_path.write_text(
            json.dumps({"api_key": secret, "default_model": "gpt-image-2"}),
            encoding="utf-8",
        )
        self.assertEqual(server.load_api_key(), secret)
        self.assertEqual(self.credentials.read(self.store.get_connection(LK_CONNECTION_ID)["secret_ref"]), secret)
        self.assertIn("api_key", json.loads(self.config_path.read_text(encoding="utf-8")))
        self.assertEqual(server._credential_migration_summary()["status"], "pending")

        self._complete_handshake()
        summary = server._finalize_plaintext_credential_migration()
        self.assertEqual(summary["status"], "complete")
        persisted = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("api_key", persisted)
        self.assertEqual(persisted["default_model"], "gpt-image-2")
        self.assertNotIn(secret.encode("utf-8"), self.catalog_path.read_bytes())

    def test_failed_catalog_handshake_preserves_legacy_plaintext(self) -> None:
        secret = "fixture-legacy-secret"
        self.legacy_path.write_text(
            json.dumps({"api_key": secret, "other": "keep"}), encoding="utf-8"
        )
        self.assertEqual(server.load_api_key(), secret)
        self.store.mark_sync_failure(
            LK_CONNECTION_ID,
            code="CATALOG_NETWORK_ERROR",
            message="catalog unavailable",
        )
        summary = server._finalize_plaintext_credential_migration()
        self.assertEqual(summary["status"], "pending")
        persisted = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["api_key"], secret)
        self.assertEqual(persisted["other"], "keep")

    def test_failed_credential_readback_preserves_plaintext(self) -> None:
        secret = "fixture-verification-secret"
        self.config_path.write_text(json.dumps({"api_key": secret}), encoding="utf-8")
        failing = _MemoryCredentialStore(fail_verify=True)
        with mock.patch.object(server, "CREDENTIAL_STORE", failing):
            with self.assertRaises(CredentialStoreError):
                server.load_api_key()
        self.assertEqual(
            json.loads(self.config_path.read_text(encoding="utf-8"))["api_key"],
            secret,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

