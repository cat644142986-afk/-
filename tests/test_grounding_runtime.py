from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from PIL import Image

from python.grounding_runtime import (
    GROUNDING_RUNTIME_CONTRACT_VERSION,
    ExternalGroundingWorkerAdapter,
    grounding_pack_status,
    verify_model_pack,
    verify_runtime_pack,
)
from python.model_artifacts import verify_artifact, write_local_receipt


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _lock(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _write_model_fixture(root: Path, artifact_id: str = "fixture-model") -> Path:
    model_root = root / "model"
    model_root.mkdir()
    files = {
        "config.json": b"{}",
        "model.safetensors": b"fixture-safe-weights",
    }
    for relative, content in files.items():
        (model_root / relative).write_bytes(content)
    manifest = {
        "schema_version": "1.0",
        "artifact_id": artifact_id,
        "purpose": "unit test",
        "source": {
            "provider": "fixture",
            "repo_id": "fixture/model",
            "revision": "a" * 40,
            "license": "apache-2.0",
        },
        "packaging_policy": {
            "distribution": "optional-external-pack",
            "development_only": False,
            "optional_external_pack": True,
            "include_in_formal_sidecar": False,
            "automatic_application_download": False,
            "storage": "external-directory-only",
        },
        "files": [_lock(path, content) for path, content in files.items()],
    }
    manifest_path = root / "model-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    verification = verify_artifact(model_root, manifest)
    write_local_receipt(model_root, verification)
    return manifest_path


def _write_runtime_fixture(root: Path, artifact_id: str = "fixture-model") -> Path:
    runtime_root = root / "runtime"
    runtime_root.mkdir()
    files = {
        "worker.bin": b"fixture-runtime-entrypoint",
        "_internal/empty-metadata": b"",
    }
    for relative, content in files.items():
        target = runtime_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    locks = [_lock(path, content) for path, content in files.items()]
    manifest = {
        "schema_version": "1.0",
        "runtime_id": "fixture-runtime",
        "contract_version": GROUNDING_RUNTIME_CONTRACT_VERSION,
        "platform": {
            "system": platform.system().lower(),
            "machine": platform.machine().lower(),
        },
        "entrypoint": locks[0],
        "supported_model_artifact_ids": [artifact_id],
        "files": locks,
    }
    (runtime_root / "grounding-runtime-manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    return runtime_root


FAKE_WORKER = r'''
import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

parser = argparse.ArgumentParser()
parser.add_argument('--serve', action='store_true')
parser.add_argument('--port', type=int, required=True)
parser.add_argument('--model-path')
parser.add_argument('--runtime-id', required=True)
parser.add_argument('--runtime-contract', required=True)
parser.add_argument('--parent-pid')
args = parser.parse_args()
token = os.environ['PRODUCT_ATELIER_GROUNDING_WORKER_TOKEN']

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return
    def authorized(self):
        supplied = self.headers.get('X-Product-Atelier-Worker-Token', '')
        return hmac.compare_digest(supplied, token)
    def reply(self, status, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        if not self.authorized():
            self.reply(403, {'status': 'forbidden'})
        else:
            self.reply(200, {'status': 'ok', 'runtime_id': args.runtime_id, 'contract_version': args.runtime_contract})
    def do_POST(self):
        if not self.authorized():
            self.reply(403, {'status': 'forbidden'})
            return
        length = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(length))
        self.reply(200, {'status': 'ok', 'candidates': [{'bbox_xyxy': [1, 2, 20, 18], 'confidence': 0.91, 'label': payload['query']}]})

ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
'''


class GroundingRuntimeTests(unittest.TestCase):
    def test_manifest_generator_locks_a_complete_candidate_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir) / "candidate"
            runtime_root.mkdir()
            entry_name = "grounding-runtime.exe" if sys.platform == "win32" else "grounding-runtime"
            (runtime_root / entry_name).write_bytes(b"fixture-entrypoint")
            internal = runtime_root / "_internal" / "fixture.dat"
            internal.parent.mkdir()
            internal.write_bytes(b"fixture-data")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "tools" / "build_grounding_runtime_manifest.py"),
                    "--runtime-root",
                    str(runtime_root),
                    "--project-root",
                    str(PROJECT_ROOT),
                    "--git-commit",
                    "b" * 40,
                    "--runtime-id",
                    "fixture-generated-runtime",
                    "--model-artifact-id",
                    "fixture-model",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads(
                (runtime_root / "grounding-runtime-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["source"]["git_commit"], "b" * 40)
            self.assertEqual({item["path"] for item in manifest["files"]}, {
                entry_name,
                "_internal/fixture.dat",
            })
            self.assertEqual(verify_runtime_pack(runtime_root, full=True)["status"], "verified")

    def test_runtime_pack_checks_complete_inventory_and_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            runtime_root = _write_runtime_fixture(root)
            self.assertEqual(verify_runtime_pack(runtime_root)["status"], "ready")
            self.assertEqual(verify_runtime_pack(runtime_root, full=True)["status"], "verified")

            entrypoint = runtime_root / "worker.bin"
            entrypoint.write_bytes(b"X" * entrypoint.stat().st_size)
            self.assertEqual(verify_runtime_pack(runtime_root)["status"], "ready")
            invalid = verify_runtime_pack(runtime_root, full=True)
            self.assertEqual(invalid["status"], "invalid")
            self.assertEqual(invalid["code"], "RUNTIME_FILES_INVALID")

    def test_runtime_pack_rejects_the_wrong_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = _write_runtime_fixture(Path(temp_dir))
            path = runtime_root / "grounding-runtime-manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["platform"]["system"] = "not-this-operating-system"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            status = verify_runtime_pack(runtime_root)
            self.assertEqual(status["status"], "incompatible")
            self.assertEqual(status["code"], "RUNTIME_SYSTEM_MISMATCH")

    def test_model_receipt_is_fast_but_execution_gate_rehashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = _write_model_fixture(root)
            model_root = root / "model"
            self.assertEqual(verify_model_pack(model_root, manifest_path)["status"], "ready")
            self.assertEqual(verify_model_pack(model_root, manifest_path, full=True)["status"], "verified")

            weights = model_root / "model.safetensors"
            weights.write_bytes(b"X" * weights.stat().st_size)
            self.assertEqual(verify_model_pack(model_root, manifest_path)["status"], "ready")
            self.assertEqual(verify_model_pack(model_root, manifest_path, full=True)["status"], "invalid")

    def test_matching_runtime_and_model_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = _write_model_fixture(root)
            runtime_root = _write_runtime_fixture(root)
            status = grounding_pack_status(runtime_root, root / "model", manifest_path)
            self.assertTrue(status["available"])
            self.assertEqual(status["runtime"]["runtime_id"], "fixture-runtime")

    def test_external_worker_protocol_keeps_detection_local_and_authenticated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest_path = _write_model_fixture(root)
            worker = root / "fake_worker.py"
            worker.write_text(textwrap.dedent(FAKE_WORKER), encoding="utf-8")
            adapter = ExternalGroundingWorkerAdapter(
                root / "unused-runtime",
                root / "model",
                manifest_path,
                command_override=[sys.executable, str(worker)],
                startup_timeout=10,
                request_timeout=10,
            )
            try:
                candidates = adapter.detect(
                    Image.new("RGB", (32, 24), "white"),
                    "wine glass",
                    box_threshold=0.4,
                    text_threshold=0.3,
                )
            finally:
                adapter.close()
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0]["label"], "wine glass")
            self.assertEqual(candidates[0]["confidence"], 0.91)


if __name__ == "__main__":
    unittest.main()
