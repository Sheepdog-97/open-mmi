"""Purpose-scoped external network service for Open MMI media integrations.

This process is the only long-lived Open MMI actor, other than the update
coordinator, that is intended to hold external AF_INET/AF_INET6 authority.
Callers reach it through a group-restricted Unix socket and can request only
fixed Radio/Jellyfin operations.  Normal RPCs never supply an arbitrary
network destination: Radio uses the broker's fixed catalogue policy and
Jellyfin uses root-owned owner configuration loaded through a systemd
credential.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import socket
import socketserver
import stat
import struct
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlparse

from open_mmi_trust.vehicle_identity import (
    RemoteVehicleIdentityDenied,
    require_remote_identity_safe,
)
from ui import media_egress_config
from ui.web_dashboard import jellyfin, radio

DEFAULT_SOCKET = Path("/run/open-mmi-media-egress/egress.sock")
DEFAULT_GROUP = "open-mmi"
MAX_REQUEST_BYTES = 64 * 1024


class MediaEgressError(RuntimeError):
    """The local media egress service could not safely fulfil a request."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as exc:
        raise ValueError("invalid request length") from exc
    if length <= 0 or length > MAX_REQUEST_BYTES:
        raise ValueError("invalid request length")
    if str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("request must use application/json")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("request body is incomplete")
    try:
        payload = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid JSON number: {value}")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("request body is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request root must be an object")
    return payload


def _exact(payload: Mapping[str, Any], keys: set[str], label: str) -> None:
    if set(payload) != keys:
        raise ValueError(f"{label} fields are invalid")


def _jellyfin_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Jellyfin configuration is invalid")
    allowed = {
        "configured", "url", "token", "username", "password", "username_configured",
        "auth_mode", "session_id", "device_name", "user_id", "library_id",
        "allow_global", "insecure_tls",
    }
    _exact(payload, allowed, "Jellyfin configuration")
    config = dict(payload)
    url = str(config.get("url") or "").strip().rstrip("/")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Jellyfin URL must be absolute HTTP(S)")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Jellyfin URL may not contain embedded credentials")
    config["url"] = url
    for key in ("configured", "username_configured", "allow_global", "insecure_tls"):
        if not isinstance(config.get(key), bool):
            raise ValueError(f"Jellyfin {key} flag is invalid")
    for key in (
        "token", "username", "password", "auth_mode", "session_id", "device_name", "user_id", "library_id"
    ):
        value = config.get(key)
        if not isinstance(value, str) or "\0" in value or "\n" in value or "\r" in value:
            raise ValueError(f"Jellyfin {key} value is invalid")
        if len(value.encode("utf-8")) > 4096:
            raise ValueError(f"Jellyfin {key} value is too long")
    expected_configured = bool(url and (config["token"] or config["username_configured"]))
    if config["configured"] != expected_configured:
        raise ValueError("Jellyfin configured state is inconsistent")
    return config


def _broker_radio_config() -> dict[str, Any]:
    """Return the fixed broker-owned Radio Browser policy.

    Dashboard-provided environment/configuration is intentionally ignored so a
    local caller cannot relabel an arbitrary URL as ``media.internet-radio``.
    Private-address streams remain prohibited in v1.
    """

    return {
        "url": radio.RADIO_BROWSER_DEFAULT_URL,
        "user_agent": radio.RADIO_USER_AGENT,
        "catalog_timeout": radio.RADIO_BROWSER_TIMEOUT_SECONDS,
        "stream_timeout": radio.RADIO_STREAM_TIMEOUT_SECONDS,
        "allow_private_streams": False,
    }


def _broker_jellyfin_values() -> dict[str, str]:
    return dict(media_egress_config.read_credential_config()["jellyfin"])


def _broker_jellyfin_config() -> dict[str, Any]:
    return _jellyfin_config(jellyfin._jellyfin_config_from_mapping(_broker_jellyfin_values()))


def _peer_uid(handler: BaseHTTPRequestHandler) -> int:
    try:
        raw = handler.connection.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", raw)
        return int(uid)
    except (AttributeError, OSError, struct.error) as exc:
        raise PermissionError("peer credentials are unavailable") from exc


def _require_root_peer(handler: BaseHTTPRequestHandler) -> None:
    if _peer_uid(handler) != 0:
        raise PermissionError("candidate Jellyfin endpoint testing requires root owner authorization")


def _send_json(handler: BaseHTTPRequestHandler, payload: Mapping[str, Any], status: int = 200) -> None:
    body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


