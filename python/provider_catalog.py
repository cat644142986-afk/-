"""Read-only provider connection and catalog synchronization.

The provider catalog is deliberately separate from PA's capability/evidence
overlay and from routing policy.  Provider claims are normalized here; they do
not become verified Product Atelier capabilities merely by appearing in a
remote response.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

import requests


CATALOG_SCHEMA_VERSION = 1
STORE_SCHEMA_VERSION = 1
LK_PROVIDER = "lk-ai-model-center"
LK_CONNECTION_ID = "provider_lk_primary"
LK_API_BASE = "https://api.lk888.ai/api"
READ_ONLY_PATHS = frozenset({
    "/v1/models",
    "/v1/media/models",
    "/v1/skills",
    "/v1/skills/guide",
    "/v1/skills/models",
    "/v1/skills/balance",
    "/v1/skills/usage",
})
_SENSITIVE_RESPONSE_KEYS = frozenset({
    "api_key", "access_token", "refresh_token", "authorization",
    "credential", "credentials", "secret", "password",
})


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def redact_provider_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if str(key).strip().lower() in _SENSITIVE_RESPONSE_KEYS
                else redact_provider_payload(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_provider_payload(item) for item in value]
    return value


class ProviderCatalogError(RuntimeError):
    def __init__(self, code: str, message: str, *, endpoint: str = ""):
        super().__init__(message)
        self.code = code
        self.endpoint = endpoint


class ProviderCatalogClient:
    """LK read-only API adapter with a fixed endpoint allowlist."""

    def __init__(
        self,
        api_key: str,
        *,
        api_base: str = LK_API_BASE,
        timeout: float = 25,
        transport: Callable[[str, Mapping[str, str] | None], Any] | None = None,
    ) -> None:
        value = str(api_key or "").strip()
        if not value:
            raise ValueError("provider API key is required")
        self._api_key = value
        self.api_base = str(api_base or LK_API_BASE).rstrip("/")
        self.timeout = max(5.0, min(float(timeout), 60.0))
        self._transport = transport
        self._local = threading.local()

    def _session(self, *, direct: bool = False) -> requests.Session:
        key = "direct_session" if direct else "session"
        session = getattr(self._local, key, None)
        if session is None:
            session = requests.Session()
            session.trust_env = not direct
            session.headers.update({"User-Agent": "ProductAtelier-CatalogSync/1.0"})
            try:
                import certifi

                session.verify = certifi.where()
            except Exception:
                session.verify = True
            setattr(self._local, key, session)
        return session

    @staticmethod
    def _allow_path(path: str) -> bool:
        if path in READ_ONLY_PATHS:
            return True
        return path.startswith("/v1/skills/models/") and path.endswith("/pricing")

    def get_json(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> Any:
        if not self._allow_path(path):
            raise ProviderCatalogError(
                "CATALOG_ENDPOINT_NOT_ALLOWED",
                "Provider catalog sync attempted a non-catalog endpoint",
                endpoint=path,
            )
        if self._transport is not None:
            return redact_provider_payload(self._transport(path, params))
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = self.api_base + path
        try:
            response = self._session().get(
                url, params=dict(params or {}), headers=headers, timeout=self.timeout
            )
        except (
            requests.exceptions.ProxyError,
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
        ):
            try:
                response = self._session(direct=True).get(
                    url, params=dict(params or {}), headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                raise ProviderCatalogError(
                    "CATALOG_NETWORK_ERROR",
                    "The provider catalog could not be reached",
                    endpoint=path,
                ) from exc
        except requests.RequestException as exc:
            raise ProviderCatalogError(
                "CATALOG_NETWORK_ERROR",
                "The provider catalog could not be reached",
                endpoint=path,
            ) from exc
        if not response.ok:
            raise ProviderCatalogError(
                f"CATALOG_HTTP_{response.status_code}",
                f"Provider catalog endpoint returned HTTP {response.status_code}",
                endpoint=path,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderCatalogError(
                "CATALOG_INVALID_JSON",
                "Provider catalog endpoint returned invalid JSON",
                endpoint=path,
            ) from exc
        return redact_provider_payload(payload)

    def _pricing(self, model_name: str) -> tuple[str, Any]:
        encoded = quote(str(model_name), safe="")
        path = f"/v1/skills/models/{encoded}/pricing"
        return str(model_name), self.get_json(path)

    def fetch(self, *, pricing_workers: int = 4) -> dict[str, Any]:
        raw: dict[str, Any] = {
            "openai_models": self.get_json("/v1/models"),
            "media_models": {
                media_type: self.get_json(
                    "/v1/media/models", {"type": media_type}
                )
                for media_type in ("image", "video", "audio")
            },
            "skills": self.get_json("/v1/skills"),
            "skill_guide": self.get_json("/v1/skills/guide"),
            "skill_models": self.get_json("/v1/skills/models"),
            "balance": self.get_json("/v1/skills/balance"),
            "usage": self.get_json("/v1/skills/usage"),
        }
        skill_items = _list_at(raw["skill_models"], "models")
        names = sorted({
            str(item.get("name") or "").strip()
            for item in skill_items
            if isinstance(item, Mapping)
            and item.get("available_for_this_key") is not False
            and str(item.get("name") or "").strip()
        })
        pricing: dict[str, Any] = {}
        errors: dict[str, str] = {}
        workers = max(1, min(int(pricing_workers), 6))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self._pricing, name): name for name in names}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    model_name, payload = future.result()
                    pricing[model_name] = payload
                except ProviderCatalogError as exc:
                    errors[name] = exc.code
        if errors:
            raise ProviderCatalogError(
                "CATALOG_PRICING_INCOMPLETE",
                f"Pricing was unavailable for {len(errors)} catalog model(s)",
                endpoint="/v1/skills/models/{model_name}/pricing",
            )
        raw["pricing"] = pricing
        return raw


def _list_at(value: Any, key: str) -> list[Any]:
    if isinstance(value, Mapping):
        items = value.get(key)
        if isinstance(items, list):
            return items
        data = value.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, Mapping) and isinstance(data.get(key), list):
            return data[key]
    return value if isinstance(value, list) else []


def _normalize_pricing(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    groups = value.get("channel_groups")
    if not isinstance(groups, list):
        groups = []
    normalized_groups = []
    allowed = (
        "group_name", "is_active", "in_key_whitelist",
        "excluded_for_this_key", "billing_method", "base_price",
        "current_time_discount", "min_price", "input_token_price",
        "output_token_price", "input_image_price", "input_image_free_count",
        "with_video_output_token_price", "reference_video_price_per_second",
        "success_rate_24h", "avg_response_seconds", "option_prices",
    )
    for group in groups:
        if isinstance(group, Mapping):
            normalized_groups.append({key: group.get(key) for key in allowed if key in group})
    return {
        "available_for_this_key": value.get("available_for_this_key"),
        "key_channel_strategy": value.get("key_channel_strategy"),
        "pricing_note": value.get("pricing_note"),
        "channel_groups": normalized_groups,
    }


def normalize_catalog(raw_catalog: Mapping[str, Any], *, api_base: str) -> dict[str, Any]:
    raw = redact_provider_payload(dict(raw_catalog))
    openai_items = _list_at(raw.get("openai_models"), "data")
    skill_items = _list_at(raw.get("skill_models"), "models")
    media_by_type = raw.get("media_models")
    media_by_type = media_by_type if isinstance(media_by_type, Mapping) else {}
    pricing = raw.get("pricing")
    pricing = pricing if isinstance(pricing, Mapping) else {}

    records: dict[str, dict[str, Any]] = {}

    def record(model_id: str) -> dict[str, Any]:
        return records.setdefault(model_id, {
            "provider_model_id": model_id,
            "display_name": model_id,
            "modalities": [],
            "available_for_this_key": None,
            "openai_compatible": False,
            "description": "",
            "input_hint": "",
            "tags": [],
            "provider_parameters": [],
            "pricing": None,
            "source_catalogs": [],
        })

    for item in openai_items:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        target = record(model_id)
        target["openai_compatible"] = True
        target["source_catalogs"].append("v1/models")

    for item in skill_items:
        if not isinstance(item, Mapping):
            continue
        model_id = str(item.get("name") or "").strip()
        if not model_id:
            continue
        target = record(model_id)
        model_type = str(item.get("type") or "").strip().lower()
        if model_type and model_type not in target["modalities"]:
            target["modalities"].append(model_type)
        target["display_name"] = str(item.get("display_name") or model_id)
        target["available_for_this_key"] = item.get("available_for_this_key")
        target["description"] = str(item.get("description") or "")
        target["input_hint"] = str(item.get("input_hint") or "")
        target["tags"] = list(item.get("tags") or [])
        target["source_catalogs"].append("v1/skills/models")

    media_counts: dict[str, int] = {}
    for media_type in ("image", "video", "audio"):
        items = _list_at(media_by_type.get(media_type), "models")
        media_counts[media_type] = len(items)
        for item in items:
            if not isinstance(item, Mapping):
                continue
            model_id = str(item.get("name") or "").strip()
            if not model_id:
                continue
            target = record(model_id)
            if media_type not in target["modalities"]:
                target["modalities"].append(media_type)
            target["display_name"] = str(item.get("display_name") or target["display_name"])
            target["description"] = str(item.get("description") or target["description"])
            target["input_hint"] = str(item.get("input_hint") or target["input_hint"])
            target["tags"] = list(item.get("tags") or target["tags"])
            target["provider_parameters"] = list(item.get("params") or [])
            target["source_catalogs"].append(f"v1/media/models?type={media_type}")

    for model_id, value in pricing.items():
        if model_id in records:
            records[model_id]["pricing"] = _normalize_pricing(value)

    for item in records.values():
        item["modalities"] = sorted(set(item["modalities"]))
        item["source_catalogs"] = sorted(set(item["source_catalogs"]))

    skills_payload = raw.get("skills")
    categories = (
        skills_payload.get("categories", [])
        if isinstance(skills_payload, Mapping)
        else []
    )
    normalized_skills = []
    endpoint_count = 0
    for category in categories:
        if not isinstance(category, Mapping):
            continue
        endpoints = []
        for endpoint in category.get("endpoints", []):
            if not isinstance(endpoint, Mapping):
                continue
            endpoints.append({
                key: endpoint.get(key)
                for key in ("method", "path", "name", "description")
                if key in endpoint
            })
        endpoint_count += len(endpoints)
        normalized_skills.append({
            "id": category.get("id"),
            "name": category.get("name"),
            "endpoints": endpoints,
        })

    model_list = sorted(records.values(), key=lambda item: item["provider_model_id"])
    available_count = sum(
        1 for item in model_list if item["available_for_this_key"] is True
    )
    return {
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "provider": LK_PROVIDER,
        "api_base": str(api_base).rstrip("/"),
        "models": model_list,
        "skills": normalized_skills,
        "skill_guide": raw.get("skill_guide", {}),
        "account": {
            "balance": raw.get("balance", {}),
            "usage": raw.get("usage", {}),
        },
        "counts": {
            "openai_models": len(openai_items),
            "catalog_models": len(model_list),
            "available_models": available_count,
            "media_models": media_counts,
            "media_models_total": sum(media_counts.values()),
            "skill_categories": len(normalized_skills),
            "skill_endpoints": endpoint_count,
            "pricing_models": len(pricing),
        },
    }


class ProviderCatalogStore:
    """Small SQLite store for provider metadata; never stores credentials."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=20, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 20000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                with connection:
                    connection.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS catalog_meta (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS provider_connections (
                            id TEXT PRIMARY KEY,
                            provider TEXT NOT NULL,
                            display_name TEXT NOT NULL,
                            auth_kind TEXT NOT NULL,
                            secret_ref TEXT NOT NULL UNIQUE,
                            credential_fingerprint TEXT NOT NULL DEFAULT '',
                            api_base TEXT NOT NULL,
                            connection_status TEXT NOT NULL,
                            catalog_status TEXT NOT NULL,
                            active_snapshot_id TEXT,
                            last_handshake_at TEXT,
                            last_sync_attempt_at TEXT,
                            last_sync_success_at TEXT,
                            last_error_code TEXT,
                            last_error_message TEXT,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        CREATE TABLE IF NOT EXISTS provider_catalog_snapshots (
                            id TEXT PRIMARY KEY,
                            connection_id TEXT NOT NULL,
                            version INTEGER NOT NULL,
                            catalog_schema_version INTEGER NOT NULL,
                            raw_catalog_json TEXT NOT NULL,
                            raw_catalog_sha256 TEXT NOT NULL,
                            normalized_catalog_json TEXT NOT NULL,
                            normalized_catalog_sha256 TEXT NOT NULL,
                            endpoint_status_json TEXT NOT NULL,
                            fetched_at TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            FOREIGN KEY(connection_id) REFERENCES provider_connections(id),
                            UNIQUE(connection_id, version)
                        );
                        CREATE INDEX IF NOT EXISTS idx_catalog_snapshots_connection
                        ON provider_catalog_snapshots(connection_id, version DESC);
                        CREATE TRIGGER IF NOT EXISTS trg_catalog_snapshots_no_update
                        BEFORE UPDATE ON provider_catalog_snapshots
                        BEGIN
                            SELECT RAISE(ABORT, 'provider catalog snapshots are immutable');
                        END;
                        CREATE TRIGGER IF NOT EXISTS trg_catalog_snapshots_no_delete
                        BEFORE DELETE ON provider_catalog_snapshots
                        BEGIN
                            SELECT RAISE(ABORT, 'provider catalog snapshots are immutable');
                        END;
                        """
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO catalog_meta(key, value) VALUES('schema_version', ?)",
                        (str(STORE_SCHEMA_VERSION),),
                    )
                    row = connection.execute(
                        "SELECT value FROM catalog_meta WHERE key='schema_version'"
                    ).fetchone()
                    if row is None or int(row[0]) != STORE_SCHEMA_VERSION:
                        raise RuntimeError("unsupported provider catalog store schema")
            finally:
                connection.close()

    def ensure_connection(
        self,
        *,
        connection_id: str,
        provider: str,
        display_name: str,
        auth_kind: str,
        secret_ref: str,
        api_base: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO provider_connections(
                    id, provider, display_name, auth_kind, secret_ref, api_base,
                    connection_status, catalog_status, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, 'disconnected', 'unavailable', ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider=excluded.provider,
                    display_name=excluded.display_name,
                    auth_kind=excluded.auth_kind,
                    secret_ref=excluded.secret_ref,
                    api_base=excluded.api_base,
                    updated_at=excluded.updated_at
                """,
                (
                    connection_id, provider, display_name, auth_kind,
                    secret_ref, str(api_base).rstrip("/"), now, now,
                ),
            )
        return self.get_connection(connection_id)

    def set_credential(self, connection_id: str, fingerprint: str) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE provider_connections
                SET credential_fingerprint=?, connection_status='configured',
                    last_error_code=NULL, last_error_message=NULL, updated_at=?
                WHERE id=?
                """,
                (str(fingerprint), now, connection_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown provider connection: {connection_id}")
        return self.get_connection(connection_id)

    def disconnect(self, connection_id: str) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE provider_connections
                SET credential_fingerprint='', connection_status='disconnected',
                    catalog_status=CASE WHEN active_snapshot_id IS NULL
                        THEN 'unavailable' ELSE 'stale' END,
                    last_error_code=NULL, last_error_message=NULL, updated_at=?
                WHERE id=?
                """,
                (now, connection_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown provider connection: {connection_id}")
        return self.get_connection(connection_id)

    def mark_sync_failure(
        self, connection_id: str, *, code: str, message: str
    ) -> dict[str, Any]:
        now = utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE provider_connections
                SET connection_status=CASE WHEN credential_fingerprint=''
                        THEN 'disconnected' ELSE 'error' END,
                    catalog_status=CASE WHEN active_snapshot_id IS NULL
                        THEN 'unavailable' ELSE 'stale' END,
                    last_sync_attempt_at=?, last_error_code=?,
                    last_error_message=?, updated_at=?
                WHERE id=?
                """,
                (now, str(code), str(message)[:300], now, connection_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"unknown provider connection: {connection_id}")
        return self.get_connection(connection_id)

    def record_snapshot(
        self,
        connection_id: str,
        *,
        raw_catalog: Mapping[str, Any],
        normalized_catalog: Mapping[str, Any],
        endpoint_status: Mapping[str, Any],
        fetched_at: str | None = None,
    ) -> dict[str, Any]:
        raw_value = redact_provider_payload(dict(raw_catalog))
        normalized_value = dict(normalized_catalog)
        raw_json = canonical_json(raw_value)
        normalized_json = canonical_json(normalized_value)
        raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        normalized_hash = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
        now = utc_now()
        fetched = str(fetched_at or now)
        snapshot_id = f"catalog_{uuid.uuid4().hex}"
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM provider_catalog_snapshots WHERE connection_id=?",
                    (connection_id,),
                ).fetchone()
                version = int(row[0]) + 1
                connection.execute(
                    """
                    INSERT INTO provider_catalog_snapshots(
                        id, connection_id, version, catalog_schema_version,
                        raw_catalog_json, raw_catalog_sha256,
                        normalized_catalog_json, normalized_catalog_sha256,
                        endpoint_status_json, fetched_at, created_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id, connection_id, version, CATALOG_SCHEMA_VERSION,
                        raw_json, raw_hash, normalized_json, normalized_hash,
                        canonical_json(dict(endpoint_status)), fetched, now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE provider_connections
                    SET active_snapshot_id=?, connection_status='connected',
                        catalog_status='fresh', last_handshake_at=?,
                        last_sync_attempt_at=?, last_sync_success_at=?,
                        last_error_code=NULL, last_error_message=NULL, updated_at=?
                    WHERE id=?
                    """,
                    (snapshot_id, fetched, fetched, fetched, now, connection_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
        return self.get_snapshot(snapshot_id, include_raw=False)

    def get_connection(self, connection_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_connections WHERE id=?", (connection_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown provider connection: {connection_id}")
        return dict(row)

    def list_connections(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_connections ORDER BY created_at, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_snapshot(self, snapshot_id: str, *, include_raw: bool = False) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM provider_catalog_snapshots WHERE id=?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown provider catalog snapshot: {snapshot_id}")
        item = dict(row)
        item["normalized_catalog"] = json.loads(item.pop("normalized_catalog_json"))
        item["endpoint_status"] = json.loads(item.pop("endpoint_status_json"))
        raw_json = item.pop("raw_catalog_json")
        if include_raw:
            item["raw_catalog"] = json.loads(raw_json)
        return item

    def latest_snapshot(
        self, connection_id: str, *, include_raw: bool = False
    ) -> dict[str, Any] | None:
        connection_item = self.get_connection(connection_id)
        snapshot_id = connection_item.get("active_snapshot_id")
        if not snapshot_id:
            return None
        return self.get_snapshot(str(snapshot_id), include_raw=include_raw)

    def stats(self) -> dict[str, int]:
        with self._connection() as connection:
            connections = connection.execute(
                "SELECT COUNT(*) FROM provider_connections"
            ).fetchone()[0]
            snapshots = connection.execute(
                "SELECT COUNT(*) FROM provider_catalog_snapshots"
            ).fetchone()[0]
        return {"connections": int(connections), "snapshots": int(snapshots)}


def synchronize_catalog(
    *,
    store: ProviderCatalogStore,
    connection_id: str,
    api_key: str,
    pricing_workers: int = 4,
) -> dict[str, Any]:
    connection = store.get_connection(connection_id)
    client = ProviderCatalogClient(
        api_key,
        api_base=str(connection["api_base"]),
    )
    started = time.monotonic()
    try:
        raw = client.fetch(pricing_workers=pricing_workers)
        normalized = normalize_catalog(raw, api_base=str(connection["api_base"]))
        endpoint_status = {
            "status": "complete",
            "read_only": True,
            "generation_calls": 0,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
        return store.record_snapshot(
            connection_id,
            raw_catalog=raw,
            normalized_catalog=normalized,
            endpoint_status=endpoint_status,
        )
    except ProviderCatalogError as exc:
        store.mark_sync_failure(
            connection_id,
            code=exc.code,
            message=str(exc),
        )
        raise
