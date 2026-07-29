from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ui.configuration import ConfigurationError
from ui.web_dashboard import service_reminder, system_settings


class ServiceReminderTests(unittest.TestCase):
    def test_defaults_are_enabled_but_unconfigured(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-reminder.json"
            payload = service_reminder.status_payload(path)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["configured"])
        self.assertTrue(payload["settings"]["enabled"])
        self.assertEqual(payload["settings"]["time_interval_months"], 12)
        self.assertIsNone(payload["next_due"]["date"])

    def test_reset_is_private_atomic_and_calculates_both_deadlines(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config" / "service-reminder.json"
            result = service_reminder.reset_interval(
                {"confirm": True, "odometer_km": 200000},
                path,
                today=date(2026, 7, 26),
            )
            self.assertTrue(result["configured"])
            self.assertEqual(result["reset"]["reset_date"], "2026-07-26")
            self.assertEqual(result["next_due"]["date"], "2027-07-26")
            self.assertAlmostEqual(result["next_due"]["odometer_km"], 216093.44)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["api_version"], 2)

    def test_settings_update_preserves_last_reset_and_recalculates_due_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-reminder.json"
            service_reminder.reset_interval(
                {"confirm": True, "odometer_km": 1000},
                path,
                today=date(2026, 1, 31),
            )
            result = service_reminder.update_settings(
                {
                    "enabled": True,
                    "distance_interval_km": 20000,
                    "time_interval_months": 1,
                    "warning_distance_km": 2000,
                    "warning_days": 14,
                },
                path,
            )
            self.assertEqual(result["reset"]["reset_date"], "2026-01-31")
            self.assertEqual(result["reset"]["odometer_km"], 1000)
            self.assertEqual(result["next_due"]["date"], "2026-02-28")
            self.assertEqual(result["next_due"]["odometer_km"], 21000)

    def test_malformed_persisted_documents_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-reminder.json"
            path.write_text('{"api_version": 1, "settings": [], "reset": {}}', encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "settings must be an object"):
                service_reminder.status_payload(path)

    def test_invalid_intervals_and_unconfirmed_reset_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-reminder.json"
            with self.assertRaisesRegex(ConfigurationError, "distance interval"):
                service_reminder.update_settings(
                    {
                        "enabled": True,
                        "distance_interval_km": 1000,
                        "time_interval_months": 12,
                        "warning_distance_km": 2000,
                        "warning_days": 30,
                    },
                    path,
                )
            with self.assertRaisesRegex(ConfigurationError, "requires confirmation"):
                service_reminder.reset_interval({"confirm": False, "odometer_km": 100}, path)


    def test_local_routes_expose_status_save_and_confirmed_reset(self):
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
            os.environ,
            {"OPEN_MMI_SERVICE_REMINDER_FILE": str(Path(temporary) / "service-reminder.json")},
        ):
            get_handler = Handler()
            self.assertTrue(system_settings._handle_get(get_handler, "/api/system/service-reminder"))
            self.assertEqual(get_handler.responses[-1][0], 200)
            self.assertFalse(get_handler.responses[-1][1]["configured"])

            save_handler = Handler({
                "enabled": True,
                "distance_interval_km": 20000,
                "time_interval_months": 18,
                "warning_distance_km": 2000,
                "warning_days": 45,
            })
            self.assertTrue(system_settings._handle_post(save_handler, "/api/system/service-reminder/settings"))
            self.assertEqual(save_handler.responses[-1][1]["settings"]["time_interval_months"], 18)

            reset_handler = Handler({"confirm": True, "odometer_km": 123456})
            self.assertTrue(system_settings._handle_post(reset_handler, "/api/system/service-reminder/reset"))
            acknowledge_handler = Handler({"confirm": True, "level": "soon"})
            self.assertTrue(system_settings._handle_post(acknowledge_handler, "/api/system/service-reminder/acknowledge"))
            self.assertTrue(reset_handler.responses[-1][1]["configured"])
            self.assertEqual(reset_handler.responses[-1][1]["reset"]["odometer_km"], 123456)

    def test_v1_document_migrates_and_acknowledgement_tracks_current_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "service-reminder.json"
            path.write_text(json.dumps({
                "api_version": 1,
                "settings": service_reminder.ReminderSettings().__dict__,
                "reset": {"reset_date": "2026-07-26", "odometer_km": 1000},
            }), encoding="utf-8")
            migrated = service_reminder.status_payload(path)
            self.assertEqual(migrated["api_version"], 2)
            result = service_reminder.acknowledge(
                {"confirm": True, "level": "soon"},
                path,
                now=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            )
            self.assertEqual(result["acknowledgement"]["level"], "soon")
            self.assertEqual(result["acknowledgement"]["due_date"], result["next_due"]["date"])
            service_reminder.reset_interval({"confirm": True, "odometer_km": 2000}, path, today=date(2026, 8, 2))
            self.assertIsNone(service_reminder.status_payload(path)["acknowledgement"]["level"])

    def test_writer_refuses_symlink_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "real.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "service-reminder.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(ConfigurationError, "symlinked"):
                service_reminder.reset_interval(
                    {"confirm": True, "odometer_km": 100},
                    link,
                    today=date(2026, 7, 26),
                )


if __name__ == "__main__":
    unittest.main()
