from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from python.credential_store import WindowsCredentialStore
from python.provider_catalog import (
    LK_API_BASE,
    LK_CONNECTION_ID,
    LK_PROVIDER,
    ProviderCatalogClient,
    ProviderCatalogError,
    ProviderCatalogStore,
    normalize_catalog,
)


class _FakeWin32Cred:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}

    def CredWrite(self, item, flags):
        assert flags == 0
        self.items[item["TargetName"]] = dict(item)

    def CredRead(self, target, credential_type, flags):
        assert credential_type == self.CRED_TYPE_GENERIC
        assert flags == 0
        if target not in self.items:
            error = RuntimeError("not found")
            error.winerror = 1168
            raise error
        return dict(self.items[target])

    def CredDelete(self, target, credential_type, flags):
        if target not in self.items:
            error = RuntimeError("not found")
            error.winerror = 1168
            raise error
        del self.items[target]


def fixture_transport(path: str, params=None):
    params = dict(params or {})
    if path == "/v1/models":
        return {"object": "list", "data": [
            {"id": "gpt-image-2", "object": "model", "owned_by": "lk"},
            {"id": "chat-only", "object": "model", "owned_by": "lk"},
        ]}
    if path == "/v1/media/models":
        media_type = params["type"]
        models = [{
            "name": "gpt-image-2" if media_type == "image" else f"{media_type}-one",
            "display_name": f"{media_type.title()} One",
            "type": media_type,
            "description": "fixture",
            "input_hint": "fixture input",
            "tags": [media_type],
            "params": [{"name": "size"}],
        }]
        return {"models": models, "total": len(models)}
    if path == "/v1/skills":
        return {
            "platform": "LK",
            "version": "1",
            "categories": [{
                "id": "models",
                "name": "模型查询",
                "endpoints": [{
                    "method": "GET",
                    "path": "/v1/skills/models",
                    "name": "获取模型列表",
                }],
            }],
        }
    if path == "/v1/skills/guide":
        return {"call_modes": ["chat", "media"]}
    if path == "/v1/skills/models":
        return {"models": [
            {
                "name": "gpt-image-2",
                "display_name": "GPT Image 2",
                "type": "image",
                "available_for_this_key": True,
                "description": "image model",
                "input_hint": "prompt and images",
                "tags": ["image"],
            },
            {
                "name": "blocked-model",
                "display_name": "Blocked",
                "type": "image",
                "available_for_this_key": False,
                "description": "",
                "input_hint": "",
                "tags": [],
            },
        ], "total": 2}
    if path == "/v1/skills/balance":
        return {"balance": 9.5, "unit": "CNY", "api_key_quota": 10}
    if path == "/v1/skills/usage":
        return {"scope": "api_key", "grand_total": 0, "unit": "CNY"}
    if path.endswith("/pricing"):
        return {
            "name": "gpt-image-2",
            "available_for_this_key": True,
            "key_channel_strategy": "auto",
            "channel_groups": [{
                "group_name": "default",
                "is_active": True,
                "base_price": 0.1,
                "billing_method": "per_call",
            }],
        }
    raise AssertionError(f"unexpected read-only endpoint: {path} {params}")


class CredentialStoreTests(unittest.TestCase):
    def test_windows_adapter_round_trips_without_exposing_secret_metadata(self) -> None:
        backend = _FakeWin32Cred()
        store = WindowsCredentialStore(namespace="ProductAtelier-Test")
        reference = store.secret_ref(LK_PROVIDER, LK_CONNECTION_ID)
        with mock.patch.object(store, "_win32cred", return_value=backend):
            stored = store.write_verified(reference, "fixture-secret", username=LK_PROVIDER)
            self.assertTrue(stored.fingerprint.startswith("sha256:"))
            self.assertNotIn("fixture-secret", repr(stored))
            self.assertEqual(store.read(reference), "fixture-secret")
            self.assertTrue(store.exists(reference))
            self.assertTrue(store.delete(reference))
            self.assertFalse(store.exists(reference))


