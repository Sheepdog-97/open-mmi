from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from ui import vehicle_profile_conformance, vehicle_profile_qualification, vehicle_setup


class VehicleProfileConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "vehicles").mkdir()
        (self.root / "docs").mkdir()
        (self.root / "docs" / "qualification.md").write_text(
            "# Qualification\n", encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def metadata(
        identifier: str = "example_car",
        *,
        maturity: str = "qualified",
        level: str = "hardware",
    ) -> dict[str, object]:
        if level == "none":
            last_tested = None
            scope: list[str] = []
            evidence: list[dict[str, str]] = []
        else:
            last_tested = "2026-07-20"
            scope = ["Passive CAN receive and canonical mappings"]
            evidence = [
                {
                    "kind": "replay",
                    "path": "docs/qualification.md",
                    "description": "Deterministic replay qualification evidence.",
                }
            ]
            if level == "hardware":
                evidence.append(
                    {
                        "kind": "hardware",
                        "path": "docs/qualification.md",
                        "description": "Reviewable hardware qualification evidence.",
                    }
                )
        return {
            "id": identifier,
            "display_name": "Example Car",
            "manufacturer": "Example",
            "model": "Car",
            "generation": "One",
            "platform": "Example Platform",
            "model_years": {"from": 2010, "to": 2014},
            "maturity": maturity,
            "license": "GPL-3.0-only",
            "maintainers": ["Open MMI contributors"],
            "qualification": {
                "level": level,
                "last_tested": last_tested,
                "scope": scope,
                "evidence": evidence,
            },
            "limitations": ["Example qualification scope only."],
        }

    def profile(
        self,
        identifier: str = "example_car",
        *,
        maturity: str = "qualified",
        level: str = "hardware",
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "metadata": self.metadata(identifier, maturity=maturity, level=level),
            "default_bus": "comfort",
            "can_buses": {
                "comfort": {
                    "interface": "can0",
                    "bitrate": 100000,
                    "provisioning": "manual",
                    "bring_up": False,
                }
            },
            "rules": [
                {
                    "id": "0x100",
                    "byte": 0,
                    "value": 1,
                    "event": "play_pause",
                }
            ],
            "presence": [],
            "status": [
                {
                    "id": "0x101",
                    "byte": 0,
                    "type": "bool",
                    "path": "doors.front_right",
                    "true": "0x01",
                    "false": "0x00",
                }
            ],
        }

    def write_profile(self, identifier: str, document: dict[str, object]) -> Path:
        relative = "example/car/one-platform/config.json"
        path = self.root / "vehicles" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        catalogue = {
            "schema_version": 1,
            "catalogue_id": "open-mmi.maintained-vehicles",
            "profiles": {
                identifier: {"path": relative, "aliases": []},
            },
        }
        (self.root / "vehicles" / "catalogue.v1.json").write_text(
            json.dumps(catalogue, indent=2) + "\n", encoding="utf-8"
        )
        fixture_path = path.parent / "fixtures" / "mappings.v1.json"
        fixture_path.parent.mkdir()
        fixture_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "fixture_id": "open-mmi.vehicle-mapping-fixtures",
                    "profile_id": identifier,
                    "cases": [
                        {
                            "name": "play-pause",
                            "bus": "comfort",
                            "frames": [{"id": "0x100", "data": "01"}],
                            "expect": {
                                "events": [{"event": "play_pause", "payload": None}],
                                "statuses": {},
                            },
                        },
                        {
                            "name": "front-right-door",
                            "bus": "comfort",
                            "frames": [{"id": "0x101", "data": "01"}],
                            "expect": {
                                "events": [],
                                "statuses": {"doors.front_right": True},
                            },
                        },
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        level = document["metadata"]["qualification"]["level"]
        record = vehicle_profile_qualification.default_record(identifier)
        if level != "none":
            record["current"] = {
                "level": level,
                "tested_on": "2026-07-20",
                "scope": ["Passive CAN receive and canonical mappings"],
                "compatibility": {
                    "equipment": ["Example passive CAN capture setup"] if level == "hardware" else [],
                    "variants": ["Example Car 2010 to 2014"] if level == "hardware" else [],
                },
                "review": {
                    "status": "approved",
                    "reviewers": ["Open MMI maintainers"],
                    "reviewed_on": "2026-07-20",
                    "recheck_after": "2027-07-20",
                },
            }
            record["history"] = [
                {
                    "from": "replay" if level == "hardware" else "none",
                    "to": level,
                    "date": "2026-07-20",
                    "reason": "Test qualification transition.",
                    "reviewers": ["Open MMI maintainers"],
                }
            ]
        evidence_path = path.parent / "evidence" / "qualification.v1.json"
        evidence_path.parent.mkdir(exist_ok=True)
        evidence_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return path

    def test_qualified_profile_requires_hardware_evidence(self) -> None:
        valid = vehicle_profile_conformance.validate_metadata(
            self.profile(), expected_id="example_car"
        )
        self.assertTrue(valid["valid"], valid)

        invalid = self.profile(level="replay")
        result = vehicle_profile_conformance.validate_metadata(
            invalid, expected_id="example_car"
        )
        self.assertFalse(result["valid"])
        self.assertIn(
            "qualified-without-hardware",
            {issue["code"] for issue in result["errors"]},
        )

    def test_candidate_requires_replay_or_hardware_evidence(self) -> None:
        result = vehicle_profile_conformance.validate_metadata(
            self.profile(maturity="candidate", level="none"),
            expected_id="example_car",
        )
        codes = {issue["code"] for issue in result["errors"]}
        self.assertIn("candidate-without-qualification", codes)
        self.assertIn("candidate-without-test-evidence", codes)

        valid = vehicle_profile_conformance.validate_metadata(
            self.profile(maturity="candidate", level="replay"),
            expected_id="example_car",
        )
        self.assertTrue(valid["valid"], valid)

    def test_experimental_profile_may_have_no_qualification(self) -> None:
        result = vehicle_profile_conformance.validate_metadata(
            self.profile(maturity="experimental", level="none"),
            expected_id="example_car",
        )
        self.assertTrue(result["valid"], result)

    def test_profile_id_must_match_maintained_catalogue_identity(self) -> None:
        result = vehicle_profile_conformance.validate_metadata(
            self.profile("other_car"), expected_id="example_car"
        )
        self.assertIn(
            "profile-id-mismatch",
            {issue["code"] for issue in result["errors"]},
        )

    def test_maintained_schema_rejects_unknown_top_level_fields(self) -> None:
        document = self.profile()
        document["private_decoder_language"] = True
        result = vehicle_profile_conformance.validate_metadata(
            document, expected_id="example_car"
        )
        self.assertIn(
            "unsupported-profile-field",
            {issue["code"] for issue in result["errors"]},
        )

    def test_normal_custom_profile_validation_does_not_require_admission_metadata(self) -> None:
        custom = {
            "default_bus": "comfort",
            "can_buses": {"comfort": {"interface": "can0"}},
            "rules": [],
            "presence": [],
            "status": [],
        }
        result = vehicle_setup.validate_profile(custom)
        self.assertTrue(result["valid"], result)

    def test_catalogue_report_checks_evidence_and_lists_capabilities(self) -> None:
        self.write_profile("example_car", self.profile())
        report = vehicle_profile_conformance.catalogue_report(self.root)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["summary"]["maturity"], {"qualified": 1})
        profile = report["profiles"][0]
        self.assertEqual(profile["capabilities"]["events"], ["play_pause"])
        self.assertEqual(profile["capabilities"]["statuses"], ["doors.front_right"])

        (self.root / "docs" / "qualification.md").unlink()
        report = vehicle_profile_conformance.catalogue_report(self.root)
        self.assertFalse(report["valid"])
        self.assertIn(
            "missing-evidence-file",
            {
                issue["code"]
                for issue in report["profiles"][0]["validation"]["errors"]
            },
        )

    def test_structured_candidate_mappings_are_validated_but_not_capabilities(self) -> None:
        profile_path = self.write_profile("example_car", self.profile())
        candidate_path = profile_path.parent / "notes" / "candidate_mappings.v1.json"
        candidate_path.parent.mkdir(exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_id": "open-mmi.vehicle-candidate-mappings",
                    "profile_id": "example_car",
                    "runtime_authority": False,
                    "candidates": [
                        {
                            "id": "fuel-level",
                            "status": "candidate",
                            "confidence": "strong",
                            "rule": {
                                "id": "0x621",
                                "byte": 3,
                                "type": "scaled",
                                "path": "fuel.level_l",
                                "mask": "0x7F",
                                "scale": 1.0,
                                "offset": 0.0,
                                "round": 0,
                                "raw_path": "fuel.level_raw",
                            },
                            "source_profiles": ["related_car"],
                            "local_evidence": "Plausible local value with related-profile corroboration.",
                            "verify": ["Compare against an independent fuel quantity."],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        report = vehicle_profile_conformance.catalogue_report(self.root)
        self.assertTrue(report["valid"], report)
        profile = report["profiles"][0]
        self.assertEqual(profile["candidates"]["count"], 1)
        self.assertEqual(
            profile["candidates"]["items"][0]["outputs"],
            ["fuel.level_l", "fuel.level_raw"],
        )
        self.assertNotIn("fuel.level_l", profile["capabilities"]["statuses"])
        self.assertNotIn("fuel.level_raw", profile["capabilities"]["statuses"])

    def test_candidate_mappings_cannot_gain_runtime_authority_or_duplicate_active_output(self) -> None:
        profile_path = self.write_profile("example_car", self.profile())
        candidate_path = profile_path.parent / "notes" / "candidate_mappings.v1.json"
        candidate_path.parent.mkdir(exist_ok=True)
        candidate_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "record_id": "open-mmi.vehicle-candidate-mappings",
                    "profile_id": "example_car",
                    "runtime_authority": True,
                    "candidates": [
                        {
                            "id": "front-right-door",
                            "status": "candidate",
                            "confidence": "strong",
                            "rule": {
                                "id": "0x101",
                                "byte": 0,
                                "type": "bool",
                                "path": "doors.front_right",
                                "true": "0x01",
                                "false": "0x00",
                            },
                            "source_profiles": ["related_car"],
                            "local_evidence": "Example overlap.",
                            "verify": ["Do not allow an already-active output to remain a candidate."],
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        report = vehicle_profile_conformance.catalogue_report(self.root)
        self.assertFalse(report["valid"])
        codes = {
            issue["code"]
            for issue in report["profiles"][0]["validation"]["errors"]
        }
        self.assertIn("candidate-runtime-authority", codes)
        self.assertIn("candidate-already-active", codes)

    def test_catalogue_report_rejects_duplicate_json_keys(self) -> None:
        relative = "example/car/one-platform/config.json"
        path = self.root / "vehicles" / relative
        path.parent.mkdir(parents=True)
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        (self.root / "vehicles" / "catalogue.v1.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "catalogue_id": "open-mmi.maintained-vehicles",
                    "profiles": {
                        "example_car": {"path": relative, "aliases": []}
                    },
                }
            ),
            encoding="utf-8",
        )
        report = vehicle_profile_conformance.catalogue_report(self.root)
        self.assertFalse(report["valid"])
        self.assertIn(
            "unreadable-profile",
            {
                issue["code"]
                for issue in report["profiles"][0]["validation"]["errors"]
            },
        )

    def test_repository_seat_profile_conforms(self) -> None:
        root = Path(__file__).resolve().parents[1]
        report = vehicle_profile_conformance.catalogue_report(
            root, identifiers=["seat_1p"]
        )
        self.assertTrue(report["valid"], report)
        profile = report["profiles"][0]
        self.assertEqual(profile["id"], "seat-leon-1p-pq35")
        self.assertEqual(profile["aliases"], ["seat_1p"])
        self.assertTrue(profile["fixtures"]["valid"])
        self.assertEqual(profile["metadata"]["maturity"], "qualified")
        self.assertEqual(profile["metadata"]["qualification"]["level"], "hardware")


if __name__ == "__main__":
    unittest.main()
