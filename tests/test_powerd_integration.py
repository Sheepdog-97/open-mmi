from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PowerdIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manage = (ROOT / "scripts/manage.sh").read_text(encoding="utf-8")
        cls.pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        cls.unit = (
            ROOT / "systemd/system/open-mmi-powerd.service"
        ).read_text(encoding="utf-8")
        cls.wake_rule = (
            ROOT / "packaging/udev/90-open-mmi-can-wake.rules"
        ).read_text(encoding="utf-8")
        cls.tmpfiles = (
            ROOT / "packaging/tmpfiles/open-mmi.conf"
        ).read_text(encoding="utf-8")

    def test_daemon_is_not_an_action_module(self) -> None:
        self.assertFalse((ROOT / "actions/power.py").exists())
        self.assertTrue((ROOT / "powerd/daemon.py").is_file())
        self.assertIn('open-mmi-powerd = "powerd.cli:main"', self.pyproject)
        self.assertIn('    "powerd*",', self.pyproject)

    def test_all_deployment_paths_copy_and_install_powerd(self) -> None:
        self.assertIn('cp -r "$REPO_ROOT/powerd" "$INSTALL_DIR/"', self.manage)
        self.assertIn('sudo cp -r "$REPO_ROOT/powerd" "$INSTALL_DIR/"', self.manage)
        self.assertIn(
            "for item in canbusd vehicles bindings actions powerd ui scripts packaging systemd; do",
            self.manage,
        )
        self.assertGreaterEqual(self.manage.count("install_power_manager"), 4)

    def test_service_is_disabled_by_default_and_policy_controls_enablement(self) -> None:
        start = self.manage.index("install_power_manager() {")
        end = self.manage.index("cmd_power() {", start)
        install_block = self.manage[start:end]
        self.assertIn('"enabled": false', install_block)
        self.assertIn("reconcile_power_manager", install_block)
        self.assertNotIn('systemctl enable "$POWERD_UNIT"', install_block)
        self.assertIn('systemctl enable --now "$POWERD_UNIT"', self.manage)
        self.assertIn('systemctl disable --now "$POWERD_UNIT"', self.manage)

    def test_prepared_update_backs_up_power_unit_and_policy(self) -> None:
        self.assertIn('"$POWERD_UNIT"; do', self.manage)
        self.assertIn("system-files/power-policy.json", self.manage)
        self.assertIn("reconcile_power_manager", self.manage)
        malformed = (
            'cp -a -- "$rollback_root/system-files/'
            'vehicle-config-coordinator-sandbox.conf" \\\n'
            '                "$VEHICLE_CONFIG_COORDINATOR_SANDBOX" \\\n'
            '        "$POWER_POLICY_FILE"'
        )
        self.assertNotIn(malformed, self.manage)

    def test_transaction_locks_are_recreated_by_systemd_tmpfiles(self) -> None:
        self.assertIn("d /run/open-mmi 0755 root root - -", self.tmpfiles)
        for name in (
            "lifecycle.lock",
            "update.lock",
            "vehicle-configuration.lock",
        ):
            self.assertIn(
                f"f /run/open-mmi/{name} 0644 root root - -",
                self.tmpfiles,
            )
        self.assertIn(
            '"$REPO_ROOT/packaging/tmpfiles/$OPEN_MMI_TMPFILES_CONFIG"',
            self.manage,
        )
        self.assertIn(
            'systemd-tmpfiles --create "$OPEN_MMI_TMPFILES_CONFIG_PATH"',
            self.manage,
        )
        self.assertIn("$OPEN_MMI_TMPFILES_CONFIG.absent", self.manage)
        self.assertIn('"$OPEN_MMI_TMPFILES_CONFIG_PATH"', self.manage)

    def test_wake_rule_is_installed_and_uses_the_packaged_helper(self) -> None:
        self.assertIn(
            "open-mmi-powerd wake-enable --interface %k",
            self.wake_rule,
        )
        self.assertIn(
            '"$REPO_ROOT/packaging/udev/$POWERD_WAKE_UDEV_RULE"',
            self.manage,
        )
        self.assertIn('udevadm control --reload-rules', self.manage)
        self.assertIn("--sysname-match='can*'", self.manage)
        self.assertIn("$POWERD_WAKE_UDEV_RULE.absent", self.manage)

    def test_power_service_bootstraps_transaction_locks(self) -> None:
        self.assertIn(
            "ExecStartPre=/usr/bin/systemd-tmpfiles --create "
            "/opt/open-mmi/packaging/tmpfiles/open-mmi.conf",
            self.unit,
        )
        self.assertIn(
            'sudo cp -r "$REPO_ROOT/packaging" "$INSTALL_DIR/"',
            self.manage,
        )
        self.assertIn(
            '"$REPO_ROOT/systemd/system/$POWERD_UNIT"',
            self.manage,
        )

    def test_system_service_runs_standalone_hardened_daemon(self) -> None:
        self.assertIn(
            "ExecStart=/opt/open-mmi/venv/bin/open-mmi-powerd run",
            self.unit,
        )
        self.assertIn("RestrictAddressFamilies=AF_CAN AF_NETLINK AF_UNIX", self.unit)
        self.assertIn("ProtectSystem=strict", self.unit)
        self.assertIn("ProtectHome=false", self.unit)
        self.assertIn("InaccessiblePaths=/home /root", self.unit)
        self.assertNotIn("actions.power", self.unit)

    def test_wheel_verifier_requires_powerd_modules(self) -> None:
        verifier = (ROOT / "tools/verify_wheel.py").read_text(encoding="utf-8")
        self.assertIn('"powerd/daemon.py"', verifier)
        self.assertIn('"powerd/policy.py"', verifier)

    def test_manage_script_has_valid_bash_syntax(self) -> None:
        completed = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts/manage.sh")],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
