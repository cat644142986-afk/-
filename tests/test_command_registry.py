from __future__ import annotations

import unittest

from python.command_registry import (
    COMMAND_REGISTRY_VERSION,
    command_for_mode,
    get_command,
    list_commands,
)


class CommandRegistryTests(unittest.TestCase):
    def test_existing_quick_modes_have_one_canonical_command_each(self) -> None:
        self.assertEqual(COMMAND_REGISTRY_VERSION, "canvas-command-v1")
        commands = list_commands()
        quick_commands = [command for command in commands if command["existing_quick_mode"]]
        self.assertEqual(
            {command["id"] for command in quick_commands},
            {
                "command:existing-generate-single",
                "command:existing-generate-multi-file",
                "command:existing-group-split",
                "command:existing-remove-background",
            },
        )
        self.assertEqual(len({command["mode"] for command in quick_commands}), 4)
        for command in quick_commands:
            self.assertTrue(command["existing_quick_mode"])
            self.assertTrue(command["supports_canvas"])
            self.assertGreaterEqual(command["max_sources"], command["min_sources"])
            self.assertEqual(command["execution_kind"], "durable-job")

        self.assertEqual(
            {
                command["id"]
                for command in commands
                if command["execution_kind"] == "canvas-mutation"
            },
            {
                "command:transform-layer",
                "command:toggle-layer",
                "command:toggle-layer-lock",
            },
        )

    def test_mode_and_id_resolution_are_stable_and_defensive(self) -> None:
        cutout = command_for_mode("cutout-batch")
        self.assertEqual(cutout["id"], "command:existing-remove-background")
        self.assertEqual(cutout["engine_key"], "local-cutout")
        self.assertEqual(cutout["cost_policy"], "free-local")

        cutout["label"] = "mutated caller copy"
        self.assertNotEqual(
            get_command("command:existing-remove-background")["label"],
            "mutated caller copy",
        )
        with self.assertRaises(KeyError):
            get_command("command:missing")
        with self.assertRaises(KeyError):
            command_for_mode("missing-mode")


if __name__ == "__main__":
    unittest.main(verbosity=2)
