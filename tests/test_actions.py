import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from actions import audio, brightness, keys, screen


class AudioActionTests(unittest.TestCase):
    def test_playerctl_success(self):
        completed = SimpleNamespace(returncode=0, stderr="")
        with mock.patch.object(audio.subprocess, "run", return_value=completed) as run:
            self.assertEqual(audio._run_pc(["play-pause"]), (True, ""))

        run.assert_called_once_with(
            ["playerctl", "play-pause"],
            capture_output=True,
            text=True,
            timeout=audio.SUBPROCESS_TIMEOUT,
        )

    def test_playerctl_failures_are_non_fatal(self):
        with mock.patch.object(
            audio.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("playerctl", 5),
        ):
            self.assertEqual(audio._run_pc(["next"]), (False, "timeout"))

        with mock.patch.object(audio.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(audio._run_pc(["next"]), (False, "not found"))

        with mock.patch.object(
            audio.subprocess, "run", side_effect=RuntimeError("broken")
        ):
            self.assertEqual(audio._run_pc(["next"]), (False, "broken"))

    def test_fallback_invokes_matching_key_action_and_isolates_failure(self):
        key_action = mock.Mock()
        # _fallback imports the real sibling module, so patch its function.
        from actions import keys as key_module

        with mock.patch.object(key_module, "play_pause", key_action):
            audio._fallback("play_pause")
        key_action.assert_called_once_with()

        with (
            mock.patch.object(key_module, "play_pause", side_effect=RuntimeError("bad key")),
            self.assertLogs("canbusd.actions.audio", level="ERROR") as logs,
        ):
            audio._fallback("play_pause")

        self.assertIn("Fallback action failed", "\n".join(logs.output))

    def test_transport_falls_back_on_any_playerctl_failure(self):
        with (
            mock.patch.object(audio, "_run_bluez_transport", return_value=False),
            mock.patch.object(audio, "_run_pc", return_value=(False, "not found")),
            mock.patch.object(audio, "_fallback") as fallback,
        ):
            audio.play_pause()
            audio.next_track()
            audio.prev_track()
            audio.stop()

        self.assertEqual(
            fallback.call_args_list,
            [
                mock.call("play_pause"),
                mock.call("next_track"),
                mock.call("prev_track"),
                mock.call("stop"),
            ],
        )

    def test_transport_does_not_fallback_on_success(self):
        with (
            mock.patch.object(audio, "_run_bluez_transport", return_value=False) as bluez,
            mock.patch.object(audio, "_run_pc", return_value=(True, "")),
            mock.patch.object(audio, "_fallback") as fallback,
        ):
            audio.play_pause()

        fallback.assert_not_called()
        bluez.assert_called_once_with("play-pause", active_only=True)

    def test_active_bluez_player_is_controlled_before_playerctl(self):
        with (
            mock.patch.object(audio, "_run_bluez_transport", return_value=True) as bluez,
            mock.patch.object(audio, "_run_pc") as playerctl,
            mock.patch.object(audio, "_fallback") as fallback,
        ):
            audio.play_pause()

        bluez.assert_called_once_with("play-pause", active_only=True)
        playerctl.assert_not_called()
        fallback.assert_not_called()

    def test_paused_bluez_player_is_used_after_playerctl_failure(self):
        with (
            mock.patch.object(audio, "_run_bluez_transport", side_effect=[False, True]) as bluez,
            mock.patch.object(audio, "_run_pc", return_value=(False, "no players")),
            mock.patch.object(audio, "_fallback") as fallback,
        ):
            audio.play_pause()

        self.assertEqual(
            bluez.call_args_list,
            [
                mock.call("play-pause", active_only=True),
                mock.call("play-pause", active_only=False),
            ],
        )
        fallback.assert_not_called()

    def test_bluez_play_pause_selects_pause_for_playing_player(self):
        path = "/org/bluez/hci0/dev_AA/player0"
        with (
            mock.patch.object(audio, "_bluez_players", return_value=[(path, "playing")]),
            mock.patch.object(audio, "_bluez_busctl", return_value="") as busctl,
        ):
            self.assertTrue(audio._run_bluez_transport("play-pause"))

        busctl.assert_called_once_with(
            "call",
            "org.bluez",
            path,
            "org.bluez.MediaPlayer1",
            "Pause",
        )

    def test_bluez_play_pause_selects_play_for_paused_player(self):
        path = "/org/bluez/hci0/dev_AA/player0"
        with (
            mock.patch.object(audio, "_bluez_players", return_value=[(path, "paused")]),
            mock.patch.object(audio, "_bluez_busctl", return_value="") as busctl,
        ):
            self.assertTrue(audio._run_bluez_transport("play-pause"))

        busctl.assert_called_once_with(
            "call",
            "org.bluez",
            path,
            "org.bluez.MediaPlayer1",
            "Play",
        )

    def test_volume_failures_are_isolated(self):
        functions = (audio.volume_up, audio.volume_down, audio.mute_toggle)
        failures = (
            subprocess.TimeoutExpired("pactl", 5),
            FileNotFoundError(),
            RuntimeError("broken"),
        )
        for function in functions:
            for failure in failures:
                with self.subTest(function=function.__name__, failure=type(failure).__name__):
                    with mock.patch.object(audio.subprocess, "run", side_effect=failure):
                        function()

    def test_volume_actions_use_bounded_subprocess_calls(self):
        with mock.patch.object(audio.subprocess, "run") as run:
            audio.volume_up("+10%")
            audio.volume_down("-10%")
            audio.mute_toggle()

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "+10%"],
                    timeout=audio.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
                mock.call(
                    ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "-10%"],
                    timeout=audio.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
                mock.call(
                    ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                    timeout=audio.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
            ],
        )


