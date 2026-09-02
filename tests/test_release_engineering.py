from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_SCRIPT = ROOT / "scripts" / "open-mmi-desktop"
DEV_SCRIPT = ROOT / "scripts" / "dev_run.sh"
ICON_SOURCE = ROOT / "packaging" / "linux-desktop" / "icons"


class DesktopInstallerTests(unittest.TestCase):
    def run_installer(self, command: str, data_home: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(data_home.parent)
        env["XDG_DATA_HOME"] = str(data_home)
        return subprocess.run(
            [str(DESKTOP_SCRIPT), command],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_install_reinstall_and_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_home = Path(tmp) / "share"
            desktop_file = data_home / "applications" / "open-mmi-status.desktop"
            chooser_file = data_home / "applications" / "open-mmi-chooser.desktop"

            installed = self.run_installer("install", data_home)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertTrue(desktop_file.is_file())
            self.assertTrue(chooser_file.is_file())

            for source in ICON_SOURCE.rglob("*"):
                if source.is_file():
                    destination = data_home / "icons" / source.relative_to(ICON_SOURCE)
                    self.assertTrue(destination.is_file(), destination)

            reinstalled = self.run_installer("reinstall", data_home)
            self.assertEqual(reinstalled.returncode, 0, reinstalled.stderr)
            self.assertTrue(desktop_file.is_file())
            self.assertTrue(chooser_file.is_file())

            removed = self.run_installer("remove", data_home)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertFalse(desktop_file.exists())
            self.assertFalse(chooser_file.exists())

            for source in ICON_SOURCE.rglob("*"):
                if source.is_file():
                    destination = data_home / "icons" / source.relative_to(ICON_SOURCE)
                    self.assertFalse(destination.exists(), destination)

    def test_unknown_command_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_installer("unknown", Path(tmp) / "share")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)


class DevelopmentLauncherTests(unittest.TestCase):
    def test_launcher_uses_module_entry_point(self):
        source = DEV_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("exec python3 -m canbusd.core", source)
        self.assertNotIn("canbusd/canbusd.py", source)


class TrustReleaseContractTests(unittest.TestCase):
    def test_trust_manifest_is_documented_packaged_and_checked_in_ci(self):
        self.assertTrue((ROOT / "docs" / "trust-architecture.md").is_file())
        self.assertTrue((ROOT / "open_mmi_telemetry" / "guard.py").is_file())
        self.assertTrue((ROOT / "open_mmi_telemetry" / "cli.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "manifest.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "accepted_state.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "accepted_state_cli.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "transition_gate.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "transition_gate_cli.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "inspector.py").is_file())
        self.assertTrue((ROOT / "open_mmi_trust" / "inspector_cli.py").is_file())
        self.assertTrue(
            (ROOT / "open_mmi_trust" / "data" / "trust-manifest.v1.json").is_file()
        )
        self.assertTrue(
            (ROOT / "open_mmi_trust" / "data" / "trust-inspection.v1.schema.json").is_file()
        )
        self.assertTrue(
            (ROOT / "open_mmi_trust" / "data" / "accepted-owner-trust.v1.schema.json").is_file()
        )
        self.assertTrue(
            (ROOT / "open_mmi_trust" / "data" / "trust-transition-authorization.v1.schema.json").is_file()
        )
        ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("python tools/verify_trust_manifest.py", ci)
        self.assertIn(
            "tests.test_trust_invariants tests.test_telemetry_guard tests.test_trust_inspector tests.test_accepted_trust_state tests.test_trust_transition_gate tests.test_trust_lineage tests.test_release_integrity tests.test_release_provenance tests.test_vehicle_identity_remote_resolution",
            ci,
        )
        self.assertIn("trust_inspector.inspect_system()", ci)
        self.assertIn("import open_mmi_trust", ci)
        wheel = (ROOT / "tools" / "verify_wheel.py").read_text(encoding="utf-8")
        self.assertIn('"open_mmi_telemetry/guard.py"', wheel)
        self.assertIn('"open_mmi_telemetry/cli.py"', wheel)
        self.assertIn('"open_mmi_trust/manifest.py"', wheel)
        self.assertIn('"open_mmi_trust/vehicle_identity.py"', wheel)
        self.assertIn('"open_mmi_trust/accepted_state.py"', wheel)
        self.assertIn('"open_mmi_trust/accepted_state_cli.py"', wheel)
        self.assertIn('"open_mmi_trust/transition_gate.py"', wheel)
        self.assertIn('"open_mmi_trust/transition_gate_cli.py"', wheel)
        self.assertIn('"open_mmi_trust/lineage.py"', wheel)
        self.assertIn('"open_mmi_trust/lineage_cli.py"', wheel)
        self.assertIn('"open_mmi_trust/release_integrity.py"', wheel)
        self.assertIn('"open_mmi_trust/release_integrity_cli.py"', wheel)
        self.assertIn('"open_mmi_trust/release_provenance.py"', wheel)
        self.assertIn('"open_mmi_trust/release_provenance_cli.py"', wheel)
        self.assertIn('"open_mmi_trust/inspector.py"', wheel)
        self.assertIn('"open_mmi_trust/inspector_cli.py"', wheel)
        self.assertIn('"open_mmi_trust/data/trust-inspection.v1.schema.json"', wheel)
        self.assertIn('"open_mmi_trust/data/accepted-owner-trust.v1.schema.json"', wheel)
        self.assertIn('"open_mmi_trust/data/trust-transition-authorization.v1.schema.json"', wheel)
        self.assertIn('"open_mmi_trust/data/trust-transition-lineage-record.v1.schema.json"', wheel)
        self.assertIn('"open_mmi_trust/data/installed-release-integrity.v1.schema.json"', wheel)
        self.assertIn('"open_mmi_trust/data/release-signer-root.v1.schema.json"', wheel)
        self.assertIn('"open_mmi_trust/data/trust-manifest.v1.json"', wheel)
        self.assertTrue((ROOT / "tools" / "vendor_bootstrap.py").is_file())
        self.assertTrue((ROOT / "ui" / "web_dashboard" / "static" / "vendor" / "BOOTSTRAP-LICENSE.txt").is_file())
        self.assertIn('"ui/web_dashboard/static/vendor/bootstrap-5.3.8.min.css"', wheel)
        self.assertIn('"ui/web_dashboard/static/vendor/BOOTSTRAP-LICENSE.txt"', wheel)

    def test_trust_package_data_is_in_build_configuration(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"open_mmi_telemetry*"', pyproject)
        self.assertIn('open-mmi-telemetry = "open_mmi_telemetry.cli:main"', pyproject)
        self.assertIn('open-mmi-trust-inspect = "open_mmi_trust.inspector_cli:main"', pyproject)
        self.assertIn('open-mmi-trust-integrity = "open_mmi_trust.release_integrity_cli:main"', pyproject)
        self.assertIn('open-mmi-trust-provenance = "open_mmi_trust.release_provenance_cli:main"', pyproject)
        self.assertIn('open-mmi-trust-lineage = "open_mmi_trust.lineage_cli:main"', pyproject)
        self.assertIn('open-mmi-trust-state = "open_mmi_trust.accepted_state_cli:main"', pyproject)
        self.assertIn('open-mmi-trust-transition = "open_mmi_trust.transition_gate_cli:main"', pyproject)
        self.assertIn('"open_mmi_trust*"', pyproject)
        self.assertIn('open_mmi_trust = ["data/*.json"]', pyproject)
        self.assertIn('"static/vendor/*.css"', pyproject)
        self.assertIn('"static/vendor/*.txt"', pyproject)


if __name__ == "__main__":
    unittest.main()
