"""Local client for the OS-confined Open MMI network-egress service.

The dashboard and CLI use this module to request one of the manifest-declared
network purposes without gaining external socket authority themselves.  The
transport is a Unix-domain HTTP connection; no TCP destination is accepted by
this client API.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from pathlib import Path
from typing import Any, Mapping

DEFAULT_SOCKET = Path("/run/open-mmi-media-egress/egress.sock")
MAX_REQUEST_BYTES = 64 * 1024
MAX_JSON_RESPONSE_BYTES = 4 * 1024 * 1024
PROXY_CHUNK_BYTES = 64 * 1024


class EgressClientError(RuntimeError):
    """The local egress service is unavailable or returned invalid evidence."""


def socket_path() -> Path:
    override = os.getenv("OPEN_MMI_MEDIA_EGRESS_SOCKET", "").strip()
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


def _encoded(payload: Mapping[str, Any]) -> bytes:
    try:
        body = json.dumps(dict(payload), separators=(",", ":"), sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EgressClientError("egress request is not JSON serializable") from exc
    if len(body) > MAX_REQUEST_BYTES:
        raise EgressClientError("egress request is too large")
    return body


def _request(
    endpoint: str,
    payload: Mapping[str, Any],
    *,
    timeout: float,
) -> tuple[_UnixHTTPConnection, http.client.HTTPResponse]:
    body = _encoded(payload)
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
        return connection, response
    except (OSError, TimeoutError, http.client.HTTPException) as exc:
        connection.close()
        raise EgressClientError("network egress service is unavailable") from exc


def request_json(endpoint: str, payload: Mapping[str, Any], *, timeout: float = 12.0) -> dict[str, Any]:
    connection, response = _request(endpoint, payload, timeout=timeout)
    try:
        declared = response.getheader("Content-Length")
        if declared:
            try:
                if int(declared) > MAX_JSON_RESPONSE_BYTES:
                    raise EgressClientError("network egress response is too large")
            except ValueError as exc:
                raise EgressClientError("network egress response length is invalid") from exc
        body = response.read(MAX_JSON_RESPONSE_BYTES + 1)
        if len(body) > MAX_JSON_RESPONSE_BYTES:
            raise EgressClientError("network egress response is too large")
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise EgressClientError("network egress service returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise EgressClientError("network egress service returned an invalid response")
        if response.status >= 400 or decoded.get("ok") is False:
            raise EgressClientError(str(decoded.get("error") or f"network egress HTTP {response.status}"))
        return decoded
    finally:
        connection.close()


def jellyfin_status() -> dict[str, Any]:
    result = request_json("/v1/jellyfin/status", {}, timeout=5.0)
    status = result.get("jellyfin")
    if not isinstance(status, dict):
        raise EgressClientError("network egress service returned an invalid Jellyfin status")
    return status


def test_jellyfin() -> dict[str, Any]:
    result = request_json("/v1/jellyfin/test", {}, timeout=15.0)
    test = result.get("test")
    if not isinstance(test, dict):
        raise EgressClientError("network egress service returned an invalid Jellyfin test result")
    return test


def test_jellyfin_candidate(config: Mapping[str, Any]) -> dict[str, Any]:
    result = request_json("/v1/jellyfin/test-candidate", {"config": dict(config)}, timeout=15.0)
    test = result.get("test")
    if not isinstance(test, dict):
        raise EgressClientError("network egress service returned an invalid Jellyfin candidate test result")
    return test


def proxy_media(
    handler: Any,
    *,
    source: str,
    path: str,
    query: str,
    demo_mode: bool = False,
) -> None:
    payload = {
        "source": source,
        "path": path,
        "query": query,
        "demo_mode": bool(demo_mode),
        "range": str(handler.headers.get("Range") or "")[:256],
    }
    try:
        connection, response = _request("/v1/media/proxy", payload, timeout=65.0)
    except EgressClientError as exc:
        handler.send_error(503, str(exc))
        return

    started = False
    try:
        handler.send_response(response.status)
        started = True
        forwarded = {
            "content-type",
            "content-length",
            "content-range",
            "accept-ranges",
            "last-modified",
            "etag",
            "cache-control",
            "x-content-type-options",
            "cross-origin-resource-policy",
            "icy-name",
            "icy-genre",
            "icy-br",
            "icy-url",
        }
        for name, value in response.getheaders():
            if name.casefold() not in forwarded:
                continue
            safe_value = str(value).replace("\r", "").replace("\n", "")[:1024]
            handler.send_header(name, safe_value)
        handler.end_headers()
        while True:
            chunk = response.read(PROXY_CHUNK_BYTES)
            if not chunk:
                break
            handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return
    except (OSError, TimeoutError, http.client.HTTPException):
        if not started:
            try:
                handler.send_error(502, "network egress proxy failed")
            except Exception:
                pass
    finally:
        connection.close()
