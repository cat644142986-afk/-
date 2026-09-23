"""Provider secrets backed by the Windows Credential Manager.

Only opaque credential references and one-way fingerprints may leave this
module.  Provider secrets must never be persisted in Product Atelier JSON,
SQLite, logs, traces, or receipts.
"""

from __future__ import annotations

import hashlib
import hmac
import sys
from dataclasses import dataclass
from typing import Any


class CredentialStoreError(RuntimeError):
    """A safe, non-secret-bearing credential operation failure."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def credential_fingerprint(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        raise ValueError("credential secret is empty")
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


@dataclass(frozen=True)
class StoredCredential:
    secret_ref: str
    fingerprint: str


class WindowsCredentialStore:
    """Thin adapter over the operating system's Generic Credential API."""

    def __init__(self, *, namespace: str = "ProductAtelier") -> None:
        self.namespace = str(namespace or "ProductAtelier").strip().strip("/")

    @staticmethod
    def _win32cred() -> Any:
        if sys.platform != "win32":
            raise CredentialStoreError(
                "CREDENTIAL_STORE_UNAVAILABLE",
                "Windows Credential Manager is unavailable on this platform",
            )
        try:
            import win32cred
        except ImportError as exc:
            raise CredentialStoreError(
                "CREDENTIAL_STORE_UNAVAILABLE",
                "Windows Credential Manager support is not installed",
            ) from exc
        return win32cred

    def secret_ref(self, provider: str, connection_id: str) -> str:
        provider_key = str(provider or "").strip().lower()
        connection_key = str(connection_id or "").strip()
        if not provider_key or not connection_key:
            raise ValueError("provider and connection_id are required")
        return f"{self.namespace}/provider/{provider_key}/{connection_key}"

    def write(
        self,
        secret_ref: str,
        secret: str,
        *,
        username: str = "provider-api-key",
    ) -> StoredCredential:
        value = str(secret or "").strip()
        if not value:
            raise ValueError("credential secret is empty")
        win32cred = self._win32cred()
        try:
            win32cred.CredWrite(
                {
                    "Type": win32cred.CRED_TYPE_GENERIC,
                    "TargetName": str(secret_ref),
                    "UserName": str(username),
                    "CredentialBlob": value,
                    "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
                    "Comment": "Product Atelier provider credential",
                },
                0,
            )
        except Exception as exc:
            raise CredentialStoreError(
                "CREDENTIAL_WRITE_FAILED",
                "Could not store the provider credential in Windows Credential Manager",
            ) from exc
        return StoredCredential(
            secret_ref=str(secret_ref),
            fingerprint=credential_fingerprint(value),
        )

    def write_verified(
        self,
        secret_ref: str,
        secret: str,
        *,
        username: str = "provider-api-key",
    ) -> StoredCredential:
        """Write and read back the secret before reporting durable success."""
        stored = self.write(secret_ref, secret, username=username)
        read_back = self.read(secret_ref)
        if read_back is None or not hmac.compare_digest(
            read_back.encode("utf-8"), str(secret).strip().encode("utf-8")
        ):
            try:
                self.delete(secret_ref)
            except CredentialStoreError:
                pass
            raise CredentialStoreError(
                "CREDENTIAL_VERIFY_FAILED",
                "Windows Credential Manager did not return the stored provider credential",
            )
        return stored

    def read(self, secret_ref: str) -> str | None:
        win32cred = self._win32cred()
        try:
            item = win32cred.CredRead(
                str(secret_ref), win32cred.CRED_TYPE_GENERIC, 0
            )
        except Exception as exc:
            not_found = getattr(exc, "winerror", None) == 1168
            if not_found:
                return None
            raise CredentialStoreError(
                "CREDENTIAL_READ_FAILED",
                "Could not read the provider credential from Windows Credential Manager",
            ) from exc
        blob = item.get("CredentialBlob")
        if isinstance(blob, bytes):
            try:
                value = blob.decode("utf-16-le")
            except UnicodeDecodeError:
                value = blob.decode("utf-8")
        else:
            value = str(blob or "")
        return value.strip() or None

    def exists(self, secret_ref: str) -> bool:
        return self.read(secret_ref) is not None

    def delete(self, secret_ref: str) -> bool:
        win32cred = self._win32cred()
        try:
            win32cred.CredDelete(
                str(secret_ref), win32cred.CRED_TYPE_GENERIC, 0
            )
            return True
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1168:
                return False
            raise CredentialStoreError(
                "CREDENTIAL_DELETE_FAILED",
                "Could not remove the provider credential from Windows Credential Manager",
            ) from exc
