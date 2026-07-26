from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from canbusd import event_registry, status_registry
from ui import config_cli


class VehicleStatusRegistryTests(unittest.TestCase):
    def test_bundled_registry_loads(self) -> None:
        registry = status_registry.registry_payload()

        self.assertEqual(registry["registry_id"], "open-mmi.vehicle-statuses")
        self.assertGreaterEqual(len(registry["statuses"]), 69)
        self.assertIn("doors.front_right", registry["statuses"])
        self.assertIn("parking.distance.rear_left", registry["statuses"])

    def test_oversized_registry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "statuses.json"
            path.write_bytes(b" " * (status_registry.MAX_REGISTRY_BYTES + 1))
            with self.assertRaisesRegex(
                status_registry.VehicleStatusRegistryError,
                "bounded regular file",
            ):
                status_registry.load_registry(path)

    def test_human_search_finds_door_and_parking_concepts(self) -> None:
        door_paths = {
            match["path"]
            for match in status_registry.search_statuses("right door")["matches"]
        }
        parking_paths = {
            match["path"]
            for match in status_registry.search_statuses("pdc_signal")["matches"]
        }

        self.assertIn("doors.front_right", door_paths)
        self.assertIn("parking.distance.rear_left", parking_paths)
        self.assertIn("parking.assist.active", parking_paths)

    def test_contribution_check_is_guidance_not_permission(self) -> None:
        known = status_registry.contribution_check("doors.front_right")
        provisional = status_registry.contribution_check("pdc_signal")

        self.assertEqual(known["decision"], "reuse")
        self.assertEqual(provisional["decision"], "rename_before_proposal")
        self.assertTrue(provisional["candidates"])
        self.assertIn("not a walled garden", " ".join(known["principles"]))

    def test_value_contract_is_enforced(self) -> None:
        status_registry.require_status(
            "vehicle.speed_kmh",
            value_type="number",
        )
        status_registry.require_status(
            "vehicle.speed_kmh",
            value_type="integer",
        )
        with self.assertRaises(status_registry.VehicleStatusRegistryError):
            status_registry.require_status(
                "vehicle.speed_kmh",
                value_type="boolean",
            )

    def test_enum_contract_rejects_private_vehicle_values(self) -> None:
        profile = {
            "status": [
                {
                    "id": "0x531",
                    "byte": 0,
                    "type": "enum",
                    "path": "lighting.mode",
                    "values": {"0x00": "off", "0x01": "vauxhall_private_mode"},
                    "default": "unknown",
                }
            ]
        }

        with self.assertRaisesRegex(
            status_registry.VehicleStatusRegistryError,
            "unsupported enum values",
        ):
            status_registry.require_profile_statuses(profile)

    def test_deprecated_alias_is_only_allowed_explicitly(self) -> None:
        with self.assertRaises(status_registry.VehicleStatusRegistryError):
            status_registry.require_status(
                "climate.front_demist_air_request",
                value_type="boolean",
            )
        status_registry.require_status(
            "climate.front_demist_air_request",
            value_type="boolean",
            allow_alias=True,
        )

    def test_maintained_seat_profile_conforms(self) -> None:
        profile = json.loads(
            Path("vehicles/seat/leon/1p-pq35/config.json").read_text(encoding="utf-8")
        )

        status_registry.require_profile_statuses(profile)

    def test_unknown_profile_status_is_rejected(self) -> None:
        profile = {
            "status": [
                {
                    "id": "0x431",
                    "byte": 4,
                    "type": "bool",
                    "path": "vauxhall.pdc_signal",
                    "true": "0x20",
                    "false": "0x00",
                }
            ]
        }

        with self.assertRaisesRegex(
            status_registry.VehicleStatusRegistryError,
            "not registered",
        ):
            status_registry.require_profile_statuses(profile)

    def test_rule_output_expansion_covers_bitfields_and_raw_paths(self) -> None:
        outputs = status_registry.rule_outputs(
            {
                "type": "bitfield",
                "path": "doors",
                "fields": {"front_right": "0x01"},
                "equals": {"boot": "0x60"},
                "any": "any_open",
                "raw": "raw",
            }
        )

        self.assertEqual(
            {(output["path"], output["value_type"]) for output in outputs},
            {
                ("doors.front_right", "boolean"),
                ("doors.boot", "boolean"),
                ("doors.any_open", "boolean"),
                ("doors.raw", "integer"),
            },
        )


    def test_cli_exposes_search_check_and_exact_status(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "statuses", "doors.front_right"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["path"], "doors.front_right")
        self.assertEqual(payload["value"]["type"], "boolean")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "statuses", "--search", "pdc signal"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertIn(
            "parking.distance.rear_left",
            {item["path"] for item in payload["matches"]},
        )

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = config_cli.main(
                ["vehicle-setup", "statuses", "--check", "pdc_signal"]
            )
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["decision"], "rename_before_proposal")
        self.assertTrue(payload["candidates"])

    def test_event_check_points_ambiguous_pdc_toward_status(self) -> None:
        result = event_registry.contribution_check("pdc_signal")

        self.assertEqual(result["decision"], "consider_status")
        self.assertTrue(result["status_candidates"])
        self.assertIn(
            "parking.distance.rear_left",
            {item["path"] for item in result["status_candidates"]},
        )


if __name__ == "__main__":
    unittest.main()
