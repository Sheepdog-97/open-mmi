from __future__ import annotations

import contextlib
import copy
import io
import json
import unittest
from pathlib import Path

from canbusd import profile_replay
from ui import config_cli


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "vehicles" / "seat" / "leon" / "1p-pq35" / "config.json"
FIXTURE = PROFILE.parent / "fixtures" / "mappings.v1.json"


class ProfileReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_reference_fixture_covers_every_declared_output(self) -> None:
        report = profile_replay.replay_fixture(
            self.profile,
            self.fixture,
            expected_profile_id="seat-leon-1p-pq35",
        )
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["case_count"], 24)
        self.assertEqual(report["coverage"]["events"], 11)
        self.assertEqual(report["coverage"]["event_total"], 11)
        self.assertEqual(report["coverage"]["statuses"], 63)
        self.assertEqual(report["coverage"]["status_total"], 63)

    def test_changed_mapping_fails_the_existing_fixture(self) -> None:
        changed = copy.deepcopy(self.profile)
        changed["rules"][0]["value"] = 99

        report = profile_replay.replay_fixture(
            changed,
            self.fixture,
            expected_profile_id="seat-leon-1p-pq35",
        )

        self.assertFalse(report["valid"])
        case = next(item for item in report["cases"] if item["name"] == "steering-volume-up")
        self.assertFalse(case["valid"])

    def test_fixture_missing_a_capability_fails_coverage(self) -> None:
        fixture = copy.deepcopy(self.fixture)
        fixture["cases"] = [
            case
            for case in fixture["cases"]
            if case["name"] != "steering-volume-up"
        ]

        report = profile_replay.replay_fixture(
            self.profile,
            fixture,
            expected_profile_id="seat-leon-1p-pq35",
        )

        self.assertFalse(report["valid"])
        self.assertIn("volume_up", report["coverage"]["missing_events"])

    def test_cli_accepts_deprecated_profile_alias(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            result = config_cli.main(
                [
                    "vehicle-setup",
                    "replay",
                    "--root",
                    str(ROOT),
                    "seat_1p",
                ]
            )
        self.assertEqual(result, 0)


class MultiByteEventReplayTests(unittest.TestCase):
    def test_frame_requires_all_multi_byte_matches(self):
        profile = {
            "default_bus": "infotainment",
            "rules": [
                {
                    "id": "0x5C1",
                    "matches": [
                        {"byte": 0, "value": "0x13"},
                        {"byte": 2, "value": "0x01"},
                    ],
                    "event": "volume_up",
                }
            ],
        }

        self.assertEqual(
            profile_replay._events_for_frame(
                profile,
                "infotainment",
                0x5C1,
                bytes([0x13, 0x00, 0x01, 0x63]),
            ),
            [{"event": "volume_up", "payload": None}],
        )
        self.assertEqual(
            profile_replay._events_for_frame(
                profile,
                "infotainment",
                0x5C1,
                bytes([0x14, 0x00, 0x01, 0x63]),
            ),
            [],
        )

if __name__ == "__main__":
    unittest.main()
