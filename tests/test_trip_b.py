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
from ui.web_dashboard import system_settings, trip_b


class TripBTests(unittest.TestCase):
    def test_defaults_are_unconfigured(self):
        with tempfile.TemporaryDirectory() as temporary:
            payload = trip_b.status_payload(Path(temporary) / "trip-b.json")
        self.assertFalse(payload["configured"])
        self.assertIsNone(payload["reset"]["odometer_km"])

    def test_reset_is_private_and_records_odometer(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "trip-b.json"
            result = trip_b.reset_trip(
                {"confirm": True, "odometer_km": 123456},
                path,
                now=datetime(2026, 7, 26, 20, 30, tzinfo=timezone.utc),
            )
            self.assertTrue(result["configured"])
            self.assertEqual(result["reset"]["odometer_km"], 123456)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_route_exposes_status_and_reset(self):
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

        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ, {"OPEN_MMI_TRIP_B_FILE": str(Path(temporary) / "trip-b.json")}
        ):
            get_handler = Handler()
            self.assertTrue(system_settings._handle_get(get_handler, "/api/system/trip-b"))
            reset_handler = Handler({"confirm": True, "odometer_km": 123456})
            self.assertTrue(system_settings._handle_post(reset_handler, "/api/system/trip-b/reset"))
            self.assertTrue(reset_handler.responses[-1][1]["configured"])

    def test_writer_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "trip-b.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(ConfigurationError, "symlinked"):
                trip_b.reset_trip({"confirm": True, "odometer_km": 100}, link)


if __name__ == "__main__":
    unittest.main()
