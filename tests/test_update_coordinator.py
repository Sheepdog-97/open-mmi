from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ui import update_coordinator


class UpdateCoordinatorTests(unittest.TestCase):
    def test_remote_update_check_runs_inside_coordinator_boundary(self):
        expected = {"state": "up-to-date", "update_available": False}
        with patch.object(
            update_coordinator.update_status, "check_for_updates", return_value=expected
        ) as check:
            response = update_coordinator.response_for_request(
                {"api_version": 1, "action": "check"}, Path("/unused")
            )
        self.assertEqual(response, {"ok": True, "status": expected})
        check.assert_called_once_with()
        self.assertFalse(
            update_coordinator.response_for_request(
                {"api_version": 1, "action": "check", "confirm": True}, Path("/unused")
            )["ok"]
        )

    def test_successful_install_response_is_flushed_before_coordinator_restart(self):
        request = {"api_version": 1, "action": "install", "confirm": True}
        output = io.BytesIO()
        handler = SimpleNamespace(
            rfile=io.BytesIO(json.dumps(request).encode("utf-8") + b"\n"),
            wfile=output,
            server=SimpleNamespace(state_path=Path("/unused")),
        )

        def restarted(command, **kwargs):
            self.assertTrue(output.getvalue().endswith(b"\n"))
            self.assertEqual(command, ["systemctl", "restart", "--no-block", "open-mmi-update-coordinator.service"])
            return subprocess.CompletedProcess(command, 0)

        with patch.object(update_coordinator, "response_for_request", return_value={"ok": True}), patch.object(
            update_coordinator.subprocess, "run", side_effect=restarted
        ):
            update_coordinator._Handler.handle(handler)

    def test_dashboard_disconnect_does_not_skip_successful_coordinator_handoff(self):
        request = {"api_version": 1, "action": "install", "confirm": True}

        class DisconnectedWriter:
            def write(self, _payload):
                raise BrokenPipeError("dashboard restarted")

            def flush(self):
                raise AssertionError("flush should not follow a failed write")

        handler = SimpleNamespace(
            rfile=io.BytesIO(json.dumps(request).encode("utf-8") + b"\n"),
            wfile=DisconnectedWriter(),
            server=SimpleNamespace(state_path=Path("/unused")),
        )
        with patch.object(update_coordinator, "response_for_request", return_value={"ok": True}), patch.object(
            update_coordinator.subprocess, "run", return_value=subprocess.CompletedProcess(["systemctl"], 0)
        ) as restart:
            update_coordinator._Handler.handle(handler)
        restart.assert_called_once()
        self.assertEqual(
            restart.call_args.args[0],
            ["systemctl", "restart", "--no-block", "open-mmi-update-coordinator.service"],
        )

    def git(self, repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=10,
            env={**os.environ, "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "tag.gpgSign",
                 "GIT_CONFIG_VALUE_0": "false", "GIT_CONFIG_KEY_1": "commit.gpgSign",
                 "GIT_CONFIG_VALUE_1": "false"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_status_prepare_and_install_are_fixed_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            state_path = Path(temporary) / "state.json"
            update_coordinator.write_state(update_coordinator.initial_state(), state_path)
            response = update_coordinator.response_for_request(
                {"api_version": 1, "action": "status"}, state_path
            )
        self.assertTrue(response["ok"])
        self.assertTrue(response["execution_enabled"])
        self.assertTrue(response["preparation_enabled"])
        self.assertTrue(response["installation_enabled"])
        self.assertEqual(response["state"]["state"], "idle")
        for request in (
            {"api_version": 1, "action": "rollback"},
            {"api_version": 1, "action": "status", "path": "/tmp/evil"},
            {"action": "status"},
        ):
            self.assertFalse(update_coordinator.response_for_request(request, state_path)["ok"])

        prepared = dict(update_coordinator.initial_state(), state="prepared", stage="prepared")
        with patch.object(update_coordinator, "_prepare_candidate", return_value=prepared) as prepare:
            response = update_coordinator.response_for_request(
                {"api_version": 1, "action": "prepare", "confirm": True},
                state_path, Path(temporary) / "lock", Path(temporary) / "staging",
            )
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"]["state"], "prepared")
        prepare.assert_called_once()

        for request in (
            {"api_version": 1, "action": "prepare"},
            {"api_version": 1, "action": "prepare", "confirm": False},
            {"api_version": 1, "action": "prepare", "confirm": True, "url": "https://evil.test"},
        ):
            self.assertFalse(update_coordinator.response_for_request(request, state_path)["ok"])

        installable = dict(
            update_coordinator.initial_state(), state="prepared", stage="prepared",
            transaction_id="prepare-" + "a" * 32, candidate_commit="b" * 40,
        )
        update_coordinator.write_state(installable, state_path)
        with patch.object(update_coordinator.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")), patch.object(
            update_coordinator, "trusted_prepared_stage", return_value=Path("/prepared")
        ), patch.object(
            update_coordinator.transition_gate, "require_prepared_candidate_allowed", return_value=object()
        ), patch.object(
            update_coordinator.subprocess, "run", return_value=subprocess.CompletedProcess(["systemctl"], 0)
        ), patch.object(update_coordinator, "read_state", side_effect=[installable, dict(installable, state="complete", stage="complete")]):
            installed = update_coordinator.response_for_request(
                {"api_version": 1, "action": "install", "confirm": True}, state_path
            )
        self.assertTrue(installed["ok"])
        self.assertEqual(installed["state"]["state"], "complete")

        for request in (
            {"api_version": 1, "action": "install"},
            {"api_version": 1, "action": "install", "confirm": False},
            {"api_version": 1, "action": "install", "confirm": True, "path": "/tmp/evil"},
        ):
            self.assertFalse(update_coordinator.response_for_request(request, state_path)["ok"])

    def test_install_preflight_blocks_before_installer_service_when_trust_gate_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_path = root / "state.json"
            installable = dict(
                update_coordinator.initial_state(),
                state="prepared", stage="prepared",
                transaction_id="prepare-" + "a" * 32, candidate_commit="b" * 40,
            )
            update_coordinator.write_state(installable, state_path)
            with patch.object(
                update_coordinator.update_policy, "read_policy",
                return_value=({"channel": "nightly"}, "configured"),
            ), patch.object(
                update_coordinator, "trusted_prepared_stage", return_value=root / "stage"
            ), patch.object(
                update_coordinator.transition_gate,
                "require_prepared_candidate_allowed",
                side_effect=update_coordinator.transition_gate.TransitionGateError(
                    "local transition acknowledgement is required"
                ),
            ), patch.object(update_coordinator.subprocess, "run") as run:
                response = update_coordinator.response_for_request(
                    {"api_version": 1, "action": "install", "confirm": True},
                    state_path, root / "lock", root / "staging",
                )
            self.assertFalse(response["ok"])
            self.assertIn("acknowledgement", response["error"])
            run.assert_not_called()
            self.assertEqual(update_coordinator.read_state(state_path)["state"], "prepared")

    def test_state_is_atomic_strict_and_mode_0644(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            state = update_coordinator.initial_state()
            rendered = update_coordinator.write_state(state, path)
            self.assertEqual(update_coordinator.read_state(path), rendered)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            malformed = dict(state, command="sh")
            with self.assertRaises(update_coordinator.CoordinatorError):
                update_coordinator.write_state(malformed, path)
            with self.assertRaises(update_coordinator.CoordinatorError):
                update_coordinator.write_state(dict(state, target_version="bad\nvalue"), path)

    def test_stable_and_beta_never_report_installation_enabled(self):
        state = update_coordinator.initial_state()
        for channel in ("stable", "beta"):
            with self.subTest(channel=channel), patch.object(
                update_coordinator.update_policy, "read_policy",
                return_value=({"channel": channel}, "configured"),
            ):
                response = update_coordinator._public_response(state)
            self.assertFalse(response["execution_enabled"])
            self.assertFalse(response["installation_enabled"])

    def test_schema_one_state_migrates_without_losing_transaction_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            state = update_coordinator.initial_state()
            state.pop("candidate_commit")
            state.pop("pip_version_before")
            state.pop("pip_version_after")
            state["schema_version"] = 1
            path.write_text(json.dumps(state), encoding="utf-8")
            migrated = update_coordinator.recover_interrupted_state(path)
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["candidate_commit"], "")
        self.assertEqual(migrated["pip_version_before"], "")
        self.assertEqual(migrated["pip_version_after"], "")

    def test_schema_two_state_imports_packaging_tools_result_from_rollback(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state.json"
            transaction = "prepare-" + "a" * 32
            state = update_coordinator.initial_state()
            state.pop("pip_version_before")
            state.pop("pip_version_after")
            state.update({
                "schema_version": 2,
                "state": "complete",
                "stage": "complete",
                "transaction_id": transaction,
            })
            path.write_text(json.dumps(state), encoding="utf-8")
            archive = root / "rollback" / transaction
            archive.mkdir(parents=True)
            before = archive / "pip-version-before"
            after = archive / "pip-version-after"
            before.write_text("26.1.2\n", encoding="utf-8")
            after.write_text("26.2.1\n", encoding="utf-8")
            before.chmod(0o644)
            after.chmod(0o644)
            migrated = update_coordinator.read_state(path)
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["pip_version_before"], "26.1.2")
        self.assertEqual(migrated["pip_version_after"], "26.2.1")

    def test_interrupted_active_state_recovers_to_failed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "state.json"
            staging = root / "staging"
            rollback = root / "rollback"
            transaction = "prepare-0123456789abcdef0123456789abcdef"
            state = update_coordinator.initial_state()
            state.update({
                "state": "installing", "stage": "installing",
                "transaction_id": transaction,
            })
            update_coordinator.write_state(state, path)
            (staging / transaction).mkdir(parents=True)
            for index, character in enumerate("abc", start=1):
                archive = rollback / ("prepare-" + character * 32)
                archive.mkdir(parents=True)
                os.utime(archive, ns=(index, index))
            recovered = update_coordinator.recover_interrupted_state(
                path, staging, rollback
            )
            retained = {entry.name for entry in rollback.iterdir()}
        self.assertEqual(recovered["state"], "failed")
        self.assertEqual(recovered["stage"], "recovery")
        self.assertTrue(recovered["recovered"])
        self.assertNotIn("/", recovered["error"])
        self.assertFalse((staging / transaction).exists())
        self.assertEqual(
            retained,
            {"prepare-" + "b" * 32, "prepare-" + "c" * 32},
        )

    def test_transaction_lock_prevents_overlap_and_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "update.lock"
            with update_coordinator.TransactionLock(path):
                self.assertTrue(path.exists())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                with self.assertRaisesRegex(update_coordinator.CoordinatorError, "Another update"):
                    with update_coordinator.TransactionLock(path):
                        pass
            self.assertTrue(path.exists())

    def test_production_state_write_requires_root(self):
        with patch.object(update_coordinator.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(update_coordinator.CoordinatorError, "requires root"):
                update_coordinator.write_state(update_coordinator.initial_state())

    def test_staging_cleanup_refuses_paths_outside_transaction_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "staging"
            root.mkdir()
            outside = Path(temporary) / ("prepare-" + "a" * 32)
            outside.mkdir()
            with self.assertRaisesRegex(update_coordinator.CoordinatorError, "staging path"):
                update_coordinator._safe_remove_staging(outside, root)
            self.assertTrue(outside.exists())

            linked = root / ("prepare-" + "b" * 32)
            linked.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(update_coordinator.CoordinatorError, "staging path"):
                update_coordinator._safe_remove_staging(linked, root)
            self.assertTrue(outside.exists())

    def test_artifact_retention_preserves_current_and_newest_archives(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            rollback = root / "rollback"
            staging.mkdir()
            rollback.mkdir()
            current = "prepare-" + "a" * 32
            stale_stage = "prepare-" + "b" * 32
            (staging / current).mkdir()
            (staging / stale_stage).mkdir()
            for index, character in enumerate("abc", start=1):
                archive = rollback / ("prepare-" + character * 32)
                archive.mkdir()
                os.utime(archive, ns=(index, index))
            (rollback / "operator-notes").mkdir()
            state = dict(
                update_coordinator.initial_state(),
                state="prepared",
                stage="prepared",
                transaction_id=current,
            )
            update_coordinator._cleanup_transaction_artifacts(
                state, staging, rollback
            )
            staged = {entry.name for entry in staging.iterdir()}
            retained = {entry.name for entry in rollback.iterdir()}
        self.assertEqual(staged, {current})
        self.assertEqual(
            retained,
            {current, "prepare-" + "c" * 32, "operator-notes"},
        )

    def test_client_request_raises_for_coordinator_failure_response(self):
        fake_socket = MagicMock()
        fake_socket.__enter__.return_value = fake_socket
        fake_socket.makefile.return_value.readline.return_value = b'{"ok": false, "error": "No candidate"}\n'
        with patch.object(update_coordinator.socket, "socket", return_value=fake_socket):
            with self.assertRaisesRegex(update_coordinator.CoordinatorError, "No candidate"):
                update_coordinator.client_prepare(Path("/tmp/coordinator.sock"))

    def test_prepare_stages_proven_candidate_without_installing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "source"
            repository.mkdir()
            state_path = root / "state.json"
            lock_path = root / "update.lock"
            staging_root = root / "staging"
            stale_stage = staging_root / ("prepare-" + "f" * 32)
            stale_stage.mkdir(parents=True)
            installed = "1" * 40
            candidate = "2" * 40
            source = {
                "repository_path": str(repository), "installed_commit": installed,
                "installed_version": "old", "branch": "main", "upstream": "origin/main",
                "remote": "origin", "remote_branch": "main", "recorded_channel": "nightly",
            }
            with patch.object(update_coordinator.update_status, "_read_source_descriptor", return_value=(source, "configured")), patch.object(
                update_coordinator.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_coordinator.update_status, "_repository_snapshot", return_value={"state": "ready"}), patch.object(
                update_coordinator, "_preparation_readiness"
            ), patch.object(update_coordinator, "_candidate", return_value=(candidate[:12], candidate, "")), patch.object(
                update_coordinator.update_status, "_remote_url", return_value="https://example.invalid/open-mmi.git"
            ), patch.object(update_coordinator.update_status, "_run_git", return_value=SimpleNamespace(returncode=0)), patch.object(
                update_coordinator.update_status, "_git_success", return_value=True
            ), patch.object(update_coordinator.update_status, "_git_output", return_value=candidate[:12]
            ), patch.object(
                update_coordinator, "_secure_staging_tree"
            ), patch.object(update_coordinator.uuid, "uuid4", return_value=SimpleNamespace(hex="a" * 32)):
                prepared = update_coordinator._prepare_candidate(state_path, lock_path, staging_root)
            stale_stage_exists = stale_stage.exists()
        self.assertEqual(prepared["state"], "prepared")
        self.assertEqual(prepared["candidate_commit"], candidate)
        self.assertEqual(prepared["previous_version"], "old")
        self.assertFalse(prepared["error"])
        self.assertFalse(stale_stage_exists)

    def test_nightly_candidate_is_cloned_and_forward_ancestry_is_proved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            source = root / "source"
            subprocess.run(["git", "init", "--bare", str(remote)], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(["git", "init", "-b", "main", str(source)], check=True, stdout=subprocess.DEVNULL)
            self.git(source, "config", "user.name", "Open MMI Test")
            self.git(source, "config", "user.email", "test@example.invalid")
            (source / "README.md").write_text("one\n", encoding="utf-8")
            self.git(source, "add", "README.md")
            self.git(source, "commit", "-m", "one")
            installed = self.git(source, "rev-parse", "HEAD")
            self.git(source, "remote", "add", "origin", str(remote))
            self.git(source, "push", "-u", "origin", "main")
            (source / "README.md").write_text("two\n", encoding="utf-8")
            self.git(source, "commit", "-am", "two")
            candidate = self.git(source, "rev-parse", "HEAD")
            self.git(source, "push", "origin", "main")
            self.git(source, "reset", "--hard", installed)
            descriptor = {
                "repository_path": str(source), "installed_commit": installed,
                "installed_version": "old", "branch": "main", "upstream": "origin/main",
                "remote": "origin", "remote_branch": "main", "recorded_channel": "nightly",
            }
            with patch.object(update_coordinator.update_status, "_read_source_descriptor", return_value=(descriptor, "configured")), patch.object(
                update_coordinator.update_policy, "read_policy", return_value=({"channel": "nightly"}, "configured")
            ), patch.object(update_coordinator, "_preparation_readiness"), patch.object(
                update_coordinator.update_status.os, "geteuid", return_value=1000
            ), patch.object(
                update_coordinator, "_secure_staging_tree"
            ):
                prepared = update_coordinator._prepare_candidate(
                    root / "state.json", root / "update.lock", root / "staging"
                )
            stage = root / "staging" / prepared["transaction_id"]
            self.assertEqual(prepared["state"], "prepared")
            self.assertEqual(prepared["candidate_commit"], candidate)
            self.assertEqual(self.git(stage, "rev-parse", "HEAD"), candidate)
            self.assertEqual(self.git(source, "rev-parse", "HEAD"), installed)


if __name__ == "__main__":
    unittest.main()
