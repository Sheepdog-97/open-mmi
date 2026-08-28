import errno
import hashlib
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

try:
    import can  # noqa: F401
except ModuleNotFoundError:
    fake_can = types.ModuleType("can")
    fake_can.interface = SimpleNamespace(Bus=None)
    sys.modules["can"] = fake_can

from canbusd import core
from canbusd.can_runtime import CanRuntimeConfig
from canbusd.status_rules import parse_status_rules


class FakeBus:
    def __init__(self, messages):
        self.messages = list(messages)
        self.shutdown_calls = 0

    def recv(self, timeout):
        if not self.messages:
            return None
        item = self.messages.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def shutdown(self):
        self.shutdown_calls += 1


class CanbusdCoreTests(unittest.TestCase):
    def setUp(self):
        runtime_patcher = mock.patch.object(core, "publish_runtime_status")
        self.runtime_publish = runtime_patcher.start()
        self.addCleanup(runtime_patcher.stop)
        core.LOADED_VEHICLE = None
        core.LOADED_BINDINGS = None
        self.runtime = CanRuntimeConfig(
            name="comfort",
            default_bus="comfort",
            interface="can0",
            interface_source="test",
        )

    def test_loaders_record_exact_loaded_identity_and_content_revision(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile_path = root / "vehicles" / "seat_1p" / "config.json"
            bindings_path = root / "bindings" / "default.json"
            profile_path.parent.mkdir(parents=True)
            bindings_path.parent.mkdir(parents=True)
            profile_content = b'{"default_bus":"comfort","can_buses":{"comfort":{"interface":"can0"}}}'
            bindings_content = b'{"play_pause":{"action":"media.playback.toggle"}}'
            profile_path.write_bytes(profile_content)
            bindings_path.write_bytes(bindings_content)

            with (
                mock.patch.object(core, "BASE_DIR", root),
                mock.patch.object(core, "VEHICLE", "seat_1p"),
                mock.patch.object(core, "BINDINGS", "default"),
                mock.patch.object(core, "_resolve_vehicle_config_path", return_value=profile_path),
                mock.patch.object(core, "_resolve_bindings_path", return_value=bindings_path),
            ):
                loaded = core._load_config(None, None)
                bindings = core._load_bindings()

        self.assertEqual(loaded[5].interface, "can0")
        self.assertIn("play_pause", bindings)
        self.assertEqual(bindings["play_pause"]["action"], "media.playback.toggle")
        self.assertEqual(bindings["play_pause"]["module"], "audio")
        self.assertEqual(bindings["play_pause"]["func"], "play_pause")
        self.assertEqual(
            core.LOADED_VEHICLE,
            {
                "source": "maintained",
                "id": "seat_1p",
                "revision": "sha256:" + hashlib.sha256(profile_content).hexdigest(),
            },
        )
        self.assertEqual(
            core.LOADED_BINDINGS,
            {
                "source": "maintained",
                "id": "default",
                "revision": "sha256:" + hashlib.sha256(bindings_content).hexdigest(),
            },
        )


    def test_missing_legacy_explicit_path_migrates_to_nested_catalogue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = (
                root
                / "vehicles"
                / "seat"
                / "leon"
                / "1p-pq35"
                / "config.json"
            )
            profile.parent.mkdir(parents=True)
            profile.write_text("{}", encoding="utf-8")
            (root / "vehicles" / "catalogue.v1.json").write_text(
                '{"schema_version":1,"catalogue_id":"open-mmi.maintained-vehicles",'
                '"profiles":{"seat-leon-1p-pq35":{'
                '"path":"seat/leon/1p-pq35/config.json",'
                '"aliases":["seat_1p"]}}}',
                encoding="utf-8",
            )
            legacy = root / "vehicles" / "seat_1p" / "config.json"

            with (
                mock.patch.object(core, "BASE_DIR", root),
                mock.patch.object(core, "VEHICLE", "seat_1p"),
                mock.patch.dict(
                    core.os.environ,
                    {
                        "OPEN_MMI_INSTALL_DIR": str(root),
                        "OPEN_MMI_VEHICLE_CONFIG": str(legacy),
                    },
                    clear=False,
                ),
            ):
                self.assertEqual(core._resolve_vehicle_config_path(), profile)

    def test_missing_arbitrary_explicit_profile_path_does_not_redirect(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "external" / "config.json"
            with mock.patch.dict(
                core.os.environ,
                {"OPEN_MMI_VEHICLE_CONFIG": str(missing)},
                clear=False,
            ):
                self.assertEqual(core._resolve_vehicle_config_path(), missing)

    def test_document_source_recognizes_installed_catalogue_outside_package_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package_root = root / "venv" / "site-packages"
            install_root = root / "opt" / "open-mmi"
            custom_root = root / "home" / "open-mmi" / ".config" / "open-mmi"

            with (
                mock.patch.object(core, "BASE_DIR", package_root),
                mock.patch.object(core, "USER_CONFIG_DIR", custom_root),
                mock.patch.dict(
                    core.os.environ,
                    {"OPEN_MMI_INSTALL_DIR": str(install_root)},
                    clear=False,
                ),
            ):
                self.assertEqual(
                    core._document_source(
                        "vehicle",
                        "seat_1p",
                        install_root / "vehicles" / "seat_1p" / "config.json",
                    ),
                    "maintained",
                )
                self.assertEqual(
                    core._document_source(
                        "bindings",
                        "default",
                        install_root / "bindings" / "default.json",
                    ),
                    "maintained",
                )
                self.assertEqual(
                    core._document_source(
                        "vehicle",
                        "seat_1p",
                        package_root / "vehicles" / "seat_1p" / "config.json",
                    ),
                    "maintained",
                )
                self.assertEqual(
                    core._document_source(
                        "bindings",
                        "default",
                        custom_root / "bindings" / "default.json",
                    ),
                    "custom",
                )
                self.assertEqual(
                    core._document_source(
                        "vehicle",
                        "seat_1p",
                        root / "elsewhere" / "config.json",
                    ),
                    "external",
                )

    def test_loaded_runtime_evidence_is_bounded_and_publication_failure_is_isolated(self):
        core.LOADED_VEHICLE = {
            "source": "custom",
            "id": "my-seat",
            "revision": "sha256:" + "a" * 64,
        }
        core.LOADED_BINDINGS = {
            "source": "maintained",
            "id": "default",
            "revision": "sha256:" + "b" * 64,
        }

        core._safe_publish_loaded_runtime(self.runtime)
        self.runtime_publish.assert_called_once_with(
            {
                "api_version": 1,
                "state": "ready",
                "errors": [],
                "vehicle": core.LOADED_VEHICLE,
                "bindings": core.LOADED_BINDINGS,
                "active_bus": "comfort",
                "interface": "can0",
            }
        )

        self.runtime_publish.reset_mock(side_effect=True)
        self.runtime_publish.side_effect = OSError("read only")
        with self.assertLogs("canbusd", level="ERROR") as logs:
            core._safe_publish_loaded_runtime(self.runtime)
        self.assertIn("Loaded runtime publication failed", "\n".join(logs.output))

    def _config(self, rules=None, presence=None, status_rules=None):
        return (
            rules or {},
            1.0,
            presence or [],
            status_rules or {},
            core.Path("/tmp/test-open-mmi-profile.json"),
            self.runtime,
        )

    def _run_main(self, bus, config, iterations, monotonic=None, reload_interval=60):
        monotonic = monotonic or mock.Mock(return_value=1.0)
        with (
            mock.patch.object(core, "_load_config", return_value=config),
            mock.patch.object(core, "_load_bindings", return_value={}),
            mock.patch.object(core.Path, "exists", return_value=True),
            mock.patch.object(core.time, "monotonic", monotonic),
            mock.patch.object(core.time, "sleep"),
            mock.patch.object(core.can.interface, "Bus", return_value=bus) as open_bus,
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "CAN_BUS", "comfort"),
            mock.patch.object(core, "RELOAD_INTERVAL", reload_interval),
        ):
            core.main(max_iterations=iterations, dispatch_fn=core.dispatch)
        return open_bus

    def test_recoverable_can_link_error_accepts_python_can_and_os_error_codes(self):
        wrapped = RuntimeError("device removed")
        wrapped.error_code = errno.ENODEV

        self.assertTrue(core._is_recoverable_can_link_error(wrapped))
        self.assertTrue(
            core._is_recoverable_can_link_error(
                OSError(errno.ENETDOWN, "network is down")
            )
        )
        self.assertFalse(core._is_recoverable_can_link_error(RuntimeError("boom")))

    def test_main_reopens_socketcan_after_recoverable_receive_errors(self):
        for error_code in (errno.ENETDOWN, errno.ENODEV):
            with self.subTest(error_code=error_code):
                failed_bus = FakeBus([OSError(error_code, "CAN link unavailable")])
                recovered_bus = FakeBus([None])
                open_bus = mock.Mock(side_effect=[failed_bus, recovered_bus])

                with (
                    mock.patch.object(
                        core, "_managed_configuration_mode", return_value=True
                    ),
                    mock.patch.object(
                        core, "_load_config", return_value=self._config()
                    ),
                    mock.patch.object(core, "_load_bindings", return_value={}),
                    mock.patch.object(core.Path, "exists", return_value=True),
                    mock.patch.object(core.time, "monotonic", return_value=1.0),
                    mock.patch.object(core.time, "sleep") as sleep,
                    mock.patch.object(core.can.interface, "Bus", open_bus),
                    mock.patch.object(core, "_safe_reset_status") as reset_status,
                    mock.patch.object(core, "IFACE", "can0"),
                    mock.patch.object(core, "CAN_BUS", "comfort"),
                    self.assertLogs("canbusd", level="WARNING") as logs,
                ):
                    core.main(max_iterations=2, dispatch_fn=core.dispatch)

                self.assertEqual(open_bus.call_count, 2)
                self.assertEqual(failed_bus.shutdown_calls, 1)
                self.assertEqual(recovered_bus.shutdown_calls, 1)
                self.assertEqual(reset_status.call_count, 2)
                sleep.assert_called_once_with(1)
                self.assertIn("became unavailable", "\n".join(logs.output))

    def test_main_retries_recoverable_socketcan_open_error(self):
        recovered_bus = FakeBus([None])
        open_bus = mock.Mock(
            side_effect=[
                OSError(errno.ENODEV, "CAN device removed"),
                recovered_bus,
            ]
        )

        with (
            mock.patch.object(core, "_managed_configuration_mode", return_value=True),
            mock.patch.object(core, "_load_config", return_value=self._config()),
            mock.patch.object(core, "_load_bindings", return_value={}),
            mock.patch.object(core.Path, "exists", return_value=True),
            mock.patch.object(core.time, "monotonic", return_value=1.0),
            mock.patch.object(core.time, "sleep") as sleep,
            mock.patch.object(core.can.interface, "Bus", open_bus),
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "CAN_BUS", "comfort"),
            self.assertLogs("canbusd", level="WARNING") as logs,
        ):
            core.main(max_iterations=2, dispatch_fn=core.dispatch)

        self.assertEqual(open_bus.call_count, 2)
        self.assertEqual(recovered_bus.shutdown_calls, 1)
        sleep.assert_called_once_with(1)
        self.assertIn("temporarily unavailable while opening", "\n".join(logs.output))

    def test_managed_main_pins_documents_until_process_restart(self):
        bus = FakeBus([None, None])
        load_config = mock.Mock(return_value=self._config())
        load_bindings = mock.Mock(return_value={})
        monotonic = mock.Mock(side_effect=[1.0, 1.1, 120.0, 120.1])

        with (
            mock.patch.object(core, "_managed_configuration_mode", return_value=True),
            mock.patch.object(core, "_load_config", load_config),
            mock.patch.object(core, "_load_bindings", load_bindings),
            mock.patch.object(core.Path, "exists", return_value=True),
            mock.patch.object(core.time, "monotonic", monotonic),
            mock.patch.object(core.can.interface, "Bus", return_value=bus),
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "CAN_BUS", "comfort"),
            mock.patch.object(core, "RELOAD_INTERVAL", 0),
        ):
            core.main(max_iterations=2, dispatch_fn=core.dispatch)

        self.assertEqual(load_config.call_count, 1)
        self.assertEqual(load_bindings.call_count, 1)
        self.assertEqual(self.runtime_publish.call_count, 1)
        self.assertEqual(bus.shutdown_calls, 1)

    def test_managed_main_reports_original_revisions_after_files_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            custom_root = Path(temporary) / "open-mmi"
            profile_path = custom_root / "vehicles" / "custom-seat" / "config.json"
            bindings_path = custom_root / "bindings" / "custom-bindings.json"
            profile_path.parent.mkdir(parents=True)
            bindings_path.parent.mkdir(parents=True)
            original_profile = b'{"default_bus":"comfort","rules":[]}'
            changed_profile = b'{"default_bus":"comfort","rules":[],"status":[]}'
            original_bindings = b'{}'
            changed_bindings = b'{"mute":{"module":"audio","func":"mute_toggle"}}'
            profile_path.write_bytes(original_profile)
            bindings_path.write_bytes(original_bindings)

            class MutatingBus(FakeBus):
                def recv(self, timeout):
                    if self.messages:
                        profile_path.write_bytes(changed_profile)
                        bindings_path.write_bytes(changed_bindings)
                    return super().recv(timeout)

            bus = MutatingBus([None, None])
            monotonic = mock.Mock(side_effect=[1.0, 1.1, 120.0, 120.1])

            with (
                mock.patch.dict(
                    core.os.environ,
                    {
                        "OPEN_MMI_VEHICLE_CONFIG": str(profile_path),
                        "OPEN_MMI_BINDINGS_FILE": str(bindings_path),
                    },
                    clear=False,
                ),
                mock.patch.object(core, "USER_CONFIG_DIR", custom_root),
                mock.patch.object(core, "VEHICLE", "custom-seat"),
                mock.patch.object(core, "BINDINGS", "custom-bindings"),
                mock.patch.object(core, "CAN_RUNTIME", self.runtime),
                mock.patch.object(core, "CAN_BUS", "comfort"),
                mock.patch.object(core, "IFACE", "can0"),
                mock.patch.object(core.Path, "exists", return_value=True),
                mock.patch.object(core.time, "monotonic", monotonic),
                mock.patch.object(core.can.interface, "Bus", return_value=bus),
                mock.patch.object(core, "RELOAD_INTERVAL", 0),
            ):
                core.main(max_iterations=2, dispatch_fn=core.dispatch)

        original_vehicle_revision = "sha256:" + hashlib.sha256(original_profile).hexdigest()
        original_bindings_revision = "sha256:" + hashlib.sha256(original_bindings).hexdigest()
        self.assertEqual(core.LOADED_VEHICLE["revision"], original_vehicle_revision)
        self.assertEqual(core.LOADED_BINDINGS["revision"], original_bindings_revision)
        self.runtime_publish.assert_called_once()
        runtime = self.runtime_publish.call_args.args[0]
        self.assertEqual(runtime["vehicle"]["revision"], original_vehicle_revision)
        self.assertEqual(runtime["bindings"]["revision"], original_bindings_revision)

    def test_main_uses_bounded_action_queue_by_default(self):
        event_rules = {0x200: [(0, 1, "button:pressed")]}
        binding = {"module": "audio", "func": "play_pause"}
        message = SimpleNamespace(arbitration_id=0x200, data=bytes([1]), dlc=1)
        bus = FakeBus([message])
        action_queue = mock.Mock()

        with (
            mock.patch.object(core, "_load_config", return_value=self._config(rules=event_rules)),
            mock.patch.object(core, "_load_bindings", return_value={"button:pressed": binding}),
            mock.patch.object(core.Path, "exists", return_value=True),
            mock.patch.object(core.time, "monotonic", return_value=1.0),
            mock.patch.object(core.can.interface, "Bus", return_value=bus),
            mock.patch.object(core, "ActionQueue", return_value=action_queue),
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "CAN_BUS", "comfort"),
            mock.patch.object(core, "RELOAD_INTERVAL", 60),
        ):
            core.main(max_iterations=1)

        action_queue.dispatch.assert_called_once_with("button:pressed", binding)
        action_queue.close.assert_called_once_with()
        self.assertEqual(bus.shutdown_calls, 1)

    def test_status_reset_is_persisted_and_failure_is_isolated(self):
        with mock.patch.object(core, "reset_status") as reset:
            core._safe_reset_status()
        reset.assert_called_once_with(persist=True, notify=True)

        with (
            mock.patch.object(core, "reset_status", side_effect=OSError("read only")),
            self.assertLogs("canbusd", level="ERROR") as logs,
        ):
            core._safe_reset_status()

        self.assertIn("Status reset failed", "\n".join(logs.output))

    def test_status_frame_is_decoded_published_and_bus_is_closed(self):
        status_rules = parse_status_rules(
            [
                {
                    "id": "0x100",
                    "byte": 0,
                    "type": "bool",
                    "path": "vehicle.reverse",
                    "true": "0x01",
                    "false": "0x00",
                }
            ]
        )
        message = SimpleNamespace(arbitration_id=0x100, data=bytes([1]), dlc=1)
        bus = FakeBus([message])

        with mock.patch.object(core, "publish_status") as publish:
            open_bus = self._run_main(bus, self._config(status_rules=status_rules), 1)

        publish.assert_called_once_with({"vehicle": {"reverse": True}})
        open_bus.assert_called_once_with(channel="can0", interface="socketcan")
        self.assertEqual(bus.shutdown_calls, 1)

    def test_status_publication_failure_is_logged_and_does_not_stop_receive_loop(self):
        status_rules = parse_status_rules(
            [
                {
                    "id": "0x100",
                    "byte": 0,
                    "type": "bool",
                    "path": "vehicle.reverse",
                    "true": "0x01",
                    "false": "0x00",
                }
            ]
        )
        messages = [
            SimpleNamespace(arbitration_id=0x100, data=bytes([1]), dlc=1),
            SimpleNamespace(arbitration_id=0x100, data=bytes([0]), dlc=1),
        ]
        bus = FakeBus(messages)

        with (
            mock.patch.object(core, "publish_status", side_effect=OSError("disk full")) as publish,
            self.assertLogs("canbusd", level="ERROR") as logs,
        ):
            self._run_main(bus, self._config(status_rules=status_rules), 2)

        self.assertEqual(publish.call_count, 2)
        self.assertEqual(bus.shutdown_calls, 1)
        self.assertIn("Status publication failed", "\n".join(logs.output))

    def test_short_frames_do_not_publish_or_dispatch(self):
        status_rules = parse_status_rules(
            [
                {
                    "id": "0x100",
                    "byte": 2,
                    "type": "bool",
                    "path": "vehicle.reverse",
                    "true": "0x01",
                    "false": "0x00",
                }
            ]
        )
        event_rules = {0x100: [(2, 1, "reverse:on")]}
        message = SimpleNamespace(arbitration_id=0x100, data=bytes([1]), dlc=1)
        bus = FakeBus([message])

        with (
            mock.patch.object(core, "publish_status") as publish,
            mock.patch.object(core, "dispatch") as dispatch,
        ):
            self._run_main(
                bus,
                self._config(rules=event_rules, status_rules=status_rules),
                1,
            )

        publish.assert_not_called()
        dispatch.assert_not_called()
        self.assertEqual(bus.shutdown_calls, 1)

    def test_fixed_value_event_dispatches_on_first_observed_press_and_each_new_edge(self):
        event_rules = {0x200: [(0, 1, "button:pressed")]}
        messages = [
            SimpleNamespace(arbitration_id=0x200, data=bytes([1]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([1]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([0]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([1]), dlc=1),
        ]
        bus = FakeBus(messages)

        with mock.patch.object(core, "dispatch") as dispatch:
            self._run_main(bus, self._config(rules=event_rules), 4)

        self.assertEqual(
            dispatch.call_args_list,
            [
                mock.call("button:pressed", None),
                mock.call("button:pressed", None),
            ],
        )

    def test_fixed_value_events_dispatch_when_button_codes_change_directly(self):
        event_rules = {
            0x200: [
                (0, 1, "button:previous"),
                (0, 2, "button:next"),
            ]
        }
        messages = [
            SimpleNamespace(arbitration_id=0x200, data=bytes([1]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([2]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([2]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([7]), dlc=1),
        ]
        bus = FakeBus(messages)

        with mock.patch.object(core, "dispatch") as dispatch:
            self._run_main(bus, self._config(rules=event_rules), 4)

        self.assertEqual(
            dispatch.call_args_list,
            [
                mock.call("button:previous", None),
                mock.call("button:next", None),
            ],
        )

    def test_multi_byte_event_rules_match_the_whole_predicate_and_edge_detect(self):
        event_rules = {
            0x5C1: [
                {
                    "matches": ((0, 0x13), (2, 0x01)),
                    "event": "volume_up",
                },
                {
                    "matches": ((0, 0x13), (2, 0x0F)),
                    "event": "volume_down",
                },
            ]
        }
        messages = [
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x01, 0x63]), dlc=4),
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x01, 0x63]), dlc=4),
            # Same direction byte from the other Superb thumbwheel must not match.
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x14, 0x00, 0x01, 0x63]), dlc=4),
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x01, 0x63]), dlc=4),
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x0F, 0x63]), dlc=4),
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x0F, 0x63]), dlc=4),
        ]
        bus = FakeBus(messages)

        with mock.patch.object(core, "dispatch") as dispatch:
            self._run_main(bus, self._config(rules=event_rules), len(messages))

        self.assertEqual(
            dispatch.call_args_list,
            [
                mock.call("volume_up", None),
                mock.call("volume_up", None),
                mock.call("volume_down", None),
            ],
        )

    def test_multi_byte_event_rule_ignores_short_frames_without_rearming(self):
        event_rules = {
            0x5C1: [
                {
                    "matches": ((0, 0x13), (2, 0x01)),
                    "event": "volume_up",
                }
            ]
        }
        messages = [
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x01]), dlc=3),
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13]), dlc=1),
            SimpleNamespace(arbitration_id=0x5C1, data=bytes([0x13, 0x00, 0x01]), dlc=3),
        ]
        bus = FakeBus(messages)

        with mock.patch.object(core, "dispatch") as dispatch:
            self._run_main(bus, self._config(rules=event_rules), len(messages))

        dispatch.assert_called_once_with("volume_up", None)

    def test_publish_presence_updates_status_and_dispatches_configured_event(self):
        rule = {
            "id": 0x65F,
            "status_path": "vehicle.present",
            "on_present": "vehicle:on",
            "on_absent": "vehicle:off",
        }
        binding = {"module": "screen", "func": "on"}
        with (
            mock.patch.object(core, "publish_status") as publish,
            mock.patch.object(core, "dispatch") as dispatch,
        ):
            core._publish_presence(rule, True, {"vehicle:on": binding})

        publish.assert_called_once_with(
            {"presence": {"0x65F": True}, "vehicle": {"present": True}}
        )
        dispatch.assert_called_once_with("vehicle:on", binding)

    def test_any_value_rule_dispatches_only_when_value_changes(self):
        event_rules = {0x200: [(0, None, "dimmer:changed")]}
        messages = [
            SimpleNamespace(arbitration_id=0x200, data=bytes([10]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([10]), dlc=1),
            SimpleNamespace(arbitration_id=0x200, data=bytes([11]), dlc=1),
        ]
        bus = FakeBus(messages)

        with mock.patch.object(core, "dispatch") as dispatch:
            self._run_main(bus, self._config(rules=event_rules), 3)

        self.assertEqual(
            dispatch.call_args_list,
            [
                mock.call("dimmer:changed", None, [10]),
                mock.call("dimmer:changed", None, [11]),
            ],
        )

    def test_presence_transitions_publish_once_per_state_change(self):
        presence_rule = {
            "id": 0x65F,
            "timeout_ms": 1000,
            "status_path": "vehicle.present",
            "on_present": "vehicle:on",
            "on_absent": "vehicle:off",
        }
        last_seen = {}
        present_state = {}

        with mock.patch.object(core, "_publish_presence") as publish_presence:
            core._check_presence([presence_rule], last_seen, present_state, {}, 0.0)
            core._check_presence([presence_rule], last_seen, present_state, {}, 0.5)
            last_seen[0x65F] = 1.0
            core._check_presence([presence_rule], last_seen, present_state, {}, 1.1)
            core._check_presence([presence_rule], last_seen, present_state, {}, 1.5)
            core._check_presence([presence_rule], last_seen, present_state, {}, 2.1)

        self.assertEqual(
            publish_presence.call_args_list,
            [
                mock.call(presence_rule, False, {}),
                mock.call(presence_rule, True, {}),
                mock.call(presence_rule, False, {}),
            ],
        )

    def test_profile_reload_resets_toggle_latch_state(self):
        first_rules = parse_status_rules(
            [
                {
                    "id": "0x3E1",
                    "byte": 0,
                    "type": "bool",
                    "path": "climate.rear_window_heater_requested",
                    "mask": "0x04",
                    "true": "0x04",
                    "false": "0x00",
                    "state": "toggle_latch",
                    "initial": False,
                }
            ]
        )
        reloaded_rules = parse_status_rules(
            [
                {
                    "id": "0x3E1",
                    "byte": 0,
                    "type": "bool",
                    "path": "climate.rear_window_heater_requested",
                    "mask": "0x04",
                    "true": "0x04",
                    "false": "0x00",
                    "state": "toggle_latch",
                    "initial": False,
                }
            ]
        )
        messages = [
            SimpleNamespace(arbitration_id=0x3E1, data=bytes([0x04]), dlc=1),
            SimpleNamespace(arbitration_id=0x3E1, data=bytes([0x00]), dlc=1),
        ]
        bus = FakeBus(messages)
        load_results = [
            self._config(status_rules=first_rules),
            self._config(status_rules=first_rules),
            self._config(status_rules=reloaded_rules),
        ]
        monotonic = mock.Mock(side_effect=[1.0, 1.1, 2.0, 2.1])

        with (
            mock.patch.object(core, "_load_config", side_effect=load_results),
            mock.patch.object(core, "_load_bindings", return_value={}),
            mock.patch.object(core.Path, "exists", return_value=True),
            mock.patch.object(core.time, "monotonic", monotonic),
            mock.patch.object(core.can.interface, "Bus", return_value=bus),
            mock.patch.object(core, "publish_status") as publish,
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "CAN_BUS", "comfort"),
            mock.patch.object(core, "RELOAD_INTERVAL", 0.5),
        ):
            core.main(max_iterations=2, dispatch_fn=core.dispatch)

        self.assertEqual(
            publish.call_args_list,
            [
                mock.call({"climate": {"rear_window_heater_requested": True}}),
                mock.call({"climate": {"rear_window_heater_requested": False}}),
            ],
        )
        self.assertEqual(bus.shutdown_calls, 1)

    def test_bus_is_closed_when_receive_raises(self):
        bus = FakeBus([RuntimeError("CAN receive failed")])

        with self.assertRaisesRegex(RuntimeError, "CAN receive failed"):
            self._run_main(bus, self._config(), 1)

        self.assertEqual(bus.shutdown_calls, 1)

    def test_open_bus_is_closed_if_interface_disappears(self):
        bus = FakeBus([None])
        with (
            mock.patch.object(core, "_load_config", return_value=self._config()),
            mock.patch.object(core, "_load_bindings", return_value={}),
            mock.patch.object(core.Path, "exists", side_effect=[True, False]),
            mock.patch.object(core.time, "monotonic", return_value=1.0),
            mock.patch.object(core.time, "sleep"),
            mock.patch.object(core.can.interface, "Bus", return_value=bus),
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "CAN_BUS", "comfort"),
            mock.patch.object(core, "RELOAD_INTERVAL", 60),
        ):
            core.main(max_iterations=2, dispatch_fn=core.dispatch)

        self.assertEqual(bus.shutdown_calls, 1)

    def test_missing_interface_does_not_open_socketcan(self):
        bus = FakeBus([])
        with (
            mock.patch.object(core, "_load_config", return_value=self._config()),
            mock.patch.object(core, "_load_bindings", return_value={}),
            mock.patch.object(core.Path, "exists", return_value=False),
            mock.patch.object(core.time, "monotonic", return_value=1.0),
            mock.patch.object(core.time, "sleep") as sleep,
            mock.patch.object(core.can.interface, "Bus", return_value=bus) as open_bus,
            mock.patch.object(core, "IFACE", "can0"),
            mock.patch.object(core, "RELOAD_INTERVAL", 60),
        ):
            core.main(max_iterations=2, dispatch_fn=core.dispatch)

        open_bus.assert_not_called()
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(bus.shutdown_calls, 0)


if __name__ == "__main__":
    unittest.main()
