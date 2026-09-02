"""Client for the purpose-scoped Open MMI vehicle-data persistence broker.

The dashboard can request only fixed persistence operations over a Unix-domain
socket.  No caller supplies a filesystem path, filename, or arbitrary purpose;
the broker maps each endpoint to one Trust Manifest persistence purpose and a
fixed root-owned state file.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SOCKET = Path("/run/open-mmi-vehicle-store/store.sock")
MAX_REQUEST_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 128 * 1024


class VehicleStoreClientError(RuntimeError):
    """The local vehicle-data persistence broker rejected or failed a request."""


def socket_path() -> Path:
    override = os.getenv("OPEN_MMI_VEHICLE_STORE_SOCKET", "").strip()
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
    payload: Mapping[str, Any] | None = None,
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    request_payload = dict(payload or {})
    try:
        body = json.dumps(request_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise VehicleStoreClientError("vehicle store request is not JSON serializable") from exc
    if len(body) > MAX_REQUEST_BYTES:
        raise VehicleStoreClientError("vehicle store request is too large")

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
        declared = response.getheader("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_RESPONSE_BYTES:
                    raise VehicleStoreClientError("vehicle store response is too large")
            except ValueError as exc:
                raise VehicleStoreClientError("vehicle store response length is invalid") from exc
        encoded = response.read(MAX_RESPONSE_BYTES + 1)
        if len(encoded) > MAX_RESPONSE_BYTES:
            raise VehicleStoreClientError("vehicle store response is too large")
        try:
            decoded = json.loads(encoded.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise VehicleStoreClientError("vehicle store returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise VehicleStoreClientError("vehicle store returned an invalid response")
        if response.status >= 400 or decoded.get("ok") is False:
            raise VehicleStoreClientError(
                str(decoded.get("error") or f"vehicle store HTTP {response.status}")
            )
        return decoded
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        raise VehicleStoreClientError("vehicle store is unavailable") from exc
    finally:
        connection.close()


def service_reminder_status() -> dict[str, Any]:
    return request_json("/v1/service-reminder/status")


def service_reminder_settings(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/service-reminder/settings", payload)


def service_reminder_reset(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/service-reminder/reset", payload)


def service_reminder_acknowledge(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/service-reminder/acknowledge", payload)


def trip_a_status() -> dict[str, Any]:
    return request_json("/v1/trip-a/status")


def trip_a_settings(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/trip-a/settings", payload)


def trip_a_reset(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/trip-a/reset", payload)


def trip_a_observe(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/trip-a/observe", payload)


def trip_b_status() -> dict[str, Any]:
    return request_json("/v1/trip-b/status")


def trip_b_reset(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/trip-b/reset", payload)


def trip_distance_status() -> dict[str, Any]:
    return request_json("/v1/trip-distance/status")


def trip_distance_observe(payload: Mapping[str, Any]) -> dict[str, Any]:
    return request_json("/v1/trip-distance/observe", payload)
