from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
import io
from pathlib import Path

from open_mmi_trust.accepted_state import (
    _record_accepted_manifest,
    _record_acknowledged_expansion,
    accepted_state_digest,
    read_accepted_state,
)
from open_mmi_trust.lineage import (
    ACK_NONE,
    ACK_TRANSITION,
    DECISION_ALLOWED,
    DECISION_EXPANSION,
    DEFAULT_TRANSITION_LINEAGE_DIR,
    SOURCE_PREPARED_UPDATE,
    TransitionLineageError,
    _record_lineage_baseline,
    _record_state_transition,
    lineage_record_digest,
    lineage_summary,
    read_transition_lineage,
    require_lineage_current,
)
from open_mmi_trust.manifest import load_manifest
from open_mmi_trust import lineage_cli


class TrustLineageTests(unittest.TestCase):
    def fixture(self, root: Path):
        state_path = root / "trust" / "accepted-owner-trust.v1.json"
        lineage_path = root / "trust" / "transition-lineage.v1.d"
        manifest = load_manifest()
        state = _record_accepted_manifest(manifest, state_path)
        baseline = _record_lineage_baseline(state, lineage_path)
        return manifest, state_path, lineage_path, state, baseline

    def test_baseline_is_hash_named_strict_and_anchors_current_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, lineage_path, state, baseline = self.fixture(root)
            records = read_transition_lineage(lineage_path)
            self.assertEqual(records, [baseline])
            self.assertEqual(require_lineage_current(state, lineage_path), baseline)
            digest = lineage_record_digest(baseline).split(":", 1)[1]
            files = list(lineage_path.iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].name, f"00000001-{digest}.json")
            self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
            self.assertEqual(lineage_path.stat().st_mode & 0o777, 0o700)
            summary = lineage_summary(records)
            self.assertEqual(summary["head_accepted_state_digest"], accepted_state_digest(state))
            self.assertEqual(summary["history_before_baseline"], "unverified")

    def test_narrowing_record_recomputes_relation_and_extends_hash_chain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, state_path, lineage_path, before, baseline = self.fixture(root)
            narrowed = copy.deepcopy(manifest)
            narrowed["policy_generation"] += 1
            narrowed["capabilities"]["network.external-egress"]["purposes"].remove(
                "media.internet-radio"
            )
            after = _record_accepted_manifest(narrowed, state_path)
            record = _record_state_transition(
                before,
                after,
                source=SOURCE_PREPARED_UPDATE,
                decision=DECISION_ALLOWED,
                acknowledgement_required=False,
                acknowledgement_method=ACK_NONE,
                transaction_id="prepare-" + "a" * 32,
                candidate_commit="b" * 40,
                path=lineage_path,
            )
            self.assertIsNotNone(record)
            records = read_transition_lineage(lineage_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[1]["previous_record_digest"], lineage_record_digest(baseline))
            self.assertEqual(records[1]["relation"], "narrower")
            self.assertIn("purposes-removed", {item["kind"] for item in records[1]["changes"]})
            self.assertEqual(require_lineage_current(after, lineage_path)["sequence"], 2)

    def test_expansion_record_requires_owner_acknowledgement_and_authorization_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = load_manifest()
            accepted_manifest = copy.deepcopy(manifest)
            accepted_manifest["policy_generation"] = 1
            accepted_manifest["capabilities"]["telemetry.collection"] = {
                "policy": "prohibited",
                "assurance": "runtime-guarded",
            }
            state_path = root / "trust" / "accepted-owner-trust.v1.json"
            lineage_path = root / "trust" / "transition-lineage.v1.d"
            before = _record_accepted_manifest(accepted_manifest, state_path)
            _record_lineage_baseline(before, lineage_path)
            after = _record_acknowledged_expansion(
                manifest,
                expected_accepted_state_digest=accepted_state_digest(before),
                path=state_path,
            )
            with self.assertRaisesRegex(TransitionLineageError, "requires owner acknowledgement"):
                _record_state_transition(
                    before,
                    after,
                    source=SOURCE_PREPARED_UPDATE,
                    decision=DECISION_ALLOWED,
                    acknowledgement_required=False,
                    acknowledgement_method=ACK_NONE,
                    transaction_id="prepare-" + "a" * 32,
                    candidate_commit="b" * 40,
                    path=lineage_path,
                )
            record = _record_state_transition(
                before,
                after,
                source=SOURCE_PREPARED_UPDATE,
                decision=DECISION_EXPANSION,
                acknowledgement_required=True,
                acknowledgement_method=ACK_TRANSITION,
                transaction_id="prepare-" + "a" * 32,
                candidate_commit="b" * 40,
                authorization_digest="sha256:" + "c" * 64,
                path=lineage_path,
            )
            self.assertEqual(record["relation"], "expansion")
            self.assertTrue(record["owner_acknowledgement"]["required"])

    def test_record_edit_and_reordering_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, state_path, lineage_path, before, _ = self.fixture(root)
            narrowed = copy.deepcopy(manifest)
            narrowed["policy_generation"] += 1
            narrowed["capabilities"]["vehicle-data.persistence"]["purposes"].remove("trip-b")
            after = _record_accepted_manifest(narrowed, state_path)
            _record_state_transition(
                before,
                after,
                source=SOURCE_PREPARED_UPDATE,
                decision=DECISION_ALLOWED,
                acknowledgement_required=False,
                acknowledgement_method=ACK_NONE,
                transaction_id="prepare-" + "a" * 32,
                candidate_commit="b" * 40,
                path=lineage_path,
            )
            files = sorted(lineage_path.iterdir())
            payload = json.loads(files[1].read_text(encoding="utf-8"))
            payload["decision"] = "local-owner-accepted-state"
            files[1].write_text(json.dumps(payload), encoding="utf-8")
            files[1].chmod(0o600)
            with self.assertRaises(TransitionLineageError):
                read_transition_lineage(lineage_path)

    def test_tail_deletion_is_detected_by_current_accepted_state_anchor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, state_path, lineage_path, before, _ = self.fixture(root)
            narrowed = copy.deepcopy(manifest)
            narrowed["policy_generation"] += 1
            narrowed["capabilities"]["network.external-egress"]["purposes"].remove("media.jellyfin")
            after = _record_accepted_manifest(narrowed, state_path)
            _record_state_transition(
                before,
                after,
                source=SOURCE_PREPARED_UPDATE,
                decision=DECISION_ALLOWED,
                acknowledgement_required=False,
                acknowledgement_method=ACK_NONE,
                transaction_id="prepare-" + "a" * 32,
                candidate_commit="b" * 40,
                path=lineage_path,
            )
            sorted(lineage_path.iterdir())[-1].unlink()
            with self.assertRaisesRegex(TransitionLineageError, "does not anchor"):
                require_lineage_current(after, lineage_path)

    def test_unknown_files_and_weakened_permissions_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, lineage_path, _, _ = self.fixture(root)
            (lineage_path / "notes.txt").write_text("nope\n", encoding="utf-8")
            with self.assertRaisesRegex(TransitionLineageError, "unexpected entry"):
                read_transition_lineage(lineage_path)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, lineage_path, _, _ = self.fixture(root)
            record = next(lineage_path.iterdir())
            record.chmod(0o644)
            with self.assertRaisesRegex(TransitionLineageError, "untrusted"):
                read_transition_lineage(lineage_path)

    def test_duplicate_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, lineage_path, _, baseline = self.fixture(root)
            record = next(lineage_path.iterdir())
            raw = record.read_text(encoding="utf-8")
            raw = '{"schema_version":1,' + raw[1:]
            record.write_text(raw, encoding="utf-8")
            record.chmod(0o600)
            with self.assertRaisesRegex(TransitionLineageError, "duplicate"):
                read_transition_lineage(lineage_path)

    def test_owner_cli_bootstrap_is_local_digest_bound_and_has_no_bypass_flags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "trust" / "accepted-owner-trust.v1.json"
            lineage_path = root / "trust" / "transition-lineage.v1.d"
            state = _record_accepted_manifest(load_manifest(), state_path)
            suffix = accepted_state_digest(state).split(":", 1)[1][:12]
            output = io.StringIO()
            with mock.patch.object(lineage_cli, "DEFAULT_ACCEPTED_STATE_PATH", state_path), mock.patch.object(
                lineage_cli, "DEFAULT_TRANSITION_LINEAGE_DIR", lineage_path
            ), mock.patch.object(lineage_cli, "_require_root", return_value=None), mock.patch.object(
                lineage_cli, "_require_local_tty", return_value=None
            ), mock.patch(
                "builtins.input", return_value=f"ESTABLISH LINEAGE {suffix}"
            ), redirect_stdout(output):
                result = lineage_cli.main(["bootstrap"])
            self.assertEqual(result, 0)
            self.assertEqual(len(read_transition_lineage(lineage_path)), 1)
            self.assertIn("history_before_baseline", output.getvalue())

        source = (Path(__file__).resolve().parents[1] / "open_mmi_trust" / "lineage_cli.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("--candidate", "--manifest", "--path", "--yes", "--confirm"):
            self.assertNotIn(forbidden, source)
        self.assertIn("interactive local terminal", source)

    def test_reconcile_appends_missing_narrowing_evidence_without_rewriting_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, state_path, lineage_path, before, baseline = self.fixture(root)
            narrowed = copy.deepcopy(manifest)
            narrowed["policy_generation"] += 1
            narrowed["capabilities"]["vehicle-data.persistence"]["purposes"].remove("trip-a")
            after = _record_accepted_manifest(narrowed, state_path)
            suffix = accepted_state_digest(after).split(":", 1)[1][:12]
            with mock.patch.object(lineage_cli, "DEFAULT_ACCEPTED_STATE_PATH", state_path), mock.patch.object(
                lineage_cli, "DEFAULT_TRANSITION_LINEAGE_DIR", lineage_path
            ), mock.patch.object(lineage_cli, "DEFAULT_TRANSITION_AUTHORIZATION_PATH", root / "trust" / "transition-authorization.v1.json"), mock.patch.object(
                lineage_cli, "_require_root", return_value=None
            ), mock.patch.object(lineage_cli, "_require_local_tty", return_value=None), mock.patch(
                "builtins.input", return_value=f"RECONCILE LINEAGE {suffix}"
            ), redirect_stdout(io.StringIO()):
                result = lineage_cli.main(["reconcile-current"])
            self.assertEqual(result, 0)
            records = read_transition_lineage(lineage_path)
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0], baseline)
            self.assertEqual(records[1]["source"], "lineage-reconcile")
            self.assertEqual(records[1]["relation"], "narrower")
            self.assertEqual(require_lineage_current(after, lineage_path)["sequence"], 2)

    def test_reconcile_refuses_unproven_expansion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = load_manifest()
            accepted = copy.deepcopy(manifest)
            accepted["policy_generation"] = 1
            accepted["capabilities"]["telemetry.collection"] = {
                "policy": "prohibited",
                "assurance": "runtime-guarded",
            }
            state_path = root / "trust" / "accepted-owner-trust.v1.json"
            lineage_path = root / "trust" / "transition-lineage.v1.d"
            before = _record_accepted_manifest(accepted, state_path)
            _record_lineage_baseline(before, lineage_path)
            _record_acknowledged_expansion(
                manifest, expected_accepted_state_digest=accepted_state_digest(before), path=state_path
            )
            error = io.StringIO()
            with mock.patch.object(lineage_cli, "DEFAULT_ACCEPTED_STATE_PATH", state_path), mock.patch.object(
                lineage_cli, "DEFAULT_TRANSITION_LINEAGE_DIR", lineage_path
            ), mock.patch.object(lineage_cli, "DEFAULT_TRANSITION_AUTHORIZATION_PATH", root / "trust" / "missing-auth.json"), mock.patch.object(
                lineage_cli, "_require_root", return_value=None
            ), redirect_stderr(error):
                result = lineage_cli.main(["reconcile-current"])
            self.assertEqual(result, 2)
            self.assertIn("without matching transition-authorization evidence", error.getvalue())
            self.assertEqual(len(read_transition_lineage(lineage_path)), 1)



if __name__ == "__main__":
    unittest.main()
