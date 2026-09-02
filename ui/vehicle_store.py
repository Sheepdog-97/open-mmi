"""Purpose-scoped broker for durable vehicle-derived Open MMI state.

Only this service is intended to own durable filesystem write authority for the
manifest-declared service-reminder and trip purposes.  Callers use fixed Unix
socket endpoints: they cannot provide a storage path, filename, or generic
``purpose`` string.  The broker maps each endpoint to one fixed root-owned
storage location and reuses the existing strict document validators/state
transitions for that purpose.
"""

from __future__ import annotations

import argparse
import json
import os
import socketserver
import stat
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from ui.configuration import ConfigurationError
from ui.web_dashboard import service_reminder, trip_a, trip_b, trip_distance

DEFAULT_SOCKET = Path("/run/open-mmi-vehicle-store/store.sock")
DEFAULT_STORAGE_ROOT = Path("/var/lib/open-mmi/vehicle-data")
MAX_REQUEST_BYTES = 32 * 1024

PURPOSES = frozenset({"service-reminder", "trip-a", "trip-b", "trip-distance"})


LEGACY_STATE_FILES = {
    "service-reminder": "service-reminder.json",
    "trip-a": "trip-a.json",
    "trip-b": "trip-b.json",
    "trip-distance": "trip-distance.json",
}


def _document_functions(purpose: str):
    mapping = {
        "service-reminder": (service_reminder.read_document, service_reminder._write_document),
        "trip-a": (trip_a.read_document, trip_a._write_document),
        "trip-b": (trip_b.read_document, trip_b._write_document),
        "trip-distance": (trip_distance.read_document, trip_distance._write_document),
    }
    try:
        return mapping[purpose]
    except KeyError as exc:
        raise VehicleStoreError("vehicle-data persistence purpose is not declared") from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def migrate_legacy_state(legacy_root: Path, storage_root: Path, expected_uid: int) -> dict[str, Any]:
    """Move legacy user-home vehicle state into the root-owned purpose store.

    Migration is deliberately fail-closed.  Symlinks, hard links, unexpected
    ownership/modes, or a conflicting already-migrated document abort the
    operation rather than leaving two durable copies with unclear authority.
    """

    legacy_root = Path(legacy_root)
    storage_root = Path(storage_root)
    _validate_root(storage_root)
    try:
        root_metadata = legacy_root.lstat()
    except FileNotFoundError:
        return {"ok": True, "migrated": []}
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or root_metadata.st_uid != expected_uid
        or root_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise VehicleStoreError("legacy vehicle-data directory is untrusted")

    migrated: list[str] = []
    for purpose, filename in LEGACY_STATE_FILES.items():
        legacy = legacy_root / filename
        try:
            metadata = legacy.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_nlink != 1
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise VehicleStoreError(f"legacy {purpose} state is untrusted")

        reader, writer = _document_functions(purpose)
        legacy_document = reader(legacy)
        destination = purpose_path(storage_root, purpose)
        if destination.exists():
            current_document = reader(destination)
            if current_document != legacy_document:
                raise VehicleStoreError(f"legacy {purpose} state conflicts with the purpose store")
        else:
            writer(legacy_document, destination)
            _validate_root(storage_root)
        legacy.unlink()
        _fsync_directory(legacy_root)
        migrated.append(purpose)

    return {"ok": True, "migrated": migrated}


class VehicleStoreError(RuntimeError):
    """The vehicle-data persistence boundary is unavailable or unsafe."""


def purpose_path(root: Path, purpose: str) -> Path:
    if purpose not in PURPOSES:
        raise VehicleStoreError("vehicle-data persistence purpose is not declared")
    return Path(root) / purpose / "state.json"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    if str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("request must use application/json")
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except ValueError as exc:
        raise ValueError("invalid request length") from exc
    if length < 0 or length > MAX_REQUEST_BYTES:
        raise ValueError("invalid request length")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("request body is incomplete")
    try:
        payload = json.loads(
            body.decode("utf-8") or "{}",
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"invalid JSON number: {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("request body is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request root must be an object")
    return payload


def _exact_empty(payload: Mapping[str, Any]) -> None:
    if payload:
        raise ValueError("status request must not contain fields")


def _send_json(handler: BaseHTTPRequestHandler, payload: Mapping[str, Any], status: int = 200) -> None:
    body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def _validate_root(root: Path) -> None:
    root = Path(root)
    expected_uid = os.geteuid()
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root.chmod(0o700)
    except OSError as exc:
        raise VehicleStoreError("vehicle-data storage root cannot be prepared") from exc
    metadata = root.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise VehicleStoreError("vehicle-data storage root is untrusted")

    for purpose in PURPOSES:
        directory = root / purpose
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        metadata = directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise VehicleStoreError("vehicle-data purpose directory is untrusted")
        directory.chmod(0o700)
        target = purpose_path(root, purpose)
        try:
            target_metadata = target.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(target_metadata.st_mode)
            or stat.S_ISLNK(target_metadata.st_mode)
            or target_metadata.st_uid != expected_uid
            or target_metadata.st_nlink != 1
            or target_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        ):
            raise VehicleStoreError("vehicle-data state file is untrusted")


