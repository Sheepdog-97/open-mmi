from __future__ import annotations

import json
import tempfile
import unittest

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ui import launcher


class LauncherConfigTests(unittest.TestCase):
    def test_missing_config_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = launcher.load_config(Path(directory) / "missing.json")
        self.assertEqual(config["default_ui"], "web")
        self.assertEqual(config["web_url"], "http://127.0.0.1:8765")
        self.assertNotIn("start_at_login", config)

    def test_web_url_must_remain_loopback_only(self):
        config = dict(launcher.DEFAULT_CONFIG)
        config["web_url"] = "https://example.test/dashboard"
        with self.assertRaisesRegex(launcher.LauncherError, "loopback"):
            launcher._validate_config(config)

    def test_launcher_network_scope_denies_external_egress(self):
        command = launcher._network_sandbox_command(["--status"])
        self.assertEqual(command[0], "/usr/bin/systemd-run")
        self.assertIn("--user", command)
        self.assertIn("--scope", command)
        self.assertIn("--property=IPAddressDeny=any", command)
        self.assertIn("--property=IPAddressAllow=localhost", command)
        self.assertNotIn(
            "--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            command,
        )
        self.assertEqual(command[-4:], ["-I", "-m", "ui.launcher", "--status"])

    def test_user_config_merges_over_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.json"
            path.write_text(json.dumps({"browser_mode": "window"}), encoding="utf-8")
            config = launcher.load_config(path)
        self.assertEqual(config["browser_mode"], "window")
        self.assertEqual(config["default_ui"], "web")

    def test_unknown_config_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.json"
            path.write_text(json.dumps({"surprise": True}), encoding="utf-8")
            with self.assertRaises(launcher.LauncherError):
                launcher.load_config(path)

    def test_save_default_ui_preserves_other_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.json"
            path.write_text(json.dumps({"browser_mode": "window"}), encoding="utf-8")
            launcher.save_default_ui("tui", path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload, {"browser_mode": "window", "default_ui": "tui"})

    def test_legacy_service_startup_preference_is_ignored_and_removed_on_save(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher.json"
            path.write_text(
                json.dumps({"browser_mode": "window", "start_at_login": True}),
                encoding="utf-8",
            )
            config = launcher.load_config(path)
            launcher.save_default_ui("tui", path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("start_at_login", config)
        self.assertEqual(payload, {"browser_mode": "window", "default_ui": "tui"})


class LauncherServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = dict(launcher.DEFAULT_CONFIG)
        self.config["startup_timeout_seconds"] = 0.2
        self.config["health_poll_interval_seconds"] = 0.1

    @staticmethod
    def result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_healthy_dashboard_is_not_restarted(self) -> None:
        commands: list[list[str]] = []

        def runner(args, *, capture_output=False):
            commands.append(list(args))
            return self.result()

        launcher.ensure_dashboard_ready(
            self.config,
            health_checker=lambda _url, _timeout: True,
            command_runner=runner,
            sleeper=lambda _seconds: None,
        )
        self.assertEqual(commands, [])

    def test_inactive_dashboard_is_started(self) -> None:
        checks = iter((False, True))
        commands: list[list[str]] = []

        def runner(args, *, capture_output=False):
            commands.append(list(args))
            if "is-active" in args:
                return self.result(returncode=3)
            return self.result()

        launcher.ensure_dashboard_ready(
            self.config,
            health_checker=lambda _url, _timeout: next(checks),
            command_runner=runner,
            sleeper=lambda _seconds: None,
        )
        self.assertIn(["systemctl", "--user", "start", launcher.SERVICE_NAME], commands)

    def test_active_unhealthy_dashboard_is_restarted(self) -> None:
        checks = iter((False, True))
        commands: list[list[str]] = []

        def runner(args, *, capture_output=False):
            commands.append(list(args))
            return self.result()

        launcher.ensure_dashboard_ready(
            self.config,
            health_checker=lambda _url, _timeout: next(checks),
            command_runner=runner,
            sleeper=lambda _seconds: None,
        )
        self.assertIn(["systemctl", "--user", "restart", launcher.SERVICE_NAME], commands)

    def test_startup_timeout_is_reported(self) -> None:
        def runner(args, *, capture_output=False):
            if "is-active" in args:
                return self.result(returncode=3)
            return self.result()

        with self.assertRaises(launcher.LauncherError):
            launcher.ensure_dashboard_ready(
                self.config,
                health_checker=lambda _url, _timeout: False,
                command_runner=runner,
                sleeper=lambda _seconds: None,
            )


class BrowserCommandTests(unittest.TestCase):
    def test_health_url_is_derived_without_fallback(self) -> None:
        self.assertEqual(
            launcher.health_url("http://127.0.0.1:8765"),
            "http://127.0.0.1:8765/api/health",
        )

    def test_chromium_kiosk_command_uses_owned_profile(self) -> None:
        profile = Path("/tmp/open-mmi/chromium")
        command = launcher.build_browser_command(
            ["/usr/bin/chromium"],
            "http://127.0.0.1:8765",
            "kiosk",
            profile_dir=profile,
        )
        self.assertIn("--kiosk", command)
        self.assertIn("--app=http://127.0.0.1:8765", command)
        self.assertIn(f"--user-data-dir={profile}", command)
        self.assertIn(f"--class={launcher.BROWSER_WINDOW_CLASS}", command)

    def test_firefox_kiosk_command_uses_owned_profile(self) -> None:
        profile = Path("/tmp/open-mmi/firefox")
        command = launcher.build_browser_command(
            ["/usr/bin/firefox"],
            "http://127.0.0.1:8765",
            "kiosk",
            profile_dir=profile,
        )
        self.assertIn("--profile", command)
        self.assertIn(str(profile), command)
        self.assertIn("--no-remote", command)
        self.assertIn("--class", command)
        self.assertIn(launcher.BROWSER_WINDOW_CLASS, command)

    def test_explicit_placeholder_is_respected(self) -> None:
        command = launcher.build_browser_command(
            ["/custom/browser", "--open={url}"],
            "http://127.0.0.1:8765",
            "window",
        )
        self.assertEqual(command, ["/custom/browser", "--open=http://127.0.0.1:8765"])

    def test_managed_profile_cannot_be_overridden(self) -> None:
        with self.assertRaises(launcher.LauncherError):
            launcher.build_browser_command(
                ["/usr/bin/chromium", "--user-data-dir=/tmp/unrelated"],
                "http://127.0.0.1:8765",
                "kiosk",
                profile_dir=Path("/tmp/open-mmi/chromium"),
            )

    @patch("ui.launcher.shutil.which")
    def test_auto_browser_uses_first_available_candidate(self, which) -> None:
        which.side_effect = lambda name: "/usr/bin/chromium" if name == "chromium" else None
        self.assertEqual(launcher.resolve_browser("auto"), ["/usr/bin/chromium"])


class BrowserInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = dict(launcher.DEFAULT_CONFIG)

    @staticmethod
    def _fake_process(pid: int) -> SimpleNamespace:
        return SimpleNamespace(pid=pid)

    def test_first_launch_records_owned_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            popen_calls = []

            def popen(command, **kwargs):
                popen_calls.append((list(command), kwargs))
                return self._fake_process(4242)

            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                command = launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=popen,
                    process_finder=lambda _marker, _required: None,
                )

            state = json.loads((root / "runtime" / launcher.BROWSER_STATE_FILE).read_text())

        self.assertEqual(len(popen_calls), 1)
        self.assertEqual(popen_calls[0][0], command)
        self.assertEqual(state["pid"], 4242)
        self.assertEqual(state["command"], command)
        self.assertEqual(state["browser_family"], "chromium")

    def test_repeated_launch_reuses_owned_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launched_commands: list[list[str]] = []

            def popen(command, **_kwargs):
                launched_commands.append(list(command))
                return self._fake_process(4242)

            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                first_command = launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=popen,
                    process_finder=lambda _marker, _required: None,
                )
                focus = Mock(return_value=True)
                second_command = launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=lambda *_args, **_kwargs: self.fail("browser relaunched"),
                    process_reader=lambda pid: first_command if pid == 4242 else [],
                    process_finder=lambda _marker, _required: self.fail("process scan not needed"),
                    focus_window=focus,
                )

        self.assertEqual(second_command, first_command)
        self.assertEqual(len(launched_commands), 1)
        focus.assert_called_once_with()

    def test_stale_state_is_replaced_after_browser_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pids = iter((111, 222))

            def popen(_command, **_kwargs):
                return self._fake_process(next(pids))

            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=popen,
                    process_finder=lambda _marker, _required: None,
                )
                launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=popen,
                    process_reader=lambda _pid: [],
                    process_finder=lambda _marker, _required: None,
                )

            state = json.loads((root / "runtime" / launcher.BROWSER_STATE_FILE).read_text())

        self.assertEqual(state["pid"], 222)

    def test_running_instance_with_changed_settings_is_not_duplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                first_command = launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=lambda *_args, **_kwargs: self._fake_process(4242),
                    process_finder=lambda _marker, _required: None,
                )
                changed = dict(self.config)
                changed["browser_mode"] = "fullscreen"
                with self.assertRaises(launcher.LauncherError):
                    launcher.launch_browser(
                        changed,
                        runtime_dir=root / "runtime",
                        state_dir=root / "state",
                        popen_factory=lambda *_args, **_kwargs: self.fail("browser duplicated"),
                        process_reader=lambda pid: first_command if pid == 4242 else [],
                        process_finder=lambda _marker, _required: None,
                    )

    def test_lost_state_recovers_managed_browser_from_process_scan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            finder_calls = []

            def finder(marker, required):
                finder_calls.append((marker, required))
                return 777 if required == self.config["web_url"] else None

            focus = Mock(return_value=True)
            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=lambda *_args, **_kwargs: self.fail("browser duplicated"),
                    process_finder=finder,
                    focus_window=focus,
                )

            state = json.loads((root / "runtime" / launcher.BROWSER_STATE_FILE).read_text())

        self.assertEqual(state["pid"], 777)
        self.assertEqual(finder_calls[0][1], self.config["web_url"])
        focus.assert_called_once_with()

    def test_unrelated_process_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launched = Mock(return_value=self._fake_process(99))
            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                launcher.launch_browser(
                    self.config,
                    runtime_dir=root / "runtime",
                    state_dir=root / "state",
                    popen_factory=launched,
                    process_finder=lambda _marker, _required: None,
                )
        launched.assert_called_once()

    def test_conflicting_managed_profile_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def finder(_marker, required):
                return None if required is not None else 888

            with patch("ui.launcher.resolve_browser", return_value=["/usr/bin/chromium"]):
                with self.assertRaises(launcher.LauncherError):
                    launcher.launch_browser(
                        self.config,
                        runtime_dir=root / "runtime",
                        state_dir=root / "state",
                        popen_factory=lambda *_args, **_kwargs: self.fail("browser duplicated"),
                        process_finder=finder,
                    )

    @patch("ui.launcher.shutil.which")
    def test_focus_uses_window_class_without_shell(self, which) -> None:
        which.side_effect = lambda name: "/usr/bin/wmctrl" if name == "wmctrl" else None
        commands = []

        def runner(args, *, capture_output=False):
            commands.append((list(args), capture_output))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        self.assertTrue(launcher.focus_browser_window(runner))
        self.assertEqual(
            commands,
            [(["/usr/bin/wmctrl", "-x", "-a", launcher.BROWSER_WINDOW_CLASS], True)],
        )


