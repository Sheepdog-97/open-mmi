from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from open_mmi_trust.inspector import (
    FAIL, PASS, UNVERIFIED,
    _inspect_vehicle_data_persistence_enforcement,
    _vehicle_persistence_source_contract,
    _vehicle_persistence_storage_contract,
    _vehicle_persistence_user_shadow_paths,
)
from open_mmi_trust.manifest import load_manifest

ROOT = Path(__file__).resolve().parents[1]


class PersistenceEnforcementTests(unittest.TestCase):
    def test_dashboard_has_no_durable_filesystem_write_authority(self):
        unit = (ROOT / "systemd/user/open-mmi-dashboard.service").read_text(encoding="utf-8")
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertNotIn("ReadWritePaths=", unit)

    def test_canbusd_can_write_only_ephemeral_runtime_status(self):
        unit = (ROOT / "systemd/user/canbusd.service").read_text(encoding="utf-8")
        self.assertIn("RuntimeDirectory=open-mmi", unit)
        self.assertIn("RuntimeDirectoryMode=0700", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ReadWritePaths=%t/open-mmi", unit)
        self.assertNotIn("/var/lib", unit)
        self.assertNotIn("/home", unit)

    def test_other_privileged_services_cannot_write_vehicle_data_store(self):
        coordinator = (
            ROOT / "systemd/system/open-mmi-update-coordinator.service"
        ).read_text(encoding="utf-8")
        self.assertIn("InaccessiblePaths=", coordinator)
        self.assertIn("-/var/lib/open-mmi/vehicle-data", coordinator)
        for relative in (
            "systemd/system/open-mmi-update-installer.service",
            "systemd/system/open-mmi-vehicle-config-coordinator.service",
        ):
            unit = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("ReadOnlyPaths=", unit, relative)
            self.assertIn("/var/lib/open-mmi/vehicle-data", unit, relative)

    def test_runtime_status_default_is_ephemeral(self):
        source = (ROOT / "canbusd/status_bus.py").read_text(encoding="utf-8")
        self.assertIn('Path(runtime_dir) / "open-mmi" / "status.json"', source)
        self.assertNotIn("/var/lib/open-mmi/vehicle-data", source)


    def _valid_storage_root(self, root: Path) -> Path:
        storage = root / "vehicle-data"
        storage.mkdir(mode=0o700)
        for purpose in ("service-reminder", "trip-a", "trip-b", "trip-distance"):
            (storage / purpose).mkdir(mode=0o700)
        return storage

    def test_source_tripwire_accepts_current_brokered_sources(self):
        self.assertEqual(_vehicle_persistence_source_contract(ROOT), [])

    def test_source_tripwire_rejects_direct_vehicle_state_import_and_durable_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            for relative in (
                "ui/vehicle_store.py",
                "ui/vehicle_store_client.py",
                "ui/web_dashboard/system_settings.py",
                "canbusd/status_bus.py",
            ):
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            bad = fixture / "ui" / "bad_component.py"
            bad.write_text(
                "from ui.web_dashboard import trip_a\n"
                "from pathlib import Path\n"
                "Path('/var/lib/open-mmi/vehicle-data/history.json').write_text('x')\n",
                encoding="utf-8",
            )
            failures = _vehicle_persistence_source_contract(fixture)
        self.assertTrue(any("direct-vehicle-state-import:ui.web_dashboard.trip_a" in item for item in failures))
        self.assertTrue(any("direct-durable-root-reference" in item for item in failures))

    def test_storage_contract_accepts_only_declared_durable_purposes(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = self._valid_storage_root(Path(tmp))
            failures, evidence = _vehicle_persistence_storage_contract(
                storage, expected_uid=os.geteuid()
            )
        self.assertEqual(failures, [])
        self.assertEqual(
            set(evidence["durable_purposes"]),
            {"service-reminder", "trip-a", "trip-b", "trip-distance"},
        )

    def test_storage_contract_rejects_symlink_and_runtime_status_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            storage = self._valid_storage_root(base)
            (storage / "trip-b").rmdir()
            (storage / "trip-b").symlink_to(base / "outside", target_is_directory=True)
            (storage / "vehicle-runtime-status").mkdir(mode=0o700)
            failures, _ = _vehicle_persistence_storage_contract(
                storage, expected_uid=os.geteuid()
            )
        self.assertIn("trip-b:not-trusted-directory", failures)
        self.assertIn("storage-root:unexpected:vehicle-runtime-status", failures)

    def test_persistence_shadow_tripwire_rejects_filesystem_weakening_dropin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            dropin = home / ".config/systemd/user/open-mmi-dashboard.service.d"
            dropin.mkdir(parents=True)
            (dropin / "50-weaken.conf").write_text(
                "[Service]\nProtectHome=false\nReadWritePaths=/home\n",
                encoding="utf-8",
            )
            with mock.patch("pathlib.Path.home", return_value=home), mock.patch.dict(
                "os.environ", {"XDG_RUNTIME_DIR": ""}, clear=False
            ):
                offenders = _vehicle_persistence_user_shadow_paths()
        self.assertTrue(any("ProtectHome=false" in item for item in offenders))
        self.assertTrue(any("ReadWritePaths=/home" in item for item in offenders))

    def test_inspector_persistence_proof_passes_with_proven_runtime_and_safe_store(self):
        manifest = load_manifest()
        with tempfile.TemporaryDirectory() as tmp:
            storage = self._valid_storage_root(Path(tmp))
            with mock.patch(
                "open_mmi_trust.inspector._vehicle_persistence_user_shadow_paths",
                return_value=[],
            ):
                check = _inspect_vehicle_data_persistence_enforcement(
                    ROOT,
                    manifest,
                    {"status": PASS},
                    production=True,
                    storage_root=storage,
                    storage_expected_uid=os.geteuid(),
                )
        self.assertIsNotNone(check)
        self.assertEqual(check["status"], PASS)
        self.assertEqual(check["evidence"]["runtime_status"], "vehicle-runtime-status:/run-only")

    def test_inspector_persistence_proof_fails_manifest_purpose_expansion(self):
        manifest = copy.deepcopy(load_manifest())
        manifest["capabilities"]["vehicle-data.persistence"]["purposes"].append("vehicle.history")
        check = _inspect_vehicle_data_persistence_enforcement(
            ROOT,
            manifest,
            {"status": PASS},
            production=False,
        )
        self.assertIsNotNone(check)
        self.assertEqual(check["status"], FAIL)

    def test_nonproduction_persistence_proof_is_truthfully_unverified(self):
        check = _inspect_vehicle_data_persistence_enforcement(
            ROOT,
            load_manifest(),
            None,
            production=False,
        )
        self.assertIsNotNone(check)
        self.assertEqual(check["status"], UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