class VehicleStoreHandler(BaseHTTPRequestHandler):
    server_version = "OpenMMIVehicleStore/1"
    sys_version = ""

    def address_string(self) -> str:
        return "local"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def storage_root(self) -> Path:
        return self.server.storage_root  # type: ignore[attr-defined]

    def _path(self, purpose: str) -> Path:
        _validate_root(self.storage_root)
        return purpose_path(self.storage_root, purpose)

    def do_GET(self) -> None:
        self.send_error(405)

    def do_POST(self) -> None:
        try:
            payload = _read_json(self)
            result = self._dispatch(payload)
            _send_json(self, result)
        except (ValueError, ConfigurationError, VehicleStoreError) as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 400)
        except (OSError, RuntimeError):
            _send_json(self, {"ok": False, "error": "vehicle store operation failed"}, 503)

    def _dispatch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        route: dict[str, tuple[str, Callable[..., dict[str, Any]], bool]] = {
            "/v1/service-reminder/status": ("service-reminder", service_reminder.status_payload, True),
            "/v1/service-reminder/settings": ("service-reminder", service_reminder.update_settings, False),
            "/v1/service-reminder/reset": ("service-reminder", service_reminder.reset_interval, False),
            "/v1/service-reminder/acknowledge": ("service-reminder", service_reminder.acknowledge, False),
            "/v1/trip-a/status": ("trip-a", trip_a.status_payload, True),
            "/v1/trip-a/settings": ("trip-a", trip_a.update_settings, False),
            "/v1/trip-a/reset": ("trip-a", trip_a.reset_trip, False),
            "/v1/trip-a/observe": ("trip-a", trip_a.observe_vehicle, False),
            "/v1/trip-b/status": ("trip-b", trip_b.status_payload, True),
            "/v1/trip-b/reset": ("trip-b", trip_b.reset_trip, False),
            "/v1/trip-distance/status": ("trip-distance", trip_distance.status_payload, True),
            "/v1/trip-distance/observe": ("trip-distance", trip_distance.observe, False),
        }
        selected = route.get(self.path)
        if selected is None:
            raise ValueError("vehicle-data persistence operation is not declared")
        purpose, function, status_only = selected
        path = self._path(purpose)
        if status_only:
            _exact_empty(payload)
            return function(path)
        return function(payload, path)


class VehicleStoreServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, socket_path: Path, storage_root: Path = DEFAULT_STORAGE_ROOT):
        self.socket_path = Path(socket_path)
        self.storage_root = Path(storage_root)
        _validate_root(self.storage_root)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
                raise VehicleStoreError("vehicle store socket path is occupied by an untrusted object")
            self.socket_path.unlink()
        super().__init__(str(self.socket_path), VehicleStoreHandler)
        os.chmod(self.socket_path, 0o660)

    def server_close(self) -> None:
        super().server_close()
        try:
            metadata = self.socket_path.lstat()
            if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.geteuid():
                self.socket_path.unlink()
        except OSError:
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Open MMI purpose-scoped vehicle-data store")
    parser.add_argument("command", choices=("serve", "migrate-legacy"))
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    parser.add_argument("--legacy-root", type=Path)
    parser.add_argument("--legacy-uid", type=int)
    args = parser.parse_args(argv)
    if args.command == "migrate-legacy":
        if args.legacy_root is None or args.legacy_uid is None or args.legacy_uid < 0:
            parser.error("migrate-legacy requires --legacy-root and --legacy-uid")
        print(
            json.dumps(
                migrate_legacy_state(args.legacy_root, args.storage_root, args.legacy_uid),
                sort_keys=True,
            )
        )
        return 0
    with VehicleStoreServer(args.socket, args.storage_root) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
