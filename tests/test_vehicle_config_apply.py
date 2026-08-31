from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ui import vehicle_config_apply as apply
from ui import vehicle_config_coordinator as coordinator
from ui import vehicle_setup


class VehicleConfigurationApplyOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.maintained = self.root / "installed"
        self.custom = self.root / "custom"
        self.profile_path = self.maintained / "vehicles" / "seat_1p" / "config.json"
        self.bindings_path = self.maintained / "bindings" / "default.json"
        self.profile_path.parent.mkdir(parents=True)
        self.bindings_path.parent.mkdir(parents=True)
        self.profile_bytes = json.dumps(
            {
                "default_bus": "comfort",
                "can_buses": {
                    "comfort": {
                        "interface": "can0",
                        "bitrate": 100000,
                        "provisioning": "udev",
                    }
                },
                "rules": [
                    {
                        "id": "0x100",
                        "byte": 0,
                        "value": 1,
                        "event": "play_pause",
                    }
                ],
                "presence": [],
                "status": [],
            },
            sort_keys=True,
        ).encode("utf-8")
        self.bindings_bytes = json.dumps(
            {"play_pause": {"action": "media.playback.toggle"}},
            sort_keys=True,
        ).encode("utf-8")
        self._write_trusted(self.profile_path, self.profile_bytes)
        self._write_trusted(self.bindings_path, self.bindings_bytes)
        self.roots = vehicle_setup.CatalogueRoots(self.maintained, self.custom)
        self.target = {
            "vehicle": {
                "source": "maintained",
                "id": "seat_1p",
                "revision": "sha256:" + hashlib.sha256(self.profile_bytes).hexdigest(),
            },
            "bindings": {
                "source": "maintained",
                "id": "default",
                "revision": "sha256:" + hashlib.sha256(self.bindings_bytes).hexdigest(),
            },
            "runtime": {
                "mode": "single",
                "active_bus": "comfort",
                "buses": {"comfort": {"interface": "can0"}},
            },
        }
        self.paths = apply.ApplyPaths(
            descriptor=self.root / "etc" / "open-mmi" / "vehicle-configuration.json",
            runtime_dropin=self.root / "home" / "user" / "10-can-runtime.conf",
            udev_rules=self.root / "etc" / "udev" / "80-canbus.rules",
            runtime_status=self.root / "run" / "status.json",
            rollback_root=self.root / "rollback",
        )
        for path in (
            self.paths.descriptor,
            self.paths.runtime_dropin,
            self.paths.udev_rules,
            self.paths.runtime_status,
            self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.commands = []
        self.operations = apply.RootApplyOperations(
            roots=self.roots,
            paths=self.paths,
            service_user="open-mmi",
            service_uid=os.getuid(),
            service_gid=os.getgid(),
            service_home=self.root / "home" / "user",
            provision_request_path=self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
            command_runner=lambda argv, as_user: self.commands.append((tuple(argv), as_user)),
            loaded_timeout=0.2,
            poll_interval=0.0,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _write_trusted(path: Path, content: bytes | str, mode: int = 0o644) -> None:
        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)
        path.chmod(mode)

    def loaded(self, *, updated_at: float = 1.0):
        return {
            "api_version": 1,
            "state": "ready",
            "errors": [],
            "vehicle": self.target["vehicle"],
            "bindings": self.target["bindings"],
            "active_bus": "comfort",
            "interface": "can0",
            "updated_at": updated_at,
        }

    def write_status(self, loaded=None) -> None:
        runtime = loaded or self.loaded()
        wrapper = {
            "api_version": 1,
            "updated_at": runtime["updated_at"],
            "runtime": {
                key: runtime[key]
                for key in (
                    "api_version",
                    "state",
                    "errors",
                    "vehicle",
                    "bindings",
                    "active_bus",
                    "interface",
                )
            },
            "state": {},
        }
        self._write_trusted(self.paths.runtime_status, json.dumps(wrapper))

    def test_render_artifacts_revalidates_revisions_and_selected_bus(self) -> None:
        rendered = apply.render_artifacts(
            self.target,
            self.roots,
            applied_at="2026-07-20T12:00:00+00:00",
        )
        descriptor = json.loads(rendered.descriptor)
        self.assertEqual(descriptor["vehicle"], self.target["vehicle"])
        self.assertEqual(descriptor["bindings"], self.target["bindings"])
        self.assertEqual(descriptor["runtime"], self.target["runtime"])
        self.assertIn("OPEN_MMI_CAN_BUS=comfort", rendered.runtime_dropin.decode())
        self.assertIn("OPEN_MMI_CAN_INTERFACE=can0", rendered.runtime_dropin.decode())
        self.assertIn("bitrate 100000", rendered.udev_rules.decode())

        changed = json.loads(json.dumps(self.target))
        changed["vehicle"]["revision"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(apply.ApplyOperationError, "changed"):
            apply.render_artifacts(changed, self.roots)

    def test_runtime_dropin_writes_canonical_bus_name(self) -> None:
        target = json.loads(json.dumps(self.target))
        target["runtime"] = {
            "mode": "single",
            "active_bus": "infotainment",
            "buses": {"infotainment": {"interface": "can1"}},
        }
        rendered = apply._runtime_dropin_text(
            target, self.profile_path, self.bindings_path
        )
        self.assertIn("OPEN_MMI_CAN_BUS=infotainment", rendered)
        self.assertIn("OPEN_MMI_CAN_INTERFACE=can1", rendered)
        self.assertNotIn("OPEN_MMI_CAN_BUS=comfort", rendered)

    def test_vcan_qualification_suppresses_hardware_udev_provisioning(self) -> None:
        target = json.loads(json.dumps(self.target))
        target["runtime"]["buses"]["comfort"]["interface"] = "vcan0"
        rendered = apply.render_artifacts(
            target,
            self.roots,
            suppress_can_provisioning=True,
        )
        rules = rendered.udev_rules.decode("utf-8")
        self.assertIn("vcan qualification", rules)
        self.assertNotIn("type can bitrate", rules)
        self.assertNotIn('KERNEL=="vcan0"', rules)

    def test_install_uses_atomic_fixed_destinations_and_expected_ownership(self) -> None:
        self.operations.install(self.target)
        descriptor = json.loads(self.paths.descriptor.read_text(encoding="utf-8"))
        self.assertEqual(descriptor["vehicle"], self.target["vehicle"])
        self.assertIn("OPEN_MMI_VEHICLE=seat_1p", self.paths.runtime_dropin.read_text())
        self.assertIn("KERNEL==\"can0\"", self.paths.udev_rules.read_text())
        self.assertEqual(self.paths.runtime_dropin.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.paths.descriptor.stat().st_mode & 0o777, 0o644)


    def test_install_rejects_world_writable_catalogue_content(self) -> None:
        self.profile_path.chmod(0o666)
        with self.assertRaisesRegex(apply.ApplyOperationError, "permissions"):
            self.operations.install(self.target)
        self.assertFalse(self.paths.descriptor.exists())

    def test_snapshot_rejects_unsafe_existing_generated_file(self) -> None:
        self.paths.runtime_dropin.write_text("unsafe\n", encoding="utf-8")
        self.paths.runtime_dropin.chmod(0o666)
        self.write_status()
        with self.assertRaisesRegex(apply.ApplyOperationError, "permissions"):
            self.operations.snapshot("configuration-" + "d" * 32)

    def test_atomic_replace_rejects_a_symlink_destination(self) -> None:
        victim = self.root / "victim"
        victim.write_text("unchanged", encoding="utf-8")
        destination = self.root / "destination"
        destination.symlink_to(victim)
        with self.assertRaisesRegex(apply.ApplyOperationError, "regular file"):
            apply._atomic_replace(
                destination,
                b"changed",
                mode=0o644,
                uid=os.getuid(),
                gid=os.getgid(),
            )
        self.assertEqual(victim.read_text(encoding="utf-8"), "unchanged")

    def test_snapshot_is_persistent_checksum_validated_and_restorable(self) -> None:
        self._write_trusted(self.paths.descriptor, "old descriptor\n")
        self._write_trusted(self.paths.runtime_dropin, "old dropin\n")
        self._write_trusted(self.paths.udev_rules, "old udev\n")
        self.write_status()
        transaction_id = "configuration-" + "a" * 32
        snapshot = self.operations.snapshot(transaction_id)
        loaded = self.operations.load_snapshot(transaction_id)
        self.assertEqual(loaded.previous_loaded["vehicle"], self.target["vehicle"])
        self.assertTrue((snapshot.directory / "manifest.json").exists())

        self._write_trusted(self.paths.descriptor, "new descriptor\n")
        self._write_trusted(self.paths.runtime_dropin, "new dropin\n")
        self._write_trusted(self.paths.udev_rules, "new udev\n")
        self.operations.restore(loaded)
        self.assertEqual(self.paths.descriptor.read_text(), "old descriptor\n")
        self.assertEqual(self.paths.runtime_dropin.read_text(), "old dropin\n")
        self.assertEqual(self.paths.udev_rules.read_text(), "old udev\n")
        self.assertTrue(self.operations.restoration_verified(loaded, self.loaded()))
        self.assertEqual(
            self.commands,
            [
                (("systemctl", "--user", "daemon-reload"), True),
                (("udevadm", "control", "--reload-rules"), False),
                (("systemctl", "start", "open-mmi-vehicle-can-provision.service"), False),
            ],
        )

        stored = snapshot.directory / "descriptor.bin"
        stored.write_bytes(b"tampered")
        with self.assertRaisesRegex(apply.ApplyOperationError, "checksum"):
            self.operations.load_snapshot(transaction_id)

    def test_verified_snapshot_can_be_discarded(self) -> None:
        self._write_trusted(self.paths.descriptor, "old descriptor\n")
        self._write_trusted(self.paths.runtime_dropin, "old dropin\n")
        self._write_trusted(self.paths.udev_rules, "old udev\n")
        self.write_status()
        snapshot = self.operations.snapshot("configuration-" + "9" * 32)
        self.assertTrue(snapshot.directory.exists())
        self.operations.discard_snapshot(snapshot)
        self.assertFalse(snapshot.directory.exists())

    def test_snapshot_preserves_absent_files_and_restore_removes_new_files(self) -> None:
        self.write_status()
        snapshot = self.operations.snapshot("configuration-" + "b" * 32)
        self.operations.install(self.target)
        self.assertTrue(self.paths.descriptor.exists())
        self.operations.restore(snapshot)
        self.assertFalse(self.paths.descriptor.exists())
        self.assertFalse(self.paths.runtime_dropin.exists())
        self.assertFalse(self.paths.udev_rules.exists())

    def test_reload_and_restart_use_only_fixed_commands(self) -> None:
        self.operations.reload(self.target)
        self.operations.restart()
        self.assertEqual(
            self.commands,
            [
                (("systemctl", "--user", "daemon-reload"), True),
                (("udevadm", "control", "--reload-rules"), False),
                (("systemctl", "start", "open-mmi-vehicle-can-provision.service"), False),
                (("systemctl", "--user", "restart", "canbusd.service"), True),
            ],
        )

    def test_vcan_qualification_does_not_reload_or_trigger_udev(self) -> None:
        operations = apply.RootApplyOperations(
            roots=self.roots,
            paths=self.paths,
            service_user="open-mmi",
            service_uid=os.getuid(),
            service_gid=os.getgid(),
            service_home=self.root / "home" / "user",
            provision_request_path=self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
            command_runner=lambda argv, as_user: self.commands.append((tuple(argv), as_user)),
            suppress_can_provisioning=True,
        )
        operations.reload(self.target)
        snapshot = apply.ApplySnapshot(
            "configuration-" + "8" * 32,
            self.paths.rollback_root / ("configuration-" + "8" * 32),
            {
                "descriptor": apply.FileSnapshot(False, b"", 0, 0, 0),
                "runtime-dropin": apply.FileSnapshot(False, b"", 0, 0, 0),
                "udev-rules": apply.FileSnapshot(False, b"", 0, 0, 0),
            },
            {},
        )
        operations.restore(snapshot)
        self.assertEqual(
            self.commands,
            [
                (("systemctl", "--user", "daemon-reload"), True),
                (("systemctl", "--user", "daemon-reload"), True),
            ],
        )

    def test_loaded_runtime_rejects_stale_status_after_restart(self) -> None:
        wall = 100.0
        operations = apply.RootApplyOperations(
            roots=self.roots,
            paths=self.paths,
            service_user="open-mmi",
            service_uid=os.getuid(),
            service_gid=os.getgid(),
            service_home=self.root / "home" / "user",
            provision_request_path=self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
            command_runner=lambda argv, as_user: None,
            wall_clock=lambda: wall,
            monotonic_clock=iter([0.0, 0.0, 0.1, 0.2, 0.3]).__next__,
            sleep=lambda delay: None,
            loaded_timeout=0.2,
            poll_interval=0.0,
        )
        operations.restart()
        stale = self.loaded(updated_at=99.0)
        fresh = self.loaded(updated_at=101.0)
        with patch.object(
            apply.vehicle_setup,
            "read_loaded_runtime",
            side_effect=[stale, fresh],
        ):
            self.assertEqual(operations.loaded_runtime(), fresh)


    def _write_loaded_target(self, target, updated_at: float) -> None:
        runtime = {
            "api_version": 1,
            "state": "ready",
            "errors": [],
            "vehicle": target["vehicle"],
            "bindings": target["bindings"],
            "active_bus": target["runtime"]["active_bus"],
            "interface": target["runtime"]["buses"][target["runtime"]["active_bus"]]["interface"],
            "updated_at": updated_at,
        }
        self.write_status(runtime)

    def test_concrete_operations_complete_through_internal_state_machine(self) -> None:
        previous = json.loads(json.dumps(self.target))
        target = json.loads(json.dumps(self.target))
        target["runtime"]["buses"]["comfort"]["interface"] = "can1"
        self._write_trusted(self.paths.descriptor, "old descriptor\n")
        self._write_trusted(self.paths.runtime_dropin, "old dropin\n")
        self._write_trusted(self.paths.udev_rules, "old udev\n")
        self._write_loaded_target(previous, 1.0)

        def runner(argv, as_user):
            self.commands.append((tuple(argv), as_user))
            if tuple(argv) == ("systemctl", "--user", "restart", "canbusd.service"):
                self._write_loaded_target(target, 2.0)

        operations = apply.RootApplyOperations(
            roots=self.roots,
            paths=self.paths,
            service_user="open-mmi",
            service_uid=os.getuid(),
            service_gid=os.getgid(),
            service_home=self.root / "home" / "user",
            provision_request_path=self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
            command_runner=runner,
            wall_clock=lambda: 1.5,
            sleep=lambda delay: None,
        )
        preview = {
            "expected_configuration_revision": "sha256:" + "c" * 64,
            "target_configuration_revision": coordinator.vehicle_configuration.selection_revision(target),
            "target": target,
        }
        with patch.object(coordinator, "coordinator_preview", return_value=preview):
            state = coordinator.run_apply_transaction(
                {},
                reviewed_target=target,
                expected_configuration_revision="sha256:" + "c" * 64,
                confirm=True,
                operations=operations,
                state_path=self.root / "state.json",
                configuration_lock=self.root / "configuration.lock",
                lifecycle_lock=self.root / "lifecycle.lock",
                update_lock=self.root / "update.lock",
            )
        self.assertEqual(state["state"], "complete")
        descriptor = json.loads(self.paths.descriptor.read_text(encoding="utf-8"))
        self.assertEqual(descriptor["runtime"]["buses"]["comfort"]["interface"], "can1")

    def test_concrete_vcan_qualification_applies_then_restores_and_discards_snapshot(self) -> None:
        previous = json.loads(json.dumps(self.target))
        target = json.loads(json.dumps(self.target))
        target["runtime"]["buses"]["comfort"]["interface"] = "vcan0"
        originals = {
            self.paths.descriptor: "old descriptor\n",
            self.paths.runtime_dropin: "old dropin\n",
            self.paths.udev_rules: "old udev\n",
        }
        for path, content in originals.items():
            self._write_trusted(path, content)
        self._write_loaded_target(previous, 1.0)
        restarts = 0

        def runner(argv, as_user):
            nonlocal restarts
            command = tuple(argv)
            self.commands.append((command, as_user))
            if command == ("systemctl", "--user", "restart", "canbusd.service"):
                restarts += 1
                self._write_loaded_target(target if restarts == 1 else previous, 1.0 + restarts)

        operations = apply.RootApplyOperations(
            roots=self.roots,
            paths=self.paths,
            service_user="open-mmi",
            service_uid=os.getuid(),
            service_gid=os.getgid(),
            service_home=self.root / "home" / "user",
            provision_request_path=self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
            command_runner=runner,
            wall_clock=lambda: 1.5,
            sleep=lambda delay: None,
            suppress_can_provisioning=True,
        )
        preview = {
            "expected_configuration_revision": "sha256:" + "c" * 64,
            "target_configuration_revision": coordinator.vehicle_configuration.selection_revision(target),
            "target": target,
        }
        with patch.object(coordinator, "coordinator_preview", return_value=preview):
            state = coordinator.run_apply_transaction(
                {},
                reviewed_target=target,
                expected_configuration_revision="sha256:" + "c" * 64,
                confirm=True,
                operations=operations,
                state_path=self.root / "state.json",
                configuration_lock=self.root / "configuration.lock",
                lifecycle_lock=self.root / "lifecycle.lock",
                update_lock=self.root / "update.lock",
                qualification_restore_on_success=True,
            )
        self.assertEqual(state["stage"], "qualification-restored")
        self.assertTrue(state["restoration_verified"])
        for path, content in originals.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)
        self.assertEqual(list(self.paths.rollback_root.iterdir()), [])
        self.assertFalse(any(command[0][0] == "udevadm" for command in self.commands))

    def test_concrete_operation_failure_restores_files_and_previous_runtime(self) -> None:
        previous = json.loads(json.dumps(self.target))
        target = json.loads(json.dumps(self.target))
        target["runtime"]["buses"]["comfort"]["interface"] = "can1"
        originals = {
            self.paths.descriptor: "old descriptor\n",
            self.paths.runtime_dropin: "old dropin\n",
            self.paths.udev_rules: "old udev\n",
        }
        for path, content in originals.items():
            self._write_trusted(path, content)
        self._write_loaded_target(previous, 1.0)
        failed = False

        def runner(argv, as_user):
            nonlocal failed
            command = tuple(argv)
            self.commands.append((command, as_user))
            if command == ("udevadm", "control", "--reload-rules") and not failed:
                failed = True
                raise apply.ApplyOperationError("injected command failure")
            if command == ("systemctl", "--user", "restart", "canbusd.service"):
                self._write_loaded_target(previous, 2.0)

        operations = apply.RootApplyOperations(
            roots=self.roots,
            paths=self.paths,
            service_user="open-mmi",
            service_uid=os.getuid(),
            service_gid=os.getgid(),
            service_home=self.root / "home" / "user",
            provision_request_path=self.root / "run" / "open-mmi" / "vehicle-can-provision-request.json",
            command_runner=runner,
            wall_clock=lambda: 1.5,
            sleep=lambda delay: None,
        )
        preview = {
            "expected_configuration_revision": "sha256:" + "c" * 64,
            "target_configuration_revision": coordinator.vehicle_configuration.selection_revision(target),
            "target": target,
        }
        with patch.object(coordinator, "coordinator_preview", return_value=preview):
            with self.assertRaises(coordinator.CoordinatorError):
                coordinator.run_apply_transaction(
                    {},
                    reviewed_target=target,
                    expected_configuration_revision="sha256:" + "c" * 64,
                    confirm=True,
                    operations=operations,
                    state_path=self.root / "state.json",
                    configuration_lock=self.root / "configuration.lock",
                    lifecycle_lock=self.root / "lifecycle.lock",
                    update_lock=self.root / "update.lock",
                )
        state = coordinator.read_state(self.root / "state.json")
        self.assertEqual(state["stage"], "restored")
        self.assertTrue(state["restoration_verified"])
        for path, content in originals.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_snapshot_refuses_an_unready_restoration_target(self) -> None:
        invalid = self.loaded()
        invalid["state"] = "invalid"
        invalid["errors"] = ["broken"]
        invalid["vehicle"] = {}
        invalid["bindings"] = {}
        self.write_status(invalid)
        with self.assertRaisesRegex(apply.ApplyOperationError, "not ready"):
            self.operations.snapshot("configuration-" + "c" * 32)

    def _can_sysfs(self, interface: str, *, virtual: bool = False) -> Path:
        class_net = self.root / "sys" / "class" / "net"
        virtual_root = self.root / "sys" / "devices" / "virtual" / "net"
        virtual_root.mkdir(parents=True, exist_ok=True)
        if virtual:
            device = virtual_root / interface
        else:
            device = self.root / "sys" / "devices" / "pci0000:00" / "net" / interface
        device.mkdir(parents=True, exist_ok=True)
        (device / "type").write_text("280\n", encoding="ascii")
        class_net.mkdir(parents=True, exist_ok=True)
        (class_net / interface).symlink_to(os.path.relpath(device, class_net))
        return class_net

    def test_host_can_provisioner_uses_only_fixed_ip_commands(self) -> None:
        class_net = self._can_sysfs("can0")
        commands = []
        result = apply.provision_can_target(
            self.target,
            self.roots,
            service_uid=os.getuid(),
            sys_class_net=class_net,
            command_runner=lambda argv: commands.append(tuple(argv)),
        )
        self.assertEqual(result, "configured")
        self.assertEqual(
            commands,
            [
                ("/sbin/ip", "link", "set", "dev", "can0", "down"),
                (
                    "/sbin/ip",
                    "link",
                    "set",
                    "dev",
                    "can0",
                    "type",
                    "can",
                    "bitrate",
                    "100000",
                ),
                ("/sbin/ip", "link", "set", "dev", "can0", "up"),
            ],
        )

    def test_host_can_provisioner_leaves_an_absent_interface_for_udev(self) -> None:
        class_net = self.root / "sys" / "class" / "net"
        (self.root / "sys" / "devices" / "virtual" / "net").mkdir(
            parents=True, exist_ok=True
        )
        class_net.mkdir(parents=True)
        commands = []
        result = apply.provision_can_target(
            self.target,
            self.roots,
            service_uid=os.getuid(),
            sys_class_net=class_net,
            command_runner=lambda argv: commands.append(tuple(argv)),
        )
        self.assertEqual(result, "absent")
        self.assertEqual(commands, [])

    def test_host_can_provisioner_rejects_virtual_can_named_can0(self) -> None:
        class_net = self._can_sysfs("can0", virtual=True)
        with self.assertRaisesRegex(apply.ApplyOperationError, "Virtual CAN"):
            apply.provision_can_target(
                self.target,
                self.roots,
                service_uid=os.getuid(),
                sys_class_net=class_net,
                command_runner=lambda argv: None,
            )

    def test_provision_request_is_root_owned_one_shot_input(self) -> None:
        request = self.root / "run" / "open-mmi" / "request.json"
        request.parent.mkdir(parents=True, exist_ok=True)
        apply._atomic_replace(
            request,
            apply._json_bytes(self.target),
            mode=0o600,
            uid=os.geteuid(),
            gid=os.getegid(),
        )
        class_net = self.root / "sys" / "class" / "net"
        (self.root / "sys" / "devices" / "virtual" / "net").mkdir(
            parents=True, exist_ok=True
        )
        class_net.mkdir(parents=True)
        result = apply.provision_from_request(
            self.roots,
            service_uid=os.getuid(),
            request_path=request,
            sys_class_net=class_net,
        )
        self.assertEqual(result, "absent")
        self.assertFalse(request.exists())



if __name__ == "__main__":
    unittest.main()
