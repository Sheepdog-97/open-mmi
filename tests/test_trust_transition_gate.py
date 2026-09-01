from __future__ import annotations

import copy
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from open_mmi_trust.accepted_state import (
    TRANSITION_EXPANSION,
    _record_accepted_manifest,
    read_accepted_state,
)
from open_mmi_trust.manifest import load_manifest, manifest_digest
from open_mmi_trust.lineage import _record_lineage_baseline, read_transition_lineage
from open_mmi_trust import transition_gate, transition_gate_cli


ROOT = Path(__file__).resolve().parents[1]
TRANSACTION = "prepare-" + "a" * 32


def manifest_copy() -> dict:
    return copy.deepcopy(load_manifest())


def expansion_pair() -> tuple[dict, dict]:
    candidate = manifest_copy()
    accepted = copy.deepcopy(candidate)
    accepted["policy_generation"] = 1
    accepted["capabilities"]["telemetry.collection"] = {
        "policy": "prohibited",
        "assurance": "runtime-guarded",
    }
    return accepted, candidate


class TrustTransitionGateTests(unittest.TestCase):
    def git(self, repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
            env={
                **os.environ,
                "GIT_CONFIG_COUNT": "2",
                "GIT_CONFIG_KEY_0": "commit.gpgSign",
                "GIT_CONFIG_VALUE_0": "false",
                "GIT_CONFIG_KEY_1": "tag.gpgSign",
                "GIT_CONFIG_VALUE_1": "false",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def staged(
        self,
        root: Path,
        *,
        accepted_manifest: dict | None = None,
        candidate_manifest: dict | None = None,
    ) -> tuple[Path, str, str, Path, Path]:
        accepted_manifest = copy.deepcopy(accepted_manifest or manifest_copy())
        candidate_manifest = copy.deepcopy(candidate_manifest or accepted_manifest)
        stage = root / "stage"
        stage.mkdir()
        self.git(stage, "init", "-b", "main")
        self.git(stage, "config", "user.name", "Open MMI Test")
        self.git(stage, "config", "user.email", "test@example.invalid")
        manifest_path = stage / transition_gate.CANDIDATE_MANIFEST_GIT_PATH
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(accepted_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (stage / "README.md").write_text("installed\n", encoding="utf-8")
        self.git(stage, "add", ".")
        self.git(stage, "commit", "-m", "installed")
        installed = self.git(stage, "rev-parse", "HEAD")

        manifest_path.write_text(
            json.dumps(candidate_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (stage / "README.md").write_text("candidate\n", encoding="utf-8")
        self.git(stage, "add", ".")
        self.git(stage, "commit", "-m", "candidate")
        candidate = self.git(stage, "rev-parse", "HEAD")

        trust = root / "trust"
        accepted_path = trust / "accepted-owner-trust.v1.json"
        authorization_path = trust / "transition-authorization.v1.json"
        accepted_state = _record_accepted_manifest(accepted_manifest, accepted_path)
        _record_lineage_baseline(accepted_state, trust / "transition-lineage.v1.d")
        return stage, installed, candidate, accepted_path, authorization_path

    def evaluate(
        self,
        stage: Path,
        candidate: str,
        accepted_path: Path,
        authorization_path: Path,
    ):
        return transition_gate.evaluate_prepared_candidate(
            stage,
            transaction_id=TRANSACTION,
            candidate_commit=candidate,
            accepted_state_path=accepted_path,
            authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
        )

    def test_equal_candidate_is_allowed_without_acknowledgement(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(Path(tmp))
            decision = self.evaluate(stage, candidate, accepted_path, authorization_path)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.acknowledgement_required)
        self.assertFalse(decision.acknowledged)
        self.assertEqual(decision.relation, "equal")

    def test_missing_lineage_blocks_even_an_equal_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, _, candidate, accepted_path, authorization_path = self.staged(root)
            lineage_path = accepted_path.parent / "transition-lineage.v1.d"
            for record in lineage_path.iterdir():
                record.unlink()
            lineage_path.rmdir()
            with self.assertRaisesRegex(transition_gate.TransitionGateError, "lineage is not current"):
                self.evaluate(stage, candidate, accepted_path, authorization_path)

    def test_missing_accepted_state_blocks_before_candidate_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, _, candidate, accepted_path, authorization_path = self.staged(root)
            accepted_path.unlink()
            decision = self.evaluate(stage, candidate, accepted_path, authorization_path)
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason, "accepted-owner-trust-not-established")
            with self.assertRaisesRegex(transition_gate.TransitionGateError, "not established"):
                transition_gate.require_prepared_candidate_allowed(
                    stage,
                    transaction_id=TRANSACTION,
                    candidate_commit=candidate,
                    accepted_state_path=accepted_path,
                    authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
                )

    def test_expansion_requires_exact_owner_acknowledgement(self):
        accepted, candidate_manifest = expansion_pair()
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(
                Path(tmp),
                accepted_manifest=accepted,
                candidate_manifest=candidate_manifest,
            )
            before = self.evaluate(stage, candidate, accepted_path, authorization_path)
            self.assertEqual(before.relation, TRANSITION_EXPANSION)
            self.assertFalse(before.allowed)
            self.assertTrue(before.acknowledgement_required)
            authorization = transition_gate._authorize_prepared_expansion(
                stage,
                transaction_id=TRANSACTION,
                candidate_commit=candidate,
                expected_candidate_manifest_digest=before.candidate_manifest_digest,
                expected_accepted_state_digest=before.accepted_state_digest,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
            after = self.evaluate(stage, candidate, accepted_path, authorization_path)

        self.assertTrue(after.allowed)
        self.assertTrue(after.acknowledged)
        self.assertEqual(authorization["candidate_commit"], candidate)
        self.assertEqual(
            authorization["candidate_manifest_digest"], before.candidate_manifest_digest
        )

    def test_acknowledgement_is_stale_for_a_new_prepared_transaction(self):
        accepted, candidate_manifest = expansion_pair()
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(
                Path(tmp),
                accepted_manifest=accepted,
                candidate_manifest=candidate_manifest,
            )
            before = self.evaluate(stage, candidate, accepted_path, authorization_path)
            transition_gate._authorize_prepared_expansion(
                stage,
                transaction_id=TRANSACTION,
                candidate_commit=candidate,
                expected_candidate_manifest_digest=before.candidate_manifest_digest,
                expected_accepted_state_digest=before.accepted_state_digest,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
            new_transaction = "prepare-" + "b" * 32
            stale = transition_gate.evaluate_prepared_candidate(
                stage,
                transaction_id=new_transaction,
                candidate_commit=candidate,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
        self.assertFalse(stale.allowed)
        self.assertFalse(stale.acknowledged)
        self.assertEqual(stale.reason, "owner-acknowledgement-required")

    def test_acknowledged_expansion_advances_accepted_state_before_execution(self):
        accepted, candidate_manifest = expansion_pair()
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(
                Path(tmp),
                accepted_manifest=accepted,
                candidate_manifest=candidate_manifest,
            )
            before = self.evaluate(stage, candidate, accepted_path, authorization_path)
            transition_gate._authorize_prepared_expansion(
                stage,
                transaction_id=TRANSACTION,
                candidate_commit=candidate,
                expected_candidate_manifest_digest=before.candidate_manifest_digest,
                expected_accepted_state_digest=before.accepted_state_digest,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
            allowed = transition_gate.require_prepared_candidate_allowed(
                stage,
                transaction_id=TRANSACTION,
                candidate_commit=candidate,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
            transition_gate.activate_acknowledged_expansion(
                allowed,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
            state = read_accepted_state(accepted_path)
            authorization_exists = authorization_path.exists()
            lineage = read_transition_lineage(accepted_path.parent / "transition-lineage.v1.d")

        self.assertIsNotNone(state)
        assert state is not None
        self.assertEqual(state["manifest"], candidate_manifest)
        self.assertFalse(authorization_exists)
        self.assertEqual(len(lineage), 2)
        self.assertEqual(lineage[-1]["relation"], "expansion")
        self.assertEqual(lineage[-1]["decision"], "owner-acknowledged-expansion")
        self.assertIsNotNone(lineage[-1]["authorization_digest"])

    def test_narrower_candidate_advances_state_only_after_successful_finalize(self):
        accepted = manifest_copy()
        accepted["capabilities"]["network.external-egress"]["purposes"].append(
            "updates.secondary-fetch"
        )
        accepted["capabilities"]["network.external-egress"]["purposes"].sort()
        candidate_manifest = manifest_copy()
        candidate_manifest["policy_generation"] += 1
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(
                Path(tmp),
                accepted_manifest=accepted,
                candidate_manifest=candidate_manifest,
            )
            decision = self.evaluate(stage, candidate, accepted_path, authorization_path)
            self.assertEqual(decision.relation, "narrower")
            state_before = read_accepted_state(accepted_path)
            self.assertEqual(state_before["manifest"], accepted)
            transition_gate.finalize_successful_transition(
                decision, accepted_state_path=accepted_path,
                lineage_path=accepted_path.parent / "transition-lineage.v1.d"
            )
            state_after = read_accepted_state(accepted_path)
            lineage = read_transition_lineage(accepted_path.parent / "transition-lineage.v1.d")
        self.assertEqual(state_after["manifest"], candidate_manifest)
        self.assertEqual(len(lineage), 2)
        self.assertEqual(lineage[-1]["relation"], "narrower")
        self.assertEqual(lineage[-1]["decision"], "allowed-without-owner-acknowledgement")

    def test_generation_regression_is_not_acknowledgeable(self):
        accepted = manifest_copy()
        accepted["policy_generation"] = 3
        candidate_manifest = copy.deepcopy(accepted)
        candidate_manifest["policy_generation"] = 2
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(
                Path(tmp),
                accepted_manifest=accepted,
                candidate_manifest=candidate_manifest,
            )
            decision = self.evaluate(stage, candidate, accepted_path, authorization_path)
            self.assertEqual(decision.relation, "generation-regression")
            self.assertFalse(decision.allowed)
            with self.assertRaisesRegex(transition_gate.TransitionGateError, "regresses"):
                transition_gate.require_prepared_candidate_allowed(
                    stage,
                    transaction_id=TRANSACTION,
                    candidate_commit=candidate,
                    accepted_state_path=accepted_path,
                    authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
                )

    def test_candidate_manifest_is_read_from_commit_not_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(Path(tmp))
            worktree = stage / transition_gate.CANDIDATE_MANIFEST_GIT_PATH
            worktree.write_text('{"candidate_code":"tampered"}\n', encoding="utf-8")
            decision = self.evaluate(stage, candidate, accepted_path, authorization_path)
        self.assertTrue(decision.allowed)
        self.assertEqual(
            decision.candidate_manifest_digest,
            "sha256:" + manifest_digest(manifest_copy()),
        )

    def test_duplicate_candidate_json_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, _, _, accepted_path, authorization_path = self.staged(root)
            manifest_path = stage / transition_gate.CANDIDATE_MANIFEST_GIT_PATH
            valid = json.dumps(manifest_copy(), sort_keys=True)
            duplicate = valid.replace(
                '{"capabilities"', '{"schema_version": 1, "capabilities"', 1
            )
            manifest_path.write_text(duplicate + "\n", encoding="utf-8")
            self.git(stage, "add", transition_gate.CANDIDATE_MANIFEST_GIT_PATH)
            self.git(stage, "commit", "-m", "duplicate manifest field")
            candidate = self.git(stage, "rev-parse", "HEAD")
            with self.assertRaisesRegex(transition_gate.TransitionGateError, "duplicate"):
                self.evaluate(stage, candidate, accepted_path, authorization_path)

    def test_candidate_manifest_symlink_git_entry_is_rejected(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlink not supported")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stage, _, _, accepted_path, authorization_path = self.staged(root)
            manifest_path = stage / transition_gate.CANDIDATE_MANIFEST_GIT_PATH
            manifest_path.unlink()
            manifest_path.symlink_to("../../README.md")
            self.git(stage, "add", "-A")
            self.git(stage, "commit", "-m", "manifest symlink")
            candidate = self.git(stage, "rev-parse", "HEAD")
            with self.assertRaisesRegex(transition_gate.TransitionGateError, "regular Git blob"):
                self.evaluate(stage, candidate, accepted_path, authorization_path)

    def test_transition_authorization_is_strict_private_and_deterministic(self):
        accepted, candidate_manifest = expansion_pair()
        with tempfile.TemporaryDirectory() as tmp:
            stage, _, candidate, accepted_path, authorization_path = self.staged(
                Path(tmp),
                accepted_manifest=accepted,
                candidate_manifest=candidate_manifest,
            )
            before = self.evaluate(stage, candidate, accepted_path, authorization_path)
            authorization = transition_gate._authorize_prepared_expansion(
                stage,
                transaction_id=TRANSACTION,
                candidate_commit=candidate,
                expected_candidate_manifest_digest=before.candidate_manifest_digest,
                expected_accepted_state_digest=before.accepted_state_digest,
                accepted_state_path=accepted_path,
                authorization_path=authorization_path,
            lineage_path=accepted_path.parent / "transition-lineage.v1.d",
            )
            loaded = transition_gate.read_transition_authorization(authorization_path)
            digest = transition_gate.transition_authorization_digest(authorization)
            mode = authorization_path.stat().st_mode & 0o777
        self.assertEqual(loaded, authorization)
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(mode, 0o600)

    def test_schema_is_strict(self):
        schema = json.loads(
            (ROOT / "open_mmi_trust" / "data" / "trust-transition-authorization.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["authorization_id"]["const"],
            transition_gate.TRANSITION_AUTHORIZATION_ID,
        )

    def test_owner_cli_has_no_candidate_selected_path_or_noninteractive_bypass(self):
        source = (ROOT / "open_mmi_trust" / "transition_gate_cli.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("--candidate", "--manifest", "--path", "--yes", "--confirm"):
            self.assertNotIn(forbidden, source)
        self.assertIn("AUTHORIZE TRANSITION", source)
        self.assertIn("interactive local terminal", source)

    def test_noninteractive_acknowledgement_refuses_before_mutation(self):
        transition = mock.Mock()
        transition.relation = TRANSITION_EXPANSION
        transition.acknowledged = False
        transition.candidate_manifest_digest = "sha256:" + "1" * 64
        transition.candidate_commit = "2" * 40
        transition.accepted_state_digest = "sha256:" + "3" * 64
        transition.summary.return_value = {"relation": "expansion"}
        state = {"transaction_id": TRANSACTION, "candidate_commit": "2" * 40}
        with (
            mock.patch.object(
                transition_gate_cli, "_prepared_transition", return_value=(Path("/stage"), state, transition)
            ),
            mock.patch.object(transition_gate_cli.os, "geteuid", return_value=0),
            mock.patch.object(transition_gate_cli.sys.stdin, "isatty", return_value=False),
            mock.patch.object(transition_gate_cli.sys.stdout, "isatty", return_value=False),
            mock.patch.object(transition_gate_cli, "_authorize_prepared_expansion") as authorize,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            result = transition_gate_cli.main(["acknowledge"])
        self.assertEqual(result, 2)
        authorize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
