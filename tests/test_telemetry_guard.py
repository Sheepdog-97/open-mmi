from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from open_mmi_trust import load_manifest

from open_mmi_telemetry.guard import (
    TelemetryDenied,
    TelemetryGuardError,
    _create_authorization,
    collect_with_guard,
    collection_decision,
    normalize_scope,
    read_authorization,
    _revoke_authorization,
    scope_digest,
)


VIN = "WVWZZZ1KZ6W000001"
OTHER_VIN = "WVWZZZ1KZ6W000002"


def scope(*signals: str, purpose: str = "owner-diagnostics") -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": purpose,
        "signals": sorted(signals or ("vehicle.rpm", "vehicle.speed")),
        "retention": "session",
        "destination": "local-only",
    }


class TelemetryGuardTests(unittest.TestCase):

    def test_generation_two_does_not_expand_telemetry_egress_or_persistence(self):
        manifest = load_manifest()
        self.assertEqual(
            set(manifest["capabilities"]["network.external-egress"]["purposes"]),
            {
                "dashboard.configured-ui",
                "media.internet-radio",
                "media.jellyfin",
                "updates.release-fetch",
            },
        )
        self.assertEqual(
            set(manifest["capabilities"]["vehicle-data.persistence"]["purposes"]),
            {
                "service-reminder",
                "trip-a",
                "trip-b",
                "trip-distance",
                "vehicle-runtime-status",
            },
        )

    def test_missing_authorization_denies_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = collection_decision(VIN, scope(), Path(tmp) / "authorization.json")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "not-authorized")

    def test_authorization_is_vin_bound_without_storing_raw_vin(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            authorization = _create_authorization(VIN, scope(), path)
            text = path.read_text(encoding="utf-8")
            self.assertNotIn(VIN, text)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(authorization["vin_binding"]["algorithm"], "pbkdf2-sha256")

            allowed = collection_decision(VIN, scope(), path)
            wrong_vehicle = collection_decision(OTHER_VIN, scope(), path)
            self.assertTrue(allowed.allowed)
            self.assertEqual(allowed.reason, "authorized")
            self.assertFalse(wrong_vehicle.allowed)
            self.assertEqual(wrong_vehicle.reason, "vehicle-mismatch")

    def test_scope_change_invalidates_prior_authorization(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            original = scope("vehicle.rpm", "vehicle.speed")
            expanded = scope("vehicle.coolant-temperature", "vehicle.rpm", "vehicle.speed")
            _create_authorization(VIN, original, path)
            decision = collection_decision(VIN, expanded, path)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "scope-mismatch")

    def test_reauthorization_replaces_old_scope_instead_of_accumulating_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            original = scope("vehicle.rpm")
            replacement = scope("vehicle.speed", purpose="owner-performance-view")
            _create_authorization(VIN, original, path)
            _create_authorization(VIN, replacement, path)
            self.assertFalse(collection_decision(VIN, original, path).allowed)
            self.assertTrue(collection_decision(VIN, replacement, path).allowed)
            stored = read_authorization(path)
            assert stored is not None
            self.assertEqual(stored["scope"], normalize_scope(replacement))

    def test_guard_runs_before_sampler(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            calls: list[str] = []

            def rpm_sampler() -> str:
                calls.append("rpm")
                return "2500"

            def speed_sampler() -> str:
                calls.append("speed")
                return "42"

            samplers = {
                "vehicle.rpm": rpm_sampler,
                "vehicle.speed": speed_sampler,
            }
            with self.assertRaises(TelemetryDenied):
                collect_with_guard(VIN, scope(), samplers, path)
            self.assertEqual(calls, [])

            _create_authorization(VIN, scope(), path)
            self.assertEqual(
                collect_with_guard(VIN, scope(), samplers, path),
                {"vehicle.rpm": "2500", "vehicle.speed": "42"},
            )
            self.assertEqual(calls, ["rpm", "speed"])

    def test_sampler_set_must_match_scope_before_collection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            calls: list[str] = []
            _create_authorization(VIN, scope(), path)

            def sampler() -> str:
                calls.append("sampled")
                return "value"

            with self.assertRaisesRegex(TelemetryGuardError, "match the authorized signal set"):
                collect_with_guard(VIN, scope(), {"vehicle.rpm": sampler}, path)
            self.assertEqual(calls, [])

    def test_v1_scope_cannot_authorize_persistence_or_remote_destination(self):
        persistent = scope()
        persistent["retention"] = "until-owner-deletes"
        with self.assertRaisesRegex(TelemetryGuardError, "session retention only"):
            normalize_scope(persistent)

        remote = scope()
        remote["destination"] = "remote"
        with self.assertRaisesRegex(TelemetryGuardError, "local-only collection only"):
            normalize_scope(remote)

    def test_scope_is_exact_sorted_and_canonical(self):
        unsorted = scope("vehicle.speed", "vehicle.rpm")
        unsorted["signals"] = ["vehicle.speed", "vehicle.rpm"]
        with self.assertRaisesRegex(TelemetryGuardError, "must be sorted"):
            normalize_scope(unsorted)

        duplicate = scope("vehicle.rpm")
        duplicate["signals"] = ["vehicle.rpm", "vehicle.rpm"]
        with self.assertRaisesRegex(TelemetryGuardError, "duplicates"):
            normalize_scope(duplicate)

        unknown_field = scope()
        unknown_field["maintainer_override"] = True
        with self.assertRaisesRegex(TelemetryGuardError, "unknown keys"):
            normalize_scope(unknown_field)

    def test_scope_digest_is_deterministic_and_binds_exact_scope(self):
        first = scope("vehicle.rpm", "vehicle.speed")
        reordered_mapping = {
            "destination": "local-only",
            "retention": "session",
            "signals": ["vehicle.rpm", "vehicle.speed"],
            "purpose": "owner-diagnostics",
            "schema_version": 1,
        }
        self.assertEqual(scope_digest(first), scope_digest(reordered_mapping))
        self.assertNotEqual(
            scope_digest(first),
            scope_digest(scope("vehicle.coolant-temperature", "vehicle.rpm", "vehicle.speed")),
        )


    def test_authorization_directory_is_private_when_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trust" / "authorization.json"
            _create_authorization(VIN, scope(), path)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_or_permission_weakened_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            _create_authorization(VIN, scope(), path)
            os.chmod(path, 0o644)
            decision = collection_decision(VIN, scope(), path)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "authorization-invalid")

    def test_duplicate_fields_in_authorization_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
            os.chmod(path, 0o600)
            decision = collection_decision(VIN, scope(), path)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "authorization-invalid")



    def test_package_does_not_export_authorization_mutators(self):
        import open_mmi_telemetry

        self.assertFalse(hasattr(open_mmi_telemetry, "authorize"))
        self.assertFalse(hasattr(open_mmi_telemetry, "revoke"))
        self.assertFalse(hasattr(open_mmi_telemetry, "_create_authorization"))
        self.assertFalse(hasattr(open_mmi_telemetry, "_revoke_authorization"))

    def test_supported_cli_has_no_noninteractive_authorization_bypass(self):
        source = (Path(__file__).resolve().parents[1] / "open_mmi_telemetry" / "cli.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_require_root()", source)
        self.assertIn("_require_local_tty()", source)
        self.assertNotIn("--confirm", source)
        self.assertNotIn("--vin-stdin", source)
        self.assertNotIn("--state-file", source)

    def test_revoke_returns_to_default_deny(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "authorization.json"
            _create_authorization(VIN, scope(), path)
            self.assertTrue(collection_decision(VIN, scope(), path).allowed)
            self.assertTrue(_revoke_authorization(path))
            self.assertFalse(collection_decision(VIN, scope(), path).allowed)
            self.assertFalse(_revoke_authorization(path))


if __name__ == "__main__":
    unittest.main()
