from __future__ import annotations

import json
import shlex
import shutil
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGE_SCRIPT = ROOT / "scripts" / "manage.sh"


class ManageScriptLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = MANAGE_SCRIPT.read_text(encoding="utf-8")

    def test_manage_script_has_valid_bash_syntax(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(MANAGE_SCRIPT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_update_source_metadata_python_has_valid_syntax(self) -> None:
        marker = "<<'PY_UPDATE_SOURCE'\n"
        start = self.text.index(marker) + len(marker)
        end = self.text.index("\nPY_UPDATE_SOURCE", start)
        compile(self.text[start:end], "manage.sh:PY_UPDATE_SOURCE", "exec")

    def test_vehicle_coordinator_environment_python_has_valid_syntax(self) -> None:
        marker = "<<'PY_VEHICLE_CONFIG_COORDINATOR_ENV'\n"
        start = self.text.index(marker) + len(marker)
        end = self.text.index("\nPY_VEHICLE_CONFIG_COORDINATOR_ENV", start)
        compile(
            self.text[start:end],
            "manage.sh:PY_VEHICLE_CONFIG_COORDINATOR_ENV",
            "exec",
        )

    def test_vehicle_coordinator_sandbox_python_has_valid_syntax(self) -> None:
        marker = "<<'PY_VEHICLE_CONFIG_COORDINATOR_SANDBOX'\n"
        start = self.text.index(marker) + len(marker)
        end = self.text.index("\nPY_VEHICLE_CONFIG_COORDINATOR_SANDBOX", start)
        compile(
            self.text[start:end],
            "manage.sh:PY_VEHICLE_CONFIG_COORDINATOR_SANDBOX",
            "exec",
        )

    def transaction_locks_program(self) -> str:
        marker = "<<'PY_OPEN_MMI_TRANSACTION_LOCKS'\n"
        start = self.text.index(marker) + len(marker)
        end = self.text.index("\nPY_OPEN_MMI_TRANSACTION_LOCKS", start)
        return self.text[start:end]

    def test_transaction_lock_tmpfiles_config_is_complete(self) -> None:
        config = (
            ROOT / "packaging/tmpfiles/open-mmi.conf"
        ).read_text(encoding="utf-8")
        self.assertIn("d /run/open-mmi 0755 root root - -", config)
        for name in (
            "lifecycle.lock",
            "update.lock",
            "vehicle-configuration.lock",
        ):
            self.assertIn(
                f"f /run/open-mmi/{name} 0644 root root - -",
                config,
            )

    def test_transaction_lock_tmpfiles_config_creates_boot_files(self) -> None:
        executable = shutil.which("systemd-tmpfiles")
        if executable is None:
            self.skipTest("systemd-tmpfiles is unavailable")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_dir = root / "usr/lib/tmpfiles.d"
            config_dir.mkdir(parents=True)
            config = config_dir / "open-mmi.conf"
            production_config = (
                ROOT / "packaging/tmpfiles/open-mmi.conf"
            ).read_text(encoding="utf-8")
            # The separate completeness test verifies the production root:root
            # ownership contract. Omit ownership in the isolated fixture so
            # systemd-tmpfiles does not issue chown calls in unprivileged CI.
            config.write_text(
                production_config.replace(" root root ", " - - "),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    executable,
                    f"--root={root}",
                    "--create",
                    str(config),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            runtime = root / "run/open-mmi"
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o755)
            for name in (
                "lifecycle.lock",
                "update.lock",
                "vehicle-configuration.lock",
            ):
                path = runtime / name
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_transaction_locks_python_has_valid_syntax(self) -> None:
        compile(
            self.transaction_locks_program(),
            "manage.sh:PY_OPEN_MMI_TRANSACTION_LOCKS",
            "exec",
        )

    def test_transaction_locks_are_created_private_without_replacement(self) -> None:
        program = self.transaction_locks_program()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run" / "open-mmi"
            root.mkdir(parents=True)
            root.chmod(0o755)
            existing = root / "lifecycle.lock"
            existing.write_text("held\n", encoding="utf-8")
            inode = existing.stat().st_ino
            existing.chmod(0o666)
            unrelated = root / "coordinator.sock.fixture"
            unrelated.write_text("unchanged\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    "python3", "-c", program, str(root),
                    str(root.stat().st_uid), str(root.stat().st_gid),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            for name in (
                "lifecycle.lock", "update.lock", "vehicle-configuration.lock"
            ):
                path = root / name
                self.assertTrue(path.is_file())
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                self.assertEqual(path.stat().st_nlink, 1)
            self.assertEqual(existing.stat().st_ino, inode)
            self.assertEqual(existing.read_text(encoding="utf-8"), "held\n")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "unchanged\n")

    def test_transaction_locks_reject_symlink_before_creating_siblings(self) -> None:
        program = self.transaction_locks_program()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run" / "open-mmi"
            outside = Path(temporary) / "outside"
            root.mkdir(parents=True)
            root.chmod(0o755)
            outside.write_text("outside\n", encoding="utf-8")
            (root / "lifecycle.lock").symlink_to(outside)
            completed = subprocess.run(
                [
                    "python3", "-c", program, str(root),
                    str(root.stat().st_uid), str(root.stat().st_gid),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("untrusted", completed.stderr.lower())
            self.assertFalse((root / "update.lock").exists())
            self.assertFalse((root / "vehicle-configuration.lock").exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def custom_catalogue_permissions_program(self) -> str:
        marker = "<<'PY_CUSTOM_CATALOGUE_PERMISSIONS'\n"
        start = self.text.index(marker) + len(marker)
        end = self.text.index("\nPY_CUSTOM_CATALOGUE_PERMISSIONS", start)
        return self.text[start:end]

    def test_custom_catalogue_permissions_python_has_valid_syntax(self) -> None:
        compile(
            self.custom_catalogue_permissions_program(),
            "manage.sh:PY_CUSTOM_CATALOGUE_PERMISSIONS",
            "exec",
        )

    def test_custom_catalogue_permission_repair_is_private_and_scoped(self) -> None:
        program = self.custom_catalogue_permissions_program()
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            config = home / ".config"
            root = config / "open-mmi"
            profile = root / "vehicles" / "example" / "config.json"
            bindings = root / "bindings" / "example.json"
            provenance = (
                root
                / ".open-mmi-provenance"
                / "profile"
                / "example.json"
            )
            unrelated = root / "dashboard.env"
            backup = root / "qualification-backup" / "99-vcan-test.conf"

            profile.parent.mkdir(parents=True)
            bindings.parent.mkdir(parents=True)
            provenance.parent.mkdir(parents=True)
            backup.parent.mkdir(parents=True)
            profile.write_text("{}\n", encoding="utf-8")
            bindings.write_text("{}\n", encoding="utf-8")
            provenance.write_text("{}\n", encoding="utf-8")
            unrelated.write_text("UNCHANGED=1\n", encoding="utf-8")
            backup.write_text("legacy\n", encoding="utf-8")
            home.chmod(0o700)
            config.chmod(0o755)
            root.chmod(0o777)
            for directory in (
                root / "vehicles",
                profile.parent,
                root / "bindings",
                root / ".open-mmi-provenance",
                provenance.parent,
            ):
                directory.chmod(0o777)
            for file_path in (profile, bindings, provenance):
                file_path.chmod(0o666)
            unrelated.chmod(0o644)
            backup.parent.chmod(0o755)
            backup.chmod(0o644)

            completed = subprocess.run(
                [
                    "python3",
                    "-c",
                    program,
                    str(home),
                    str(root),
                    str(home.stat().st_uid),
                    str(home.stat().st_gid),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            for directory in (
                root,
                root / "vehicles",
                profile.parent,
                root / "bindings",
                root / ".open-mmi-provenance",
                provenance.parent,
                root / ".open-mmi-provenance" / "bindings",
            ):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            for file_path in (profile, bindings, provenance):
                self.assertEqual(file_path.stat().st_mode & 0o777, 0o600)

            self.assertEqual(unrelated.stat().st_mode & 0o777, 0o644)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "UNCHANGED=1\n")
            self.assertEqual(backup.parent.stat().st_mode & 0o777, 0o755)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o644)

    def test_custom_catalogue_permission_repair_rejects_symlinks_before_changes(self) -> None:
        program = self.custom_catalogue_permissions_program()
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            home = base / "home"
            root = home / ".config" / "open-mmi"
            outside = base / "outside"
            root.mkdir(parents=True)
            outside.mkdir()
            home.chmod(0o700)
            (home / ".config").chmod(0o755)
            root.chmod(0o755)
            (root / "vehicles").symlink_to(outside, target_is_directory=True)

            completed = subprocess.run(
                [
                    "python3",
                    "-c",
                    program,
                    str(home),
                    str(root),
                    str(home.stat().st_uid),
                    str(home.stat().st_gid),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("symlink", completed.stderr.lower())
            self.assertEqual(root.stat().st_mode & 0o777, 0o755)
            self.assertFalse((root / "bindings").exists())
            self.assertFalse((root / ".open-mmi-provenance").exists())

    def test_install_and_update_paths_harden_only_the_custom_catalogue(self) -> None:
        coordinator_start = self.text.index("install_vehicle_config_coordinator() {")
        coordinator_end = self.text.index("remove_login_autostart() {", coordinator_start)
        coordinator_block = self.text[coordinator_start:coordinator_end]
        self.assertIn("harden_custom_catalogue_permissions", coordinator_block)

        update_start = self.text.index("cmd_update() {")
        update_end = self.text.index("cmd_deploy_prepared() {", update_start)
        update_block = self.text[update_start:update_end]
        self.assertLess(
            update_block.index("harden_custom_catalogue_permissions"),
            update_block.index("Already up to date"),
        )

        provisioning_start = self.text.index("apply_profile_provisioning() {")
        provisioning_end = self.text.index("reload_profile_provisioning() {", provisioning_start)
        provisioning_block = self.text[provisioning_start:provisioning_end]
        self.assertIn("harden_custom_catalogue_permissions", provisioning_block)
        self.assertNotIn(
            'chown -R "$REAL_USER:$REAL_USER" "$USER_CONFIG_DIR"',
            provisioning_block,
        )

    def test_update_source_writer_migrates_and_preserves_named_channel_policy(self) -> None:
        marker = "<<'PY_UPDATE_SOURCE'\n"
        start = self.text.index(marker) + len(marker)
        end = self.text.index("\nPY_UPDATE_SOURCE", start)
        program = self.text[start:end]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metadata = root / "install" / ".update-source.json"
            policy = root / "etc" / "update-policy.json"
            source = root / "source"
            source.mkdir()
            arguments = [
                "python3", "-c", program,
                str(metadata), str(source), "main", "origin/main",
                "a" * 40, "v0.1.0", str(policy),
            ]
            first = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(policy.read_text(encoding="utf-8"))["channel"], "nightly")
            self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["channel"], "nightly")

            policy.write_text(json.dumps({
                "schema_version": 1,
                "channel": "development",
                "updated_at": "2026-07-18T12:00:00+00:00",
            }), encoding="utf-8")
            policy.chmod(0o644)
            migrated = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(migrated.returncode, 0, migrated.stderr)
            self.assertEqual(json.loads(policy.read_text(encoding="utf-8"))["channel"], "nightly")
            self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["channel"], "nightly")

            policy.write_text(json.dumps({
                "schema_version": 1,
                "channel": "beta",
                "updated_at": "2026-07-18T12:00:00+00:00",
            }), encoding="utf-8")
            policy.chmod(0o644)
            second = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(metadata.read_text(encoding="utf-8"))["channel"], "beta")

            policy.write_text(json.dumps({
                "schema_version": 1,
                "channel": "beta",
                "repository": "https://evil.test/repo",
            }), encoding="utf-8")
            policy.chmod(0o644)
            invalid = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            self.assertNotEqual(invalid.returncode, 0)

    def test_checkout_metadata_ignores_stale_prepared_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            repository = root / "source"
            policy = root / "etc" / "update-policy.json"
            install.mkdir()
            repository.mkdir()
            commit = "b" * 40
            script = f"""
source {shlex.quote(str(MANAGE_SCRIPT))}
INSTALL_DIR={shlex.quote(str(install))}
UPDATE_POLICY_FILE={shlex.quote(str(policy))}
REPO_ROOT={shlex.quote(str(repository))}
get_repo_branch() {{ printf '%s\\n' main; }}
get_repo_upstream() {{ printf '%s\\n' origin/main; }}
get_repo_commit() {{ printf '%s\\n' {commit}; }}
get_current_version() {{ printf '%s\\n' v1-foundation-alpha-80-gb; }}
export OPEN_MMI_MANAGED_BRANCH=v1-update-management
export OPEN_MMI_MANAGED_UPSTREAM=origin/v1-update-management
export OPEN_MMI_MANAGED_REPOSITORY=/tmp/stale-source
export OPEN_MMI_PREPARED_COMMIT={'a' * 40}
export OPEN_MMI_PREPARED_VERSION=v1-foundation-alpha-79-ga
write_checkout_update_source_metadata
"""
            completed = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            descriptor = json.loads((install / ".update-source.json").read_text(encoding="utf-8"))
            self.assertEqual(descriptor["repository_path"], str(repository.resolve()))
            self.assertEqual(descriptor["branch"], "main")
            self.assertEqual(descriptor["upstream"], "origin/main")
            self.assertEqual(descriptor["installed_commit"], commit)
            self.assertEqual(descriptor["installed_version"], "v1-foundation-alpha-80-gb")

    def test_service_units_use_single_command_source_and_destination(self) -> None:
        install_canbusd = (
            'install -m 0644 -o "$REAL_USER" -g "$REAL_USER" '
            '"$REPO_ROOT/systemd/user/canbusd.service" '
            '"$user_systemd_dir/canbusd.service"'
        )
        install_dashboard = (
            'install -m 0644 -o "$REAL_USER" -g "$REAL_USER" '
            '"$REPO_ROOT/systemd/user/open-mmi-dashboard.service" '
            '"$user_systemd_dir/open-mmi-dashboard.service"'
        )
        self.assertGreaterEqual(self.text.count(install_canbusd), 2)
        self.assertGreaterEqual(self.text.count(install_dashboard), 2)
        self.assertNotIn(
            'cp "$REPO_ROOT/systemd/user/canbusd.service" \\\n',
            self.text,
        )
        self.assertNotIn(
            'cp "$REPO_ROOT/systemd/user/open-mmi-dashboard.service" \\\n',
            self.text,
        )

    def test_update_systemctl_commands_are_complete_single_lines(self) -> None:
        start = self.text.index("cmd_update() {")
        end = self.text.index("cmd_deploy_prepared() {", start)
        update_block = self.text[start:end]
        commands = [
            line.strip()
            for line in update_block.splitlines()
            if line.strip().startswith('sudo -u "$REAL_USER"')
            and "systemctl --user" in line
        ]

        self.assertEqual(len(commands), 2)
        for command in commands:
            with self.subTest(command=command):
                self.assertFalse(command.endswith("\\"))

                result = subprocess.run(
                    [
                        "bash",
                        "-c",
                        f'''\
REAL_USER=pitto
REAL_HOME=/home/pitto
XDG_RUNTIME_DIR=/run/user/1000
sudo() {{ printf '%s\\0' "$@"; }}
{command}
''',
                    ],
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    result.stderr.decode("utf-8", errors="replace"),
                )
                arguments = [
                    value.decode("utf-8")
                    for value in result.stdout.split(b"\0")
                    if value
                ]
                self.assertIn("systemctl", arguments)
                systemctl_index = arguments.index("systemctl")
                self.assertEqual(arguments[systemctl_index + 1], "--user")

    def test_update_manages_both_service_units(self) -> None:
        self.assertIn(
            "systemctl --user restart "
            "canbusd.service open-mmi-dashboard.service",
            self.text,
        )
        self.assertIn(
            "systemctl --user enable canbusd.service",
            self.text,
        )
        self.assertNotIn(
            "systemctl --user enable open-mmi-dashboard.service",
            self.text,
        )
        self.assertIn(
            "systemctl --user disable open-mmi-dashboard.service",
            self.text,
        )

    def test_dashboard_service_is_not_the_user_facing_autostart_setting(self) -> None:
        self.assertIn("configure_install_service_defaults()", self.text)
        self.assertIn("configure_update_service_defaults()", self.text)
        self.assertIn("migrate_legacy_dashboard_startup()", self.text)
        self.assertIn('payload.pop("start_at_login", None)', self.text)
        self.assertIn("remove_login_autostart", self.text)
        self.assertNotIn("configure_dashboard_autostart", self.text)

    def test_desktop_entry_is_managed_by_install_update_and_uninstall(self) -> None:
        install_start = self.text.index("cmd_install() {")
        update_start = self.text.index("cmd_update() {")
        uninstall_start = self.text.index("cmd_uninstall() {")
        status_start = self.text.index("cmd_status() {")

        install_block = self.text[install_start:update_start]
        update_block = self.text[update_start:uninstall_start]
        uninstall_block = self.text[uninstall_start:status_start]

        self.assertIn("install_desktop_entry", install_block)
        self.assertIn("install_desktop_entry", update_block)
        self.assertIn("remove_desktop_entry", uninstall_block)
        self.assertIn("install_desktop_icons", self.text)
        self.assertIn("remove_desktop_icons", self.text)
        self.assertIn('cp -r "$REPO_ROOT/packaging" "$INSTALL_DIR/"', install_block)
        self.assertIn('sudo cp -r "$REPO_ROOT/packaging" "$INSTALL_DIR/"', update_block)

    def test_package_and_command_links_are_managed_by_lifecycle(self) -> None:
        install_start = self.text.index("cmd_install() {")
        update_start = self.text.index("cmd_update() {")
        uninstall_start = self.text.index("cmd_uninstall() {")
        status_start = self.text.index("cmd_status() {")

        install_block = self.text[install_start:update_start]
        update_block = self.text[update_start:uninstall_start]
        uninstall_block = self.text[uninstall_start:status_start]

        self.assertIn("install_open_mmi_package", install_block)
        self.assertIn("install_command_links", install_block)
        self.assertIn("install_open_mmi_package", update_block)
        self.assertIn("install_command_links", update_block)
        self.assertIn("remove_command_links", uninstall_block)
        self.assertIn('local pip_arguments=(install --upgrade --force-reinstall)', self.text)
        self.assertIn('local package_source="${1:-$INSTALL_DIR}"', self.text)
        self.assertIn('( umask 0022; env -u PYTHONPATH "$python" -m pip', self.text)
        self.assertIn('sudo -u "$REAL_USER" env -u PYTHONPATH "$python" -I', self.text)
        self.assertIn('cp "$REPO_ROOT/README.md" "$INSTALL_DIR/"', install_block)
        self.assertIn('cp "$REPO_ROOT/LICENSE" "$INSTALL_DIR/"', install_block)
        self.assertIn('sudo cp "$REPO_ROOT/README.md" "$INSTALL_DIR/"', update_block)
        self.assertIn('sudo cp "$REPO_ROOT/LICENSE" "$INSTALL_DIR/"', update_block)

    def test_install_and_update_share_packaging_tools_upgrade(self) -> None:
        self.assertIn("upgrade_python_packaging_tools()", self.text)
        install_start = self.text.index("cmd_install() {")
        update_start = self.text.index("cmd_update() {")
        deploy_start = self.text.index("cmd_deploy_prepared() {")
        install_block = self.text[install_start:update_start]
        update_block = self.text[update_start:deploy_start]
        self.assertIn(
            'upgrade_python_packaging_tools "$INSTALL_DIR/venv/bin/python"',
            install_block,
        )
        self.assertIn(
            'upgrade_python_packaging_tools "$INSTALL_DIR/venv/bin/python"',
            update_block,
        )
        self.assertIn('pip install --upgrade pip', self.text)
        self.assertIn('pip-version-before', self.text)
        self.assertIn('pip-version-after', self.text)

    def test_installed_maintained_catalogue_is_root_owned_and_world_readable(self) -> None:
        self.assertIn("configure_maintained_catalogue_permissions()", self.text)
        self.assertEqual(
            self.text.count("    configure_maintained_catalogue_permissions\n"),
            3,
        )
        self.assertIn('for catalogue_root in "$INSTALL_DIR/vehicles" "$INSTALL_DIR/bindings"', self.text)
        self.assertIn('-exec chown root:root {} +', self.text)
        self.assertIn('-exec chmod 0755 {} +', self.text)
        self.assertIn('-exec chmod 0644 {} +', self.text)

    def test_expected_console_commands_are_declared(self) -> None:
        for command in (
            "open-mmi-canbusd",
            "open-mmi-config",
            "open-mmi-dashboard",
            "open-mmi-launcher",
            "open-mmi-status",
            "open-mmi-update-coordinator",
            "open-mmi-update-installer",
            "open-mmi-vehicle-config-coordinator",
        ):
            self.assertIn(command, self.text)
        self.assertIn(
            'COMMAND_LINK_DIR="${OPEN_MMI_COMMAND_LINK_DIR:-/usr/local/bin}"',
            self.text,
        )

    def test_manage_script_can_be_sourced_without_running_main(self) -> None:
        self.assertIn(
            'if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then\n'
            '    main "$@"\n'
            "fi",
            self.text,
        )

    def test_custom_template_creation_prefers_installed_maintained_files(self) -> None:
        self.assertIn(
            'source_vehicle="$(resolve_maintained_profile_source "$vehicle")"',
            self.text,
        )
        self.assertIn(
            'profile_catalogue.resolve_profile(root, vehicle)',
            self.text,
        )
        self.assertIn(
            'if Path(resolved["path"]).is_file():',
            self.text,
        )
        self.assertIn(
            'local source_bindings="$INSTALL_DIR/bindings/$bindings.json"',
            self.text,
        )
        self.assertIn(
            'source_bindings="$REPO_ROOT/bindings/$bindings.json"',
            self.text,
        )

    def test_config_paths_describes_custom_files_as_explicit_only(self) -> None:
        self.assertIn(
            'echo "  User config files are used only when explicitly selected."',
            self.text,
        )
        self.assertNotIn(
            'echo "    2. User config directory"',
            self.text,
        )

    def test_uninstall_handles_absent_units_quietly(self) -> None:
        self.assertIn(
            "for service in canbusd.service open-mmi-dashboard.service; do",
            self.text,
        )
        self.assertIn(
            'systemctl --user disable --now "$service" >/dev/null 2>&1 || true',
            self.text,
        )
        self.assertIn('systemctl stop "$UPDATE_INSTALLER_UNIT"', self.text)
        self.assertIn('"/etc/systemd/system/$UPDATE_INSTALLER_UNIT"', self.text)
        self.assertIn('systemctl disable --now "$VEHICLE_CONFIG_COORDINATOR_UNIT"', self.text)
        self.assertIn('systemctl stop "$VEHICLE_CAN_PROVISION_UNIT"', self.text)
        self.assertIn('"/etc/systemd/system/$VEHICLE_CONFIG_COORDINATOR_UNIT"', self.text)
        self.assertIn('"/etc/systemd/system/$VEHICLE_CAN_PROVISION_UNIT"', self.text)
        self.assertIn('"$VEHICLE_CONFIG_UI_QUALIFICATION_GATE"', self.text)

    def test_prepared_deployment_is_fixed_root_only_and_rolls_back_on_error(self) -> None:
        start = self.text.index("cmd_deploy_prepared() {")
        end = self.text.index("# UNINSTALL", start)
        block = self.text[start:end]
        self.assertIn('^prepare-[0-9a-f]{32}$', block)
        self.assertIn('/var/lib/open-mmi/staging/$transaction', block)
        self.assertIn("trap 'log_error \"Prepared deployment failed at stage: $deployment_stage\"' ERR", block)
        self.assertIn('trap rollback_prepared_deployment ERR', block)
        self.assertIn('Prepared deployment failed at stage: $deployment_stage', block)
        self.assertIn('deployment_stage="packaging-tools"', block)
        self.assertIn(
            'upgrade_python_packaging_tools "$INSTALL_DIR/venv/bin/python" "$rollback_root"',
            block,
        )
        self.assertLess(
            block.index('deployment_stage="packaging-tools"'),
            block.index('deployment_stage="package-artifact"'),
        )
        self.assertIn('env -u PYTHONPATH "$rollback_root/installation/venv/bin/python" -I -c \'import ui.config_cli\'', block)
        self.assertIn('env -u PYTHONPATH "$INSTALL_DIR/venv/bin/python" -I -c \'import ui.config_cli\'', block)
        self.assertIn('Prepared rollback verified', block)
        self.assertNotIn('pip wheel --no-deps', block)
        self.assertNotIn('tools/verify_wheel.py', block)
        self.assertIn('OPEN_MMI_PREPARED_WHEEL', block)
        self.assertIn('deployment_stage="package-artifact"', block)
        self.assertIn('install_open_mmi_package "$candidate_wheel"', block)
        self.assertIn("import canbusd.core, ui.config_cli, ui.web_dashboard.server", self.text)
        self.assertIn('mv -- "$restored_install" "$INSTALL_DIR"', block)
        self.assertNotIn('pip install --upgrade --force-reinstall "$INSTALL_DIR"', block)
        self.assertIn('curl --fail --silent --max-time 2 http://127.0.0.1:8765/api/health', block)
        self.assertIn('payload.get("build_id") == expected', block)
        self.assertIn('for _attempt in {1..15}; do', block)
        self.assertIn('[[ "$api_ready" == true ]]', block)
        self.assertIn('[[ "$version_ready" == true ]]', block)
        self.assertIn('"$VEHICLE_CONFIG_COORDINATOR_UNIT"', block)
        self.assertIn('"$VEHICLE_CAN_PROVISION_UNIT"', block)
        self.assertIn('vehicle-config-coordinator.env', block)
        self.assertIn('deployment_stage="vehicle-config-coordinator"', block)
        self.assertIn('deployment_stage="power-manager"', block)
        self.assertIn('systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT"', block)
        self.assertNotIn("eval ", block)

    def test_vehicle_coordinator_health_check_requires_live_socket_and_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install = root / "install"
            command = install / "venv/bin/open-mmi-config"
            command.parent.mkdir(parents=True)
            command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            command.chmod(0o755)
            socket_path = root / "coordinator.sock"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(socket_path))
            listener.close()

            common = f"""
source {shlex.quote(str(MANAGE_SCRIPT))}
INSTALL_DIR={shlex.quote(str(install))}
VEHICLE_CONFIG_COORDINATOR_SOCKET={shlex.quote(str(socket_path))}
OPEN_MMI_COORDINATOR_HEALTH_ATTEMPTS=1
OPEN_MMI_COORDINATOR_HEALTH_DELAY=0
systemctl() {{ return 0; }}
sleep() {{ :; }}
"""
            success = subprocess.run(
                ["bash", "-c", common + "wait_for_vehicle_config_coordinator"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(success.returncode, 0, success.stderr)

            socket_path.unlink()
            failure = subprocess.run(
                [
                    "bash",
                    "-c",
                    common
                    + "if wait_for_vehicle_config_coordinator; then exit 9; else exit 0; fi",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(failure.returncode, 0, failure.stderr)
            self.assertIn("post-install health check", failure.stderr)

    def test_install_root_is_root_owned_and_prepared_deploy_hardens_legacy_root(self) -> None:
        install_start = self.text.index("cmd_install() {")
        update_start = self.text.index("cmd_update() {")
        install_block = self.text[install_start:update_start]
        self.assertIn(
            'sudo install -d -m 0755 -o root -g root "$INSTALL_DIR"',
            install_block,
        )
        self.assertNotIn(
            'sudo chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"',
            install_block,
        )
        self.assertIn("harden_install_root_ownership() {", self.text)
        deploy_start = self.text.index("cmd_deploy_prepared() {")
        deploy_end = self.text.index("\n# =============================================================================", deploy_start)
        deploy_block = self.text[deploy_start:deploy_end]
        self.assertIn('deployment_stage="install-root"', deploy_block)
        self.assertIn("harden_install_root_ownership", deploy_block)

    def test_installer_unit_is_one_shot_and_accepts_no_arguments(self) -> None:
        unit = (ROOT / "systemd/system/open-mmi-update-installer.service").read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", unit)
        self.assertIn("Environment=OPEN_MMI_PREPARED_DEPLOYMENT=1", unit)
        self.assertIn(
            "ExecStart=/opt/open-mmi/venv/bin/python -I -m ui.update_installer\n",
            unit,
        )
        self.assertIn("/run/open-mmi", unit)
        self.assertIn("ProtectHome=false", unit)
        self.assertNotIn("ProtectHome=read-only", unit)
        self.assertIn("ReadWritePaths=/opt ", unit)
        self.assertIn("/usr/lib/tmpfiles.d", unit)
        self.assertIn("/etc/udev/rules.d", unit)
        self.assertNotIn("ReadWritePaths=/opt/open-mmi ", unit)
        self.assertNotIn("%i", unit)
        self.assertIn("ProtectSystem=strict", unit)

        coordinator = (
            ROOT / "systemd/system/open-mmi-update-coordinator.service"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ExecStart=/opt/open-mmi/venv/bin/python -I -m ui.update_coordinator serve\n",
            coordinator,
        )
        self.assertNotIn(
            "ExecStart=/opt/open-mmi/venv/bin/open-mmi-update-coordinator serve\n",
            coordinator,
        )

    def test_vehicle_configuration_coordinator_is_publicly_read_only_and_hardened(self) -> None:
        unit = (ROOT / "systemd/system/open-mmi-vehicle-config-coordinator.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/opt/open-mmi/venv/bin/open-mmi-vehicle-config-coordinator serve", unit)
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn(
            "EnvironmentFile=/etc/open-mmi/vehicle-config-coordinator.env",
            unit,
        )
        self.assertNotIn("PrivateNetwork=true", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn(
            "ReadWritePaths=/var/lib/open-mmi /run/open-mmi /etc/open-mmi /etc/udev/rules.d",
            unit,
        )
        self.assertIn("RuntimeDirectoryPreserve=yes", unit)
        self.assertIn(
            "ExecStartPre=/usr/bin/systemd-tmpfiles --create "
            "/opt/open-mmi/packaging/tmpfiles/open-mmi.conf",
            unit,
        )
        start = self.text.index("install_vehicle_config_coordinator() {")
        end = self.text.index("remove_login_autostart() {", start)
        block = self.text[start:end]
        self.assertIn('groupadd --system "$VEHICLE_CONFIG_COORDINATOR_GROUP"', block)
        self.assertIn('usermod -aG "$VEHICLE_CONFIG_COORDINATOR_GROUP" "$REAL_USER"', block)
        self.assertIn('systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT"', block)
        self.assertIn("wait_for_vehicle_config_coordinator", block)
        self.assertIn("write_vehicle_config_coordinator_environment", block)
        self.assertIn("write_vehicle_config_coordinator_sandbox", block)
        self.assertIn("install_open_mmi_transaction_locks", block)
        self.assertLess(
            block.index("install_open_mmi_transaction_locks"),
            block.index('systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT"'),
        )
        environment_end = self.text.index("\nPY_VEHICLE_CONFIG_COORDINATOR_ENV")
        sandbox_definition = self.text.index(
            "write_vehicle_config_coordinator_sandbox() {"
        )
        self.assertGreater(sandbox_definition, environment_end)
        self.assertIn(
            'install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$runtime_directory"',
            self.text,
        )
        self.assertIn("vehicle-config-coordinator-sandbox.conf", self.text)
        self.assertIn("ReadWritePaths=\"-", self.text)
        self.assertIn('"$USER_CONFIG_DIR"', self.text)
        self.assertIn('"/run/user/$USER_ID/open-mmi/status.json"', self.text)
        self.assertNotIn('${OPEN_MMI_PREPARED_DEPLOYMENT:-0}', block)
        profile_reload = self.text.index('reload_profile_provisioning')
        coordinator_restart = self.text.index(
            'systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT"',
            profile_reload,
        )
        self.assertGreater(coordinator_restart, profile_reload)

    def test_can_provision_helper_has_host_network_with_only_net_admin(self) -> None:
        unit = (
            ROOT / "systemd/system/open-mmi-vehicle-can-provision.service"
        ).read_text(encoding="utf-8")
        self.assertIn("Type=oneshot", unit)
        self.assertIn(
            "ExecStart=/opt/open-mmi/venv/bin/open-mmi-vehicle-config-coordinator provision-can",
            unit,
        )
        self.assertNotIn("PrivateNetwork=true", unit)
        self.assertIn("RestrictAddressFamilies=AF_NETLINK AF_UNIX", unit)
        self.assertIn(
            "CapabilityBoundingSet=CAP_NET_ADMIN CAP_DAC_READ_SEARCH", unit
        )
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ReadWritePaths=/run/open-mmi", unit)
        self.assertNotIn("/sys", unit)
        self.assertNotIn("%i", unit)
        start = self.text.index("install_vehicle_config_coordinator() {")
        end = self.text.index("remove_login_autostart() {", start)
        block = self.text[start:end]
        self.assertIn(
            '"$REPO_ROOT/systemd/system/$VEHICLE_CAN_PROVISION_UNIT"', block
        )
        self.assertIn(
            '"/etc/systemd/system/$VEHICLE_CAN_PROVISION_UNIT"', block
        )

    def test_coordinator_can_read_the_managed_checkout_and_is_restarted(self) -> None:
        unit = (ROOT / "systemd/system/open-mmi-update-coordinator.service").read_text(encoding="utf-8")
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn("RuntimeDirectoryPreserve=yes", unit)
        self.assertIn(
            "ExecStartPre=/usr/bin/systemd-tmpfiles --create "
            "/opt/open-mmi/packaging/tmpfiles/open-mmi.conf",
            unit,
        )
        self.assertNotIn("ProtectHome=true", unit)
        start = self.text.index("install_update_coordinator() {")
        end = self.text.index("remove_login_autostart() {", start)
        block = self.text[start:end]
        self.assertIn('systemctl restart "$UPDATE_COORDINATOR_UNIT"', block)
        self.assertIn('${OPEN_MMI_PREPARED_DEPLOYMENT:-0}', block)
        self.assertIn("Log out and back in", block)


    def test_install_and_update_record_managed_update_source_metadata(self) -> None:
        install_start = self.text.index("cmd_install() {")
        update_start = self.text.index("cmd_update() {")
        uninstall_start = self.text.index("cmd_uninstall() {")
        install_block = self.text[install_start:update_start]
        update_block = self.text[update_start:uninstall_start]
        metadata_start = self.text.index("write_update_source_metadata() {")
        metadata_end = self.text.index("copy_if_missing() {", metadata_start)
        metadata_block = self.text[metadata_start:metadata_end]

        self.assertIn('destination="$INSTALL_DIR/.update-source.json"', metadata_block)
        self.assertIn('UPDATE_POLICY_FILE="/etc/open-mmi/update-policy.json"', self.text)
        self.assertIn('"schema_version": 1', metadata_block)
        self.assertIn('"channel": "nightly"', metadata_block)
        self.assertIn('"channel": policy["channel"]', metadata_block)
        self.assertIn('approved_channels = {"stable", "beta", "nightly"}', metadata_block)
        self.assertIn('"repository_path": str(Path(sys.argv[2]).resolve())', metadata_block)
        self.assertIn('"installed_commit": sys.argv[5].lower()', metadata_block)
        self.assertIn('tempfile.NamedTemporaryFile(', metadata_block)
        self.assertIn('os.fsync(temporary.fileno())', metadata_block)
        self.assertIn('os.replace(temporary_name, path)', metadata_block)
        self.assertIn('atomic_json(metadata_path, payload, 0o644)', metadata_block)
        self.assertIn("write_checkout_update_source_metadata", install_block)
        self.assertIn("write_checkout_update_source_metadata", update_block)
        self.assertGreater(install_block.index('get_current_version > "$VERSION_FILE"'), 0)
        self.assertGreater(
            install_block.index("write_checkout_update_source_metadata"),
            install_block.index('get_current_version > "$VERSION_FILE"'),
        )
        self.assertGreater(
            update_block.index("write_checkout_update_source_metadata"),
            update_block.index("echo '$new_version' > '$VERSION_FILE'"),
        )

    def test_uninstall_removes_root_owned_update_policy(self) -> None:
        uninstall_start = self.text.index("cmd_uninstall() {")
        status_start = self.text.index("cmd_status() {")
        uninstall_block = self.text[uninstall_start:status_start]
        self.assertIn('sudo rm -f "$UPDATE_POLICY_FILE"', uninstall_block)
        self.assertIn('sudo rmdir "$(dirname "$UPDATE_POLICY_FILE")"', uninstall_block)


    def test_edit_can_defaults_to_current_seat_infotainment_bus(self) -> None:
        start = self.text.index("        edit-can)")
        end = self.text.index("        show)", start)
        block = self.text[start:end]
        self.assertIn('#   infotainment -> can0', block)
        self.assertIn('Environment="OPEN_MMI_CAN_BUS=infotainment"', block)
        self.assertNotIn('Environment="OPEN_MMI_CAN_BUS=comfort"', block)

    def test_group_cleanup_guidance_is_safe(self) -> None:
        self.assertNotIn("sudo usermod -G $(groups", self.text)
        self.assertIn("sudo gpasswd -d $REAL_USER video", self.text)
        self.assertIn("sudo gpasswd -d $REAL_USER input", self.text)


if __name__ == "__main__":
    unittest.main()
