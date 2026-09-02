"""Unix-socket client for fixed owner-configuration operations."""

from __future__ import annotations

import http.client
import json
import os
import socket
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SOCKET = (
    Path(os.getenv("XDG_RUNTIME_DIR", "/tmp"))
    / "open-mmi-owner-config"
    / "config.sock"
)
MAX_RESPONSE_BYTES = 512 * 1024


class OwnerConfigClientError(RuntimeError):
    """The owner-configuration broker rejected or could not serve a request."""


class OwnerConfigConflictError(OwnerConfigClientError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def socket_path() -> Path:
    override = os.getenv("OPEN_MMI_OWNER_CONFIG_SOCKET", "").strip()
    return Path(override) if override else DEFAULT_SOCKET


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: Path, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self._path = Path(path)

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.settimeout(self.timeout)
            connection.connect(str(self._path))
        except Exception:
            connection.close()
            raise
        self.sock = connection


def request_json(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    connection = _UnixHTTPConnection(socket_path(), timeout)
    try:
        connection.request(
            "POST",
            endpoint,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        encoded = response.read(MAX_RESPONSE_BYTES + 1)
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise OwnerConfigClientError("owner configuration response is too large")
        decoded = json.loads(encoded.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise OwnerConfigClientError("owner configuration returned an invalid response")
        if response.status == 409:
            raise OwnerConfigConflictError(
                str(decoded.get("error") or "owner configuration conflict"),
                str(decoded.get("code") or "owner-config-conflict"),
            )
        if response.status >= 400 or decoded.get("ok") is False:
            raise OwnerConfigClientError(
                str(decoded.get("error") or f"owner configuration HTTP {response.status}")
            )
        return decoded
    except (
        OSError,
        TimeoutError,
        http.client.HTTPException,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        raise OwnerConfigClientError("owner configuration broker is unavailable") from exc
    finally:
        connection.close()


def update_launcher(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/launcher/update", payload)


def create_custom(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/vehicle-custom/create", payload)


def save_custom(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/vehicle-custom/save", payload)


def manage_custom(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/vehicle-custom/manage", payload)


def import_custom(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/vehicle-custom/import", payload)