class LauncherChooserTests(unittest.TestCase):
    @staticmethod
    def result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    @patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
    @patch("ui.launcher.shutil.which")
    def test_graphical_chooser_uses_zenity(self, which) -> None:
        which.side_effect = lambda name: "/usr/bin/zenity" if name == "zenity" else None
        commands = []

        def runner(args, *, capture_output=False):
            commands.append((list(args), capture_output))
            return self.result(stdout="Terminal UI\n")

        self.assertEqual(launcher.choose_ui(runner), "tui")
        self.assertEqual(commands[0][0][0], "/usr/bin/zenity")
        self.assertIn("--radiolist", commands[0][0])
        self.assertTrue(commands[0][1])

    @patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
    @patch("ui.launcher.shutil.which", return_value="/usr/bin/zenity")
    def test_graphical_chooser_can_remember_or_use_once(self, _which) -> None:
        remember = launcher.confirm_remember_choice(
            "web",
            lambda args, *, capture_output=False: self.result(
                returncode=0 if "--question" in args else 1
            ),
        )
        use_once = launcher.confirm_remember_choice(
            "tui",
            lambda _args, *, capture_output=False: self.result(returncode=1),
        )

        self.assertTrue(remember)
        self.assertFalse(use_once)

    @patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}, clear=True)
    @patch("ui.launcher.shutil.which", return_value="/usr/bin/zenity")
    def test_graphical_chooser_reports_cancellation(self, _which) -> None:
        with self.assertRaisesRegex(launcher.LauncherError, "cancelled"):
            launcher.choose_ui(
                lambda _args, *, capture_output=False: self.result(returncode=1)
            )

    @patch.dict("os.environ", {}, clear=True)
    @patch("ui.launcher.sys.stdin.isatty", return_value=True)
    @patch("ui.launcher.sys.stdout.isatty", return_value=True)
    @patch("builtins.input", return_value="1")
    def test_terminal_chooser_remains_available(
        self,
        _input,
        _stdout_tty,
        _stdin_tty,
    ) -> None:
        self.assertEqual(launcher.choose_ui(), "web")

    def test_native_gnome_terminal_waits_for_terminal_lifecycle(self) -> None:
        command = ["/usr/local/bin/open-mmi-status"]
        self.assertEqual(
            launcher._terminal_command("/usr/bin/gnome-terminal.real", command),
            ["/usr/bin/gnome-terminal.real", "--wait", "--"] + command,
        )

    def test_linux_mint_wrapper_is_unwrapped_to_native_terminal(self) -> None:
        with patch(
            "ui.launcher.os.path.realpath",
            return_value="/usr/bin/gnome-terminal.wrapper",
        ), patch("ui.launcher.os.access", return_value=True):
            self.assertEqual(
                launcher._unwrap_terminal_executable("/usr/bin/x-terminal-emulator"),
                "/usr/bin/gnome-terminal.real",
            )

    def test_wrapper_fallback_uses_portable_execute_argument(self) -> None:
        command = ["/usr/local/bin/open-mmi-status"]
        self.assertEqual(
            launcher._terminal_command("/usr/bin/gnome-terminal.wrapper", command),
            ["/usr/bin/gnome-terminal.wrapper", "-e"] + command,
        )

    @patch("ui.launcher.sys.stdin.isatty", return_value=False)
    @patch("ui.launcher.sys.stdout.isatty", return_value=False)
    def test_tui_uses_native_binary_behind_mint_wrapper(
        self,
        _stdout_tty,
        _stdin_tty,
    ) -> None:
        process = SimpleNamespace(wait=lambda: 0)

        def which(name: str):
            if name == "open-mmi-status":
                return "/usr/local/bin/open-mmi-status"
            if name == "x-terminal-emulator":
                return "/usr/bin/x-terminal-emulator"
            return None

        with patch("ui.launcher.shutil.which", side_effect=which), patch(
            "ui.launcher.os.path.realpath",
            return_value="/usr/bin/gnome-terminal.wrapper",
        ), patch("ui.launcher.os.access", return_value=True), patch(
            "ui.launcher.subprocess.Popen",
            return_value=process,
        ) as popen:
            self.assertEqual(launcher._run_tui_once(), 0)

        self.assertEqual(
            popen.call_args.args[0],
            [
                "/usr/bin/gnome-terminal.real",
                "--wait",
                "--",
                "/usr/local/bin/open-mmi-status",
            ],
        )

    def test_tui_exit_can_recover_to_web_dashboard(self) -> None:
        config = dict(launcher.DEFAULT_CONFIG)
        with patch("ui.launcher._run_tui_once", return_value=0) as run_tui, patch(
            "ui.launcher._recover_after_tui", return_value="web"
        ) as recover, patch("ui.launcher._open_web_dashboard", return_value=0) as open_web:
            result = launcher.launch_tui(config, Path("/tmp/launcher.json"))

        self.assertEqual(result, 0)
        run_tui.assert_called_once_with()
        recover.assert_called_once_with(config, Path("/tmp/launcher.json"))
        open_web.assert_called_once_with(config)

    def test_tui_can_be_selected_again_without_losing_recovery(self) -> None:
        config = dict(launcher.DEFAULT_CONFIG)
        selections = iter(("tui", "web"))
        with patch("ui.launcher._run_tui_once", side_effect=(0, 0)) as run_tui, patch(
            "ui.launcher._recover_after_tui", side_effect=lambda *_args: next(selections)
        ), patch("ui.launcher._open_web_dashboard", return_value=0):
            self.assertEqual(
                launcher.launch_tui(config, Path("/tmp/launcher.json")),
                0,
            )

        self.assertEqual(run_tui.call_count, 2)

    @patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
    def test_tui_launch_failure_enters_recovery(self) -> None:
        config = dict(launcher.DEFAULT_CONFIG)
        with patch(
            "ui.launcher._run_tui_once",
            side_effect=launcher.LauncherError("no terminal emulator found"),
        ), patch("ui.launcher._recover_after_tui", return_value="web") as recover, patch(
            "ui.launcher._open_web_dashboard", return_value=0
        ) as open_web:
            result = launcher.launch_tui(config, Path("/tmp/launcher.json"))

        self.assertEqual(result, 0)
        recover.assert_called_once_with(config, Path("/tmp/launcher.json"))
        open_web.assert_called_once_with(config)

    @patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True)
    @patch("ui.launcher.choose_ui", side_effect=launcher.LauncherError("cancelled"))
    def test_cancelled_recovery_opens_web_for_the_session(self, _choose) -> None:
        self.assertEqual(
            launcher._recover_after_tui(
                dict(launcher.DEFAULT_CONFIG),
                Path("/tmp/launcher.json"),
            ),
            "web",
        )


