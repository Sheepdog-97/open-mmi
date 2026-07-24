from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from powerd import daemon
from powerd.runtime import CanHealth


class _StopLoop(BaseException):
    pass


class _FakeBus:
    def __init__(self, results: list[object]) -> None:
        self._results = iter(results)
        self.shutdown_calls = 0

    def recv(self, *, timeout: float) -> object:
        result = next(self._results)
        if isinstance(result, BaseException):
            raise result
        return result

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class PowerDaemonRecoveryTests(unittest.TestCase):
    def _paths(self, root: Path) -> tuple[Path, Path]:
        policy_path = root / "power-policy.json"
        policy_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "enabled": True,
                    "silence_seconds": 60,
                    "resume_guard_seconds": 30,
                    "require_remote_wake": True,
                    "trigger": "can_bus_silence",
                }
            ),
            encoding="utf-8",
        )
        status_path = root / "status.json"
        status_path.write_text(
            json.dumps(
                {"runtime": {"state": "ready", "interface": "can0"}}
            ),
            encoding="utf-8",
        )
        return policy_path, status_path

    def test_waits_for_interface_to_be_healthy_before_opening_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path, status_path = self._paths(Path(temporary))
            bus = _FakeBus([object(), _StopLoop()])
            factory = mock.Mock(return_value=bus)
            sleeper = mock.Mock()

            with mock.patch(
                "powerd.daemon.can_health",
                side_effect=(
                    CanHealth(False, False, "DISCONNECTED"),
                    CanHealth(True, False, "STOPPED"),
                    CanHealth(True, True, "ERROR-ACTIVE"),
                ),
            ) as health_check:
                with self.assertRaises(_StopLoop):
                    daemon.run(
                        policy_path=policy_path,
                        status_path=status_path,
                        clock=lambda: 100.0,
                        sleeper=sleeper,
                        bus_factory=factory,
                    )

            self.assertEqual(health_check.call_count, 3)
            factory.assert_called_once_with(
                channel="can0",
                interface="socketcan",
            )
            self.assertEqual(
                sleeper.call_args_list,
                [
                    mock.call(daemon.DEFAULT_LOOP_INTERVAL_SECONDS),
                    mock.call(daemon.DEFAULT_LOOP_INTERVAL_SECONDS),
                ],
            )
            self.assertEqual(bus.shutdown_calls, 1)

    def test_receive_failure_backs_off_and_reopens_after_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path, status_path = self._paths(Path(temporary))
            failed_bus = _FakeBus([OSError("Network is down")])
            recovered_bus = _FakeBus([_StopLoop()])
            factory = mock.Mock(side_effect=(failed_bus, recovered_bus))
            sleeper = mock.Mock()

            with mock.patch(
                "powerd.daemon.can_health",
                side_effect=(
                    CanHealth(True, True, "ERROR-ACTIVE"),
                    CanHealth(True, False, "STOPPED"),
                    CanHealth(True, True, "ERROR-ACTIVE"),
                ),
            ) as health_check:
                with self.assertRaises(_StopLoop):
                    daemon.run(
                        policy_path=policy_path,
                        status_path=status_path,
                        clock=lambda: 100.0,
                        sleeper=sleeper,
                        bus_factory=factory,
                    )

            self.assertEqual(health_check.call_count, 3)
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(
                sleeper.call_args_list,
                [
                    mock.call(daemon.DEFAULT_LOOP_INTERVAL_SECONDS),
                    mock.call(daemon.DEFAULT_LOOP_INTERVAL_SECONDS),
                ],
            )
            self.assertEqual(failed_bus.shutdown_calls, 1)
            self.assertEqual(recovered_bus.shutdown_calls, 1)

    def test_health_loss_closes_socket_until_interface_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path, status_path = self._paths(Path(temporary))
            failed_bus = _FakeBus([None])
            recovered_bus = _FakeBus([_StopLoop()])
            factory = mock.Mock(side_effect=(failed_bus, recovered_bus))
            sleeper = mock.Mock()
            clock_values = iter(range(0, 1000, 10))

            with mock.patch(
                "powerd.daemon.can_health",
                side_effect=(
                    CanHealth(True, True, "ERROR-ACTIVE"),
                    CanHealth(True, False, "STOPPED"),
                    CanHealth(True, True, "ERROR-ACTIVE"),
                ),
            ) as health_check:
                with mock.patch(
                    "powerd.daemon.remote_wake_ready",
                    return_value=True,
                ):
                    with self.assertRaises(_StopLoop):
                        daemon.run(
                            policy_path=policy_path,
                            status_path=status_path,
                            clock=lambda: float(next(clock_values)),
                            sleeper=sleeper,
                            bus_factory=factory,
                        )

            self.assertEqual(health_check.call_count, 3)
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(sleeper.call_count, 1)
            self.assertEqual(failed_bus.shutdown_calls, 1)
            self.assertEqual(recovered_bus.shutdown_calls, 1)

    def test_repeated_open_failure_is_rate_limited_and_logged_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            policy_path, status_path = self._paths(Path(temporary))
            factory = mock.Mock(
                side_effect=(
                    OSError("permission denied"),
                    OSError("permission denied"),
                    _StopLoop(),
                )
            )
            sleeper = mock.Mock()

            with mock.patch(
                "powerd.daemon.can_health",
                return_value=CanHealth(True, True, "ERROR-ACTIVE"),
            ):
                with self.assertLogs("open_mmi.powerd", level="WARNING") as logs:
                    with self.assertRaises(_StopLoop):
                        daemon.run(
                            policy_path=policy_path,
                            status_path=status_path,
                            clock=lambda: 100.0,
                            sleeper=sleeper,
                            bus_factory=factory,
                        )

            self.assertEqual(factory.call_count, 3)
            self.assertEqual(sleeper.call_count, 2)
            warnings = [
                message
                for message in logs.output
                if "Could not observe can0" in message
            ]
            self.assertEqual(len(warnings), 1)


if __name__ == "__main__":
    unittest.main()
