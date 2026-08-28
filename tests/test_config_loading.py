import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from canbusd import core
from canbusd.can_runtime import CanRuntimeConfig


class ConfigLoadingTests(unittest.TestCase):
    def setUp(self):
        self.original_runtime = core.CAN_RUNTIME
        self.original_bus = core.CAN_BUS
        self.original_iface = core.IFACE
        self.original_reload = core._need_reload
        self.original_loaded_vehicle = core.LOADED_VEHICLE
        self.original_loaded_bindings = core.LOADED_BINDINGS
        core.LOADED_VEHICLE = None
        core.LOADED_BINDINGS = None

    def tearDown(self):
        core.CAN_RUNTIME = self.original_runtime
        core.CAN_BUS = self.original_bus
        core.IFACE = self.original_iface
        core._need_reload = self.original_reload
        core.LOADED_VEHICLE = self.original_loaded_vehicle
        core.LOADED_BINDINGS = self.original_loaded_bindings

    def _write_json(self, path: Path, value) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_explicit_vehicle_and_bindings_paths_win(self):
        with mock.patch.dict(
            os.environ,
            {
                "OPEN_MMI_VEHICLE_CONFIG": "~/vehicle.json",
                "OPEN_MMI_BINDINGS_FILE": "~/bindings.json",
            },
            clear=False,
        ):
            self.assertEqual(
                core._resolve_vehicle_config_path(),
                Path("~/vehicle.json").expanduser(),
            )
            self.assertEqual(
                core._resolve_bindings_path(),
                Path("~/bindings.json").expanduser(),
            )

    def test_user_overrides_are_not_implicitly_activated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user_dir = root / "user"
            base_dir = root / "base"
            self._write_json(user_dir / "vehicles/demo/config.json", {})
            self._write_json(user_dir / "bindings/demo.json", {})

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "OPEN_MMI_VEHICLE_CONFIG": "",
                        "OPEN_MMI_BINDINGS_FILE": "",
                    },
                    clear=False,
                ),
                mock.patch.object(core, "USER_CONFIG_DIR", user_dir),
                mock.patch.object(core, "BASE_DIR", base_dir),
                mock.patch.object(core, "VEHICLE", "demo"),
                mock.patch.object(core, "BINDINGS", "demo"),
                self.assertLogs("canbusd", level="WARNING") as logs,
            ):
                self.assertEqual(
                    core._resolve_vehicle_config_path(),
                    base_dir / "vehicles/demo/config.json",
                )
                self.assertEqual(
                    core._resolve_bindings_path(),
                    base_dir / "bindings/demo.json",
                )

            output = "\n".join(logs.output)
            self.assertIn("not active", output)
            self.assertIn("OPEN_MMI_VEHICLE_CONFIG", output)
            self.assertIn("OPEN_MMI_BINDINGS_FILE", output)

    def test_managed_configuration_requires_both_exact_paths(self):
        self.assertFalse(core._managed_configuration_mode({}))
        self.assertFalse(
            core._managed_configuration_mode(
                {"OPEN_MMI_VEHICLE_CONFIG": "/tmp/profile.json"}
            )
        )
        self.assertFalse(
            core._managed_configuration_mode(
                {"OPEN_MMI_BINDINGS_FILE": "/tmp/bindings.json"}
            )
        )
        self.assertTrue(
            core._managed_configuration_mode(
                {
                    "OPEN_MMI_VEHICLE_CONFIG": "/tmp/profile.json",
                    "OPEN_MMI_BINDINGS_FILE": "/tmp/bindings.json",
                }
            )
        )

    def test_sighup_marks_unmanaged_configuration_for_reload(self):
        core._need_reload = False
        with (
            mock.patch.dict(
                os.environ,
                {
                    "OPEN_MMI_VEHICLE_CONFIG": "",
                    "OPEN_MMI_BINDINGS_FILE": "",
                },
                clear=False,
            ),
            self.assertLogs("canbusd", level="INFO") as logs,
        ):
            core._sig_hup(1, None)

        self.assertTrue(core._need_reload)
        self.assertIn("reload config", "\n".join(logs.output))

    def test_sighup_cannot_reload_managed_configuration(self):
        core._need_reload = False
        with (
            mock.patch.dict(
                os.environ,
                {
                    "OPEN_MMI_VEHICLE_CONFIG": "/tmp/profile.json",
                    "OPEN_MMI_BINDINGS_FILE": "/tmp/bindings.json",
                },
                clear=False,
            ),
            self.assertLogs("canbusd", level="INFO") as logs,
        ):
            core._sig_hup(1, None)

        self.assertFalse(core._need_reload)
        self.assertIn("ignored for managed", "\n".join(logs.output))

    def test_bindings_load_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(
                Path(tmp) / "bindings.json",
                {"mute_toggle": {"action": "media.mute.toggle"}},
            )
            with mock.patch.object(core, "_resolve_bindings_path", return_value=path):
                bindings = core._load_bindings()

        self.assertEqual(
            bindings,
            {
                "mute_toggle": {
                    "action": "media.mute.toggle",
                    "module": "audio",
                    "func": "mute_toggle",
                    "args": [],
                }
            },
        )

    def test_bindings_reject_unregistered_event_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(
                Path(tmp) / "bindings.json",
                {
                    "vauxhall_steering_volume_off": {
                        "module": "audio",
                        "func": "mute_toggle",
                    }
                },
            )
            with (
                mock.patch.object(core, "_resolve_bindings_path", return_value=path),
                self.assertLogs("canbusd", level="ERROR") as logs,
            ):
                bindings = core._load_bindings()

        self.assertEqual(bindings, {})
        self.assertIsNone(core.LOADED_BINDINGS)
        self.assertIn("event is not registered", "\n".join(logs.output))

    def test_bindings_reject_unregistered_action_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(
                Path(tmp) / "bindings.json",
                {"mute_toggle": {"action": "sound.module.off"}},
            )
            with (
                mock.patch.object(core, "_resolve_bindings_path", return_value=path),
                self.assertLogs("canbusd", level="ERROR") as logs,
            ):
                bindings = core._load_bindings()

        self.assertEqual(bindings, {})
        self.assertIsNone(core.LOADED_BINDINGS)
        self.assertIn("not registered", "\n".join(logs.output))

    def test_bindings_load_failure_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bindings.json"
            path.write_text("{not-json", encoding="utf-8")
            with (
                mock.patch.object(core, "_resolve_bindings_path", return_value=path),
                self.assertLogs("canbusd", level="ERROR") as logs,
            ):
                self.assertEqual(core._load_bindings(), {})

            self.assertIn("Bindings load failed", "\n".join(logs.output))

    def test_runtime_rejects_unregistered_or_mismatched_profile_events(self):
        cases = (
            {
                "id": "0x231",
                "byte": 3,
                "value": 30,
                "event": "vauxhall_steering_volume_off",
            },
            {
                "id": "0x231",
                "byte": 3,
                "value": "any",
                "event": "mute_toggle",
            },
        )
        for rule in cases:
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as tmp:
                path = self._write_json(
                    Path(tmp) / "config.json",
                    {"rules": [rule]},
                )
                with (
                    mock.patch.object(
                        core,
                        "_resolve_vehicle_config_path",
                        return_value=path,
                    ),
                    self.assertLogs("canbusd", level="ERROR") as logs,
                ):
                    result = core._load_config()

                self.assertIsNone(result[0])
                self.assertIsNone(result[4])
                self.assertIsNone(core.LOADED_VEHICLE)
                self.assertIn("Config load failed", "\n".join(logs.output))

    def test_load_config_filters_rules_for_selected_bus(self):
        config = {
            "default_bus": "comfort",
            "can_buses": {
                "comfort": {"interface": "can0", "bitrate": 100000},
                "powertrain": {"interface": "can1", "bitrate": 500000},
            },
            "rules": [
                {"id": "0x100", "byte": 0, "value": 1, "event": "play_pause"},
                {
                    "id": "0x101",
                    "byte": 1,
                    "value": "any",
                    "event": "brightness_level",
                    "bus": "powertrain",
                },
            ],
            "presence": [
                {"id": "0x200", "on_present": "vehicle_present:on"},
                {
                    "id": "0x201",
                    "timeout_ms": 2500,
                    "status_path": "vehicle.present",
                    "bus": "powertrain",
                },
            ],
            "status": [
                {
                    "id": "0x300",
                    "byte": 0,
                    "type": "raw",
                    "path": "vehicle.reverse_raw",
                },
                {
                    "id": "0x301",
                    "byte": 0,
                    "type": "raw",
                    "path": "engine.speed_raw",
                    "bus": "powertrain",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(Path(tmp) / "config.json", config)
            expected_mtime = path.stat().st_mtime
            with (
                mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path),
                mock.patch.dict(
                    os.environ,
                    {"OPEN_MMI_CAN_BUS": "powertrain", "OPEN_MMI_CAN_INTERFACE": ""},
                    clear=False,
                ),
            ):
                rules, mtime, presence, status, loaded_path, runtime = core._load_config()

        self.assertEqual(loaded_path, path)
        self.assertEqual(mtime, expected_mtime)
        self.assertEqual(runtime.name, "powertrain")
        self.assertEqual(runtime.interface, "can1")
        self.assertEqual(runtime.bitrate, 500000)
        self.assertEqual(rules, {0x101: [(1, None, "brightness_level")]})
        self.assertEqual(
            presence,
            [
                {
                    "id": 0x201,
                    "timeout_ms": 2500,
                    "on_present": None,
                    "on_absent": None,
                    "status_path": "vehicle.present",
                }
            ],
        )
        self.assertEqual(list(status), [0x301])
        self.assertEqual(core.CAN_BUS, "powertrain")
        self.assertEqual(core.IFACE, "can1")

    def test_load_config_normalizes_single_byte_hex_event_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(
                Path(tmp) / "config.json",
                {
                    "rules": [
                        {
                            "id": "0x5C1",
                            "byte": 0,
                            "value": "0x06",
                            "event": "volume_up",
                        },
                        {
                            "id": "0x5C1",
                            "byte": 0,
                            "value": "7",
                            "event": "volume_down",
                        },
                    ]
                },
            )
            with mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path):
                rules, *_ = core._load_config()

        self.assertEqual(
            rules,
            {
                0x5C1: [
                    (0, 0x06, "volume_up"),
                    (0, 7, "volume_down"),
                ]
            },
        )

    def test_load_config_normalizes_multi_byte_event_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(
                Path(tmp) / "config.json",
                {
                    "rules": [
                        {
                            "id": "0x5C1",
                            "matches": [
                                {"byte": 0, "value": "0x13"},
                                {"byte": 2, "value": "0x01"},
                            ],
                            "event": "volume_up",
                        }
                    ]
                },
            )
            with mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path):
                rules, *_ = core._load_config()

        self.assertEqual(
            rules,
            {
                0x5C1: [
                    {
                        "matches": ((0, 0x13), (2, 0x01)),
                        "event": "volume_up",
                    }
                ]
            },
        )

    def test_unchanged_config_returns_existing_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(Path(tmp) / "config.json", {})
            mtime = path.stat().st_mtime
            rules = {1: [(0, 1, "event")]}
            presence = [{"id": 2}]
            status = {3: [{"id": 3}]}
            runtime = CanRuntimeConfig(
                name="comfort",
                default_bus="comfort",
                interface="can0",
                interface_source="test",
            )
            with mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path):
                result = core._load_config(
                    rules,
                    mtime,
                    presence,
                    status,
                    path,
                    runtime,
                )

        self.assertIs(result[0], rules)
        self.assertIs(result[2], presence)
        self.assertIs(result[3], status)
        self.assertIs(result[5], runtime)

    def test_missing_profile_preserves_last_known_good_configuration(self):
        runtime = CanRuntimeConfig(
            name="comfort",
            default_bus="comfort",
            interface="can9",
            interface_source="test",
        )
        rules = {1: [(0, 1, "event")]}
        presence = [{"id": 2}]
        status = {3: [{"id": 3}]}
        old_path = Path("/tmp/last-good.json")
        missing_path = Path("/tmp/definitely-missing-open-mmi-profile.json")

        with (
            mock.patch.object(core, "_resolve_vehicle_config_path", return_value=missing_path),
            self.assertLogs("canbusd", level="ERROR") as logs,
        ):
            result = core._load_config(
                rules,
                1.0,
                presence,
                status,
                old_path,
                runtime,
            )

        self.assertIs(result[0], rules)
        self.assertIs(result[2], presence)
        self.assertIs(result[3], status)
        self.assertEqual(result[4], old_path)
        self.assertIs(result[5], runtime)
        self.assertIn("Vehicle config not found", "\n".join(logs.output))

    def test_profile_bring_up_metadata_is_warned_but_not_executed(self):
        config = {
            "default_bus": "comfort",
            "can_buses": {
                "comfort": {"interface": "can0", "bring_up": True},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(Path(tmp) / "config.json", config)
            with (
                mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path),
                self.assertLogs("canbusd", level="WARNING") as logs,
            ):
                core._load_config()

        self.assertIn("bring_up=true", "\n".join(logs.output))

    def test_invalid_reload_preserves_last_known_good_configuration(self):
        runtime = CanRuntimeConfig(
            name="comfort",
            default_bus="comfort",
            interface="can9",
            interface_source="test",
        )
        rules = {1: [(0, 1, "event")]}
        presence = [{"id": 2}]
        status = {3: [{"id": 3}]}
        old_path = Path("/tmp/last-good.json")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{not-json", encoding="utf-8")
            with (
                mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path),
                self.assertLogs("canbusd", level="ERROR") as logs,
            ):
                result = core._load_config(
                    rules,
                    1.0,
                    presence,
                    status,
                    old_path,
                    runtime,
                )

        self.assertIs(result[0], rules)
        self.assertIs(result[2], presence)
        self.assertIs(result[3], status)
        self.assertEqual(result[4], old_path)
        self.assertIs(result[5], runtime)
        self.assertIn("Config load failed", "\n".join(logs.output))

    def test_sighup_forces_reload_and_is_cleared_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_json(Path(tmp) / "config.json", {})
            core._need_reload = True
            with mock.patch.object(core, "_resolve_vehicle_config_path", return_value=path):
                result = core._load_config(
                    {1: []},
                    path.stat().st_mtime,
                    [],
                    {},
                    path,
                    self.original_runtime,
                )

        self.assertEqual(result[0], {})
        self.assertFalse(core._need_reload)


if __name__ == "__main__":
    unittest.main()
