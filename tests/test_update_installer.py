from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_mmi_trust.accepted_state import _record_accepted_manifest, read_accepted_state
from open_mmi_trust import transition_gate
from open_mmi_trust.manifest import load_manifest
from open_mmi_trust.lineage import _record_lineage_baseline

from ui import update_coordinator, update_installer


class UpdateInstallerTests(unittest.TestCase):
    def prepared(
        self,
        root: Path,
        *,
        accepted_manifest: dict | None = None,
        candidate_manifest: dict | None = None,
    ) -> tuple[dict, Path, Path, Path]:
        accepted_manifest = copy.deepcopy(accepted_manifest or load_manifest())
        candidate_manifest = copy.deepcopy(candidate_manifest or accepted_manifest)
        transaction = "prepare-" + "a" * 32
        staging = root / "staging"
        stage = staging / transaction
        stage.mkdir(parents=True)
        staging.chmod(0o711)
        stage.chmod(0o700)
        subprocess.run(["git", "init", "-b", "main", str(stage)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(stage), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(stage), "config", "user.email", "test@example.invalid"], check=True)
        (stage / "README.md").write_text("one\n", encoding="utf-8")
        manifest_path = stage / "open_mmi_trust" / "data" / "trust-manifest.v1.json"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(
            json.dumps(accepted_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(stage), "add", "README.md", str(manifest_path.relative_to(stage))], check=True)
        git_identity = [
            "-c", "user.name=Open MMI Test",
            "-c", "user.email=test@open-mmi.invalid",
            "-c", "commit.gpgSign=false",
            "-c", "tag.gpgSign=false",
        ]
        subprocess.run(["git", *git_identity, "-C", str(stage), "commit", "-m", "one"], check=True, stdout=subprocess.DEVNULL)
        installed = subprocess.run(["git", "-C", str(stage), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        (stage / "README.md").write_text("two\n", encoding="utf-8")
        manifest_path.write_text(
            json.dumps(candidate_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        subprocess.run(["git", "-C", str(stage), "add", str(manifest_path.relative_to(stage))], check=True)
        subprocess.run(["git", *git_identity, "-C", str(stage), "commit", "-am", "two"], check=True, stdout=subprocess.DEVNULL)
        candidate = subprocess.run(["git", "-C", str(stage), "rev-parse", "HEAD"], check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
        state = update_coordinator.initial_state()
        state.update({
            "state": "prepared", "stage": "prepared", "transaction_id": transaction,
            "candidate_commit": candidate, "target_version": "candidate-build",
            "previous_version": "old-build",
        })
        state_path = root / "state.json"
        update_coordinator.write_state(state, state_path)
        accepted_state = _record_accepted_manifest(
            accepted_manifest, root / "trust" / "accepted-owner-trust.v1.json"
        )
        _record_lineage_baseline(
            accepted_state, root / "trust" / "transition-lineage.v1.d"
        )
        source = {
            "repository_path": str(root), "installed_commit": installed,
            "installed_version": "old-build", "branch": "main", "upstream": "origin/main",
            "remote": "origin", "remote_branch": "main", "recorded_channel": "nightly",
        }
        return source, state_path, root / "lock", staging

    def test_installs_only_revalidated_nightly_candidate_with_fixed_command(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(root)
            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), patch.object(update_installer.pwd, "getpwuid", return_value=type("Account", (), {"pw_name": "tester"})()), patch.object(
                update_installer, "_run_deployment", return_value=subprocess.CompletedProcess(["deploy"], 0)
            ) as run:
                completed = update_installer.install_prepared(
                    state_path, lock_path, staging, command=("fixed-deploy",)
                )
        self.assertEqual(completed["state"], "complete")
        self.assertEqual(run.call_args.args[0], ["fixed-deploy"])
        environment = run.call_args.args[1]
        self.assertEqual(environment["OPEN_MMI_PREPARED_COMMIT"], completed["candidate_commit"])

    def test_successful_deployment_records_packaging_tools_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(root)
            rollback = root / "rollback"

            def deploy(command, environment):
                archive = rollback / environment["OPEN_MMI_PREPARED_TRANSACTION"]
                archive.mkdir(parents=True)
                before = archive / "pip-version-before"
                after = archive / "pip-version-after"
                before.write_text("26.1.2\n", encoding="utf-8")
                after.write_text("26.2.1\n", encoding="utf-8")
                before.chmod(0o644)
                after.chmod(0o644)
                return subprocess.CompletedProcess(command, 0, stdout="")

            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), patch.object(update_installer.pwd, "getpwuid", return_value=type("Account", (), {"pw_name": "tester"})()), patch.object(
                update_installer, "_run_deployment", side_effect=deploy
            ):
                completed = update_installer.install_prepared(
                    state_path, lock_path, staging,
                    command=("fixed-deploy",), rollback_root=rollback,
                )
        self.assertEqual(completed["pip_version_before"], "26.1.2")
        self.assertEqual(completed["pip_version_after"], "26.2.1")

    def test_wrong_channel_and_tampered_candidate_fail_before_deployment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(root)
            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "beta"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), self.assertRaisesRegex(update_installer.InstallerError, "only for nightly"):
                update_installer.install_prepared(state_path, lock_path, staging, command=("never",))

            state = update_coordinator.read_state(state_path)
            state["candidate_commit"] = "f" * 40
            update_coordinator.write_state(state, state_path)
            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), self.assertRaisesRegex(update_installer.InstallerError, "identity changed"):
                update_installer.install_prepared(state_path, lock_path, staging, command=("never",))

    def test_deployment_failure_is_persisted_without_output_leakage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(root)
            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), patch.object(update_installer.pwd, "getpwuid", return_value=type("Account", (), {"pw_name": "tester"})()), patch.object(
                update_installer, "_run_deployment", return_value=subprocess.CompletedProcess(["deploy"], 1, stdout="secret path")
            ), self.assertRaisesRegex(update_installer.InstallerError, "deployment failed"):
                update_installer.install_prepared(state_path, lock_path, staging, command=("fixed-deploy",))
            failed = update_coordinator.read_state(state_path)
        self.assertEqual(failed["state"], "failed")
        self.assertNotIn("secret", failed["error"])

    def test_deployment_failure_exposes_only_allowlisted_stage(self):
        self.assertEqual(
            update_installer._deployment_failure(
                "noise\nPrepared deployment failed at stage: api-health\nPrepared rollback verified\nsecret"
            ),
            "Prepared deployment failed during api-health; rollback verified",
        )
        self.assertEqual(
            update_installer._deployment_failure("Prepared deployment failed at stage: secret-path"),
            "Prepared deployment failed",
        )
        self.assertEqual(
            update_installer._deployment_failure("Prepared deployment failed at stage: repository-fetch"),
            "Prepared deployment failed during repository-fetch; rollback unverified",
        )
        self.assertEqual(
            update_installer._deployment_failure("Prepared deployment failed at stage: packaging-tools"),
            "Prepared deployment failed during packaging-tools; rollback unverified",
        )
        self.assertEqual(
            update_installer._deployment_failure("Prepared deployment failed at stage: package-build"),
            "Prepared deployment failed during package-build; rollback unverified",
        )
        self.assertEqual(
            update_installer._deployment_failure(
                "Prepared deployment failed at stage: vehicle-config-coordinator"
            ),
            "Prepared deployment failed during vehicle-config-coordinator; rollback unverified",
        )
        self.assertEqual(
            update_installer._deployment_failure(
                "Prepared deployment failed at stage: power-manager\nPrepared rollback verified"
            ),
            "Prepared deployment failed during power-manager; rollback verified",
        )

    def test_terminal_install_cleans_staging_and_bounds_rollback_archives(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, state_path, lock_path, staging = self.prepared(root)
                rollback = root / "rollback"
                for index, character in enumerate("bcd", start=1):
                    archive = rollback / ("prepare-" + character * 32)
                    archive.mkdir(parents=True)
                    os.utime(archive, ns=(index, index))

                def deploy(command, environment):
                    current = rollback / environment["OPEN_MMI_PREPARED_TRANSACTION"]
                    current.mkdir()
                    return subprocess.CompletedProcess(
                        command,
                        returncode,
                        stdout=(
                            "Prepared deployment failed at stage: api-health\n"
                            "Prepared rollback verified\n"
                            if returncode
                            else ""
                        ),
                    )

                with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                    update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
                ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
                ), patch.object(update_installer.pwd, "getpwuid", return_value=type("Account", (), {"pw_name": "tester"})()), patch.object(
                    update_installer, "_run_deployment", side_effect=deploy
                ):
                    if returncode:
                        with self.assertRaisesRegex(update_installer.InstallerError, "api-health"):
                            update_installer.install_prepared(
                                state_path, lock_path, staging,
                                command=("fixed-deploy",), rollback_root=rollback,
                            )
                    else:
                        completed = update_installer.install_prepared(
                            state_path, lock_path, staging,
                            command=("fixed-deploy",), rollback_root=rollback,
                        )
                        self.assertEqual(completed["state"], "complete")

                transaction = "prepare-" + "a" * 32
                retained = {entry.name for entry in rollback.iterdir()}
                self.assertFalse((staging / transaction).exists())
                self.assertEqual(
                    retained,
                    {transaction, "prepare-" + "d" * 32},
                )

    def test_source_change_after_preparation_blocks_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(root)
            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "dirty"}), self.assertRaisesRegex(
                update_installer.InstallerError, "source changed"
            ):
                update_installer.install_prepared(state_path, lock_path, staging, command=("never",))

    def test_trust_expansion_without_acknowledgement_blocks_before_deployment(self):
        accepted = copy.deepcopy(load_manifest())
        accepted["policy_generation"] = 1
        accepted["capabilities"]["telemetry.collection"] = {
            "policy": "prohibited", "assurance": "runtime-guarded"
        }
        candidate_manifest = load_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(
                root, accepted_manifest=accepted, candidate_manifest=candidate_manifest
            )
            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), patch.object(update_installer, "_run_deployment") as run, self.assertRaisesRegex(
                update_installer.InstallerError, "acknowledgement is required"
            ):
                update_installer.install_prepared(
                    state_path, lock_path, staging, command=("must-not-run",)
                )
            run.assert_not_called()
            self.assertEqual(update_coordinator.read_state(state_path)["state"], "prepared")

    def test_acknowledged_expansion_is_accepted_before_candidate_deployment(self):
        accepted = copy.deepcopy(load_manifest())
        accepted["policy_generation"] = 1
        accepted["capabilities"]["telemetry.collection"] = {
            "policy": "prohibited", "assurance": "runtime-guarded"
        }
        candidate_manifest = load_manifest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, state_path, lock_path, staging = self.prepared(
                root, accepted_manifest=accepted, candidate_manifest=candidate_manifest
            )
            state = update_coordinator.read_state(state_path)
            stage = staging / state["transaction_id"]
            accepted_path = root / "trust" / "accepted-owner-trust.v1.json"
            authorization_path = root / "trust" / "transition-authorization.v1.json"
            review = transition_gate.evaluate_prepared_candidate(
                stage, transaction_id=state["transaction_id"], candidate_commit=state["candidate_commit"],
                accepted_state_path=accepted_path, authorization_path=authorization_path,
                lineage_path=root / "trust" / "transition-lineage.v1.d",
            )
            transition_gate._authorize_prepared_expansion(
                stage, transaction_id=state["transaction_id"], candidate_commit=state["candidate_commit"],
                expected_candidate_manifest_digest=review.candidate_manifest_digest,
                expected_accepted_state_digest=review.accepted_state_digest,
                accepted_state_path=accepted_path, authorization_path=authorization_path,
                lineage_path=root / "trust" / "transition-lineage.v1.d",
            )

            def deploy(command, environment):
                del environment
                accepted_now = read_accepted_state(accepted_path)
                self.assertIsNotNone(accepted_now)
                self.assertEqual(accepted_now["manifest"], candidate_manifest)
                self.assertFalse(authorization_path.exists())
                return subprocess.CompletedProcess(command, 0, stdout="")

            with patch.object(update_installer.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_installer.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_installer.update_status, "_repository_snapshot", return_value={"state": "ready"}
            ), patch.object(update_installer.pwd, "getpwuid", return_value=type("Account", (), {"pw_name": "tester"})()), patch.object(
                update_installer, "_run_deployment", side_effect=deploy
            ):
                completed = update_installer.install_prepared(
                    state_path, lock_path, staging, command=("candidate-deploy",)
                )
            self.assertEqual(completed["state"], "complete")

    def test_service_entry_point_accepts_no_arguments(self):
        with self.assertRaises(SystemExit):
            update_installer.main(["/tmp/stage"])


if __name__ == "__main__":
    unittest.main()