class BrightnessActionTests(unittest.TestCase):
    def test_brightness_is_clamped_before_execution(self):
        with mock.patch.object(brightness.subprocess, "run") as run:
            brightness._set_brightness(-5)
            brightness._set_brightness(130)

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["brightnessctl", "set", "0%"],
                    timeout=brightness.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
                mock.call(
                    ["brightnessctl", "set", "100%"],
                    timeout=brightness.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
            ],
        )

    def test_can_dimmer_conversion_preserves_current_inversion_contract(self):
        with mock.patch.object(brightness, "_set_brightness") as set_brightness:
            brightness.from_can(0)
            brightness.from_can(50)
            brightness.from_can(100)
            brightness.from_can(255)

        self.assertEqual(
            set_brightness.call_args_list,
            [mock.call(100), mock.call(50), mock.call(0), mock.call(0)],
        )

    def test_set_percent_applies_current_inversion_contract(self):
        with mock.patch.object(brightness, "_set_brightness") as set_brightness:
            brightness.set_percent(25)

        set_brightness.assert_called_once_with(75)

    def test_subprocess_failures_are_isolated(self):
        failures = (
            subprocess.TimeoutExpired("brightnessctl", 5),
            FileNotFoundError(),
            RuntimeError("broken"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with mock.patch.object(brightness.subprocess, "run", side_effect=failure):
                    brightness._set_brightness(50)

    def test_invalid_can_value_is_logged_not_raised(self):
        with (
            mock.patch.object(brightness, "_set_brightness") as set_brightness,
            self.assertLogs("canbusd.actions.brightness", level="ERROR"),
        ):
            brightness.from_can("invalid")

        set_brightness.assert_not_called()


class KeyActionTests(unittest.TestCase):
    def setUp(self):
        keys._ui = None

    def tearDown(self):
        keys._ui = None

    def test_virtual_input_device_is_reused(self):
        device = mock.Mock()
        with mock.patch.object(keys, "UInput", return_value=device) as constructor:
            self.assertIs(keys._get_ui(), device)
            self.assertIs(keys._get_ui(), device)

        constructor.assert_called_once_with(keys._caps, name="canbusd-input")

    def test_virtual_input_creation_failure_is_propagated_to_press_boundary(self):
        with (
            mock.patch.object(keys, "UInput", side_effect=PermissionError("denied")),
            self.assertRaises(PermissionError),
        ):
            keys._get_ui()

    def test_key_press_writes_press_release_and_sync(self):
        device = mock.Mock()
        keys._ui = device
        keys.play_pause()

        self.assertEqual(
            device.write.call_args_list,
            [
                mock.call(keys.e.EV_KEY, keys.e.KEY_PLAYPAUSE, 1),
                mock.call(keys.e.EV_KEY, keys.e.KEY_PLAYPAUSE, 0),
            ],
        )
        device.syn.assert_called_once_with()

    def test_public_key_actions_map_to_expected_codes(self):
        mappings = [
            (keys.play_pause, keys.e.KEY_PLAYPAUSE),
            (keys.next_track, keys.e.KEY_NEXTSONG),
            (keys.prev_track, keys.e.KEY_PREVIOUSSONG),
            (keys.stop, keys.e.KEY_STOPCD),
            (keys.mute_toggle, keys.e.KEY_MUTE),
            (keys.volume_up, keys.e.KEY_VOLUMEUP),
            (keys.volume_down, keys.e.KEY_VOLUMEDOWN),
            (keys.arrow_left, keys.e.KEY_LEFT),
            (keys.arrow_right, keys.e.KEY_RIGHT),
            (keys.arrow_up, keys.e.KEY_UP),
            (keys.arrow_down, keys.e.KEY_DOWN),
            (keys.confirm, keys.e.KEY_ENTER),
            (keys.back, keys.e.KEY_ESC),
        ]
        with mock.patch.object(keys, "_press") as press:
            for function, expected_code in mappings:
                function()
                press.assert_called_with(expected_code)

        self.assertEqual(press.call_count, len(mappings))

    def test_input_failure_is_isolated(self):
        with (
            mock.patch.object(keys, "_get_ui", side_effect=PermissionError("denied")),
            self.assertLogs("canbusd.actions.keys", level="ERROR") as logs,
        ):
            keys.next_track()

        self.assertIn("Key press failed", "\n".join(logs.output))


class ScreenActionTests(unittest.TestCase):
    def test_environment_supplies_display_defaults_without_overwriting_values(self):
        with mock.patch.dict(screen.os.environ, {"DISPLAY": ":7"}, clear=True):
            env = screen._env()

        self.assertEqual(env["DISPLAY"], ":7")
        self.assertEqual(env["XAUTHORITY"], screen.XAUTH)

    def test_display_power_commands_include_environment_and_timeout(self):
        with (
            mock.patch.object(screen, "_env", return_value={"DISPLAY": ":0"}),
            mock.patch.object(screen.subprocess, "run") as run,
        ):
            screen.off()
            screen.on()

        self.assertEqual(
            run.call_args_list,
            [
                mock.call(
                    ["xset", "dpms", "force", "off"],
                    env={"DISPLAY": ":0"},
                    timeout=screen.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
                mock.call(
                    ["xset", "dpms", "force", "on"],
                    env={"DISPLAY": ":0"},
                    timeout=screen.SUBPROCESS_TIMEOUT,
                    check=False,
                ),
            ],
        )

    def test_display_power_failures_are_isolated(self):
        failures = (
            subprocess.TimeoutExpired("xset", 5),
            FileNotFoundError(),
            RuntimeError("broken"),
        )
        for function in (screen.off, screen.on):
            for failure in failures:
                with self.subTest(function=function.__name__, failure=type(failure).__name__):
                    with mock.patch.object(screen.subprocess, "run", side_effect=failure):
                        function()

    def test_wake_without_user_does_not_call_display_manager(self):
        with (
            mock.patch.object(screen, "on") as wake,
            mock.patch.object(screen.subprocess, "run") as run,
        ):
            screen.wake_and_login()

        wake.assert_called_once_with()
        run.assert_not_called()

    def test_user_switch_failures_are_isolated(self):
        failures = (
            FileNotFoundError(),
            subprocess.TimeoutExpired("dm-tool", 5),
            RuntimeError("broken"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with (
                    mock.patch.object(screen, "on"),
                    mock.patch.object(screen.subprocess, "run", side_effect=failure),
                ):
                    screen.wake_and_login("openmmi")

    def test_wake_and_login_switches_to_requested_user(self):
        with (
            mock.patch.object(screen, "on"),
            mock.patch.object(screen, "_env", return_value={"DISPLAY": ":0"}),
            mock.patch.object(screen.subprocess, "run") as run,
        ):
            screen.wake_and_login("openmmi")

        run.assert_called_once_with(
            ["dm-tool", "switch-to-user", "openmmi"],
            env={"DISPLAY": ":0"},
            timeout=screen.SUBPROCESS_TIMEOUT,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
