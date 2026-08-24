from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path


# Importing python.server initializes its SQLite singleton. Isolate that import
# even when this test module is executed by itself (outside full discovery,
# where another API test may already have imported the server safely).
MODULE_DATA_DIR = tempfile.TemporaryDirectory()
os.environ["PRODUCT_ATELIER_DATA_DIR"] = MODULE_DATA_DIR.name
os.environ["PRODUCT_ATELIER_LEGACY_CONFIG"] = str(
    Path(MODULE_DATA_DIR.name) / "no-legacy-config.json"
)
os.environ["PRODUCT_ATELIER_KNOWLEDGE_BASE"] = str(
    Path(MODULE_DATA_DIR.name) / "no-knowledge-vault"
)


def _save_config_in_process(
    data_dir: str,
    field: str,
    value: str,
    start_barrier,
) -> None:
    os.environ["PRODUCT_ATELIER_DATA_DIR"] = data_dir
    from python import server

    server.CONFIG_PATH = Path(data_dir) / "config.json"
    start_barrier.wait(timeout=15)
    server.save_config({field: value})


class _RecordingKnowledge:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.vault_path = Path()

    def set_path(self, path: str):
        self.paths.append(str(path))
        self.vault_path = Path(path)
        return self.status()

    def status(self):
        return {"path": str(self.vault_path)}


class ConfigConcurrencyTests(unittest.TestCase):
    def test_process_writes_merge_atomically_and_runtime_reloads_shared_values(self) -> None:
        from python import server

        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "config.json"
            original = {
                "CONFIG_PATH": server.CONFIG_PATH,
                "API_KEY": server.API_KEY,
                "KNOWLEDGE": server.KNOWLEDGE,
                "_RUNTIME_KNOWLEDGE_PATH": server._RUNTIME_KNOWLEDGE_PATH,
                "OUTPUT_DIR": server.OUTPUT_DIR,
                "_RUNTIME_OUTPUT_ROOT": server._RUNTIME_OUTPUT_ROOT,
            }
            try:
                server.CONFIG_PATH = config_path
                server.OUTPUT_DIR = Path(temp_dir) / "output"
                server.OUTPUT_DIR.mkdir()
                server._RUNTIME_OUTPUT_ROOT = server.OUTPUT_DIR
                server.API_KEY = "stale-process-value"
                recorder = _RecordingKnowledge()
                server.KNOWLEDGE = recorder
                server._RUNTIME_KNOWLEDGE_PATH = ""

                context = multiprocessing.get_context("spawn")
                barrier = context.Barrier(2)
                first = context.Process(
                    target=_save_config_in_process,
                    args=(temp_dir, "api_key", "shared-offline-key", barrier),
                )
                second = context.Process(
                    target=_save_config_in_process,
                    args=(temp_dir, "knowledge_base_path", str(Path(temp_dir) / "vault"), barrier),
                )
                first.start()
                second.start()
                for process in (first, second):
                    process.join(timeout=20)
                    self.assertFalse(process.is_alive())
                    self.assertEqual(process.exitcode, 0)

                stored = json.loads(config_path.read_text(encoding="utf-8"))
                self.assertEqual(stored["api_key"], "shared-offline-key")
                self.assertEqual(stored["knowledge_base_path"], str(Path(temp_dir) / "vault"))
                self.assertEqual(server.get_api_key(), "shared-offline-key")
                refreshed = server.refresh_runtime_config()
                self.assertEqual(
                    refreshed,
                    json.loads(config_path.read_text(encoding="utf-8")),
                )
                self.assertEqual(refreshed["output_root"], str(server.OUTPUT_DIR.resolve()))
                self.assertEqual(recorder.paths, [str(Path(temp_dir) / "vault")])
            finally:
                for name, value in original.items():
                    setattr(server, name, value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
