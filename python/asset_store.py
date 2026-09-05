# -*- coding: utf-8 -*-
"""Content-addressed, local-only source asset storage for Product Atelier."""

from __future__ import annotations

import hashlib
import io
import ntpath
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
DEFAULT_MAX_VIDEO_FILE_BYTES = 512 * 1024 * 1024
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
VIDEO_FORMAT_INFO = {
    ".mp4": ("video/mp4", ".mp4"),
    ".webm": ("video/webm", ".webm"),
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
        max_video_file_bytes: int = DEFAULT_MAX_VIDEO_FILE_BYTES,
        max_pixels: int = DEFAULT_MAX_PIXELS,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Resolve the trusted root once after it exists. Re-resolving the root
        # concurrently with creation of its content-addressed child directory
        # can transiently produce different Windows path representations and a
        # false "escaped root" result for duplicate imports.
        self._resolved_root = self.root.resolve(strict=True)
        self._comparison_root = self._comparison_key(self._resolved_root)
        self.ledger = ledger
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_video_file_bytes = max(1, int(max_video_file_bytes))
        self.max_pixels = max(1, int(max_pixels))
        self._hash_locks_guard = threading.Lock()
        self._hash_locks: dict[str, threading.Lock] = {}

    def _hash_lock(self, sha256: str) -> threading.Lock:
        with self._hash_locks_guard:
            return self._hash_locks.setdefault(sha256, threading.Lock())

    @staticmethod
    def _comparison_key(path: Path) -> str:
        """Return a stable key for an already-resolved path.

        Windows may return the same path as either ``C:\\...`` or
        ``\\\\?\\C:\\...`` while another thread creates the final directory.
        The namespace prefix changes ``Path.is_relative_to`` semantics even
        though both names address the same file. Strip only the documented
        Windows extended namespace after resolution, then compare with the
        platform path module.
        """
        value = str(path)
        if os.name == "nt":
            if value.startswith("\\\\?\\UNC\\"):
                value = "\\\\" + value[8:]
            elif value.startswith("\\\\?\\"):
                value = value[4:]
            return ntpath.normcase(ntpath.normpath(value))
        return os.path.normcase(os.path.normpath(value))

    def _is_within_root(self, path: Path, *, strict: bool) -> bool:
        try:
            candidate_key = self._comparison_key(path.resolve(strict=strict))
            path_module = ntpath if os.name == "nt" else os.path
            return path_module.commonpath(
                [self._comparison_root, candidate_key]
            ) == self._comparison_root
        except (FileNotFoundError, OSError, ValueError):
            return False

    def _spool_and_hash(
        self,
        stream: BinaryIO,
        *,
        max_file_bytes: int | None = None,
        label: str = "Image",
    ) -> tuple[tempfile.SpooledTemporaryFile, str, int]:
        limit = self.max_file_bytes if max_file_bytes is None else max(1, int(max_file_bytes))
        spool = tempfile.SpooledTemporaryFile(max_size=min(limit, 8 * 1024 * 1024), mode="w+b")
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
                if size > limit:
                    raise AssetValidationError(
                        f"{label} exceeds {limit} byte limit",
                        code="FILE_TOO_LARGE",
                    )
                hasher.update(chunk)
                spool.write(chunk)
            if size == 0:
                raise AssetValidationError(f"{label} file is empty", code="EMPTY_FILE")
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
        path = self._resolved_root / sha256[:2] / f"{sha256}{extension}"
        if not self._is_within_root(path, strict=False):
            raise AssetAccessError("Resolved asset path escaped the asset root")
        return path

    def _expected_video_cover_path(self, video_sha256: str, extension: str) -> Path:
        path = self._resolved_root / video_sha256[:2] / f"{video_sha256}.cover{extension}"
        if not self._is_within_root(path, strict=False):
            raise AssetAccessError("Resolved video cover path escaped the asset root")
        return path

    @staticmethod
    def _inspect_video_container(spool: BinaryIO, original_name: str) -> tuple[str, str]:
        extension = Path(original_name or "").suffix.lower()
        format_info = VIDEO_FORMAT_INFO.get(extension)
        if format_info is None:
            raise AssetValidationError(
                "Only MP4 and WebM source videos are supported",
                code="UNSUPPORTED_EXTENSION",
            )
        spool.seek(0)
        header = spool.read(64)
        spool.seek(0)
        valid = (
            extension == ".webm" and header.startswith(b"\x1aE\xdf\xa3")
        ) or (
            extension == ".mp4" and len(header) >= 12 and header[4:8] == b"ftyp"
        )
        if not valid:
            raise AssetValidationError(
                "Video extension does not match a supported container",
                code="INVALID_VIDEO_CONTAINER",
            )
        return format_info

    def _video_cover_path(self, asset: dict) -> Path:
        metadata = asset.get("metadata") if isinstance(asset.get("metadata"), dict) else {}
        raw_path = Path(str(metadata.get("cover_storage_path") or ""))
        expected_sha256 = str(metadata.get("cover_sha256") or "")
        if len(expected_sha256) != 64 or not raw_path.is_absolute():
            raise AssetAccessError("Workspace video cover metadata is invalid", code="ASSET_FILE_MISSING")
        try:
            resolved = raw_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise AssetAccessError("Workspace video cover is unavailable", code="ASSET_FILE_MISSING") from exc
        if not self._is_within_root(resolved, strict=True) or not resolved.is_file():
            raise AssetAccessError("Workspace video cover escaped the asset root")
        if self._file_sha256(resolved) != expected_sha256:
            raise AssetAccessError("Workspace video cover hash does not match", code="ASSET_HASH_MISMATCH")
        return resolved

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

    def import_video_stream(
        self,
        stream: BinaryIO,
        original_name: str,
        cover_stream: BinaryIO,
        cover_name: str,
        *,
        width: int,
        height: int,
        duration_seconds: float,
        collection_key: str = "product",
    ) -> dict:
        try:
            width = int(width)
            height = int(height)
            duration_seconds = float(duration_seconds)
        except (TypeError, ValueError) as exc:
            raise AssetValidationError("Video metadata is invalid", code="INVALID_VIDEO_METADATA") from exc
        if width <= 0 or height <= 0 or width * height > self.max_pixels:
            raise AssetValidationError("Video dimensions exceed the supported limit", code="PIXEL_LIMIT_EXCEEDED")
        if not 0 < duration_seconds <= 3600:
            raise AssetValidationError("Video duration must be between 0 and 3600 seconds", code="INVALID_VIDEO_METADATA")

        video_spool, video_sha256, size_bytes = self._spool_and_hash(
            stream,
            max_file_bytes=self.max_video_file_bytes,
            label="Video",
        )
        cover_spool: tempfile.SpooledTemporaryFile | None = None
        video_path: Path | None = None
        cover_path: Path | None = None
        created_video = False
        created_cover = False
        try:
            mime, extension = self._inspect_video_container(video_spool, original_name)
            cover_spool, cover_sha256, _ = self._spool_and_hash(
                cover_stream,
                max_file_bytes=self.max_file_bytes,
                label="Video cover",
            )
            _, _, cover_extension, _, _ = self._inspect_image(cover_spool, cover_name)
            video_path = self._expected_path(video_sha256, extension)
            cover_path = self._expected_video_cover_path(video_sha256, cover_extension)
            with self._hash_lock(video_sha256):
                existing = self.ledger.find_workspace_asset_by_sha256(video_sha256)
                if existing is not None:
                    existing_path = Path(existing["blob"]["storage_path"])
                    if (
                        str(existing.get("kind") or "") != "video"
                        or existing_path != video_path
                        or not video_path.exists()
                        or self._file_sha256(video_path) != video_sha256
                    ):
                        raise AssetStoreError(
                            "Stored video metadata does not match its content-addressed path",
                            code="STORAGE_METADATA_CONFLICT",
                        )
                    self._video_cover_path(existing)
                    return self.ledger.add_asset_to_collection(existing["id"], collection_key)

                created_video = self._write_atomic(video_spool, video_path, video_sha256)
                try:
                    created_cover = self._write_atomic(cover_spool, cover_path, cover_sha256)
                    return self.ledger.register_workspace_asset(
                        sha256=video_sha256,
                        storage_path=str(video_path),
                        mime=mime,
                        size_bytes=size_bytes,
                        width=width,
                        height=height,
                        name=Path(original_name).name,
                        kind="video",
                        metadata={
                            "original_name": Path(original_name).name,
                            "duration_seconds": round(duration_seconds, 3),
                            "cover_storage_path": str(cover_path),
                            "cover_sha256": cover_sha256,
                        },
                        collection_key=collection_key,
                    )
                except Exception:
                    if created_video:
                        keep_video = False
                        try:
                            keep_video = self.ledger.has_asset_blob(video_sha256)
                        except Exception:
                            keep_video = False
                        if not keep_video:
                            video_path.unlink(missing_ok=True)
                    if created_cover:
                        cover_path.unlink(missing_ok=True)
                    raise
        finally:
            video_spool.close()
            if cover_spool is not None:
                cover_spool.close()

    def purge_asset(self, asset_id: str, *, retention_days: int = 30) -> dict:
        """Purge eligible metadata first, then remove its now-unreferenced blob file."""
        summary = self.ledger.asset_reference_summary(
            asset_id, retention_days=retention_days
        )
        raw_path = Path(str(summary["storage_path"]))
        candidate = raw_path.resolve(strict=False)
        if not self._is_within_root(candidate, strict=False):
            raise AssetAccessError("Workspace asset path is outside the allowed root")
        cover_path: Path | None = None
        asset = self.ledger.get_workspace_asset(asset_id)
        if str(asset.get("kind") or "") == "video":
            try:
                cover_path = self._video_cover_path(asset)
            except AssetStoreError:
                cover_path = None
        result = self.ledger.purge_workspace_asset(
            asset_id, retention_days=retention_days
        )
        file_deleted = False
        file_error = ""
        if result["blob_deleted"]:
            try:
                candidate.unlink(missing_ok=True)
                if cover_path is not None:
                    cover_path.unlink(missing_ok=True)
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
        candidate_path = raw_path.resolve(strict=False)
        if not self._is_within_root(candidate_path, strict=False):
            raise AssetAccessError("Workspace asset path is outside the allowed root")
        try:
            resolved_path = raw_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise AssetAccessError("Workspace asset file is unavailable", code="ASSET_FILE_MISSING") from exc
        if not self._is_within_root(resolved_path, strict=True) or not resolved_path.is_file():
            raise AssetAccessError("Workspace asset path is outside the allowed root")
        if self._file_sha256(resolved_path) != asset["blob"]["sha256"]:
            raise AssetAccessError("Workspace asset content hash does not match", code="ASSET_HASH_MISMATCH")
        return asset, resolved_path

    def thumbnail_bytes(self, asset_id: str, max_size: int = 512) -> bytes:
        asset, path = self.resolve_asset_path(asset_id)
        if str(asset.get("kind") or "") == "video":
            path = self._video_cover_path(asset)
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
