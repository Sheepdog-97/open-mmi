from __future__ import annotations

import fcntl
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from powerd import cli, inhibitors, policy, runtime, wake


class PowerPolicyTests(unittest.TestCase):
    def test_missing_policy_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            loaded = policy.load_policy(Path(temporary) / "missing.json")
        self.assertFalse(loaded.enabled)
        self.assertEqual(loaded.silence_seconds, 60)

    def test_policy_rejects_string_booleans(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "power.json"
            path.write_text(
                json.dumps({"schema_version": 1, "enabled": "false"}),
                encoding="utf-8",
            )
            with self.assertRaises(policy.PowerPolicyError):
                policy.load_policy(path)

    def test_cli_enables_and_disables_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "power.json"
            self.assertEqual(
                cli.main(
                    [
                        "policy",
                        "enable",
                        "--policy",
                        str(path),
                        "--silence-seconds",
                        "90",
                    ]
                ),
                0,
            )
            enabled = policy.load_policy(path)
            self.assertTrue(enabled.enabled)
            self.assertEqual(enabled.silence_seconds, 90)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

            self.assertEqual(
                cli.main(["policy", "disable", "--policy", str(path)]),
                0,
            )
            disabled = policy.load_policy(path)
            self.assertFalse(disabled.enabled)
            self.assertEqual(disabled.silence_seconds, 90)

    def test_suspend_requires_all_fail_closed_evidence(self) -> None:
        configured = policy.PowerPolicy(enabled=True, silence_seconds=60)
        base = dict(
            policy=configured,
            healthy_can=True,
            wake_ready=True,
            transaction_busy=False,
            observed_frame=True,
            silent_for=60,
            awake_for=30,
        )
        self.assertTrue(policy.suspend_allowed(**base))
        for field, value in (
            ("healthy_can", False),
            ("wake_ready", False),
            ("transaction_busy", True),
            ("observed_frame", False),
            ("silent_for", 59.9),
            ("awake_for", 29.9),
        ):
            self.assertFalse(policy.suspend_allowed(**{**base, field: value}))


class RuntimeTests(unittest.TestCase):
    def test_active_interface_requires_ready_physical_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "status.json"
            path.write_text(
                json.dumps(
                    {"runtime": {"state": "ready", "interface": "can12"}}
                ),
                encoding="utf-8",
            )
            self.assertEqual(runtime.active_interface(path), "can12")

            path.write_text(
                json.dumps(
                    {"runtime": {"state": "invalid", "interface": "can12"}}
                ),
                encoding="utf-8",
            )
            self.assertIsNone(runtime.active_interface(path))

            path.write_text(
                json.dumps(
                    {"runtime": {"state": "ready", "interface": "vcan0"}}
                ),
                encoding="utf-8",
            )
            self.assertIsNone(runtime.active_interface(path))

    def test_can_health_uses_socketcan_controller_state(self) -> None:
        def runner(*_args, **_kwargs):
            return subprocess.CompletedProcess(
                [],
                0,
                stdout=json.dumps(
                    [
                        {
                            "flags": ["UP", "LOWER_UP"],
                            "linkinfo": {
                                "info_data": {"state": "ERROR-ACTIVE"}
                            },
                        }
                    ]
                ),
                stderr="",
            )

        health = runtime.can_health("can0", runner=runner)
        self.assertTrue(health.healthy)
        self.assertEqual(health.state, "ERROR-ACTIVE")


class WakeTests(unittest.TestCase):
    @staticmethod
    def _wake_tree(root: Path, *, enabled: bool) -> tuple[Path, Path, Path, Path]:
        pci = root / "devices" / "pci0000:00" / "0000:00:14.0"
        root_hub = pci / "usb1"
        usb = root_hub / "1-1"
        interface = usb / "1-1:1.0"
        net = root / "class" / "net" / "can0"
        interface.mkdir(parents=True)
        net.mkdir(parents=True)
        (net / "device").symlink_to(interface)

        state = "enabled\n" if enabled else "disabled\n"
        for node, subsystem in (
            (pci, "pci"),
            (root_hub, "usb"),
            (usb, "usb"),
        ):
            (node / "power").mkdir()
            (node / "power" / "wakeup").write_text(state, encoding="utf-8")
            target = root / "subsystems" / subsystem
            target.mkdir(parents=True, exist_ok=True)
            (node / "subsystem").symlink_to(target)

        return pci, root_hub, usb, root / "class" / "net"

    def test_remote_wake_requires_enabled_usb_device_and_pci_controller(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, root_hub, _, sys_class_net = self._wake_tree(
                root, enabled=True
            )

            self.assertTrue(wake.remote_wake_ready("can0", sys_class_net))
            (root_hub / "power" / "wakeup").write_text(
                "disabled\n", encoding="utf-8"
            )
            self.assertFalse(wake.remote_wake_ready("can0", sys_class_net))

    def test_enable_remote_wake_enables_verified_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pci, root_hub, usb, sys_class_net = self._wake_tree(
                root, enabled=False
            )

            self.assertTrue(wake.enable_remote_wake("can0", sys_class_net))
            self.assertTrue(wake.remote_wake_ready("can0", sys_class_net))
            for node in (pci, root_hub, usb):
                self.assertEqual(
                    (node / "power" / "wakeup").read_text(encoding="utf-8"),
                    "enabled\n",
                )

    def test_enable_remote_wake_fails_before_writing_incomplete_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pci, root_hub, usb, sys_class_net = self._wake_tree(
                root, enabled=False
            )
            (usb / "power" / "wakeup").unlink()

            self.assertFalse(wake.enable_remote_wake("can0", sys_class_net))
            self.assertEqual(
                (pci / "power" / "wakeup").read_text(encoding="utf-8"),
                "disabled\n",
            )
            self.assertEqual(
                (root_hub / "power" / "wakeup").read_text(encoding="utf-8"),
                "disabled\n",
            )

    def test_wake_enable_cli_uses_the_same_fail_closed_topology(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._wake_tree(root, enabled=False)
            sys_class_net = root / "class" / "net"

            self.assertEqual(
                cli.main(
                    [
                        "wake-enable",
                        "--interface",
                        "can0",
                        "--sys-class-net",
                        str(sys_class_net),
                    ]
                ),
                0,
            )
            self.assertTrue(wake.remote_wake_ready("can0", sys_class_net))
            self.assertEqual(
                cli.main(
                    [
                        "wake-enable",
                        "--interface",
                        "vcan0",
                        "--sys-class-net",
                        str(sys_class_net),
                    ]
                ),
                2,
            )


class InhibitorTests(unittest.TestCase):
    def test_missing_or_locked_transaction_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = tuple(root / name for name in ("a.lock", "b.lock", "c.lock"))
            uid = os.getuid()
            for path in paths:
                path.write_text("", encoding="utf-8")
                path.chmod(0o644)

            self.assertFalse(
                inhibitors.transaction_active(paths, expected_uid=uid)
            )

            with paths[1].open("r", encoding="utf-8") as held:
                fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(
                    inhibitors.transaction_active(paths, expected_uid=uid)
                )

            paths[2].unlink()
            self.assertTrue(
                inhibitors.transaction_active(paths, expected_uid=uid)
            )


if __name__ == "__main__":
    unittest.main()