class ProviderCatalogClientTests(unittest.TestCase):
    def test_sync_uses_only_read_only_catalog_endpoints(self) -> None:
        calls: list[tuple[str, dict]] = []

        def transport(path, params):
            calls.append((path, dict(params or {})))
            return fixture_transport(path, params)

        client = ProviderCatalogClient("fixture-secret", transport=transport)
        raw = client.fetch(pricing_workers=2)
        self.assertEqual(len(raw["pricing"]), 1)
        self.assertTrue(all(path.startswith("/v1/") for path, _ in calls))
        self.assertFalse(any("generate" in path for path, _ in calls))
        self.assertFalse(any("task-status" in path for path, _ in calls))
        self.assertEqual(
            sorted(params["type"] for path, params in calls if path == "/v1/media/models"),
            ["audio", "image", "video"],
        )
        with self.assertRaisesRegex(ProviderCatalogError, "non-catalog"):
            client.get_json("/v1/media/generate")

    def test_normalized_catalog_keeps_provider_claims_separate(self) -> None:
        client = ProviderCatalogClient("fixture-secret", transport=fixture_transport)
        raw = client.fetch(pricing_workers=1)
        normalized = normalize_catalog(raw, api_base=LK_API_BASE)
        self.assertEqual(normalized["counts"]["openai_models"], 2)
        self.assertEqual(normalized["counts"]["available_models"], 1)
        self.assertEqual(normalized["counts"]["media_models_total"], 3)
        self.assertEqual(normalized["counts"]["skill_categories"], 1)
        self.assertEqual(normalized["counts"]["pricing_models"], 1)
        self.assertNotIn("recommended", json.dumps(normalized))
        self.assertNotIn("default_model", json.dumps(normalized))
        gpt = next(
            item for item in normalized["models"]
            if item["provider_model_id"] == "gpt-image-2"
        )
        self.assertEqual(gpt["modalities"], ["image"])
        self.assertTrue(gpt["openai_compatible"])
        self.assertIsNotNone(gpt["pricing"])


class ProviderCatalogStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProviderCatalogStore(Path(self.temp.name) / "catalog.sqlite3")
        self.store.ensure_connection(
            connection_id=LK_CONNECTION_ID,
            provider=LK_PROVIDER,
            display_name="LK",
            auth_kind="api_key",
            secret_ref="ProductAtelier-Test/provider/lk/primary",
            api_base=LK_API_BASE,
        )
        self.store.set_credential(LK_CONNECTION_ID, "sha256:fixture")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_failure_keeps_last_snapshot_and_marks_it_stale(self) -> None:
        raw = ProviderCatalogClient("fixture", transport=fixture_transport).fetch(
            pricing_workers=1
        )
        normalized = normalize_catalog(raw, api_base=LK_API_BASE)
        first = self.store.record_snapshot(
            LK_CONNECTION_ID,
            raw_catalog=raw,
            normalized_catalog=normalized,
            endpoint_status={"status": "complete", "generation_calls": 0},
        )
        self.assertEqual(first["version"], 1)
        self.store.mark_sync_failure(
            LK_CONNECTION_ID,
            code="CATALOG_NETWORK_ERROR",
            message="The provider catalog could not be reached",
        )
        connection = self.store.get_connection(LK_CONNECTION_ID)
        self.assertEqual(connection["catalog_status"], "stale")
        self.assertEqual(connection["active_snapshot_id"], first["id"])
        latest = self.store.latest_snapshot(LK_CONNECTION_ID, include_raw=True)
        self.assertEqual(latest["raw_catalog_sha256"], first["raw_catalog_sha256"])
        self.assertNotIn("fixture", latest["raw_catalog_json"] if "raw_catalog_json" in latest else "")

    def test_snapshots_are_versioned_immutable_and_contain_no_secret_column(self) -> None:
        raw = ProviderCatalogClient("fixture", transport=fixture_transport).fetch(
            pricing_workers=1
        )
        normalized = normalize_catalog(raw, api_base=LK_API_BASE)
        first = self.store.record_snapshot(
            LK_CONNECTION_ID,
            raw_catalog=raw,
            normalized_catalog=normalized,
            endpoint_status={"status": "complete"},
        )
        second = self.store.record_snapshot(
            LK_CONNECTION_ID,
            raw_catalog=raw,
            normalized_catalog=normalized,
            endpoint_status={"status": "complete"},
        )
        self.assertEqual((first["version"], second["version"]), (1, 2))
        with self.store._connection() as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(provider_connections)")
            }
            self.assertFalse({"api_key", "token", "secret"}.intersection(columns))
            with self.assertRaises(Exception):
                connection.execute(
                    "UPDATE provider_catalog_snapshots SET version=99 WHERE id=?",
                    (first["id"],),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