class MediaEgressHandler(BaseHTTPRequestHandler):
    server_version = "OpenMMIMediaEgress/1"
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
            if self.path == "/v1/jellyfin/status":
                _exact(payload, set(), "Jellyfin status request")
                _send_json(
                    self,
                    {"ok": True, "jellyfin": media_egress_config.jellyfin_status(_broker_jellyfin_values())},
                )
                return
            if self.path == "/v1/jellyfin/test":
                _exact(payload, set(), "Jellyfin test request")
                result = jellyfin._jellyfin_test_connection(_broker_jellyfin_config())
                _send_json(self, {"ok": True, "test": result})
                return
            if self.path == "/v1/jellyfin/test-candidate":
                _require_root_peer(self)
                _exact(payload, {"config"}, "Jellyfin candidate test request")
                config = _jellyfin_config(payload["config"])
                result = jellyfin._jellyfin_test_connection(config)
                _send_json(self, {"ok": True, "test": result})
                return
            if self.path == "/v1/media/proxy":
                self._proxy_media(payload)
                return
            self.send_error(404)
        except RemoteVehicleIdentityDenied as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 403)
        except PermissionError as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 403)
        except ValueError as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 400)
        except media_egress_config.MediaEgressConfigError as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 503)
        except (RuntimeError, OSError, TimeoutError) as exc:
            _send_json(self, {"ok": False, "error": str(exc)}, 502)

    def _proxy_media(self, payload: Mapping[str, Any]) -> None:
        _exact(
            payload,
            {"source", "path", "query", "demo_mode", "range"},
            "media proxy request",
        )
        source = str(payload["source"] or "")
        path = str(payload["path"] or "")
        query_text = str(payload["query"] or "")
        if len(path) > 1024 or len(query_text) > 4096:
            raise ValueError("media request target is too long")
        demo_mode = payload["demo_mode"]
        if not isinstance(demo_mode, bool):
            raise ValueError("media demo flag is invalid")
        range_header = str(payload["range"] or "")
        if len(range_header) > 256 or "\n" in range_header or "\r" in range_header:
            raise ValueError("media Range header is invalid")
        if range_header:
            self.headers["Range"] = range_header

        query = parse_qs(query_text, keep_blank_values=True)
        require_remote_identity_safe(
            [path, query_text, range_header, *(item for values in query.values() for item in values)]
        )
        if source == "radio":
            config = _broker_radio_config()
            if path == "/api/radio/status":
                _send_json(self, radio._radio_status_payload(config))
                return
            if path == "/api/radio/options":
                _send_json(self, radio._radio_filter_options_payload(config))
                return
            if path == "/api/radio/search":
                try:
                    limit = int(query.get("limit", ["60"])[0])
                except (TypeError, ValueError):
                    limit = 60
                _send_json(
                    self,
                    radio._radio_search_payload(
                        query.get("q", [""])[0],
                        limit,
                        query.get("filter", ["popular"])[0],
                        country_code=query.get("country", [""])[0],
                        language=query.get("language", [""])[0],
                        config=config,
                    ),
                )
                return
            if path.startswith("/api/radio/stream/"):
                station_id = unquote(path.rsplit("/", 1)[-1])
                radio._radio_proxy_audio(self, station_id, config)
                return
            raise ValueError("radio operation is not declared")

        if source == "jellyfin":
            config = _broker_jellyfin_config()
            if path == "/api/jellyfin/status":
                _send_json(self, jellyfin._jellyfin_status_payload(demo_mode, config))
                return
            if path == "/api/jellyfin/search":
                try:
                    limit = int(query.get("limit", ["24"])[0])
                except (TypeError, ValueError):
                    limit = 24
                _send_json(
                    self,
                    jellyfin._jellyfin_search_payload(
                        query.get("q", [""])[0],
                        limit,
                        query.get("filter", ["recent"])[0],
                        demo_mode,
                        config,
                    ),
                )
                return
            if path.startswith("/api/jellyfin/stream/"):
                item_id = unquote(path.rsplit("/", 1)[-1])
                jellyfin._jellyfin_proxy_audio(self, item_id, config)
                return
            if path.startswith("/api/jellyfin/image/"):
                item_id = unquote(path.rsplit("/", 1)[-1])
                jellyfin._jellyfin_proxy_image(self, item_id, config)
                return
            raise ValueError("Jellyfin operation is not declared")

        raise ValueError("media egress purpose is not declared")


class MediaEgressServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = self.path.lstat()
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not stat.S_ISSOCK(existing.st_mode) or existing.st_uid != os.geteuid():
                raise MediaEgressError("media egress socket path is occupied by an untrusted object")
            self.path.unlink()
        super().__init__(str(self.path), MediaEgressHandler)
        try:
            group = grp.getgrnam(DEFAULT_GROUP).gr_gid
        except KeyError as exc:
            self.server_close()
            raise MediaEgressError("Open MMI access group is unavailable") from exc
        os.chmod(self.path.parent, 0o750)
        os.chown(self.path.parent, -1, group)
        os.chmod(self.path, 0o660)
        os.chown(self.path, -1, group)

    def server_close(self) -> None:
        super().server_close()
        try:
            metadata = self.path.lstat()
            if stat.S_ISSOCK(metadata.st_mode) and metadata.st_uid == os.geteuid():
                self.path.unlink()
        except OSError:
            pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Open MMI purpose-scoped media egress service")
    parser.add_argument("command", choices=("serve",))
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    args = parser.parse_args(argv)
    with MediaEgressServer(args.socket) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
