from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from pathlib import Path

from open_mmi_trust.accepted_state import (
    ACCEPTED_STATE_ID,
    TRANSITION_EQUAL,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    TRANSITION_NARROWER,
    AcceptedTrustStateError,
    _record_accepted_manifest,
    accepted_state_digest,
    canonical_accepted_state_bytes,
    compare_trust_manifests,
    read_accepted_state,
    validate_accepted_state,
)
from open_mmi_trust.manifest import load_manifest, manifest_digest
from open_mmi_trust import accepted_state as accepted_state_module
from open_mmi_trust import accepted_state_cli


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "open_mmi_trust" / "data" / "accepted-owner-trust.v1.schema.json"


def manifest_copy() -> dict:
    return copy.deepcopy(load_manifest())


class AcceptedOwnerTrustStateTests(unittest.TestCase):
    def test_record_and_read_round_trip_uses_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trust" / "accepted-owner-trust.v1.json"
            recorded = _record_accepted_manifest(manifest_copy(), path)
            loaded = read_accepted_state(path)

            self.assertEqual(loaded, recorded)
            self.assertEqual(recorded["state_id"], ACCEPTED_STATE_ID)
            self.assertEqual(
                recorded["manifest_digest"],
                "sha256:" + manifest_digest(recorded["manifest"]),
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_missing_state_is_not_established(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            self.assertIsNone(read_accepted_state(path))

    def test_unknown_state_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = _record_accepted_manifest(manifest_copy(), path)
            state["candidate_override"] = True
            with self.assertRaisesRegex(AcceptedTrustStateError, "unknown keys"):
                validate_accepted_state(state)

    def test_embedded_manifest_digest_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = _record_accepted_manifest(manifest_copy(), path)
            state["manifest_digest"] = "sha256:" + "0" * 64
            with self.assertRaisesRegex(AcceptedTrustStateError, "does not match"):
                validate_accepted_state(state)

    def test_symlink_state_is_rejected_instead_of_treated_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("{}\n", encoding="utf-8")
            path = root / "state.json"
            path.symlink_to(target)
            with self.assertRaisesRegex(AcceptedTrustStateError, "untrusted"):
                read_accepted_state(path)

    def test_untrusted_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trust" / "state.json"
            _record_accepted_manifest(manifest_copy(), path)
            path.chmod(0o644)
            with self.assertRaisesRegex(AcceptedTrustStateError, "untrusted"):
                read_accepted_state(path)

    def test_equal_manifest_is_allowed_without_ack(self):
        current = manifest_copy()
        comparison = compare_trust_manifests(current, current)
        self.assertEqual(comparison["relation"], TRANSITION_EQUAL)
        self.assertTrue(comparison["allowed_without_owner_ack"])
        self.assertEqual(comparison["changes"], [])

    def test_mutation_primitive_refuses_broadening_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trust" / "accepted-owner-trust.v1.json"
            candidate = manifest_copy()
            accepted = copy.deepcopy(candidate)
            accepted["policy_generation"] = 1
            accepted["capabilities"]["telemetry.collection"] = {
                "policy": "prohibited",
                "assurance": "runtime-guarded",
            }
            _record_accepted_manifest(accepted, path)
            original = path.read_bytes()
            with self.assertRaisesRegex(AcceptedTrustStateError, "refusing to broaden"):
                _record_accepted_manifest(candidate, path)
            self.assertEqual(path.read_bytes(), original)

    def test_mutation_primitive_allows_narrowing_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trust" / "accepted-owner-trust.v1.json"
            accepted = manifest_copy()
            accepted["capabilities"]["network.external-egress"]["purposes"].append(
                "updates.secondary-fetch"
            )
            accepted["capabilities"]["network.external-egress"]["purposes"].sort()
            _record_accepted_manifest(accepted, path)
            candidate = manifest_copy()
            candidate["policy_generation"] += 1
            updated = _record_accepted_manifest(candidate, path)
            self.assertEqual(updated["manifest"], candidate)

    def test_generation_advance_without_boundary_change_is_equal(self):
        accepted = manifest_copy()
        candidate = copy.deepcopy(accepted)
        candidate["policy_generation"] += 1
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_EQUAL)
        self.assertTrue(comparison["allowed_without_owner_ack"])

    def test_policy_expansion_requires_owner_ack(self):
        candidate = manifest_copy()
        accepted = copy.deepcopy(candidate)
        accepted["policy_generation"] = 1
        accepted["capabilities"]["telemetry.collection"] = {
            "policy": "prohibited",
            "assurance": "runtime-guarded",
        }
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_EXPANSION)
        self.assertFalse(comparison["allowed_without_owner_ack"])
        self.assertIn(
            "policy-expansion",
            {change["kind"] for change in comparison["changes"]},
        )

    def test_added_purpose_is_expansion(self):
        accepted = manifest_copy()
        candidate = copy.deepcopy(accepted)
        candidate["policy_generation"] += 1
        purposes = candidate["capabilities"]["network.external-egress"]["purposes"]
        purposes.append("updates.secondary-fetch")
        purposes.sort()
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_EXPANSION)
        addition = next(change for change in comparison["changes"] if change["kind"] == "purposes-added")
        self.assertEqual(addition["purposes"], ["updates.secondary-fetch"])

    def test_removed_purpose_is_narrower(self):
        accepted = manifest_copy()
        candidate = copy.deepcopy(accepted)
        candidate["policy_generation"] += 1
        candidate["capabilities"]["network.external-egress"]["purposes"].remove(
            "media.internet-radio"
        )
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_NARROWER)
        self.assertTrue(comparison["allowed_without_owner_ack"])

    def test_assurance_weakening_is_expansion(self):
        accepted = manifest_copy()
        candidate = copy.deepcopy(accepted)
        candidate["policy_generation"] += 1
        accepted["capabilities"]["telemetry.collection"]["assurance"] = "os-enforced"
        candidate["capabilities"]["telemetry.collection"]["assurance"] = "runtime-guarded"
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_EXPANSION)
        weakened = next(change for change in comparison["changes"] if change["kind"] == "assurance-weakened")
        self.assertEqual(weakened["capability"], "telemetry.collection")

    def test_assurance_strengthening_is_narrower(self):
        accepted = manifest_copy()
        candidate = copy.deepcopy(accepted)
        candidate["policy_generation"] += 1
        candidate["capabilities"]["telemetry.collection"]["assurance"] = "os-enforced"
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_NARROWER)
        self.assertTrue(comparison["allowed_without_owner_ack"])

    def test_generation_regression_is_blocked_even_if_boundary_is_narrower(self):
        accepted = manifest_copy()
        accepted["policy_generation"] = 3
        candidate = copy.deepcopy(accepted)
        candidate["policy_generation"] = 2
        candidate["capabilities"]["network.external-egress"] = {
            "policy": "prohibited",
            "assurance": "declared",
            "purposes": [],
        }
        comparison = compare_trust_manifests(accepted, candidate)
        self.assertEqual(comparison["relation"], TRANSITION_GENERATION_REGRESSION)
        self.assertFalse(comparison["allowed_without_owner_ack"])

    def test_state_bytes_and_digest_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = _record_accepted_manifest(manifest_copy(), path)
            payload = json.loads(canonical_accepted_state_bytes(state))
            self.assertEqual(payload, state)
            self.assertRegex(accepted_state_digest(state), r"^sha256:[0-9a-f]{64}$")

    def test_schema_is_strict_and_checked(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "Open MMI Accepted Owner Trust State v1")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["state_id"]["const"], ACCEPTED_STATE_ID)

    def test_noninteractive_bootstrap_refuses_without_writing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "trust-manifest.json"
            state_path = root / "trust" / "accepted-owner-trust.v1.json"
            manifest_path.write_text(
                json.dumps(manifest_copy()), encoding="utf-8"
            )
            with (
                mock.patch.object(accepted_state_cli, "DEFAULT_MANIFEST_PATH", manifest_path),
                mock.patch.object(accepted_state_cli, "DEFAULT_ACCEPTED_STATE_PATH", state_path),
                mock.patch.object(accepted_state_module, "DEFAULT_ACCEPTED_STATE_PATH", Path("/var/lib/open-mmi/trust/accepted-owner-trust.v1.json")),
                mock.patch.object(accepted_state_cli.os, "geteuid", return_value=0),
                mock.patch.object(accepted_state_cli.sys.stdin, "isatty", return_value=False),
                mock.patch.object(accepted_state_cli.sys.stdout, "isatty", return_value=False),
            ):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = accepted_state_cli.main(["accept-current"])
            self.assertEqual(result, 2)
            self.assertFalse(state_path.exists())

    def test_existing_state_cannot_be_broadened_by_accept_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = root / "trust-manifest.json"
            state_path = root / "trust" / "accepted-owner-trust.v1.json"
            current = manifest_copy()
            manifest_path.write_text(json.dumps(current), encoding="utf-8")
            accepted = copy.deepcopy(current)
            accepted["policy_generation"] = 1
            accepted["capabilities"]["telemetry.collection"] = {
                "policy": "prohibited",
                "assurance": "runtime-guarded",
            }
            _record_accepted_manifest(accepted, state_path)
            original = state_path.read_bytes()
            with (
                mock.patch.object(accepted_state_cli, "DEFAULT_MANIFEST_PATH", manifest_path),
                mock.patch.object(accepted_state_cli, "DEFAULT_ACCEPTED_STATE_PATH", state_path),
                mock.patch.object(accepted_state_cli.os, "geteuid", return_value=0),
            ):
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    result = accepted_state_cli.main(["accept-current"])
            self.assertEqual(result, 2)
            self.assertEqual(state_path.read_bytes(), original)

    def test_owner_cli_exposes_no_candidate_path_or_broadening_command(self):
        source = (ROOT / "open_mmi_trust" / "accepted_state_cli.py").read_text(encoding="utf-8")
        self.assertNotIn("--manifest", source)
        self.assertNotIn("--candidate", source)
        self.assertIn("accept-current", source)
        self.assertIn("cannot broaden state after installation", source)
        self.assertNotIn("_write_accepted_state", source)


if __name__ == "__main__":
    unittest.main()
