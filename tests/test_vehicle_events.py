import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from canbusd import event_registry as vehicle_events
from ui import config_cli, vehicle_setup


class VehicleEventRegistryTests(unittest.TestCase):
    def test_bundled_registry_freezes_current_universal_events(self):
        registry = vehicle_events.registry_payload()
        self.assertEqual(registry["schema_version"], 1)
        self.assertEqual(registry["registry_id"], "open-mmi.vehicle-events")
        self.assertEqual(
            set(registry["events"]),
            {
                "arrow_down",
                "arrow_left",
                "arrow_right",
                "arrow_up",
                "back",
                "brightness_level",
                "confirm",
                "mute_toggle",
                "next_track",
                "play_pause",
                "previous_track",
                "stop_playback",
                "telephone",
                "vehicle_present:off",
                "vehicle_present:on",
                "volume_down",
                "volume_up",
            },
        )
        self.assertEqual(
            registry["events"]["brightness_level"]["payload"],
            {
                "type": "integer",
                "minimum": 0,
                "maximum": 100,
                "unit": "percent",
            },
        )
        self.assertIn(
            "never means mute-on or mute-off",
            registry["events"]["mute_toggle"]["description"],
        )

    def test_event_lookup_distinguishes_canonical_alias_and_unknown(self):
        registry = vehicle_events.registry_payload()
        registry["aliases"]["legacy_mute"] = {
            "event": "mute_toggle",
            "description": "Historical private synonym.",
            "status": "deprecated",
        }
        self.assertEqual(
            vehicle_events.event_status("mute_toggle", registry),
            ("canonical", "mute_toggle"),
        )
        self.assertEqual(
            vehicle_events.event_status("legacy_mute", registry),
            ("alias", "mute_toggle"),
        )
        self.assertEqual(
            vehicle_events.event_status("vauxhall_volume_off", registry),
            ("unknown", None),
        )
        self.assertEqual(
            vehicle_events.event_status("../../mute", registry),
            ("invalid", None),
        )

    def test_registry_loader_rejects_duplicate_keys_and_unsupported_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "events.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1,'
                '"registry_id":"open-mmi.vehicle-events",'
                '"events":{},"aliases":{}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                vehicle_events.VehicleEventRegistryError,
                "duplicate JSON key",
            ):
                vehicle_events.load_registry(path)

        registry = vehicle_events.registry_payload()
        registry["events"]["mute_toggle"]["vehicle"] = "seat"
        with self.assertRaisesRegex(
            vehicle_events.VehicleEventRegistryError,
            "unsupported fields",
        ):
            vehicle_events.normalize_registry(registry)

    def test_profiles_and_bindings_require_registered_canonical_events(self):
        profile = {
            "rules": [
                {
                    "id": "0x231",
                    "byte": 3,
                    "value": 30,
                    "event": "vauxhall_steering_volume_off",
                }
            ]
        }
        profile_validation = vehicle_setup.validate_profile(profile)
        self.assertFalse(profile_validation["valid"])
        self.assertIn(
            "unregistered-event",
            {issue["code"] for issue in profile_validation["errors"]},
        )

        bindings_validation = vehicle_setup.validate_bindings(
            {
                "vauxhall_steering_volume_off": {
                    "module": "audio",
                    "func": "mute_toggle",
                }
            }
        )
        self.assertFalse(bindings_validation["valid"])
        self.assertIn(
            "unregistered-event",
            {issue["code"] for issue in bindings_validation["errors"]},
        )

    def test_deprecated_alias_is_diagnostic_not_a_valid_profile_contract(self):
        registry = vehicle_events.registry_payload()
        registry["aliases"]["legacy_mute"] = {
            "event": "mute_toggle",
            "description": "Historical private synonym.",
            "status": "deprecated",
        }
        validation = vehicle_setup.validate_profile(
            {
                "rules": [
                    {
                        "id": "0x231",
                        "byte": 3,
                        "value": 30,
                        "event": "legacy_mute",
                    }
                ]
            },
            event_registry=registry,
        )
        self.assertFalse(validation["valid"])
        error = next(
            issue
            for issue in validation["errors"]
            if issue["code"] == "deprecated-event-alias"
        )
        self.assertIn("mute_toggle", error["message"])

    def test_rule_payload_delivery_matches_registry_contract(self):
        unexpected = vehicle_setup.validate_profile(
            {
                "rules": [
                    {
                        "id": "0x100",
                        "byte": 0,
                        "value": "any",
                        "event": "play_pause",
                    }
                ]
            }
        )
        self.assertIn(
            "unexpected-event-payload",
            {issue["code"] for issue in unexpected["errors"]},
        )

        missing = vehicle_setup.validate_profile(
            {
                "rules": [
                    {
                        "id": "0x470",
                        "byte": 2,
                        "value": 50,
                        "event": "brightness_level",
                    }
                ]
            }
        )
        self.assertIn(
            "missing-event-payload",
            {issue["code"] for issue in missing["errors"]},
        )

        valid = vehicle_setup.validate_profile(
            {
                "rules": [
                    {
                        "id": "0x470",
                        "byte": 2,
                        "value": "any",
                        "event": "brightness_level",
                    }
                ]
            }
        )
        self.assertTrue(valid["valid"])

    def test_maintained_catalogue_conforms_to_registry(self):
        root = Path(__file__).resolve().parents[1]
        profile = json.loads(
            (root / "vehicles/seat/leon/1p-pq35/config.json").read_text(encoding="utf-8")
        )
        bindings = json.loads(
            (root / "bindings/default.json").read_text(encoding="utf-8")
        )
        self.assertTrue(vehicle_setup.validate_profile(profile)["valid"])
        self.assertTrue(vehicle_setup.validate_bindings(bindings)["valid"])

    def test_search_uses_human_wording_and_returns_canonical_events(self):
        payload = vehicle_events.search_events("audio mute")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["matches"][0]["event"], "mute_toggle")
        self.assertIn("description", payload["matches"][0]["matched_on"])
        self.assertIn("continuity", payload["guidance"])

        media = vehicle_events.search_events("media")
        self.assertIn(
            "volume_up",
            {entry["event"] for entry in media["matches"]},
        )

    def test_contribution_check_guides_reuse_alias_and_new_proposal(self):
        reuse = vehicle_events.contribution_check("mute_toggle")
        self.assertEqual(reuse["decision"], "reuse")
        self.assertIn("change only", reuse["message"])
        self.assertIn("not a walled garden", reuse["principles"][0])

        proposal = vehicle_events.contribution_check("pdc_signal")
        self.assertEqual(proposal["decision"], "consider_status")
        self.assertEqual(proposal["status"], "unknown")
        self.assertIn("same pull request", proposal["message"])
        self.assertIn(
            "parking.distance.rear_left",
            {item["path"] for item in proposal["status_candidates"]},
        )

        invalid = vehicle_events.contribution_check("PDC_signal")
        self.assertEqual(invalid["decision"], "rename_before_proposal")
        self.assertIn("manufacturer", invalid["message"])

        registry = vehicle_events.registry_payload()
        registry["aliases"]["legacy_mute"] = {
            "event": "mute_toggle",
            "description": "Historical private synonym.",
            "status": "deprecated",
        }
        alias = vehicle_events.contribution_check(
            "legacy_mute",
            registry=registry,
        )
        self.assertEqual(alias["decision"], "use_canonical")
        self.assertEqual(alias["event"], "mute_toggle")

    def test_unknown_validation_explains_reuse_or_propose_workflow(self):
        validation = vehicle_setup.validate_profile(
            {
                "rules": [
                    {
                        "id": "0x431",
                        "byte": 2,
                        "value": 17,
                        "event": "private_mute_name",
                    }
                ]
            }
        )
        issue = next(
            item
            for item in validation["errors"]
            if item["code"] == "unregistered-event"
        )
        self.assertIn("--search <meaning>", issue["message"])
        self.assertIn("same pull request", issue["message"])

    def test_cli_exposes_registry_search_check_and_one_definition(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(["vehicle-setup", "events", "mute_toggle"])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["event"], "mute_toggle")
        self.assertEqual(payload["payload"], {"type": "none"})

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "events", "--search", "audio mute"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["matches"][0]["event"], "mute_toggle")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "events", "--check", "pdc_signal"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["decision"], "consider_status")
        self.assertIn(
            "parking.assist.active",
            {item["path"] for item in payload["status_candidates"]},
        )

    def test_cli_rejects_conflicting_event_lookup_modes(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            result = config_cli.main(
                [
                    "vehicle-setup",
                    "events",
                    "mute_toggle",
                    "--search",
                    "mute",
                ]
            )
        self.assertEqual(result, 1)
        self.assertIn("choose one event", error.getvalue())

    def test_generated_documentation_matches_registry(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [
                sys.executable,
                "tools/generate_vehicle_event_docs.py",
                "--check",
            ],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