class LauncherMainTests(unittest.TestCase):
    def test_ask_remember_requires_interactive_choice(self) -> None:
        self.assertEqual(launcher.main(["--ask-remember"]), 1)

    def test_chooser_persists_only_after_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "launcher.json"
            with patch("ui.launcher.choose_ui", return_value="web"), patch(
                "ui.launcher.confirm_remember_choice", return_value=True
            ), patch("ui.launcher._open_web_dashboard", return_value=0):
                result = launcher.main(
                    ["--choose", "--ask-remember", "--config", str(config_path)]
                )

            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(payload["default_ui"], "web")


class LauncherAutostartPreferenceTests(unittest.TestCase):
    @staticmethod
    def result(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    def test_configure_open_at_login_creates_managed_desktop_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autostart" / "open-mmi.desktop"
            launcher.configure_open_at_login(True, path)
            body = path.read_text(encoding="utf-8")
            self.assertTrue(launcher.open_at_login_enabled(path))
            self.assertIn("Exec=/usr/local/bin/open-mmi-launcher", body)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_configure_open_at_login_removes_managed_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "autostart" / "open-mmi.desktop"
            launcher.configure_open_at_login(True, path)
            launcher.configure_open_at_login(False, path)
            self.assertFalse(path.exists())
            self.assertFalse(launcher.open_at_login_enabled(path))

    def test_configure_open_at_login_refuses_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target.desktop"
            target.write_text("unrelated", encoding="utf-8")
            link = root / "open-mmi.desktop"
            link.symlink_to(target)
            with self.assertRaisesRegex(launcher.LauncherError, "symlinked"):
                launcher.configure_open_at_login(True, link)

    def test_dashboard_service_actions_remain_available_for_cli(self) -> None:
        commands = []

        def runner(args, *, capture_output=False):
            commands.append((list(args), capture_output))
            return self.result()

        launcher.configure_dashboard_service("disable", runner)
        self.assertEqual(
            commands,
            [(["systemctl", "--user", "disable", launcher.SERVICE_NAME], True)],
        )

    def test_status_reports_application_autostart_and_service_state(self) -> None:
        config = dict(launcher.DEFAULT_CONFIG)
        with patch("ui.launcher.service_is_active", return_value=True), patch(
            "ui.launcher.service_is_enabled", return_value=False
        ), patch("ui.launcher.open_at_login_enabled", return_value=True), patch(
            "ui.launcher.check_health", return_value=True
        ), patch("ui.launcher.browser_status", return_value={"running": False}):
            payload = launcher.status_payload(config, Path("/tmp/launcher.json"))

        self.assertTrue(payload["open_at_login"])
        self.assertFalse(payload["service_enabled"])


if __name__ == "__main__":
    unittest.main()
