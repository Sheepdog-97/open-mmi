from __future__ import annotations

import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from open_mmi_trust import release_integrity
from ui import vehicle_store, vehicle_store_client


ROOT = Path(__file__).resolve().parents[1]


class _RunningStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.socket = self.root / "runtime" / "store.sock"
        self.storage = self.root / "state"
        self.server = vehicle_store.VehicleStoreServer(self.socket, self.storage)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.environment = patch.dict(
            os.environ,
            {"OPEN_MMI_VEHICLE_STORE_SOCKET": str(self.socket)},
        )
        self.environment.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.environment.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class VehicleStoreTests(unittest.TestCase):
    def test_fixed_broker_endpoints_persist_only_declared_purposes(self):
        with tempfile.TemporaryDirectory() as temporary, _RunningStore(Path(temporary)) as running:
            self.assertFalse(vehicle_store_client.service_reminder_status()["configured"])
            reminder = vehicle_store_client.service_reminder_reset(
                {"confirm": True, "odometer_km": 123456}
            )
            self.assertTrue(reminder["configured"])

            trip_a = vehicle_store_client.trip_a_reset(
                {"confirm": True, "odometer_km": 123456, "distance_total_km": 12.5}
            )
            self.assertTrue(trip_a["configured"])
            trip_b = vehicle_store_client.trip_b_reset(
                {"confirm": True, "odometer_km": 123456, "distance_total_km": 12.5}
            )
            self.assertTrue(trip_b["configured"])
            distance = vehicle_store_client.trip_distance_observe(
                {"distance_delta_km": 0.1, "elapsed_seconds": 10, "odometer_km": 123456}
            )
            self.assertAlmostEqual(distance["total_km"], 0.1)

            expected = {
                "service-reminder",
                "trip-a",
                "trip-b",
                "trip-distance",
            }
            self.assertEqual({path.name for path in running.storage.iterdir()}, expected)
            for purpose in expected:
                state = running.storage / purpose / "state.json"
                self.assertTrue(state.is_file())
                self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(state.parent.stat().st_mode), 0o700)
                self.assertIsInstance(json.loads(state.read_text(encoding="utf-8")), dict)

    def test_no_generic_write_or_caller_selected_purpose_exists(self):
        with tempfile.TemporaryDirectory() as temporary, _RunningStore(Path(temporary)):
            with self.assertRaisesRegex(
                vehicle_store_client.VehicleStoreClientError,
                "not declared",
            ):
                vehicle_store_client.request_json(
                    "/v1/write",
                    {"purpose": "diagnostic-history", "path": "../../history.json", "value": {}},
                )

    def test_path_injection_and_purpose_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary, _RunningStore(Path(temporary)) as running:
            escape = Path(temporary) / "escape.json"
            with self.assertRaises(vehicle_store_client.VehicleStoreClientError):
                vehicle_store_client.trip_b_reset(
                    {
                        "confirm": True,
                        "odometer_km": 1000,
                        "path": str(escape),
                    }
                )
            self.assertFalse(escape.exists())

            with self.assertRaises(vehicle_store_client.VehicleStoreClientError):
                vehicle_store_client.request_json(
                    "/v1/trip-b/reset",
                    {"confirm": True, "reset_date": "2026-09-02", "odometer_km": 1000},
                )
            self.assertFalse((running.storage / "trip-b" / "state.json").exists())

    def test_symlinked_purpose_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            storage = root / "state"
            outside = root / "outside"
            storage.mkdir()
            outside.mkdir()
            (storage / "trip-a").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(vehicle_store.VehicleStoreError, "purpose directory"):
                vehicle_store.VehicleStoreServer(root / "runtime" / "store.sock", storage)

    def test_symlinked_state_file_is_rejected_before_operation(self):
        with tempfile.TemporaryDirectory() as temporary, _RunningStore(Path(temporary)) as running:
            outside = Path(temporary) / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            target = running.storage / "trip-b" / "state.json"
            target.symlink_to(outside)
            with self.assertRaisesRegex(
                vehicle_store_client.VehicleStoreClientError,
                "state file is untrusted",
            ):
                vehicle_store_client.trip_b_status()
            self.assertEqual(outside.read_text(encoding="utf-8"), "{}\n")

    def test_service_unit_has_fixed_state_and_unix_only_authority(self):
        unit = (ROOT / "systemd/system/open-mmi-vehicle-store.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("StateDirectory=open-mmi/vehicle-data", unit)
        self.assertIn("StateDirectoryMode=0700", unit)
        self.assertIn("RuntimeDirectory=open-mmi-vehicle-store", unit)
        self.assertIn("ProtectHome=yes", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", unit)
        self.assertNotIn("AF_INET", unit)
        self.assertIn("CapabilityBoundingSet=\n", unit)
        self.assertIn("AmbientCapabilities=\n", unit)

    def test_privileged_release_integrity_tracks_store_unit(self):
        self.assertIn(
            "open-mmi-vehicle-store.service",
            release_integrity.PRIVILEGED_SYSTEM_UNITS,
        )

    def test_dashboard_routes_use_broker_not_direct_persistence_modules(self):
        source = (ROOT / "ui/web_dashboard/system_settings.py").read_text(encoding="utf-8")
        self.assertIn("vehicle_store_client.service_reminder_status", source)
        self.assertIn("vehicle_store_client.trip_distance_observe", source)
        self.assertNotIn("service_reminder.update_settings", source)
        self.assertNotIn("trip_a.reset_trip", source)
        self.assertNotIn("trip_b.reset_trip", source)
        self.assertNotIn("trip_distance.observe(_json_body", source)

    def test_legacy_state_migrates_into_purpose_root_and_is_removed(self):
        from ui.web_dashboard import trip_b
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "config"
            storage = root / "state"
            legacy.mkdir(mode=0o700)
            source = legacy / "trip-b.json"
            trip_b.reset_trip(
                {"confirm": True, "odometer_km": 1234, "distance_total_km": 12.0},
                source,
            )
            result = vehicle_store.migrate_legacy_state(legacy, storage, os.geteuid())
            self.assertEqual(result["migrated"], ["trip-b"])
            self.assertFalse(source.exists())
            self.assertTrue((storage / "trip-b" / "state.json").is_file())

    def test_legacy_migration_conflict_fails_without_deleting_source(self):
        from ui.web_dashboard import trip_b
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "config"
            storage = root / "state"
            legacy.mkdir(mode=0o700)
            source = legacy / "trip-b.json"
            trip_b.reset_trip(
                {"confirm": True, "odometer_km": 1000, "distance_total_km": 10.0},
                source,
            )
            vehicle_store._validate_root(storage)
            trip_b.reset_trip(
                {"confirm": True, "odometer_km": 2000, "distance_total_km": 20.0},
                storage / "trip-b" / "state.json",
            )
            with self.assertRaisesRegex(vehicle_store.VehicleStoreError, "conflicts"):
                vehicle_store.migrate_legacy_state(legacy, storage, os.geteuid())
            self.assertTrue(source.exists())

    def test_legacy_migration_rejects_symlinked_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy = root / "config"
            storage = root / "state"
            legacy.mkdir(mode=0o700)
            outside = root / "outside.json"
            outside.write_text("{}\n", encoding="utf-8")
            (legacy / "trip-a.json").symlink_to(outside)
            with self.assertRaisesRegex(vehicle_store.VehicleStoreError, "untrusted"):
                vehicle_store.migrate_legacy_state(legacy, storage, os.geteuid())
            self.assertEqual(outside.read_text(encoding="utf-8"), "{}\n")


if __name__ == "__main__":
    unittest.main()
