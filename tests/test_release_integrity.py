from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from open_mmi_trust.accepted_state import _record_accepted_manifest
from open_mmi_trust.lineage import _record_lineage_baseline, lineage_summary, read_transition_lineage
from open_mmi_trust.manifest import load_manifest
from open_mmi_trust import release_integrity, release_integrity_cli
from open_mmi_trust.release_integrity import (
    ReleaseIntegrityError,
    _record_integrity_state,
    expected_release_from_git,
    integrity_state_digest,
    inventory_from_git_commit,
    read_integrity_state,
    verify_installed_runtime,
    verify_privileged_installed_runtime,
    verify_wheel_against_inventory,
)


MANIFEST = load_manifest()


class ReleaseIntegrityTests(unittest.TestCase):
    def repository(self, root: Path, manifest: dict | None = None) -> tuple[Path, str]:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        (repo / "open_mmi_trust" / "data").mkdir(parents=True)
        (repo / "open_mmi_trust" / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "open_mmi_trust" / "data" / "trust-manifest.v1.json").write_text(
            json.dumps(manifest or MANIFEST, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (repo / "scripts").mkdir()
        (repo / "scripts" / "manage.sh").write_text("#!/bin/sh\necho managed\n", encoding="utf-8")
        (repo / "packaging" / "tmpfiles").mkdir(parents=True)
        (repo / "packaging" / "tmpfiles" / "open-mmi.conf").write_text("d /run/open-mmi 0755 root root -\n", encoding="utf-8")
        (repo / "systemd" / "system").mkdir(parents=True)
        (repo / "systemd" / "system" / "open-mmi-test.service").write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
        (repo / "systemd" / "system" / "open-mmi-update-coordinator.service").write_text(
            "[Service]\nExecStart=/opt/open-mmi/venv/bin/python -I -m ui.update_coordinator serve\n",
            encoding="utf-8",
        )
        (repo / "systemd" / "system" / "open-mmi-update-installer.service").write_text(
            "[Service]\nExecStart=/opt/open-mmi/venv/bin/python -I -m ui.update_installer\n",
            encoding="utf-8",
        )
        (repo / "ui" / "web_dashboard").mkdir(parents=True)
        (repo / "ui" / "web_dashboard" / "README.md").write_text("managed dashboard source\n", encoding="utf-8")
        (repo / "README.md").write_text("Open MMI test release\n", encoding="utf-8")
        (repo / "LICENSE").write_text("test license\n", encoding="utf-8")
        (repo / "pyproject.toml").write_text("[project]\nname='open-mmi'\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-c", "commit.gpgSign=false", "-C", str(repo), "commit", "-m", "release"],
            check=True, stdout=subprocess.DEVNULL,
        )
        commit = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
            text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        return repo, commit

    def anchors(self, root: Path, manifest: dict | None = None):
        accepted_path = root / "trust" / "accepted-owner-trust.v1.json"
        lineage_path = root / "trust" / "transition-lineage.v1.d"
        accepted = _record_accepted_manifest(manifest or MANIFEST, accepted_path)
        _record_lineage_baseline(accepted, lineage_path)
        head = lineage_summary(read_transition_lineage(lineage_path))["head_record_digest"]
        return accepted, head

    def materialize(self, repo: Path, expected: dict, destination: Path) -> None:
        for entry in expected["inventory"]:
            path = destination / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            data = subprocess.run(
                ["git", "-C", str(repo), "show", f"{expected['candidate_commit']}:{entry['path']}"],
                check=True, stdout=subprocess.PIPE,
            ).stdout
            path.write_bytes(data)

    def state(self, root: Path, repo: Path, commit: str):
        expected = expected_release_from_git(repo, commit)
        accepted, head = self.anchors(root, expected["trust_manifest"])
        path = root / "trust" / "installed-release-integrity.v1.json"
        state = _record_integrity_state(
            candidate_commit=commit,
            trust_manifest=expected["trust_manifest"],
            inventory=expected["inventory"],
            accepted_state=accepted,
            lineage_head_record_digest=head,
            record_source="baseline-existing-state",
            path=path,
        )
        return expected, state, path

    def test_inventory_is_derived_from_commit_object_not_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            inventory = inventory_from_git_commit(repo, commit)
            expected = next(item for item in inventory if item["path"] == "open_mmi_trust/__init__.py")
            (repo / "open_mmi_trust" / "__init__.py").write_text("VALUE = 999\n", encoding="utf-8")
            inventory_after = inventory_from_git_commit(repo, commit)
        self.assertEqual(inventory, inventory_after)
        self.assertEqual(
            expected["sha256"],
            "sha256:" + hashlib.sha256(b"VALUE = 1\n").hexdigest(),
        )

    def test_candidate_unknown_runtime_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = self.repository(root)
            (repo / "open_mmi_trust" / "native.so").write_bytes(b"not reviewed")
            subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
            subprocess.run(
                ["git", "-c", "commit.gpgSign=false", "-C", str(repo), "commit", "-m", "native"],
                check=True, stdout=subprocess.DEVNULL,
            )
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                text=True, stdout=subprocess.PIPE,
            ).stdout.strip()
            with self.assertRaisesRegex(ReleaseIntegrityError, "unsupported file"):
                inventory_from_git_commit(repo, commit)

    def test_integrity_state_round_trips_strictly_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            _, state, path = self.state(root, repo, commit)
            loaded = read_integrity_state(path)
        self.assertEqual(loaded, state)
        self.assertEqual(integrity_state_digest(state), integrity_state_digest(copy.deepcopy(state)))

    def test_runtime_exact_match_passes_and_tampered_byte_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            runtime = root / "runtime"
            self.materialize(repo, expected, runtime)
            self.assertTrue(verify_installed_runtime(state, runtime)["matches"])
            (runtime / "open_mmi_trust" / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
            result = verify_installed_runtime(state, runtime)
        self.assertFalse(result["matches"])
        self.assertEqual(result["modified"], ["open_mmi_trust/__init__.py"])

    def test_runtime_missing_extra_and_symlink_are_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            runtime = root / "runtime"
            self.materialize(repo, expected, runtime)
            (runtime / "open_mmi_trust" / "__init__.py").unlink()
            (runtime / "open_mmi_trust" / "extra.py").write_text("pass\n", encoding="utf-8")
            (runtime / "open_mmi_trust" / "linked.py").symlink_to("extra.py")
            result = verify_installed_runtime(state, runtime)
        self.assertFalse(result["matches"])
        self.assertIn("open_mmi_trust/__init__.py", result["missing"])
        self.assertIn("open_mmi_trust/extra.py", result["extra"])
        self.assertIn("open_mmi_trust/linked.py", result["unsafe"])

    def test_pycache_is_ignored_but_unknown_runtime_source_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            runtime = root / "runtime"
            self.materialize(repo, expected, runtime)
            cache = runtime / "open_mmi_trust" / "__pycache__"
            cache.mkdir()
            (cache / "__init__.cpython-313.pyc").write_bytes(b"cache")
            self.assertTrue(verify_installed_runtime(state, runtime)["matches"])
            (runtime / "open_mmi_trust" / "future.py").write_text("pass\n", encoding="utf-8")
            self.assertFalse(verify_installed_runtime(state, runtime)["matches"])

    def test_split_runtime_layout_verifies_source_and_site_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            source_root = root / "source-runtime"
            package_root = root / "site-packages"
            self.materialize(repo, expected, package_root)
            source_paths = {
                entry["path"] for entry in expected["inventory"]
                if (
                    entry["path"] in release_integrity.SOURCE_RELEASE_FILES
                    or entry["path"].split("/", 1)[0] in release_integrity.SOURCE_RELEASE_ROOTS
                )
            }
            for relative in sorted(source_paths):
                target = source_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((repo / relative).read_bytes())
            # Dashboard source documentation is managed source evidence, while it is
            # intentionally absent from the wheel payload.
            self.assertTrue((source_root / "ui" / "web_dashboard" / "README.md").is_file())
            result = verify_installed_runtime(state, source_root, package_root)
            self.assertTrue(result["matches"], result)
            (package_root / "open_mmi_trust" / "__init__.py").write_text("VALUE = 9\n", encoding="utf-8")
            tampered = verify_installed_runtime(state, source_root, package_root)
        self.assertFalse(tampered["matches"])
        self.assertIn("package:open_mmi_trust/__init__.py", tampered["modified"])

    def test_managed_release_source_includes_privileged_deployment_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            paths = {entry["path"] for entry in expected["inventory"]}
            self.assertIn("scripts/manage.sh", paths)
            self.assertIn("systemd/system/open-mmi-test.service", paths)
            self.assertIn("packaging/tmpfiles/open-mmi.conf", paths)
            self.assertIn("ui/web_dashboard/README.md", paths)
            source_root = root / "source-runtime"
            package_root = root / "site-packages"
            self.materialize(repo, expected, source_root)
            self.materialize(repo, expected, package_root)
            self.assertTrue(verify_installed_runtime(state, source_root, package_root)["matches"])
            (source_root / "scripts" / "manage.sh").write_text("#!/bin/sh\necho tampered\n", encoding="utf-8")
            result = verify_installed_runtime(state, source_root, package_root)
        self.assertFalse(result["matches"])
        self.assertIn("source:scripts/manage.sh", result["modified"])

    def test_privileged_runtime_binding_covers_site_packages_and_deployed_update_units(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            source_root = root / "source-runtime"
            package_root = root / "site-packages"
            unit_root = root / "systemd"
            self.materialize(repo, expected, source_root)
            self.materialize(repo, expected, package_root)
            unit_root.mkdir()

            for unit in release_integrity.PRIVILEGED_SYSTEM_UNITS:
                (unit_root / unit).write_bytes(
                    (repo / "systemd" / "system" / unit).read_bytes()
                )

            exact = verify_privileged_installed_runtime(
                state, source_root, package_root, unit_root
            )
            self.assertTrue(exact["matches"], exact)

            package_file = package_root / "open_mmi_trust" / "__init__.py"
            original_package = package_file.read_bytes()
            package_file.write_text("VALUE = 99\n", encoding="utf-8")
            stale_package = verify_privileged_installed_runtime(
                state, source_root, package_root, unit_root
            )
            self.assertFalse(stale_package["matches"])
            self.assertIn(
                "package:open_mmi_trust/__init__.py", stale_package["modified"]
            )
            package_file.write_bytes(original_package)

            unit = unit_root / "open-mmi-update-installer.service"
            unit.write_text("[Service]\nExecStart=/bin/false\n", encoding="utf-8")
            stale_unit = verify_privileged_installed_runtime(
                state, source_root, package_root, unit_root
            )

        self.assertFalse(stale_unit["matches"])
        self.assertIn(
            "systemd:open-mmi-update-installer.service", stale_unit["modified"]
        )

    def test_production_privileged_binding_rejects_user_owned_install_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected, state, _ = self.state(root, repo, commit)
            source_root = root / "source-runtime"
            package_root = root / "site-packages"
            unit_root = root / "systemd"
            self.materialize(repo, expected, source_root)
            self.materialize(repo, expected, package_root)
            unit_root.mkdir()
            for unit in release_integrity.PRIVILEGED_SYSTEM_UNITS:
                (unit_root / unit).write_bytes(
                    (repo / "systemd" / "system" / unit).read_bytes()
                )

            # The temp root is deliberately owned by the test user. Treating it as
            # the fixed production install root must therefore fail even though all
            # bytes are an exact inventory match.
            with mock.patch.object(
                release_integrity, "DEFAULT_INSTALL_ROOT", source_root
            ):
                result = verify_privileged_installed_runtime(
                    state, source_root, package_root, unit_root
                )

        self.assertFalse(result["matches"])
        self.assertTrue(
            any(item.startswith("ownership:") for item in result["unsafe"]),
            result,
        )

    def test_wheel_runtime_payload_must_exactly_match_git_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected = expected_release_from_git(repo, commit)
            wheel = root / "open_mmi-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for entry in expected["inventory"]:
                    data = subprocess.run(
                        ["git", "-C", str(repo), "show", f"{commit}:{entry['path']}"],
                        check=True, stdout=subprocess.PIPE,
                    ).stdout
                    archive.writestr(entry["path"], data)
                archive.writestr("open_mmi-0.dist-info/METADATA", "Name: open-mmi\n")
            result = verify_wheel_against_inventory(wheel, expected["inventory"])
            self.assertTrue(result["matches"])
            with zipfile.ZipFile(wheel, "a") as archive:
                archive.writestr("open_mmi_trust/injected.py", "pass\n")
            with self.assertRaisesRegex(ReleaseIntegrityError, "does not match"):
                verify_wheel_against_inventory(wheel, expected["inventory"])

    def test_wheel_tampered_expected_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected = expected_release_from_git(repo, commit)
            wheel = root / "open_mmi-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for entry in expected["inventory"]:
                    data = b"tampered" if entry["path"].endswith("__init__.py") else subprocess.run(
                        ["git", "-C", str(repo), "show", f"{commit}:{entry['path']}"],
                        check=True, stdout=subprocess.PIPE,
                    ).stdout
                    archive.writestr(entry["path"], data)
            with self.assertRaisesRegex(ReleaseIntegrityError, "does not match"):
                verify_wheel_against_inventory(wheel, expected["inventory"])

    def test_owner_bootstrap_is_digest_bound_and_records_exact_clean_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected = expected_release_from_git(repo, commit)
            runtime = root / "runtime"
            self.materialize(repo, expected, runtime)
            accepted_path = root / "trust" / "accepted-owner-trust.v1.json"
            lineage_path = root / "trust" / "transition-lineage.v1.d"
            accepted = _record_accepted_manifest(expected["trust_manifest"], accepted_path)
            _record_lineage_baseline(accepted, lineage_path)
            state_path = root / "trust" / "installed-release-integrity.v1.json"
            phrase = "ESTABLISH INTEGRITY " + expected["inventory_digest"].split(":", 1)[1][:12]
            with mock.patch.object(release_integrity_cli, "_require_root"), mock.patch.object(
                release_integrity_cli, "_require_local_tty"
            ), mock.patch.object(
                release_integrity_cli, "DEFAULT_INTEGRITY_STATE_PATH", state_path
            ), mock.patch.object(
                release_integrity_cli, "DEFAULT_ACCEPTED_STATE_PATH", accepted_path
            ), mock.patch.object(
                release_integrity_cli, "DEFAULT_TRANSITION_LINEAGE_DIR", lineage_path
            ), mock.patch.object(
                release_integrity_cli, "default_install_root", return_value=runtime
            ), mock.patch.object(
                release_integrity_cli, "default_package_root", return_value=runtime
            ), mock.patch.object(
                release_integrity_cli, "_bootstrap_source", return_value=(repo, commit)
            ), mock.patch("builtins.input", return_value=phrase):
                result = release_integrity_cli.main(["bootstrap"])
            state = read_integrity_state(state_path)
        self.assertEqual(result, 0)
        self.assertIsNotNone(state)
        self.assertEqual(state["candidate_commit"], commit)
        self.assertEqual(state["inventory_digest"], expected["inventory_digest"])
        self.assertEqual(state["record_source"], "baseline-existing-state")

    def test_editable_bootstrap_source_rejects_dirty_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, _ = self.repository(root)
            (repo / "open_mmi_trust" / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseIntegrityError, "not clean"):
                release_integrity_cli._bootstrap_source(repo)

    def test_default_install_root_prefers_production_installation_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = root / "checkout"
            (checkout / ".git").mkdir(parents=True)
            conventional = root / "opt-open-mmi"
            conventional.mkdir()
            with mock.patch.object(release_integrity, "default_package_root", return_value=checkout), mock.patch.object(
                release_integrity, "DEFAULT_INSTALL_ROOT", conventional
            ):
                self.assertEqual(release_integrity.default_install_root(), conventional)

    def test_owner_bootstrap_generic_confirmation_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            expected = expected_release_from_git(repo, commit)
            runtime = root / "runtime"
            self.materialize(repo, expected, runtime)
            accepted_path = root / "trust" / "accepted-owner-trust.v1.json"
            lineage_path = root / "trust" / "transition-lineage.v1.d"
            accepted = _record_accepted_manifest(expected["trust_manifest"], accepted_path)
            _record_lineage_baseline(accepted, lineage_path)
            state_path = root / "trust" / "installed-release-integrity.v1.json"
            with mock.patch.object(release_integrity_cli, "_require_root"), mock.patch.object(
                release_integrity_cli, "_require_local_tty"
            ), mock.patch.object(
                release_integrity_cli, "DEFAULT_INTEGRITY_STATE_PATH", state_path
            ), mock.patch.object(
                release_integrity_cli, "DEFAULT_ACCEPTED_STATE_PATH", accepted_path
            ), mock.patch.object(
                release_integrity_cli, "DEFAULT_TRANSITION_LINEAGE_DIR", lineage_path
            ), mock.patch.object(
                release_integrity_cli, "default_install_root", return_value=runtime
            ), mock.patch.object(
                release_integrity_cli, "default_package_root", return_value=runtime
            ), mock.patch.object(
                release_integrity_cli, "_bootstrap_source", return_value=(repo, commit)
            ), mock.patch("builtins.input", return_value="ESTABLISH INTEGRITY"):
                result = release_integrity_cli.main(["bootstrap"])
        self.assertEqual(result, 2)
        self.assertFalse(state_path.exists())

    def test_integrity_state_rejects_weakened_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo, commit = self.repository(root)
            _, _, path = self.state(root, repo, commit)
            path.chmod(0o644)
            with self.assertRaisesRegex(ReleaseIntegrityError, "untrusted"):
                read_integrity_state(path)


if __name__ == "__main__":
    unittest.main()
