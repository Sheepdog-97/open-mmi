#!/usr/bin/env python3
"""Independent live CAN trust test for Open MMI.

This program intentionally imports no Open MMI Python package.

It provides two independent measurements:

* production: verify a live physical SocketCAN interface explicitly reports
  LISTEN-ONLY;
* challenge: exercise the installed canbusd implementation as a black box on
  an already-created isolated vcanN interface.

The challenge is receive-side only from Open MMI's perspective.  The checker
owns the transmitting socket.  Open MMI is never given a CAN transmit action
or challenge-response protocol.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence


PASS = "PASS"
FAIL = "FAIL"
UNVERIFIED = "UNVERIFIED"
STATUSES = {PASS, FAIL, UNVERIFIED}

INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
VCAN_RE = re.compile(r"^vcan[0-9]{1,3}$")

CAN_FRAME = struct.Struct("=IB3x8s")
CAN_ID_MASK = 0x1FFFFFFF
CHALLENGE_STEPS = 16
MAX_IP_OUTPUT = 512 * 1024
MAX_STATUS_BYTES = 256 * 1024


class CanTrustError(RuntimeError):
    pass


class EvidenceUnavailable(CanTrustError):
    pass


def check(check_id: str, status: str, summary: str, **evidence: Any) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(status)
    return {
        "id": check_id,
        "status": status,
        "summary": summary,
        "evidence": evidence,
    }


def overall_status(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(item.get("status")) for item in checks}
    if FAIL in statuses:
        return FAIL
    if UNVERIFIED in statuses:
        return UNVERIFIED
    return PASS


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def validate_interface(value: str, *, vcan: bool = False) -> str:
    expression = VCAN_RE if vcan else INTERFACE_RE
    if not isinstance(value, str) or not expression.fullmatch(value):
        raise CanTrustError("CAN interface name is invalid")
    return value


def trusted_ip_program(explicit: str | None = None) -> Path:
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit))
    else:
        for value in (
            "/usr/sbin/ip",
            "/usr/bin/ip",
            "/sbin/ip",
            "/bin/ip",
        ):
            candidates.append(Path(value))

        resolved = shutil.which("ip")
        if resolved:
            candidate = Path(resolved)
            if candidate not in candidates:
                candidates.append(candidate)

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            metadata = resolved.stat()
        except OSError:
            continue

        if (
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_uid == 0
            and not metadata.st_mode & 0o022
            and metadata.st_mode & 0o111
        ):
            return resolved

    raise EvidenceUnavailable("trusted system ip executable is unavailable")


def ip_link_document(ip_program: Path, interface: str) -> Any:
    try:
        result = subprocess.run(
            [
                str(ip_program),
                "-details",
                "-json",
                "link",
                "show",
                "dev",
                interface,
            ],
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EvidenceUnavailable("could not inspect live CAN interface") from exc

    if len(result.stdout) + len(result.stderr) > MAX_IP_OUTPUT:
        raise CanTrustError("ip output exceeds safety limit")

    if result.returncode != 0:
        raise EvidenceUnavailable("live CAN interface cannot be inspected")

    try:
        return json.loads(result.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceUnavailable("ip did not return usable JSON") from exc


def one_link(document: Any, interface: str) -> Mapping[str, Any]:
    if not isinstance(document, list) or len(document) != 1:
        raise EvidenceUnavailable("live interface evidence is missing or ambiguous")

    item = document[0]
    if not isinstance(item, Mapping) or item.get("ifname") != interface:
        raise EvidenceUnavailable("live interface evidence does not identify requested interface")

    return item


def production_listen_only_check(document: Any, interface: str) -> dict[str, Any]:
    try:
        item = one_link(document, interface)
        linkinfo = item.get("linkinfo")
        if not isinstance(linkinfo, Mapping):
            raise EvidenceUnavailable("SocketCAN link metadata is unavailable")

        if linkinfo.get("info_kind") != "can":
            return check(
                "can.production-listen-only",
                FAIL,
                "Configured production interface is not a physical CAN link.",
                interface=interface,
                info_kind=linkinfo.get("info_kind"),
            )

        info_data = linkinfo.get("info_data")
        if not isinstance(info_data, Mapping):
            raise EvidenceUnavailable("SocketCAN controller metadata is unavailable")

        ctrlmode = info_data.get("ctrlmode")
        if ctrlmode is None:
            raise EvidenceUnavailable(
                "SocketCAN driver/iproute2 did not expose controller mode"
            )

        if (
            not isinstance(ctrlmode, list)
            or any(not isinstance(value, str) for value in ctrlmode)
        ):
            raise EvidenceUnavailable("SocketCAN controller mode evidence is malformed")

        normalized = sorted(set(ctrlmode))

        if "LISTEN-ONLY" not in normalized:
            return check(
                "can.production-listen-only",
                FAIL,
                "Production SocketCAN interface is measurably not in listen-only mode.",
                interface=interface,
                ctrlmode=normalized,
            )

        return check(
            "can.production-listen-only",
            PASS,
            "Production SocketCAN interface explicitly reports LISTEN-ONLY.",
            interface=interface,
            ctrlmode=normalized,
        )

    except EvidenceUnavailable as exc:
        return check(
            "can.production-listen-only",
            UNVERIFIED,
            "Production CAN listen-only state could not be independently established.",
            interface=interface,
            reason=str(exc),
        )


def vcan_interface_check(document: Any, interface: str) -> dict[str, Any]:
    try:
        item = one_link(document, interface)
        linkinfo = item.get("linkinfo")
        if (
            not isinstance(linkinfo, Mapping)
            or linkinfo.get("info_kind") != "vcan"
        ):
            raise EvidenceUnavailable(
                "challenge interface is not independently identified as vcan"
            )

        return check(
            "can.challenge-isolation",
            PASS,
            "Challenge interface is an explicitly selected virtual CAN device.",
            interface=interface,
            info_kind="vcan",
        )
    except EvidenceUnavailable as exc:
        return check(
            "can.challenge-isolation",
            UNVERIFIED,
            "Challenge interface isolation could not be established.",
            interface=interface,
            reason=str(exc),
        )


def pack_can_frame(can_id: int, payload: bytes) -> bytes:
    if not 0 <= can_id <= 0x7FF:
        raise CanTrustError("challenge CAN id must be an 11-bit identifier")
    if not 1 <= len(payload) <= 8:
        raise CanTrustError("challenge CAN payload length is invalid")

    return CAN_FRAME.pack(
        can_id,
        len(payload),
        payload.ljust(8, b"\x00"),
    )


def unpack_can_frame(raw: bytes) -> tuple[int, bytes]:
    if len(raw) != CAN_FRAME.size:
        raise CanTrustError("unexpected SocketCAN frame size")

    can_id, dlc, payload = CAN_FRAME.unpack(raw)
    if dlc > 8:
        raise CanTrustError("unexpected CAN DLC")

    return can_id & CAN_ID_MASK, payload[:dlc]


def make_challenge() -> dict[str, Any]:
    values: list[int] = []
    while len(values) < CHALLENGE_STEPS:
        value = secrets.randbelow(256)
        if value not in values:
            values.append(value)

    challenge = {
        "schema_version": 1,
        "can_id": 0x500 + secrets.randbelow(0x100),
        "values": values,
    }
    challenge["digest"] = sha256_bytes(canonical_json(challenge))
    return challenge


def challenge_profile(interface: str, can_id: int) -> dict[str, Any]:
    return {
        "default_bus": "trust-challenge",
        "can_buses": {
            "trust-challenge": {
                "interface": interface,
                "provisioning": "manual",
            }
        },
        "rules": [],
        "presence": [],
        "status": [
            {
                "id": f"0x{can_id:X}",
                "bus": "trust-challenge",
                "byte": 0,
                "type": "raw",
                "path": "engine.speed_raw",
            }
        ],
    }


def read_status(path: Path) -> Mapping[str, Any] | None:
    try:
        metadata = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CanTrustError("challenge status file cannot be inspected") from exc

    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > MAX_STATUS_BYTES
    ):
        raise CanTrustError("challenge status file metadata is unsafe")

    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None

    return payload if isinstance(payload, Mapping) else None


def nested_status_value(payload: Mapping[str, Any] | None) -> int | None:
    if not isinstance(payload, Mapping):
        return None

    state = payload.get("state")
    if not isinstance(state, Mapping):
        return None

    engine = state.get("engine")
    if not isinstance(engine, Mapping):
        return None

    value = engine.get("speed_raw")
    if isinstance(value, bool) or not isinstance(value, int):
        return None

    return value


def runtime_ready(
    payload: Mapping[str, Any] | None,
    interface: str,
) -> bool:
    if not isinstance(payload, Mapping):
        return False

    runtime = payload.get("runtime")
    return (
        isinstance(runtime, Mapping)
        and runtime.get("state") == "ready"
        and runtime.get("interface") == interface
        and runtime.get("active_bus") == "trust-challenge"
    )


def drain_frames(sock: socket.socket, duration: float) -> list[tuple[int, bytes]]:
    frames: list[tuple[int, bytes]] = []
    deadline = time.monotonic() + duration

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        sock.settimeout(min(0.05, remaining))
        try:
            raw = sock.recv(CAN_FRAME.size)
        except socket.timeout:
            continue
        except OSError as exc:
            raise EvidenceUnavailable("independent CAN observation failed") from exc

        frames.append(unpack_can_frame(raw))

    return frames


def classify_challenge_observation(
    challenge: Mapping[str, Any],
    observed_status_values: Sequence[int],
    observed_frames: Sequence[tuple[int, bytes]],
) -> dict[str, Any]:
    can_id = int(challenge["can_id"])
    values = [int(value) for value in challenge["values"]]

    expected_frames = [
        (can_id, bytes([value]))
        for value in values
    ]

    evidence = {
        "challenge_digest": challenge["digest"],
        "can_id": f"0x{can_id:X}",
        "steps": len(values),
        "observed_status_values": list(observed_status_values),
        "expected_frame_count": len(expected_frames),
        "observed_frame_count": len(observed_frames),
        "observed_frames_sha256": sha256_bytes(
            canonical_json(
                [
                    [frame_id, payload.hex()]
                    for frame_id, payload in observed_frames
                ]
            )
        ),
    }

    if list(observed_status_values) != values:
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "The target did not reproduce the fresh receive-side challenge exactly.",
            **evidence,
        )

    if list(observed_frames) != expected_frames:
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "The isolated challenge bus contained missing, duplicate, or additional traffic.",
            **evidence,
        )

    return check(
        "can.challenge-bound-observation",
        PASS,
        "The target consumed the fresh challenge while independent observation saw only checker-injected frames.",
        **evidence,
    )


def target_environment(
    temporary: Path,
    interface: str,
    profile_path: Path,
    bindings_path: Path,
    status_path: Path,
) -> dict[str, str]:
    env = dict(os.environ)

    for key in list(env):
        if key.startswith("OPEN_MMI_"):
            env.pop(key, None)

    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)

    home = temporary / "home"
    runtime = temporary / "runtime"
    config = temporary / "config"
    home.mkdir(mode=0o700)
    runtime.mkdir(mode=0o700)
    config.mkdir(mode=0o700)

    env.update(
        {
            "HOME": str(home),
            "XDG_RUNTIME_DIR": str(runtime),
            "OPEN_MMI_VEHICLE": "independent-can-trust-challenge",
            "OPEN_MMI_BINDINGS": "independent-can-trust-challenge",
            "OPEN_MMI_VEHICLE_CONFIG": str(profile_path),
            "OPEN_MMI_BINDINGS_FILE": str(bindings_path),
            "OPEN_MMI_CAN_BUS": "trust-challenge",
            "OPEN_MMI_CAN_INTERFACE": interface,
            "OPEN_MMI_STATUS_PATH": str(status_path),
            "OPEN_MMI_CONFIG_DIR": str(config),
            "OPEN_MMI_LOG_LEVEL": "WARNING",
            "PYTHONUNBUFFERED": "1",
        }
    )

    return env


def run_challenge(
    interface: str,
    *,
    target_python: Path,
    target_working_directory: Path,
    startup_timeout: float,
    step_timeout: float,
) -> dict[str, Any]:
    validate_interface(interface, vcan=True)

    sys_path = Path("/sys/class/net") / interface
    try:
        resolved = sys_path.resolve(strict=True)
    except OSError:
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "Challenge vcan interface is unavailable.",
            interface=interface,
        )

    if "/virtual/net/" not in str(resolved):
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "Challenge interface is not a kernel virtual network device.",
            interface=interface,
            resolved=str(resolved),
        )

    if not target_python.is_file() or not os.access(target_python, os.X_OK):
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "Target Open MMI Python executable is unavailable.",
            target_python=str(target_python),
        )

    if not target_working_directory.is_dir():
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "Target Open MMI working directory is unavailable.",
            target_working_directory=str(target_working_directory),
        )

    if not all(
        hasattr(socket, name)
        for name in ("PF_CAN", "CAN_RAW")
    ):
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "This Python/kernel does not expose raw SocketCAN.",
        )

    challenge = make_challenge()

    try:
        sender = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        monitor = socket.socket(socket.PF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        sender.bind((interface,))
        monitor.bind((interface,))
    except OSError as exc:
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "Raw SocketCAN challenge sockets could not be opened.",
            error=str(exc),
        )

    process: subprocess.Popen[bytes] | None = None

    try:
        preexisting = drain_frames(monitor, 0.25)
        if preexisting:
            return check(
                "can.challenge-bound-observation",
                UNVERIFIED,
                "Challenge interface was not quiet before the target started.",
                observed_frame_count=len(preexisting),
            )

        with tempfile.TemporaryDirectory(
            prefix="open-mmi-independent-can-"
        ) as directory:
            temporary = Path(directory)
            profile_path = temporary / "challenge-profile.json"
            bindings_path = temporary / "challenge-bindings.json"
            status_path = temporary / "status.json"
            stdout_path = temporary / "target.stdout"
            stderr_path = temporary / "target.stderr"

            profile_path.write_text(
                json.dumps(
                    challenge_profile(
                        interface,
                        int(challenge["can_id"]),
                    ),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            bindings_path.write_text("{}\n", encoding="utf-8")

            env = target_environment(
                temporary,
                interface,
                profile_path,
                bindings_path,
                status_path,
            )

            with (
                stdout_path.open("wb") as stdout,
                stderr_path.open("wb") as stderr,
            ):
                try:
                    process = subprocess.Popen(
                        [
                            str(target_python),
                            "-m",
                            "canbusd.core",
                        ],
                        cwd=str(target_working_directory),
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                    )
                except OSError as exc:
                    return check(
                        "can.challenge-bound-observation",
                        UNVERIFIED,
                        "Target Open MMI daemon could not be started for isolated challenge.",
                        error=str(exc),
                    )

                deadline = time.monotonic() + startup_timeout
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        return check(
                            "can.challenge-bound-observation",
                            UNVERIFIED,
                            "Target Open MMI daemon exited before challenge readiness.",
                            returncode=process.returncode,
                        )

                    if runtime_ready(read_status(status_path), interface):
                        break

                    time.sleep(0.02)
                else:
                    return check(
                        "can.challenge-bound-observation",
                        UNVERIFIED,
                        "Target Open MMI daemon did not publish ready challenge runtime.",
                    )

                ambient = drain_frames(monitor, 0.15)
                if ambient:
                    return check(
                        "can.challenge-bound-observation",
                        UNVERIFIED,
                        "Challenge interface was not quiet immediately before injection.",
                        observed_frame_count=len(ambient),
                    )

                observed_status_values: list[int] = []
                observed_frames: list[tuple[int, bytes]] = []

                for value in challenge["values"]:
                    frame = pack_can_frame(
                        int(challenge["can_id"]),
                        bytes([int(value)]),
                    )

                    try:
                        sender.send(frame)
                    except OSError as exc:
                        return check(
                            "can.challenge-bound-observation",
                            UNVERIFIED,
                            "Independent checker could not inject challenge frame.",
                            error=str(exc),
                        )

                    step_deadline = time.monotonic() + step_timeout
                    observed_value: int | None = None

                    while time.monotonic() < step_deadline:
                        if process.poll() is not None:
                            return check(
                                "can.challenge-bound-observation",
                                UNVERIFIED,
                                "Target Open MMI daemon exited during challenge.",
                                returncode=process.returncode,
                            )

                        observed_value = nested_status_value(
                            read_status(status_path)
                        )
                        if observed_value == value:
                            break

                        time.sleep(0.01)

                    if observed_value != value:
                        return check(
                            "can.challenge-bound-observation",
                            UNVERIFIED,
                            "Target did not consume one fresh challenge transition.",
                            challenge_digest=challenge["digest"],
                            expected_value=value,
                            observed_value=observed_value,
                            completed_steps=len(observed_status_values),
                        )

                    observed_status_values.append(observed_value)
                    observed_frames.extend(
                        drain_frames(monitor, 0.04)
                    )

                observed_frames.extend(
                    drain_frames(monitor, 0.25)
                )

                return classify_challenge_observation(
                    challenge,
                    observed_status_values,
                    observed_frames,
                )

    except (CanTrustError, EvidenceUnavailable) as exc:
        return check(
            "can.challenge-bound-observation",
            UNVERIFIED,
            "Independent CAN challenge evidence could not be completed.",
            reason=str(exc),
        )

    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)

        sender.close()
        monitor.close()


def inspect(args: argparse.Namespace) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    try:
        ip_program = trusted_ip_program(args.ip_program)
    except EvidenceUnavailable as exc:
        ip_program = None
        if args.mode in {"full", "production"}:
            checks.append(
                check(
                    "can.production-listen-only",
                    UNVERIFIED,
                    "Production CAN listen-only state could not be independently established.",
                    reason=str(exc),
                )
            )
        if args.mode in {"full", "challenge"}:
            checks.append(
                check(
                    "can.challenge-isolation",
                    UNVERIFIED,
                    "Challenge interface type could not be independently established.",
                    reason=str(exc),
                )
            )

    if ip_program is not None and args.mode in {"full", "production"}:
        try:
            interface = validate_interface(args.production_interface)
            document = ip_link_document(ip_program, interface)
            checks.append(
                production_listen_only_check(
                    document,
                    interface,
                )
            )
        except (CanTrustError, EvidenceUnavailable) as exc:
            checks.append(
                check(
                    "can.production-listen-only",
                    UNVERIFIED,
                    "Production CAN listen-only state could not be independently established.",
                    reason=str(exc),
                )
            )

    challenge_isolated = True

    if ip_program is not None and args.mode in {"full", "challenge"}:
        try:
            interface = validate_interface(
                args.challenge_interface,
                vcan=True,
            )
            document = ip_link_document(ip_program, interface)
            isolation = vcan_interface_check(document, interface)
            checks.append(isolation)
            challenge_isolated = isolation["status"] == PASS
        except (CanTrustError, EvidenceUnavailable) as exc:
            challenge_isolated = False
            checks.append(
                check(
                    "can.challenge-isolation",
                    UNVERIFIED,
                    "Challenge interface type could not be independently established.",
                    reason=str(exc),
                )
            )

    if args.mode in {"full", "challenge"}:
        if not challenge_isolated:
            checks.append(
                check(
                    "can.challenge-bound-observation",
                    UNVERIFIED,
                    "Challenge was not run because isolated vcan evidence was unavailable.",
                )
            )
        else:
            checks.append(
                run_challenge(
                    args.challenge_interface,
                    target_python=Path(args.target_python).resolve(),
                    target_working_directory=Path(
                        args.target_working_directory
                    ).resolve(),
                    startup_timeout=args.startup_timeout,
                    step_timeout=args.step_timeout,
                )
            )

    return {
        "checker": "open-mmi-independent-can-trust-test-v1",
        "mode": args.mode,
        "overall_status": overall_status(checks),
        "checks": checks,
        "note": (
            "PASS requires positive fresh receive-side challenge evidence; "
            "absence of CAN traffic alone is never sufficient."
        ),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Independent live Open MMI CAN trust test"
    )
    parser.add_argument(
        "--mode",
        choices=("full", "production", "challenge"),
        default="full",
    )
    parser.add_argument(
        "--production-interface",
        default="can0",
    )
    parser.add_argument(
        "--challenge-interface",
        default="vcan99",
    )
    parser.add_argument(
        "--target-python",
        default="/opt/open-mmi/venv/bin/python",
    )
    parser.add_argument(
        "--target-working-directory",
        default="/opt/open-mmi",
    )
    parser.add_argument(
        "--ip-program",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=5.0,
    )
    parser.add_argument(
        "--step-timeout",
        type=float,
        default=1.0,
    )
    parser.add_argument(
        "--json",
        action="store_true",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.startup_timeout <= 0 or args.step_timeout <= 0:
        raise SystemExit("timeouts must be positive")

    report = inspect(args)

    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"Overall: {report['overall_status']}")
        for item in report["checks"]:
            print(
                f"{item['status']:10s} "
                f"{item['id']}: "
                f"{item['summary']}"
            )
        print(report["note"])

    return {
        PASS: 0,
        FAIL: 1,
        UNVERIFIED: 2,
    }[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
