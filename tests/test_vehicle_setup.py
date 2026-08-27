import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ui import config_cli, vehicle_setup


class VehicleSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.maintained = self.root / "installed"
        self.custom = self.root / "custom"
        self.roots = vehicle_setup.CatalogueRoots(
            maintained=self.maintained,
            custom=self.custom,
        )

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write_json(path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def profile(self, identifier="seat_1p", root=None, **updates):
        document = {
            "default_bus": "comfort",
            "can_buses": {
                "comfort": {
                    "interface": "can0",
                    "bitrate": 100000,
                    "provisioning": "udev",
                    "bring_up": False,
                }
            },
            "rules": [
                {"id": "0x100", "byte": 0, "value": 1, "event": "play_pause"}
            ],
            "presence": [
                {
                    "id": "0x101",
                    "timeout_ms": 1000,
                    "on_present": "vehicle_present:on",
                    "on_absent": "vehicle_present:off",
                }
            ],
            "status": [
                {
                    "id": "0x102",
                    "byte": 0,
                    "type": "raw",
                    "path": "vehicle.reverse_raw",
                }
            ],
        }
        document.update(updates)
        selected_root = root or self.maintained
        return self.write_json(
            selected_root / "vehicles" / identifier / "config.json",
            document,
        )

    def bindings(self, identifier="default", root=None, document=None):
        selected_root = root or self.maintained
        return self.write_json(
            selected_root / "bindings" / f"{identifier}.json",
            document
            or {
                "play_pause": {"action": "media.playback.toggle"},
                "vehicle_present:on": {"action": "display.power.wake"},
            },
        )

    def test_identifier_and_fixed_root_resolution(self):
        expected = self.maintained / "vehicles" / "seat_1p" / "config.json"
        self.assertEqual(
            vehicle_setup.resolve_catalogue_path(
                self.roots,
                "profile",
                "maintained",
                "seat_1p",
            ),
            expected,
        )
        for identifier in ("../seat_1p", ".hidden", "Seat", "seat/1p", "séat"):
            with self.subTest(identifier=identifier), self.assertRaises(
                vehicle_setup.VehicleSetupError
            ):
                vehicle_setup.resolve_catalogue_path(
                    self.roots,
                    "profile",
                    "maintained",
                    identifier,
                )

    def test_symlinked_catalogue_component_fails_closed(self):
        outside = self.root / "outside"
        self.profile(root=outside)
        (self.custom / "vehicles").mkdir(parents=True)
        (self.custom / "vehicles" / "seat_1p").symlink_to(
            outside / "vehicles" / "seat_1p",
            target_is_directory=True,
        )

        with self.assertRaisesRegex(
            vehicle_setup.VehicleSetupError,
            "Symlinked catalogue paths",
        ):
            vehicle_setup.resolve_catalogue_path(
                self.roots,
                "profile",
                "custom",
                "seat_1p",
            )

    def test_profile_validator_checks_runtime_critical_fields(self):
        valid_document = json.loads(self.profile().read_text(encoding="utf-8"))
        self.assertTrue(vehicle_setup.validate_profile(valid_document)["valid"])

        invalid_document = dict(valid_document)
        invalid_document["can_buses"] = {
            "comfort": {
                "interface": "../../can0",
                "bitrate": -1,
                "provisioning": "shell",
                "bring_up": "yes",
            }
        }
        invalid_document["rules"] = [
            {
                "id": "0x20000000",
                "byte": 8,
                "value": 999,
                "event": "",
                "bus": "missing",
            }
        ]
        result = vehicle_setup.validate_profile(invalid_document)
        self.assertFalse(result["valid"])
        self.assertEqual(
            {
                "invalid-interface",
                "invalid-bitrate",
                "invalid-provisioning",
                "invalid-bring-up",
                "undeclared-bus",
                "invalid-can-id",
                "invalid-byte-index",
                "invalid-event",
                "invalid-rule-value",
            },
            {issue["code"] for issue in result["errors"]},
        )

    def test_profile_validator_enforces_canonical_status_paths_and_types(self):
        valid_document = json.loads(self.profile().read_text(encoding="utf-8"))
        valid_document["status"] = [
            {
                "id": "0x431",
                "byte": 4,
                "type": "bool",
                "path": "doors.front_right",
                "true": "0x20",
                "false": "0x00",
            }
        ]
        self.assertTrue(vehicle_setup.validate_profile(valid_document)["valid"])

        valid_document["status"][0]["path"] = "vauxhall.pdc_signal"
        result = vehicle_setup.validate_profile(valid_document)
        self.assertFalse(result["valid"])
        self.assertIn(
            "unregistered-status",
            {issue["code"] for issue in result["errors"]},
        )

        valid_document["status"][0]["path"] = "vehicle.speed_kmh"
        result = vehicle_setup.validate_profile(valid_document)
        self.assertFalse(result["valid"])
        self.assertIn(
            "status-type-mismatch",
            {issue["code"] for issue in result["errors"]},
        )

    def test_profile_validator_accepts_masked_bitfield_equals(self):
        valid_document = json.loads(self.profile().read_text(encoding="utf-8"))
        valid_document["status"] = [
            {
                "id": "0x470",
                "byte": 1,
                "type": "bitfield",
                "path": "doors",
                "fields": {"front_right": "0x01"},
                "equals": {
                    "boot": {
                        "mask": "0x60",
                        "value": "0x60",
                    }
                },
                "any": "any_open",
                "raw": "raw",
            }
        ]
        self.assertTrue(vehicle_setup.validate_profile(valid_document)["valid"])

        valid_document["status"][0]["equals"]["boot"]["mask"] = "0x100"
        result = vehicle_setup.validate_profile(valid_document)
        self.assertFalse(result["valid"])
        self.assertIn(
            "invalid-bitfield-entry",
            {issue["code"] for issue in result["errors"]},
        )

    def test_profile_validator_supports_documented_legacy_bus_fallback(self):
        result = vehicle_setup.validate_profile(
            {
                "rules": [
                    {"id": "0x100", "byte": 0, "value": "any", "event": "brightness_level"}
                ]
            }
        )
        self.assertTrue(result["valid"])
        self.assertIn(
            "legacy-bus-fallback",
            {issue["code"] for issue in result["warnings"]},
        )

    def test_bindings_validator_is_non_executing_and_flags_legacy_schema(self):
        valid = vehicle_setup.validate_bindings(
            {"play_pause": {"module": "audio", "func": "play_pause", "args": []}}
        )
        self.assertTrue(valid["valid"])
        self.assertEqual(valid["warnings"][0]["code"], "legacy-action-schema")

        invalid = vehicle_setup.validate_bindings(
            {
                "play_pause": {
                    "module": "audio.tools",
                    "func": "play-pause",
                    "args": [{}],
                    "command": "ignored",
                }
            }
        )
        self.assertFalse(invalid["valid"])
        self.assertEqual(
            {
                "invalid-module",
                "invalid-func",
                "invalid-argument",
                "unsupported-binding-field",
            },
            {issue["code"] for issue in invalid["errors"]},
        )

    def test_catalogue_is_deterministic_and_reports_malformed_custom_entries(self):
        self.profile("zeta")
        self.profile("seat_1p")
        self.profile("alpha", root=self.custom)
        malformed = self.custom / "vehicles" / "broken" / "config.json"
        malformed.parent.mkdir(parents=True)
        malformed.write_text("{not-json", encoding="utf-8")
        invalid_name = self.custom / "vehicles" / "Bad Name" / "config.json"
        self.write_json(invalid_name, {})
        self.bindings("default")
        self.bindings("custom_keys", root=self.custom)

        payload = vehicle_setup.catalogue_payload(self.roots)
        self.assertEqual(
            [(entry["source"], entry["id"]) for entry in payload["profiles"]],
            [
                ("maintained", "seat_1p"),
                ("maintained", "zeta"),
                ("custom", "Bad Name"),
                ("custom", "alpha"),
                ("custom", "broken"),
            ],
        )
        entries = {entry["id"]: entry for entry in payload["profiles"]}
        self.assertFalse(entries["Bad Name"]["valid"])
        self.assertFalse(entries["broken"]["valid"])
        self.assertTrue(entries["alpha"]["valid"])
        self.assertEqual(entries["seat_1p"]["event_count"], 1)
        self.assertTrue(entries["seat_1p"]["revision"].startswith("sha256:"))
        self.assertEqual(
            [(entry["source"], entry["id"]) for entry in payload["bindings"]],
            [("maintained", "default"), ("custom", "custom_keys")],
        )

    def test_compatibility_report_is_stable(self):
        report = vehicle_setup.compatibility_report(
            {
                "rules": [
                    {"event": "shared"},
                    {"event": "shared"},
                    {"event": "unbound"},
                ],
                "presence": [{"on_present": "present", "on_absent": "absent"}],
            },
            {"shared": {}, "present": {}, "unused": {}},
        )
        self.assertEqual(report["emitted_and_bound"], ["present", "shared"])
        self.assertEqual(report["emitted_unbound"], ["absent", "unbound"])
        self.assertEqual(report["bound_unemitted"], ["unused"])
        self.assertEqual(report["duplicate_emitted"], ["shared"])

    def test_socketcan_discovery_uses_kernel_link_type(self):
        sys_class_net = self.root / "sys" / "class" / "net"
        for name, link_type, operstate, flags, bitrate in (
            ("can1", "280", "down", "0x1002", "500000"),
            ("eth0", "1", "up", "0x1003", None),
            ("can0", "280", "unknown", "0x1003", "100000"),
        ):
            interface = sys_class_net / name
            (interface / "can").mkdir(parents=True)
            (interface / "type").write_text(link_type, encoding="utf-8")
            (interface / "operstate").write_text(operstate, encoding="utf-8")
            (interface / "flags").write_text(flags, encoding="utf-8")
            if bitrate:
                (interface / "can" / "bitrate").write_text(bitrate, encoding="utf-8")

        self.assertEqual(
            vehicle_setup.discover_interfaces(sys_class_net),
            [
                {
                    "name": "can0",
                    "kind": "socketcan",
                    "present": True,
                    "up": True,
                    "operstate": "unknown",
                    "configured_bitrate": 100000,
                    "last_frame_age_seconds": None,
                },
                {
                    "name": "can1",
                    "kind": "socketcan",
                    "present": True,
                    "up": False,
                    "operstate": "down",
                    "configured_bitrate": 500000,
                    "last_frame_age_seconds": None,
                },
            ],
        )

    def test_status_uses_same_catalogue_and_reports_active_runtime(self):
        profile_path = self.profile()
        bindings_path = self.bindings()
        sys_class_net = self.root / "sys" / "class" / "net"
        interface = sys_class_net / "can0"
        (interface / "can").mkdir(parents=True)
        (interface / "type").write_text("280", encoding="utf-8")
        (interface / "operstate").write_text("down", encoding="utf-8")
        (interface / "can" / "bitrate").write_text("100000", encoding="utf-8")
        payload = vehicle_setup.status_payload(
            self.roots,
            environment={
                "OPEN_MMI_VEHICLE": "seat_1p",
                "OPEN_MMI_BINDINGS": "default",
                "OPEN_MMI_VEHICLE_CONFIG": str(profile_path),
                "OPEN_MMI_BINDINGS_FILE": str(bindings_path),
                "OPEN_MMI_CAN_BUS": "comfort",
                "OPEN_MMI_CAN_INTERFACE": "can0",
            },
            sys_class_net=sys_class_net,
        )
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["runtime_mode"], "single")
        self.assertEqual(payload["active"]["state"], "ready")
        self.assertEqual(payload["active"]["vehicle"]["source"], "maintained")
        self.assertEqual(payload["active"]["bindings"]["id"], "default")
        self.assertTrue(payload["active"]["interface_present"])
        self.assertTrue(payload["active"]["configuration_revision"].startswith("sha256:"))
        self.assertIsNone(payload["active"]["loaded"])
        self.assertEqual(payload["compatibility"]["emitted_unbound"], ["vehicle_present:off"])

    def test_status_surfaces_strict_daemon_loaded_runtime_evidence(self):
        profile_path = self.profile()
        bindings_path = self.bindings()
        profile_revision = "sha256:" + hashlib.sha256(profile_path.read_bytes()).hexdigest()
        bindings_revision = "sha256:" + hashlib.sha256(bindings_path.read_bytes()).hexdigest()
        status_path = self.root / "runtime" / "status.json"
        status_path.parent.mkdir(parents=True)
        status_path.write_text(
            json.dumps(
                {
                    "updated_at": 1234.5,
                    "state": {"vehicle": {"present": False}},
                    "runtime": {
                        "api_version": 1,
                        "state": "ready",
                        "errors": [],
                        "vehicle": {
                            "source": "maintained",
                            "id": "seat_1p",
                            "revision": profile_revision,
                        },
                        "bindings": {
                            "source": "maintained",
                            "id": "default",
                            "revision": bindings_revision,
                        },
                        "active_bus": "comfort",
                        "interface": "can0",
                    },
                }
            ),
            encoding="utf-8",
        )

        payload = vehicle_setup.status_payload(
            self.roots,
            environment={
                "OPEN_MMI_VEHICLE": "seat_1p",
                "OPEN_MMI_BINDINGS": "default",
                "OPEN_MMI_VEHICLE_CONFIG": str(profile_path),
                "OPEN_MMI_BINDINGS_FILE": str(bindings_path),
            },
            sys_class_net=self.root / "missing-sysfs",
            status_path=status_path,
        )

        self.assertEqual(
            payload["active"]["loaded"],
            {
                "api_version": 1,
                "state": "ready",
                "errors": [],
                "vehicle": {
                    "source": "maintained",
                    "id": "seat_1p",
                    "revision": profile_revision,
                },
                "bindings": {
                    "source": "maintained",
                    "id": "default",
                    "revision": bindings_revision,
                },
                "active_bus": "comfort",
                "interface": "can0",
                "updated_at": 1234.5,
            },
        )

    def test_loaded_runtime_evidence_rejects_links_and_malformed_capabilities(self):
        status_path = self.root / "status.json"
        status_path.write_text(
            json.dumps(
                {
                    "updated_at": 1,
                    "runtime": {
                        "api_version": 1,
                        "state": "ready",
                        "errors": [],
                        "vehicle": {},
                        "bindings": {},
                        "active_bus": "comfort",
                        "interface": "can0",
                        "apply": True,
                    },
                }
            ),
            encoding="utf-8",
        )
        self.assertIsNone(vehicle_setup.read_loaded_runtime(status_path))

        target = self.root / "real-status.json"
        target.write_text("{}", encoding="utf-8")
        linked = self.root / "linked-status.json"
        linked.symlink_to(target)
        self.assertIsNone(vehicle_setup.read_loaded_runtime(linked))

    def test_external_runtime_paths_are_reported_but_never_followed(self):
        self.profile()
        self.bindings()
        payload = vehicle_setup.status_payload(
            self.roots,
            environment={
                "OPEN_MMI_VEHICLE": "seat_1p",
                "OPEN_MMI_BINDINGS": "default",
                "OPEN_MMI_VEHICLE_CONFIG": "/tmp/untrusted-profile.json",
                "OPEN_MMI_BINDINGS_FILE": "/tmp/untrusted-bindings.json",
            },
            sys_class_net=self.root / "missing-sysfs",
        )
        self.assertEqual(payload["active"]["state"], "invalid")
        self.assertEqual(
            payload["active"]["errors"],
            ["external-profile-path", "external-bindings-path"],
        )

    def test_runtime_dropin_parser_accepts_only_known_environment_keys(self):
        dropin = self.root / "10-can-runtime.conf"
        dropin.write_text(
            '[Service]\nEnvironment="OPEN_MMI_VEHICLE=seat_1p"\n'
            'Environment="OPEN_MMI_CAN_INTERFACE=can0" "UNTRUSTED=value"\n',
            encoding="utf-8",
        )
        self.assertEqual(
            vehicle_setup.read_runtime_environment(dropin),
            {"OPEN_MMI_VEHICLE": "seat_1p", "OPEN_MMI_CAN_INTERFACE": "can0"},
        )

    def preview_request(self, **updates):
        request = {
            "vehicle": {"source": "maintained", "id": "seat_1p"},
            "bindings": {"source": "maintained", "id": "default"},
            "runtime": {
                "active_bus": "comfort",
                "buses": {"comfort": {"interface": "can0"}},
            },
        }
        request.update(updates)
        return request

    def current_status(self, profile_revision, bindings_revision, **updates):
        active = {
            "state": "ready",
            "vehicle": {
                "source": "maintained",
                "id": "seat_1p",
                "revision": profile_revision,
            },
            "bindings": {
                "source": "maintained",
                "id": "default",
                "revision": bindings_revision,
            },
            "active_bus": "comfort",
            "interface": "can0",
            "configuration_revision": "sha256:" + "c" * 64,
        }
        active.update(updates)
        return {"active": active, "interfaces": []}

    def test_preview_is_deterministic_read_only_and_contains_no_paths(self):
        profile_path = self.profile()
        bindings_path = self.bindings()
        profile_revision = "sha256:" + hashlib.sha256(
            profile_path.read_bytes()
        ).hexdigest()
        bindings_revision = "sha256:" + hashlib.sha256(
            bindings_path.read_bytes()
        ).hexdigest()
        current = self.current_status(profile_revision, bindings_revision)
        request = self.preview_request()

        first = vehicle_setup.preview_payload(
            request,
            self.roots,
            current_status=current,
        )
        second = vehicle_setup.preview_payload(
            request,
            self.roots,
            current_status=current,
        )
        self.assertEqual(first, second)
        self.assertTrue(first["read_only"])
        self.assertFalse(first["apply_available"])
        self.assertEqual(first["state"], "ready")
        self.assertEqual(first["plan"]["changes"], [])
        self.assertFalse(
            first["plan"]["effects"]["restart_can_service"]
        )
        self.assertIn(
            "interface-not-present",
            {issue["code"] for issue in first["validation"]["warnings"]},
        )
        rendered = json.dumps(first)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn("/opt/open-mmi", rendered)
        self.assertNotIn("manage.sh", rendered)

    def test_preview_reports_changes_interface_health_and_udev_effects(self):
        profile_path = self.profile()
        bindings_path = self.bindings()
        profile_revision = "sha256:" + hashlib.sha256(
            profile_path.read_bytes()
        ).hexdigest()
        bindings_revision = "sha256:" + hashlib.sha256(
            bindings_path.read_bytes()
        ).hexdigest()
        current = self.current_status(
            profile_revision,
            bindings_revision,
            interface="can9",
        )
        current["interfaces"] = [
            {
                "name": "can0",
                "present": True,
                "up": True,
                "configured_bitrate": 500000,
            }
        ]
        preview = vehicle_setup.preview_payload(
            self.preview_request(),
            self.roots,
            current_status=current,
        )
        self.assertEqual(
            [change["field"] for change in preview["plan"]["changes"]],
            ["interface"],
        )
        self.assertTrue(preview["interface"]["present"])
        self.assertTrue(preview["plan"]["effects"]["write_udev_rules"])
        self.assertTrue(preview["plan"]["effects"]["restart_can_service"])
        self.assertIn(
            "bitrate-mismatch",
            {issue["code"] for issue in preview["validation"]["warnings"]},
        )

    def test_preview_rewrites_udev_plan_when_switching_to_manual_provisioning(self):
        profile_path = self.profile(
            can_buses={
                "comfort": {
                    "interface": "vcan0",
                    "provisioning": "manual",
                }
            }
        )
        bindings_path = self.bindings()
        current = self.current_status(
            "sha256:" + hashlib.sha256(profile_path.read_bytes()).hexdigest(),
            "sha256:" + hashlib.sha256(bindings_path.read_bytes()).hexdigest(),
            interface="can0",
        )
        request = self.preview_request(
            runtime={
                "active_bus": "comfort",
                "buses": {"comfort": {"interface": "vcan0"}},
            }
        )
        preview = vehicle_setup.preview_payload(
            request,
            self.roots,
            current_status=current,
        )
        self.assertEqual(preview["active_bus"]["provisioning"], "manual")
        self.assertTrue(preview["plan"]["effects"]["write_udev_rules"])
        self.assertTrue(preview["plan"]["effects"]["reload_udev"])

    def test_preview_rejects_unknown_fields_paths_and_undeclared_buses(self):
        self.profile()
        self.bindings()
        current = self.current_status(
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
        )
        cases = [
            {**self.preview_request(), "command": "manage.sh"},
            self.preview_request(
                vehicle={"source": "maintained", "id": "../seat"}
            ),
            self.preview_request(
                runtime={
                    "active_bus": "powertrain",
                    "buses": {"powertrain": {"interface": "can1"}},
                }
            ),
            self.preview_request(
                runtime={
                    "active_bus": "comfort",
                    "buses": {"comfort": {"interface": "../../can0"}},
                }
            ),
        ]
        for request in cases:
            with self.subTest(request=request), self.assertRaises(
                vehicle_setup.VehicleSetupError
            ):
                vehicle_setup.preview_payload(
                    request,
                    self.roots,
                    current_status=current,
                )

    def test_cli_vehicle_setup_preview_uses_shared_backend(self):
        expected = {"api_version": 1, "read_only": True, "state": "ready"}
        output = io.StringIO()
        with (
            mock.patch.object(
                config_cli.vehicle_config_coordinator,
                "client_preview",
                return_value=expected,
            ) as preview,
            contextlib.redirect_stdout(output),
        ):
            result = config_cli.main(
                [
                    "vehicle-setup",
                    "preview",
                    "seat_1p",
                    "default",
                    "--bus",
                    "comfort",
                    "--interface",
                    "can0",
                ]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)
        preview.assert_called_once_with(self.preview_request())

    def test_cli_vehicle_setup_status_uses_shared_backend(self):
        expected = {"api_version": 1, "read_only": True}
        output = io.StringIO()
        with (
            mock.patch.object(vehicle_setup, "status_payload", return_value=expected),
            contextlib.redirect_stdout(output),
        ):
            result = config_cli.main(["vehicle-setup", "status"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), expected)


if __name__ == "__main__":
    unittest.main()
