from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ui.configuration import ConfigurationError
from ui.web_dashboard import system_settings, trip_distance


class TripDistanceTests(unittest.TestCase):
    def test_defaults_start_at_zero_without_an_odometer_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = trip_distance.status_payload(Path(temporary) / "trip-distance.json")
        self.assertEqual(payload["total_km"], 0.0)
        self.assertIsNone(payload["odometer_km"])
        self.assertIsNone(payload["updated_at"])

    def test_observation_persists_fractional_distance_privately(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "trip-distance.json"
            result = trip_distance.observe(
                {"distance_delta_km": 0.1609344, "elapsed_seconds": 10, "odometer_km": 123456},
                path,
                now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            )
            self.assertAlmostEqual(result["total_km"], 0.1609344)
            self.assertEqual(result["odometer_km"], 123456)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_rejects_delta_above_supported_speed_envelope(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-distance.json"
            with self.assertRaisesRegex(ConfigurationError, "speed envelope"):
                trip_distance.observe(
                    {"distance_delta_km": 2, "elapsed_seconds": 1, "odometer_km": 1000},
                    path,
                )

    def test_zero_elapsed_observation_cannot_add_fractional_distance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-distance.json"
            with self.assertRaisesRegex(ConfigurationError, "must be zero"):
                trip_distance.observe(
                    {"distance_delta_km": 0.01, "elapsed_seconds": 0, "odometer_km": 1000},
                    path,
                )

    def test_restart_anchor_recovers_confirmed_odometer_advance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-distance.json"
            start = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
            trip_distance.observe(
                {"distance_delta_km": 0, "elapsed_seconds": 0, "odometer_km": 1000},
                path,
                now=start,
            )
            result = trip_distance.observe(
                {"distance_delta_km": 0, "elapsed_seconds": 0, "odometer_km": 1002},
                path,
                now=start + timedelta(seconds=10),
            )
            self.assertEqual(result["accepted_delta_km"], 2)
            self.assertEqual(result["total_km"], 2)

    def test_long_interruption_recovers_whole_odometer_advance(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trip-distance.json"
            start = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
            trip_distance.observe(
                {"distance_delta_km": 0, "elapsed_seconds": 0, "odometer_km": 1000},
                path,
                now=start,
            )
            result = trip_distance.observe(
                {"distance_delta_km": 0.05, "elapsed_seconds": 5, "odometer_km": 1003},
                path,
                now=start + timedelta(minutes=10),
            )
            self.assertEqual(result["accepted_delta_km"], 3)
            self.assertEqual(result["total_km"], 3)

    def test_local_routes_expose_and_update_accumulator(self):
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
            path = Path(temporary) / "trip-distance.json"
            with (
                patch.object(
                    system_settings.vehicle_store_client,
                    "trip_distance_status",
                    side_effect=lambda: trip_distance.status_payload(path),
                ),
                patch.object(
                    system_settings.vehicle_store_client,
                    "trip_distance_observe",
                    side_effect=lambda payload: trip_distance.observe(payload, path),
                ),
            ):
                get_handler = Handler()
                self.assertTrue(system_settings._handle_get(get_handler, "/api/system/trip-distance"))
                self.assertEqual(get_handler.responses[-1][1]["total_km"], 0)

                observe_handler = Handler(
                    {"distance_delta_km": 0.1, "elapsed_seconds": 10, "odometer_km": 1000}
                )
                self.assertTrue(system_settings._handle_post(observe_handler, "/api/system/trip-distance/observe"))
                self.assertAlmostEqual(observe_handler.responses[-1][1]["total_km"], 0.1)

    def test_writer_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "trip-distance.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(ConfigurationError, "symlinked"):
                trip_distance.observe(
                    {"distance_delta_km": 0, "elapsed_seconds": 0, "odometer_km": 100},
                    link,
                )


if __name__ == "__main__":
    unittest.main()
