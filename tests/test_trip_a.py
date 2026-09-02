from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ui.configuration import ConfigurationError
from ui.web_dashboard import system_settings, trip_a


class TripATests(unittest.TestCase):
    def test_defaults_are_unconfigured(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = trip_a.status_payload(Path(temporary) / "trip-a.json")
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["reset"]["reset_at"])
        self.assertIsNone(payload["reset"]["odometer_km"])

    def test_reset_is_private_atomic_and_records_odometer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "trip-a.json"
            result = trip_a.reset_trip(
                {"confirm": True, "odometer_km": 123456},
                path,
                now=datetime(2026, 7, 26, 20, 30, tzinfo=timezone.utc),
            )
            self.assertTrue(result["configured"])
            self.assertEqual(result["reset"]["reset_at"], "2026-07-26T20:30:00+00:00")
            self.assertEqual(result["reset"]["odometer_km"], 123456)
            self.assertIsNone(result["reset"]["distance_total_km"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)


    def test_reset_records_high_resolution_distance_total(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-a.json"
            result = trip_a.reset_trip(
                {"confirm": True, "odometer_km": 123456, "distance_total_km": 42.125},
                path,
                now=datetime(2026, 7, 26, 20, 30, tzinfo=timezone.utc),
            )
            self.assertEqual(result["reset"]["distance_total_km"], 42.125)

    def test_invalid_or_unconfirmed_resets_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-a.json"
            with self.assertRaisesRegex(ConfigurationError, "requires confirmation"):
                trip_a.reset_trip({"confirm": False, "odometer_km": 100}, path)
            with self.assertRaisesRegex(ConfigurationError, "supported range"):
                trip_a.reset_trip({"confirm": True, "odometer_km": -1}, path)

    def test_malformed_persisted_document_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-a.json"
            path.write_text('{"api_version": 1, "reset": []}', encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "reset must be an object"):
                trip_a.status_payload(path)

    def test_local_routes_expose_status_and_confirmed_reset(self):
        class Handler:
            client_address = ("127.0.0.1", 1234)

            def __init__(self, body=None):
                encoded = json.dumps(body).encode("utf-8") if body is not None else b""
                self.headers = {
                    "Host": "127.0.0.1:8765",
                    "Origin": "http://127.0.0.1:8765",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(encoded)),
                }
                self.rfile = io.BytesIO(encoded)
                self.responses = []

            def _send_json(self, payload, status=200):
                self.responses.append((status, payload))

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-a.json"
            with (
                patch.object(
                    system_settings.vehicle_store_client,
                    "trip_a_status",
                    side_effect=lambda: trip_a.status_payload(path),
                ),
                patch.object(
                    system_settings.vehicle_store_client,
                    "trip_a_reset",
                    side_effect=lambda payload: trip_a.reset_trip(payload, path),
                ),
                patch.object(
                    system_settings.vehicle_store_client,
                    "trip_a_settings",
                    side_effect=lambda payload: trip_a.update_settings(payload, path),
                ),
                patch.object(
                    system_settings.vehicle_store_client,
                    "trip_a_observe",
                    side_effect=lambda payload: trip_a.observe_vehicle(payload, path),
                ),
            ):
                get_handler = Handler()
                self.assertTrue(system_settings._handle_get(get_handler, "/api/system/trip-a"))
                self.assertFalse(get_handler.responses[-1][1]["configured"])

                reset_handler = Handler({"confirm": True, "odometer_km": 123456})
                self.assertTrue(system_settings._handle_post(reset_handler, "/api/system/trip-a/reset"))
                settings_handler = Handler({"auto_reset_hours": 4})
                self.assertTrue(system_settings._handle_post(settings_handler, "/api/system/trip-a/settings"))
                observe_handler = Handler({"odometer_km": 123456})
                self.assertTrue(system_settings._handle_post(observe_handler, "/api/system/trip-a/observe"))
                self.assertTrue(reset_handler.responses[-1][1]["configured"])
                self.assertEqual(reset_handler.responses[-1][1]["reset"]["odometer_km"], 123456)

    def test_v1_documents_migrate_and_auto_reset_after_parked_interval(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-a.json"
            path.write_text('{"api_version": 1, "reset": {"reset_at": "2026-07-26T10:00:00+00:00", "odometer_km": 1000}}', encoding="utf-8")
            migrated = trip_a.status_payload(path)
            self.assertEqual(migrated["api_version"], 3)
            self.assertEqual(migrated["settings"]["auto_reset_hours"], 0)
            trip_a.update_settings({"auto_reset_hours": 2}, path)
            trip_a.observe_vehicle({"odometer_km": 1000}, path, now=datetime(2026, 7, 26, 10, 5, tzinfo=timezone.utc))
            result = trip_a.observe_vehicle(
                {"odometer_km": 1000.2, "distance_total_km": 12.75},
                path,
                now=datetime(2026, 7, 26, 12, 10, tzinfo=timezone.utc),
            )
            self.assertTrue(result["auto_reset"])
            self.assertEqual(result["reset"]["odometer_km"], 1000.2)
            self.assertEqual(result["reset"]["distance_total_km"], 12.75)

    def test_auto_reset_is_conservative_when_odometer_advanced_during_gap(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-a.json"
            trip_a.reset_trip({"confirm": True, "odometer_km": 1000}, path, now=datetime(2026, 7, 26, 10, 0, tzinfo=timezone.utc))
            trip_a.update_settings({"auto_reset_hours": 2}, path)
            result = trip_a.observe_vehicle({"odometer_km": 1010}, path, now=datetime(2026, 7, 26, 12, 30, tzinfo=timezone.utc))
            self.assertFalse(result["auto_reset"])
            self.assertEqual(result["reset"]["odometer_km"], 1000)

    def test_writer_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "trip-a.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(ConfigurationError, "symlinked"):
                trip_a.reset_trip({"confirm": True, "odometer_km": 100}, link)


if __name__ == "__main__":
    unittest.main()
