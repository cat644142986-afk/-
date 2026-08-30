from __future__ import annotations

import argparse
import base64
import hmac
import importlib.metadata
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PIL import Image

try:
    from grounding_runtime import GROUNDING_RUNTIME_TOKEN_ENV
    from semantic_grounding import (
        GroundingAdapterUnavailable,
        TransformersGroundingDinoAdapter,
    )
except ImportError:  # pragma: no cover - package imports used by source tests
    from python.grounding_runtime import GROUNDING_RUNTIME_TOKEN_ENV
    from python.semantic_grounding import (
        GroundingAdapterUnavailable,
        TransformersGroundingDinoAdapter,
    )


MAX_REQUEST_BYTES = 48 * 1024 * 1024


def runtime_probe(model_path: str | Path) -> dict[str, Any]:
    root = Path(model_path).expanduser().resolve()
    packages = {}
    for name in ("torch", "transformers", "safetensors", "Pillow"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "missing"
    try:
        import torch

        cuda = {
            "available": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        }
    except Exception:
        cuda = {"available": False, "device": "unavailable"}
    model_ready = root.is_dir() and (root / "config.json").is_file() and (root / "model.safetensors").is_file()
    return {
        "status": "ready" if model_ready and packages["torch"] != "missing" and packages["transformers"] != "missing" else "unavailable",
        "model_path": str(root),
        "model_ready": model_ready,
        "packages": packages,
        "cuda": cuda,
    }


def _parent_is_alive(parent_pid: int) -> bool:
    if parent_pid <= 0:
        return True
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(parent_pid, 0)
        return True
    except OSError:
        return False


class GroundingWorkerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        token: str,
        model_path: Path,
        runtime_id: str,
        contract_version: str,
    ) -> None:
        super().__init__(address, GroundingWorkerHandler)
        self.token = token
        self.runtime_id = runtime_id
        self.contract_version = contract_version
        self.adapter = TransformersGroundingDinoAdapter(model_path)


class GroundingWorkerHandler(BaseHTTPRequestHandler):
    server: GroundingWorkerServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _authorized(self) -> bool:
        supplied = str(self.headers.get("X-Product-Atelier-Worker-Token") or "")
        return bool(supplied) and hmac.compare_digest(supplied, self.server.token)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(403, {"status": "forbidden"})
            return
        if self.path != "/health":
            self._json(404, {"status": "not_found"})
            return
        self._json(200, {
            "status": "ok",
            "runtime_id": self.server.runtime_id,
            "contract_version": self.server.contract_version,
        })

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._json(403, {"status": "forbidden"})
            return
        if self.path != "/detect":
            self._json(404, {"status": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length < 2 or length > MAX_REQUEST_BYTES:
            self._json(413, {"status": "failed", "reason": "runtime_request_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            image_bytes = base64.b64decode(str(payload.get("image_base64") or ""), validate=True)
            if len(image_bytes) > MAX_REQUEST_BYTES:
                raise ValueError("decoded image is too large")
            with Image.open(io.BytesIO(image_bytes)) as opened:
                image = opened.convert("RGB")
            candidates = self.server.adapter.detect(
                image,
                str(payload.get("query") or ""),
                box_threshold=float(payload.get("box_threshold") or 0.4),
                text_threshold=float(payload.get("text_threshold") or 0.3),
            )
            self._json(200, {"status": "ok", "candidates": list(candidates)})
        except GroundingAdapterUnavailable as exc:
            self._json(503, {"status": "unavailable", "reason": str(exc) or "runtime_unavailable"})
        except Exception:
            self._json(500, {"status": "failed", "reason": "runtime_inference_failed"})


def _watch_parent(server: ThreadingHTTPServer, parent_pid: int) -> None:
    while _parent_is_alive(parent_pid):
        time.sleep(1.0)
    server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Product Atelier optional grounding runtime worker.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--runtime-id", default="source-runtime")
    parser.add_argument("--runtime-contract", default="source")
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args()
    if args.probe:
        print(json.dumps(runtime_probe(args.model_path), ensure_ascii=False))
        return 0
    token = str(os.environ.get(GROUNDING_RUNTIME_TOKEN_ENV, "") or "")
    if not token or args.port < 1 or args.port > 65535:
        return 2
    server = GroundingWorkerServer(
        ("127.0.0.1", args.port),
        token=token,
        model_path=args.model_path.resolve(),
        runtime_id=str(args.runtime_id),
        contract_version=str(args.runtime_contract),
    )
    if args.parent_pid:
        threading.Thread(
            target=_watch_parent,
            args=(server, args.parent_pid),
            daemon=True,
        ).start()
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
