"""Vehicle-generic automatic suspend from healthy SocketCAN bus silence."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from powerd.inhibitors import transaction_active
from powerd.policy import PowerPolicyError, load_policy, suspend_allowed
from powerd.runtime import CanHealth, active_interface, can_health
from powerd.wake import DEFAULT_SYS_CLASS_NET, remote_wake_ready


logger = logging.getLogger("open_mmi.powerd")
DEFAULT_HEALTH_INTERVAL_SECONDS = 5.0
DEFAULT_LOOP_INTERVAL_SECONDS = 1.0


def request_suspend(
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> bool:
    try:
        result = runner(
            ["systemctl", "suspend"],
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _close_bus(bus: Optional[Any]) -> None:
    if bus is None:
        return
    try:
        bus.shutdown()
    except Exception:
        logger.exception("Could not close CAN observation socket")


def _health_description(health: CanHealth) -> str:
    if not health.present:
        return "not present"
    if not health.up:
        return f"down ({health.state})"
    return health.state


def run(
    *,
    policy_path: Path,
    status_path: Path,
    sys_class_net: Path = DEFAULT_SYS_CLASS_NET,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    bus_factory: Optional[Callable[..., Any]] = None,
) -> None:
    """Run the persistent power-policy state machine."""

    if bus_factory is None:
        import can

        bus_factory = can.interface.Bus

    interface: Optional[str] = None
    bus: Optional[Any] = None
    last_frame: Optional[float] = None
    awake_since = clock()
    last_health_check = 0.0
    health = CanHealth(False, False, "UNKNOWN")
    wake_ready = False
    reported_wait_state: Optional[tuple[bool, bool, str]] = None
    reported_observation_error: Optional[str] = None

    try:
        while True:
            try:
                policy = load_policy(policy_path)
            except PowerPolicyError as exc:
                logger.error("Power policy disabled: %s", exc)
                _close_bus(bus)
                bus = None
                interface = None
                last_frame = None
                sleeper(DEFAULT_LOOP_INTERVAL_SECONDS)
                continue

            selected = active_interface(status_path)
            if selected != interface:
                _close_bus(bus)
                bus = None
                interface = selected
                last_frame = None
                awake_since = clock()
                last_health_check = 0.0
                health = CanHealth(False, False, "UNKNOWN")
                wake_ready = False
                reported_wait_state = None
                reported_observation_error = None

            if not policy.enabled or interface is None:
                _close_bus(bus)
                bus = None
                last_frame = None
                sleeper(DEFAULT_LOOP_INTERVAL_SECONDS)
                continue

            if bus is None:
                previous_health = health
                health = can_health(interface)
                last_health_check = clock()

                if not health.healthy:
                    wait_state = (health.present, health.up, health.state)
                    if wait_state != reported_wait_state:
                        logger.info(
                            "Waiting for healthy CAN interface %s: %s",
                            interface,
                            _health_description(health),
                        )
                        reported_wait_state = wait_state
                    last_frame = None
                    wake_ready = False
                    sleeper(DEFAULT_LOOP_INTERVAL_SECONDS)
                    continue

                if not previous_health.healthy:
                    awake_since = clock()
                    last_frame = None
                    logger.info(
                        "CAN interface %s is healthy; starting observation",
                        interface,
                    )
                reported_wait_state = None

                try:
                    bus = bus_factory(channel=interface, interface="socketcan")
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    if error != reported_observation_error:
                        logger.warning("Could not observe %s: %s", interface, exc)
                        reported_observation_error = error
                    sleeper(DEFAULT_LOOP_INTERVAL_SECONDS)
                    continue
            try:
                message = bus.recv(timeout=DEFAULT_LOOP_INTERVAL_SECONDS)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                if error != reported_observation_error:
                    logger.warning("CAN observation failed on %s: %s", interface, exc)
                    reported_observation_error = error
                _close_bus(bus)
                bus = None
                last_frame = None
                health = CanHealth(False, False, "DISCONNECTED")
                wake_ready = False
                sleeper(DEFAULT_LOOP_INTERVAL_SECONDS)
                continue

            reported_observation_error = None
            now = clock()
            if message is not None:
                last_frame = now
                continue

            if now - last_health_check >= DEFAULT_HEALTH_INTERVAL_SECONDS:
                health = can_health(interface)
                wake_ready = remote_wake_ready(interface, sys_class_net)
                last_health_check = now
                if not health.healthy:
                    wait_state = (health.present, health.up, health.state)
                    if wait_state != reported_wait_state:
                        logger.info(
                            "CAN interface %s became unavailable: %s",
                            interface,
                            _health_description(health),
                        )
                        reported_wait_state = wait_state
                    _close_bus(bus)
                    bus = None
                    last_frame = None
                    wake_ready = False
                    sleeper(DEFAULT_LOOP_INTERVAL_SECONDS)
                    continue

            silent_for = 0.0 if last_frame is None else now - last_frame
            if not suspend_allowed(
                policy=policy,
                healthy_can=health.healthy,
                wake_ready=wake_ready,
                transaction_busy=transaction_active(),
                observed_frame=last_frame is not None,
                silent_for=silent_for,
                awake_for=now - awake_since,
            ):
                continue

            logger.info(
                "Suspending after %.1f seconds of healthy CAN silence on %s",
                silent_for,
                interface,
            )
            suspended = request_suspend()
            if not suspended:
                logger.error("System suspend request failed")

            # systemctl returns after resume. Reopen the CAN socket and require
            # fresh traffic before another suspend attempt. The same reset also
            # prevents a failed request from creating a tight retry loop.
            _close_bus(bus)
            bus = None
            last_frame = None
            awake_since = clock()
            last_health_check = 0.0
            health = CanHealth(False, False, "UNKNOWN")
            wake_ready = False
            reported_wait_state = None
            reported_observation_error = None
    finally:
        _close_bus(bus)
