from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from canbusd import action_registry
from ui import config_cli, vehicle_setup


class VehicleActionRegistryTests(unittest.TestCase):
    def test_bundled_registry_loads(self) -> None:
        registry = action_registry.registry_payload()

        self.assertEqual(registry["registry_id"], "open-mmi.vehicle-actions")
        self.assertEqual(len(registry["actions"]), 17)
        self.assertIn("media.mute.toggle", registry["actions"])
        self.assertIn("display.brightness.set", registry["actions"])

    def test_oversized_registry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "actions.json"
            path.write_bytes(b" " * (action_registry.MAX_REGISTRY_BYTES + 1))
            with self.assertRaisesRegex(
                action_registry.VehicleActionRegistryError,
                "bounded regular file",
            ):
                action_registry.load_registry(path)

    def test_human_search_finds_behavior_without_exposing_implementation(self) -> None:
        payload = action_registry.search_actions("audio mute")

        self.assertEqual(payload["matches"][0]["action"], "media.mute.toggle")
        self.assertNotIn("implementation", payload["matches"][0])
        self.assertIn("not a walled garden", payload["guidance"])

    def test_contribution_check_is_guidance_not_permission(self) -> None:
        known = action_registry.contribution_check("media.mute.toggle")
        provisional = action_registry.contribution_check("sound_module_off")

        self.assertEqual(known["decision"], "reuse")
        self.assertNotIn("implementation", known["definition"])
        self.assertEqual(provisional["decision"], "rename_before_proposal")
        self.assertIn("Python module", provisional["message"])

    def test_canonical_binding_resolves_private_implementation(self) -> None:
        resolved = action_registry.resolve_binding(
            {"action": "media.mute.toggle"},
            carries_event_payload=False,
        )

        self.assertEqual(
            resolved,
            {
                "action": "media.mute.toggle",
                "module": "audio",
                "func": "mute_toggle",
                "args": [],
            },
        )

    def test_action_argument_and_payload_contracts_are_enforced(self) -> None:
        volume = action_registry.resolve_binding(
            {"action": "media.volume.increase", "args": ["+10%"]},
            carries_event_payload=False,
        )
        self.assertEqual(volume["args"], ["+10%"])

        with self.assertRaisesRegex(
            action_registry.VehicleActionRegistryError,
            "maximum length|does not match",
        ):
            action_registry.resolve_binding(
                {"action": "media.volume.increase", "args": ["louder"]},
                carries_event_payload=False,
            )

        action_registry.resolve_binding(
            {"action": "display.brightness.set"},
            carries_event_payload=True,
        )
        with self.assertRaisesRegex(
            action_registry.VehicleActionRegistryError,
            "requires an event payload",
        ):
            action_registry.resolve_binding(
                {"action": "display.brightness.set"},
                carries_event_payload=False,
            )
        with self.assertRaisesRegex(
            action_registry.VehicleActionRegistryError,
            "does not accept an event payload",
        ):
            action_registry.resolve_binding(
                {"action": "media.mute.toggle"},
                carries_event_payload=True,
            )

    def test_legacy_custom_binding_remains_compatible(self) -> None:
        binding = {"module": "audio", "func": "mute_toggle", "args": []}

        self.assertEqual(action_registry.resolve_binding(binding), binding)
        validation = vehicle_setup.validate_bindings({"mute_toggle": binding})
        self.assertTrue(validation["valid"])
        self.assertIn(
            "legacy-action-schema",
            {issue["code"] for issue in validation["warnings"]},
        )

    def test_maintained_bindings_reject_legacy_schema(self) -> None:
        validation = vehicle_setup.validate_bindings(
            {"mute_toggle": {"module": "audio", "func": "mute_toggle"}},
            maintained=True,
        )

        self.assertFalse(validation["valid"])
        self.assertIn(
            "legacy-action-schema",
            {issue["code"] for issue in validation["errors"]},
        )

    def test_binding_event_payload_must_match_action_payload(self) -> None:
        wrong = vehicle_setup.validate_bindings(
            {"brightness_level": {"action": "media.mute.toggle"}}
        )
        missing = vehicle_setup.validate_bindings(
            {"mute_toggle": {"action": "display.brightness.set"}}
        )

        self.assertIn(
            "unexpected-action-payload",
            {issue["code"] for issue in wrong["errors"]},
        )
        self.assertIn(
            "missing-action-payload",
            {issue["code"] for issue in missing["errors"]},
        )

    def test_maintained_default_bindings_are_canonical(self) -> None:
        bindings = json.loads(Path("bindings/default.json").read_text(encoding="utf-8"))

        validation = vehicle_setup.validate_bindings(bindings, maintained=True)
        self.assertTrue(validation["valid"], validation)
        self.assertEqual(validation["warnings"], [])
        self.assertTrue(all(set(binding) <= {"action", "args"} for binding in bindings.values()))

    def test_cli_exposes_search_check_and_exact_action(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "actions", "media.mute.toggle"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["action"], "media.mute.toggle")
        self.assertIn("implementation", payload)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "actions", "--search", "audio mute"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["matches"][0]["action"], "media.mute.toggle")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "actions", "--check", "media.mute.toggle"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["decision"], "reuse")


if __name__ == "__main__":
    unittest.main()
