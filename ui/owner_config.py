"""Fixed-operation owner-configuration broker for the sandboxed dashboard.

This user service is intentionally separate from vehicle-data persistence.  It
may write only the owner's Open MMI configuration roots, and callers cannot
supply filesystem paths.  Vehicle-derived durable state belongs exclusively to
``ui.vehicle_store``.
"""

from __future__ import annotations

import argparse
import json
import os
import socketserver
import stat
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ui import launcher, vehicle_catalogue, vehicle_setup
from ui.configuration import ConfigurationError

DEFAULT_SOCKET = Path(os.getenv("XDG_RUNTIME_DIR", "/tmp")) / "open-mmi-owner-config" / "config.sock"
MAX_REQUEST_BYTES = vehicle_setup.MAX_PROFILE_BYTES * 6 + 32 * 1024


class OwnerConfigError(RuntimeError):
    pass


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
        payload = json.loads(body.decode("utf-8") or "{}", object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("request body is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request root must be an object")
    return payload


def _send_json(handler: BaseHTTPRequestHandler, payload: Mapping[str, Any], status: int = 200) -> None:
    body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def update_launcher(payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"default_ui", "open_at_login"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigurationError(f"unsupported launcher settings: {', '.join(unknown)}")
    updates: dict[str, Any] = {}
    changed = False
    if "default_ui" in payload:
        selected = str(payload["default_ui"] or "").strip().lower()
        if selected not in {"web", "tui"}:
            raise ConfigurationError("default_ui must be web or tui")
        updates["default_ui"] = selected
        changed = True
    if "open_at_login" in payload:
        enabled = payload["open_at_login"]
        if not isinstance(enabled, bool):
            raise ConfigurationError("open_at_login must be true or false")
        launcher.configure_open_at_login(enabled)
        changed = True
    if not changed:
        raise ConfigurationError("no launcher settings were supplied")
    if updates:
        launcher.save_preferences(updates)
    path = launcher.default_config_path()
    return {"ok": True, "launcher": launcher.status_payload(launcher.load_config(path), path)}


class OwnerConfigHandler(BaseHTTPRequestHandler):
    server_version = "OpenMMIOwnerConfig/1"
    sys_version = ""

    def address_string(self) -> str:
        return "local"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self.send_error(405)

    def do_POST(self) -> None:
        try:
            payload = _read_json(self)
            routes = {
                "/v1/launcher/update": update_launcher,
                "/v1/vehicle-custom/create": vehicle_catalogue.copy_maintained_template,
                "/v1/vehicle-custom/save": vehicle_catalogue.save_custom_item,
                "/v1/vehicle-custom/manage": vehicle_catalogue.manage_custom_item,
                "/v1/vehicle-custom/import": vehicle_catalogue.import_custom_item,
            }
            function = routes.get(self.path)
            if function is None:
                raise ValueError("owner-configuration operation is not declared")
            _send_json(self, function(payload))
        except vehicle_catalogue.VehicleCatalogueConflictError as exc:
            _send_json(self, {"ok": False, "code": exc.code, "error": str(exc)}, 409)
        except (ValueError, ConfigurationError, vehicle_catalogue.VehicleCatalogueError) as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 400)
        except (OSError, RuntimeError):
            _send_json(self, {"ok": False, "error": "owner configuration operation failed"}, 503)


class OwnerConfigServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, socket_path: Path = DEFAULT_SOCKET):
        self.socket_path = Path(socket_path)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = self.socket_path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
                raise OwnerConfigError("owner configuration socket is occupied by an untrusted object")
            self.socket_path.unlink()
        super().__init__(str(self.socket_path), OwnerConfigHandler)
        os.chmod(self.socket_path, 0o600)

    def server_close(self) -> None:
        super().server_close()
        try:
            metadata = self.socket_path.lstat()
            if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.geteuid():
                self.socket_path.unlink()
        except OSError:
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Open MMI owner-configuration broker")
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    args = parser.parse_args(argv)
    with OwnerConfigServer(args.socket) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
