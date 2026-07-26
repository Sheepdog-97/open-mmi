import json
import unittest
from pathlib import Path
from unittest import mock

from canbusd.status_rules import evaluate_status_rules, parse_status_rules
from ui.web_dashboard import server

ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "vehicles" / "seat" / "leon" / "1p-pq35" / "config.json"
VEHICLE_PATH = ROOT / "ui" / "web_dashboard" / "static" / "vehicle.js"
STATUS_CLI_PATH = ROOT / "ui" / "dashboard" / "status_cli.py"

class StatusSchemaCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = json.loads(PROFILE_PATH.read_text())

    def _recirculation_rule(self):
        return next(rule for rule in self.profile["status"] if rule.get("path") == "climate.recirculation_active")

    def test_profile_uses_only_canonical_recirculation_names(self):
        rule = self._recirculation_rule()
        self.assertEqual(rule["raw_path"], "climate.recirculation_raw")
        self.assertNotIn("aliases", rule)
        self.assertNotIn("raw_aliases", rule)

    def test_decoder_publishes_only_canonical_recirculation_fields(self):
        grouped = parse_status_rules([self._recirculation_rule()])
        active = evaluate_status_rules(grouped[0x3E3], bytes([0, 0, 0, 0, 0x80]), 5)
        inactive = evaluate_status_rules(grouped[0x3E3], bytes([0, 0, 0, 0, 0x00]), 5)
        for update, expected, raw in ((active, True, 0x80), (inactive, False, 0x00)):
            climate = update["climate"]
            self.assertIs(climate["recirculation_active"], expected)
            self.assertEqual(climate["recirculation_raw"], raw)
            self.assertEqual(set(climate), {"recirculation_active", "recirculation_raw"})

    def test_demo_payload_uses_canonical_recirculation_field(self):
        for now in (0.0, 50.0):
            with mock.patch.object(server.time, "time", return_value=now):
                climate = server.demo_status("drive", started_at=0.0)["state"]["climate"]
            self.assertEqual(climate["air_intake"], "Recirc" if climate["recirculation_active"] else "Normal")

    def test_consumers_use_canonical_recirculation_name(self):
        vehicle_source = VEHICLE_PATH.read_text()
        cli_source = STATUS_CLI_PATH.read_text()
        self.assertIn("recirculation: climate.recirculation_active", vehicle_source)
        self.assertIn("climate.get('recirculation_active')", cli_source)
        self.assertNotIn("Front demist air", cli_source)

if __name__ == "__main__":
    unittest.main()
