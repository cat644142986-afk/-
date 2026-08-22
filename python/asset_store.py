# -*- coding: utf-8 -*-
"""Content-addressed, local-only source asset storage for Product Atelier."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import threading
import uuid
import warnings
from pathlib import Path
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

try:
    from atelier_ledger import AtelierLedger
except ImportError:  # Allows importing as python.asset_store during tests.
    from python.atelier_ledger import AtelierLedger


DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_PIXELS = 100_000_000
STREAM_CHUNK_BYTES = 1024 * 1024

FORMAT_INFO = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
EXTENSION_FORMATS = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".webp": "WEBP",
}


class AssetStoreError(RuntimeError):
    code = "ASSET_STORE_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = code or self.code


class AssetValidationError(AssetStoreError):
    code = "INVALID_ASSET"


class AssetAccessError(AssetStoreError):
    code = "ASSET_ACCESS_DENIED"


class AssetStore:
    def __init__(
        self,
        root: str | Path,
        ledger: AtelierLedger,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        max_pixels: int = DEFAULT_MAX_PIXELS,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_pixels = max(1, int(max_pixels))
        self._hash_locks_guard = threading.Lock()
        self._hash_locks: dict[str, threading.Lock] = {}

    def _hash_lock(self, sha256: str) -> threading.Lock:
        with self._hash_locks_guard:
            return self._hash_locks.setdefault(sha256, threading.Lock())

    def _spool_and_hash(self, stream: BinaryIO) -> tuple[tempfile.SpooledTemporaryFile, str, int]:
        spool = tempfile.SpooledTemporaryFile(max_size=min(self.max_file_bytes, 8 * 1024 * 1024), mode="w+b")
        hasher = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = stream.read(STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                if not isinstance(chunk, (bytes, bytearray)):
                    raise AssetValidationError("Asset stream returned non-binary data", code="INVALID_STREAM")
                size += len(chunk)
                if size > self.max_file_bytes:
                    raise AssetValidationError(
                        f"Image exceeds {self.max_file_bytes} byte limit",
                        code="FILE_TOO_LARGE",
                    )
                hasher.update(chunk)
                spool.write(chunk)
            if size == 0:
                raise AssetValidationError("Image file is empty", code="EMPTY_FILE")
            spool.seek(0)
            return spool, hasher.hexdigest(), size
        except Exception:
            spool.close()
            raise

    def _inspect_image(self, spool: BinaryIO, original_name: str) -> tuple[str, str, str, int, int]:
        declared_extension = Path(original_name or "").suffix.lower()
        declared_format = EXTENSION_FORMATS.get(declared_extension)
        if declared_format is None:
            raise AssetValidationError(
                "Only JPG, PNG, and WEBP source images are supported",
                code="UNSUPPORTED_EXTENSION",
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                spool.seek(0)
                with Image.open(spool) as image:
                    detected_format = str(image.format or "").upper()
                    width, height = image.size
                    image.verify()
                if detected_format not in FORMAT_INFO:
                    raise AssetValidationError(
                        f"Unsupported image format: {detected_format or 'unknown'}",
                        code="UNSUPPORTED_IMAGE_FORMAT",
                    )
                if declared_format != detected_format:
                    raise AssetValidationError(
                        f"File extension does not match detected {detected_format} content",
                        code="EXTENSION_MISMATCH",
                    )
                if width <= 0 or height <= 0 or width * height > self.max_pixels:
                    raise AssetValidationError(
                        f"Image dimensions exceed {self.max_pixels} pixel limit",
                        code="PIXEL_LIMIT_EXCEEDED",
                    )
                spool.seek(0)
                with Image.open(spool) as image:
                    image.load()
        except AssetValidationError:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise AssetValidationError("Image is corrupt or cannot be decoded", code="INVALID_IMAGE") from exc
        finally:
            spool.seek(0)
        mime, canonical_extension = FORMAT_INFO[detected_format]
        return detected_format, mime, canonical_extension, width, height

    def _expected_path(self, sha256: str, extension: str) -> Path:
        path = self.root / sha256[:2] / f"{sha256}{extension}"
        resolved_root = self.root.resolve()
        if not path.resolve().is_relative_to(resolved_root):
            raise AssetAccessError("Resolved asset path escaped the asset root")
        return path

    @staticmethod
    def _file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(STREAM_CHUNK_BYTES):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _write_atomic(self, spool: BinaryIO, final_path: Path, sha256: str) -> bool:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if final_path.exists():
            if not final_path.is_file() or self._file_sha256(final_path) != sha256:
                raise AssetStoreError(
                    "Existing content-addressed path has unexpected data",
                    code="STORAGE_CONFLICT",
                )
            return False

        temp_path = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            spool.seek(0)
            with temp_path.open("xb") as handle:
                while chunk := spool.read(STREAM_CHUNK_BYTES):
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, final_path)
            return True
        finally:
            temp_path.unlink(missing_ok=True)

    def import_stream(
        self,
        stream: BinaryIO,
        original_name: str,
        collection_key: str = "product",
    ) -> dict:
        spool, sha256, size_bytes = self._spool_and_hash(stream)
        final_path: Path | None = None
        created_file = False
        try:
            _, mime, extension, width, height = self._inspect_image(spool, original_name)
            final_path = self._expected_path(sha256, extension)
            with self._hash_lock(sha256):
                existing = self.ledger.find_workspace_asset_by_sha256(sha256)
                if existing is not None:
                    existing_path = Path(existing["blob"]["storage_path"])
                    if existing_path != final_path:
                        raise AssetStoreError(
                            "Stored asset metadata does not match its content-addressed path",
                            code="STORAGE_METADATA_CONFLICT",
                        )
                    if final_path.exists() and self._file_sha256(final_path) == sha256:
                        return self.ledger.add_asset_to_collection(
                            existing["id"], collection_key
                        )

                created_file = self._write_atomic(spool, final_path, sha256)
                try:
                    return self.ledger.register_workspace_asset(
                        sha256=sha256,
                        storage_path=str(final_path),
                        mime=mime,
                        size_bytes=size_bytes,
                        width=width,
                        height=height,
                        name=Path(original_name).name,
                        metadata={"original_name": Path(original_name).name},
                        collection_key=collection_key,
                    )
                except Exception:
                    if created_file:
                        # A failed metadata commit must never leave a file that
                        # looks like a valid workspace asset. If the database is
                        # still readable, preserve the file only when another
                        # concurrent importer committed the same blob. If even
                        # that check fails, this importer owns the freshly
                        # published file and rolls it back conservatively.
                        keep_file = False
                        try:
                            keep_file = self.ledger.has_asset_blob(sha256)
                        except Exception:
                            keep_file = False
                        if not keep_file:
                            final_path.unlink(missing_ok=True)
                    raise
        finally:
            spool.close()

    def import_bytes(
        self,
        data: bytes,
        original_name: str,
        collection_key: str = "product",
    ) -> dict:
        return self.import_stream(io.BytesIO(data), original_name, collection_key)

    def purge_asset(self, asset_id: str, *, retention_days: int = 30) -> dict:
        """Purge eligible metadata first, then remove its now-unreferenced blob file."""
        summary = self.ledger.asset_reference_summary(
            asset_id, retention_days=retention_days
        )
        raw_path = Path(str(summary["storage_path"]))
        candidate = raw_path.resolve(strict=False)
        if not candidate.is_relative_to(self.root.resolve()):
            raise AssetAccessError("Workspace asset path is outside the allowed root")
        result = self.ledger.purge_workspace_asset(
            asset_id, retention_days=retention_days
        )
        file_deleted = False
        file_error = ""
        if result["blob_deleted"]:
            try:
                candidate.unlink(missing_ok=True)
                file_deleted = True
                try:
                    candidate.parent.rmdir()
                except OSError:
                    pass
            except OSError as exc:
                file_error = str(exc)
        return {**result, "file_deleted": file_deleted, "file_error": file_error}

    def resolve_asset_path(self, asset_id: str) -> tuple[dict, Path]:
        try:
            asset = self.ledger.get_workspace_asset(asset_id)
        except KeyError as exc:
            raise AssetAccessError("Unknown workspace asset", code="ASSET_NOT_FOUND") from exc
        raw_path = Path(str(asset["blob"]["storage_path"]))
        resolved_root = self.root.resolve()
        candidate_path = raw_path.resolve(strict=False)
        if not candidate_path.is_relative_to(resolved_root):
            raise AssetAccessError("Workspace asset path is outside the allowed root")
        try:
            resolved_path = raw_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise AssetAccessError("Workspace asset file is unavailable", code="ASSET_FILE_MISSING") from exc
        if not resolved_path.is_relative_to(resolved_root) or not resolved_path.is_file():
            raise AssetAccessError("Workspace asset path is outside the allowed root")
        if self._file_sha256(resolved_path) != asset["blob"]["sha256"]:
            raise AssetAccessError("Workspace asset content hash does not match", code="ASSET_HASH_MISMATCH")
        return asset, resolved_path

    def thumbnail_bytes(self, asset_id: str, max_size: int = 512) -> bytes:
        _, path = self.resolve_asset_path(asset_id)
        max_size = max(32, min(int(max_size), 1024))
        with Image.open(path) as image:
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, "white")
                alpha = image.getchannel("A")
                background.paste(image.convert("RGB"), mask=alpha)
                image = background
            elif image.mode != "RGB":
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=86, optimize=True)
            return buffer.getvalue()
