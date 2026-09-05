"""Privileged read-only Trust Inspector status boundary.

The dashboard is intentionally unprivileged and cannot read root-owned trust
state directly.  This coordinator exposes one fixed local Unix-socket action:
return a fresh Trust Inspector report collected as root.  It has no mutation
operations, caller-selected paths, commands, references, or network access.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import socket
import socketserver
import stat
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Sequence

from open_mmi_trust.inspector import FAIL, PASS, UNVERIFIED, inspect_system


API_VERSION = 1
DEFAULT_SOCKET = Path("/run/open-mmi/trust-status.sock")
DEFAULT_GROUP = "open-mmi-update"
MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_BYTES = 512 * 1024
DEFAULT_TIMEOUT_SECONDS = 5.0
_VALID_STATUSES = frozenset({PASS, FAIL, UNVERIFIED})


class TrustStatusCoordinatorError(RuntimeError):
    """The fixed read-only trust status boundary failed closed."""


class TrustStatusUnavailableError(TrustStatusCoordinatorError):
    """The local privileged trust status service could not be reached."""


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> Dict[str, Any]:
    value: Dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TrustStatusCoordinatorError(f"Duplicate trust status JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise TrustStatusCoordinatorError(f"Invalid trust status JSON number: {value}")


def _status_payload(
    inspector: Callable[[], Mapping[str, Any]] = inspect_system,
) -> Dict[str, Any]:
    """Collect and validate one fresh privileged Inspector report."""

    try:
        report = inspector()
    except Exception:
        return {
            "ok": False,
            "api_version": API_VERSION,
            "status": UNVERIFIED,
            "report": None,
            "error": "Privileged trust inspection evidence is unavailable.",
        }

    if not isinstance(report, Mapping) or report.get("status") not in _VALID_STATUSES:
        return {
            "ok": False,
            "api_version": API_VERSION,
            "status": UNVERIFIED,
            "report": None,
            "error": "Privileged trust inspection evidence is malformed.",
        }

    return {
        "ok": True,
        "api_version": API_VERSION,
        "status": str(report["status"]),
        "report": dict(report),
        "error": None,
    }


def response_for_request(
    payload: object,
    *,
    inspector: Callable[[], Mapping[str, Any]] = inspect_system,
) -> Dict[str, Any]:
    """Handle the only supported request: exact read-only status."""

    if not isinstance(payload, dict):
        return {"ok": False, "error": "Invalid trust status request schema"}
    if set(payload) != {"api_version", "action"}:
        return {"ok": False, "error": "Invalid trust status request schema"}
    if payload.get("api_version") != API_VERSION:
        return {"ok": False, "error": "Unsupported trust status API version"}
    if payload.get("action") != "status":
        return {"ok": False, "error": "Trust status action is not enabled"}
    return _status_payload(inspector)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            response = {"ok": False, "error": "Invalid trust status request size"}
        else:
            try:
                payload: object = json.loads(
                    raw.decode("utf-8"),
                    object_pairs_hook=_unique_json_object,
                    parse_constant=_reject_json_constant,
                )
            except (UnicodeError, json.JSONDecodeError, TrustStatusCoordinatorError):
                payload = None
            response = response_for_request(payload, inspector=self.server.inspector)  # type: ignore[attr-defined]

        try:
            encoded = (json.dumps(response, sort_keys=True) + "\n").encode("utf-8")
            if len(encoded) > MAX_RESPONSE_BYTES:
                encoded = (
                    json.dumps(
                        {"ok": False, "error": "Trust status response exceeds the size limit"},
                        sort_keys=True,
                    )
                    + "\n"
                ).encode("utf-8")
            self.wfile.write(encoded)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class TrustStatusServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        socket_path: Path = DEFAULT_SOCKET,
        *,
        inspector: Callable[[], Mapping[str, Any]] = inspect_system,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.inspector = inspector
        self.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)

        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            existing = None
        expected_uid = 0 if self.socket_path == DEFAULT_SOCKET else os.geteuid()
        if existing is not None:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != expected_uid:
                raise TrustStatusCoordinatorError(
                    "Trust status socket path is occupied by an untrusted object"
                )
            self.socket_path.unlink()

        super().__init__(str(self.socket_path), _Handler)
        os.chmod(self.socket_path, 0o660)
        if self.socket_path == DEFAULT_SOCKET:
            try:
                group = grp.getgrnam(DEFAULT_GROUP)
            except KeyError as exc:
                self.server_close()
                raise TrustStatusCoordinatorError(
                    "Trust status access group is unavailable"
                ) from exc
            try:
                os.chown(self.socket_path, 0, group.gr_gid)
            except OSError as exc:
                self.server_close()
                raise TrustStatusCoordinatorError(
                    "Trust status socket permissions could not be established"
                ) from exc

    def server_close(self) -> None:
        super().server_close()
        try:
            metadata = self.socket_path.lstat()
        except OSError:
            return
        if stat.S_ISSOCK(metadata.st_mode):
            try:
                self.socket_path.unlink()
            except OSError:
                pass


def _client_request(
    payload: Mapping[str, Any],
    socket_path: Path = DEFAULT_SOCKET,
) -> Dict[str, Any]:
    request = (json.dumps(dict(payload), sort_keys=True) + "\n").encode("utf-8")
    if len(request) > MAX_REQUEST_BYTES:
        raise TrustStatusCoordinatorError("Trust status request exceeds the size limit")

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(DEFAULT_TIMEOUT_SECONDS)
    try:
        client.connect(str(socket_path))
        client.sendall(request)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = client.recv(min(65536, MAX_RESPONSE_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise TrustStatusCoordinatorError("Trust status response exceeds the size limit")
            if b"\n" in chunk:
                break
    except (OSError, TimeoutError) as exc:
        raise TrustStatusUnavailableError(
            "Privileged trust status service is unavailable"
        ) from exc
    finally:
        client.close()

    raw = b"".join(chunks)
    if not raw or b"\n" not in raw:
        raise TrustStatusCoordinatorError("Trust status response is incomplete")
    line, trailing = raw.split(b"\n", 1)
    if trailing:
        raise TrustStatusCoordinatorError("Trust status response framing is invalid")
    try:
        response = json.loads(
            line.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, TrustStatusCoordinatorError) as exc:
        raise TrustStatusCoordinatorError("Trust status response is invalid") from exc
    if not isinstance(response, dict):
        raise TrustStatusCoordinatorError("Trust status response schema is invalid")
    return response


def client_status(socket_path: Path = DEFAULT_SOCKET) -> Dict[str, Any]:
    return _client_request(
        {"api_version": API_VERSION, "action": "status"},
        socket_path,
    )


def serve(socket_path: Path = DEFAULT_SOCKET) -> None:
    if socket_path == DEFAULT_SOCKET and os.geteuid() != 0:
        raise TrustStatusCoordinatorError("Production trust status service requires root")
    with TrustStatusServer(socket_path) as server:
        server.serve_forever(poll_interval=0.5)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ui.trust_status_coordinator",
        description="Privileged read-only Open MMI trust status boundary",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="serve the fixed local read-only status protocol")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            serve()
            return 0
        raise TrustStatusCoordinatorError("Trust status action is not enabled")
    except TrustStatusCoordinatorError as exc:
        print(f"open-mmi-trust-status: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
