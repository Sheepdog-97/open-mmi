#!/usr/bin/env python3
"""Internet-radio catalogue access and pinned same-origin stream proxying.

This module owns the Radio Browser integration and outbound stream security
boundary.  It deliberately has no dependency on the dashboard HTTP handler;
``server.py`` supplies a handler only when proxying audio to a client.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

RADIO_BROWSER_DEFAULT_URL = "https://all.api.radio-browser.info"
RADIO_BROWSER_TIMEOUT_SECONDS = 6.0
RADIO_STREAM_TIMEOUT_SECONDS = 12.0
RADIO_STREAM_CHUNK_BYTES = 64 * 1024
RADIO_CATALOG_MAX_BYTES = 2 * 1024 * 1024
RADIO_USER_AGENT = "Open-MMI/0.1 (+https://github.com/open-mmi/open-mmi)"
RADIO_FILTERS = {
    "popular": ("clickcount", "Popular stations"),
    "votes": ("votes", "Top rated"),
    "recent": ("clicktimestamp", "Recently active"),
    # Favourites are stored and filtered in the browser; this value remains
    # accepted so stale requests degrade to a harmless catalogue ordering.
    "favorites": ("name", "Favourites"),
}
RADIO_FILTER_OPTION_LIMIT = 200


def _radio_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _radio_float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _radio_config() -> Dict[str, Any]:
    return {
        "url": os.getenv("OPEN_MMI_RADIO_BROWSER_URL", RADIO_BROWSER_DEFAULT_URL)
        .strip()
        .rstrip("/"),
        "user_agent": os.getenv("OPEN_MMI_RADIO_USER_AGENT", RADIO_USER_AGENT).strip()
        or RADIO_USER_AGENT,
        "catalog_timeout": _radio_float_env(
            "OPEN_MMI_RADIO_CATALOG_TIMEOUT", RADIO_BROWSER_TIMEOUT_SECONDS, 1.0, 30.0
        ),
        "stream_timeout": _radio_float_env(
            "OPEN_MMI_RADIO_STREAM_TIMEOUT", RADIO_STREAM_TIMEOUT_SECONDS, 2.0, 60.0
        ),
        "allow_private_streams": _radio_bool_env(
            "OPEN_MMI_RADIO_ALLOW_PRIVATE_STREAMS", False
        ),
    }


def _safe_radio_station_id(value: Any) -> str:
    import uuid

    text = str(value or "").strip()
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("Invalid radio station ID") from exc
    return str(parsed)


def _radio_media_filter(value: Any) -> str:
    selected = str(value or "popular").strip().lower()
    return selected if selected in RADIO_FILTERS else "popular"


def _radio_country_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    return code if len(code) == 2 and code.isalpha() else ""


def _radio_language_filter(value: Any) -> str:
    text = str(value or "").strip()
    if any(ord(character) < 32 for character in text):
        return ""
    return text[:64]


def _radio_station_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _radio_catalog_json(
    path: str,
    params: Dict[str, Any] | None = None,
    config: Dict[str, Any] | None = None,
) -> Any:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    config = dict(config or _radio_config())
    if not config["url"]:
        raise RuntimeError("Radio Browser URL is not configured")
    suffix = ""
    if params:
        suffix = "?" + urlencode(
            {key: value for key, value in params.items() if value is not None}
        )
    request = Request(
        f"{config['url']}{path}{suffix}",
        headers={
            "Accept": "application/json",
            "User-Agent": config["user_agent"],
        },
    )
    try:
        with urlopen(request, timeout=config["catalog_timeout"]) as response:
            body = response.read(RADIO_CATALOG_MAX_BYTES + 1)
            if len(body) > RADIO_CATALOG_MAX_BYTES:
                raise RuntimeError("Radio Browser response is too large")
            return json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = exc.read(512).decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Radio Browser HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        raise RuntimeError(f"Radio Browser connection failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Radio Browser request timed out") from exc


def _format_radio_station(station: Dict[str, Any]) -> Dict[str, Any]:
    station_id = _safe_radio_station_id(station.get("stationuuid"))
    name = str(station.get("name") or "Unnamed station").strip()
    country = str(
        station.get("country") or station.get("countrycode") or "Internet radio"
    ).strip()
    country_code = _radio_country_code(station.get("countrycode"))
    language = str(station.get("language") or "").strip()
    language_codes = str(station.get("languagecodes") or "").strip()
    codec = str(station.get("codec") or "").strip().upper()
    try:
        bitrate = int(station.get("bitrate") or 0)
    except (TypeError, ValueError):
        bitrate = 0
    tags = [part.strip() for part in str(station.get("tags") or "").split(",")]
    tags = [part for part in tags if part][:2]
    details = []
    if tags:
        details.append(" / ".join(tags))
    if codec:
        details.append(codec)
    if bitrate > 0:
        details.append(f"{bitrate} kbps")
    return {
        "id": station_id,
        "source": "radio",
        "is_live": True,
        "name": name,
        "artist": country,
        "album": " · ".join(details) or "Live station",
        "duration_seconds": None,
        # Do not expose arbitrary third-party image or stream URLs to the browser.
        "image_url": None,
        "codec": codec or None,
        "bitrate": bitrate or None,
        "country": country,
        "country_code": country_code or None,
        "language": language or None,
        "language_codes": language_codes or None,
    }


def _radio_search_payload(
    query: str = "",
    limit: int = 60,
    media_filter: str = "popular",
    country_code: str = "",
    language: str = "",
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    selected_filter = _radio_media_filter(media_filter)
    order, _label = RADIO_FILTERS[selected_filter]
    q = str(query or "").strip()
    try:
        bounded_limit = max(1, min(int(limit), 60))
    except (TypeError, ValueError):
        bounded_limit = 60
    params: Dict[str, Any] = {
        "hidebroken": "true",
        "limit": str(bounded_limit),
        "order": order,
        "reverse": "true",
    }
    if q:
        params["name"] = q
        params["nameExact"] = "false"
    selected_country = _radio_country_code(country_code)
    selected_language = _radio_language_filter(language)
    if selected_country:
        params["countrycode"] = selected_country
    if selected_language:
        params["language"] = selected_language
        params["languageExact"] = "false"
    try:
        data = (
            _radio_catalog_json("/json/stations/search", params)
            if config is None
            else _radio_catalog_json("/json/stations/search", params, config)
        )
        stations = data if isinstance(data, list) else []
        items = []
        for station in stations:
            if not isinstance(station, dict) or not station.get("stationuuid"):
                continue
            try:
                items.append(_format_radio_station(station))
            except ValueError:
                continue
        return {
            "configured": True,
            "source": "radio",
            "filter": selected_filter,
            "country": selected_country or None,
            "language": selected_language or None,
            "items": items,
        }
    except Exception as exc:
        return {
            "configured": True,
            "source": "radio",
            "filter": selected_filter,
            "country": selected_country or None,
            "language": selected_language or None,
            "items": [],
            "error": str(exc),
        }


def _radio_filter_options_payload(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    params = {
        "hidebroken": "true",
        "order": "stationcount",
        "reverse": "true",
        "limit": str(RADIO_FILTER_OPTION_LIMIT),
    }
    if config is None:
        countries_raw = _radio_catalog_json("/json/countrycodes", params)
        languages_raw = _radio_catalog_json("/json/languages", params)
    else:
        countries_raw = _radio_catalog_json("/json/countrycodes", params, config)
        languages_raw = _radio_catalog_json("/json/languages", params, config)

    countries = []
    for entry in countries_raw if isinstance(countries_raw, list) else []:
        if not isinstance(entry, dict):
            continue
        code = _radio_country_code(entry.get("name"))
        if code:
            countries.append({
                "code": code,
                "station_count": _radio_station_count(entry.get("stationcount")),
            })

    languages = []
    seen_languages = set()
    for entry in languages_raw if isinstance(languages_raw, list) else []:
        if not isinstance(entry, dict):
            continue
        name = _radio_language_filter(entry.get("name"))
        if not name or name.casefold() in seen_languages:
            continue
        seen_languages.add(name.casefold())
        languages.append({
            "name": name,
            "code": str(entry.get("iso_639") or "").strip() or None,
            "station_count": _radio_station_count(entry.get("stationcount")),
        })

    return {
        "configured": True,
        "source": "radio",
        "countries": countries,
        "languages": languages,
    }


def _radio_status_payload(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    config = dict(config or _radio_config())
    return {
        "configured": bool(config["url"]),
        "source": "radio",
        "status": "ready" if config["url"] else "unconfigured",
        "state_label": "radio ready" if config["url"] else "not configured",
        "title": "Internet Radio",
        "subtitle": (
            "Search or choose a station to play locally"
            if config["url"]
            else "Set OPEN_MMI_RADIO_BROWSER_URL"
        ),
    }


def _radio_station_by_uuid(
    station_id: str, config: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    from urllib.parse import quote

    safe_id = _safe_radio_station_id(station_id)
    path = f"/json/stations/byuuid/{quote(safe_id, safe='')}"
    data = (
        _radio_catalog_json(path)
        if config is None
        else _radio_catalog_json(path, config=config)
    )
    stations = data if isinstance(data, list) else []
    for station in stations:
        if (
            isinstance(station, dict)
            and str(station.get("stationuuid") or "").lower() == safe_id
        ):
            return station
    raise LookupError("Radio station was not found")


def _radio_resolve_stream_target(url: Any, allow_private: bool = False) -> Dict[str, Any]:
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    text = str(url or "").strip()
    parsed = urlsplit(text)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("Radio stream must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Radio stream URLs may not contain credentials")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Radio stream URL has no hostname")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("Radio stream URL has an invalid port") from exc

    try:
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError(f"Could not resolve radio stream host: {exc}") from exc
    if not results:
        raise RuntimeError("Radio stream host did not resolve")

    addresses = []
    seen = set()
    for family, socktype, protocol, _canonname, sockaddr in results:
        raw_ip = sockaddr[0]
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError as exc:
            raise RuntimeError("Radio stream resolved to an invalid address") from exc
        if not allow_private and not ip.is_global:
            raise PermissionError(
                f"Radio stream resolved to a non-public address ({ip.compressed})"
            )
        key = (family, protocol, sockaddr)
        if key not in seen:
            seen.add(key)
            addresses.append((family, protocol, sockaddr))

    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return {
        "url": text,
        "scheme": scheme,
        "hostname": hostname,
        "port": port,
        "path": path,
        "addresses": addresses,
    }


def _radio_validate_stream_url(url: Any, allow_private: bool = False) -> str:
    return str(_radio_resolve_stream_target(url, allow_private=allow_private)["url"])


class _RadioPinnedResponse:
    def __init__(self, response: Any, connection: Any):
        self._response = response
        self._connection = connection

    def __getattr__(self, name: str) -> Any:
        return getattr(self._response, name)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _radio_connection(target: Dict[str, Any], address: tuple[Any, ...], timeout: float):
    import http.client
    import socket

    family, protocol, sockaddr = address

    class PinnedHTTPConnection(http.client.HTTPConnection):
        def connect(self):
            sock = socket.socket(family, socket.SOCK_STREAM, protocol)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                self.sock = sock
            except Exception:
                sock.close()
                raise

    class PinnedHTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            sock = socket.socket(family, socket.SOCK_STREAM, protocol)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                self.sock = self._context.wrap_socket(
                    sock, server_hostname=target["hostname"]
                )
            except Exception:
                sock.close()
                raise

    connection_type = (
        PinnedHTTPSConnection
        if target["scheme"] == "https"
        else PinnedHTTPConnection
    )
    return connection_type(target["hostname"], target["port"], timeout=timeout)


def _radio_stream_url(
    station_id: str, config: Dict[str, Any] | None = None
) -> str:
    from urllib.parse import quote

    safe_id = _safe_radio_station_id(station_id)
    supplied_config = config
    config = dict(config or _radio_config())
    station = (
        _radio_station_by_uuid(safe_id)
        if supplied_config is None
        else _radio_station_by_uuid(safe_id, config)
    )
    stream_url = str(station.get("url_resolved") or station.get("url") or "").strip()
    if not stream_url:
        raise LookupError("Radio station has no stream URL")
    validated = _radio_validate_stream_url(
        stream_url, allow_private=config["allow_private_streams"]
    )
    # Best effort: Radio Browser asks clients to count each station click.
    try:
        if supplied_config is None:
            _radio_catalog_json(f"/json/url/{quote(safe_id, safe='')}")
        else:
            _radio_catalog_json(
                f"/json/url/{quote(safe_id, safe='')}", config=config
            )
    except Exception:
        pass
    return validated


def _radio_open_stream(
    url: str,
    range_header: str | None = None,
    config: Dict[str, Any] | None = None,
):
    import http.client
    from urllib.error import HTTPError, URLError
    from urllib.parse import urljoin

    config = dict(config or _radio_config())
    headers = {
        "Accept": "audio/*,application/ogg,application/octet-stream;q=0.8,*/*;q=0.2",
        "User-Agent": config["user_agent"],
        "Icy-MetaData": "0",
    }
    if range_header:
        headers["Range"] = range_header

    current_url = url
    for redirect_count in range(6):
        target = _radio_resolve_stream_target(
            current_url, allow_private=config["allow_private_streams"]
        )
        last_error = None
        for address in target["addresses"]:
            connection = _radio_connection(target, address, config["stream_timeout"])
            try:
                connection.request("GET", target["path"], headers=headers)
                response = connection.getresponse()
            except (OSError, TimeoutError, http.client.HTTPException) as exc:
                last_error = exc
                connection.close()
                continue

            if response.status in {301, 302, 303, 307, 308}:
                status = response.status
                response_headers = response.headers
                location = response_headers.get("Location")
                response.close()
                connection.close()
                if not location:
                    raise RuntimeError(
                        f"Radio stream HTTP {status} redirect had no Location"
                    )
                current_url = urljoin(current_url, location)
                break
            if response.status >= 400:
                error = HTTPError(
                    current_url,
                    response.status,
                    response.reason,
                    response.headers,
                    response,
                )
                connection.close()
                raise error
            return _RadioPinnedResponse(response, connection)
        else:
            raise URLError(last_error or "Radio stream connection failed")

        if redirect_count == 5:
            raise RuntimeError("Radio stream exceeded the redirect limit")
    raise RuntimeError("Radio stream redirect handling failed")


def _radio_proxy_audio(
    handler: Any, station_id: str, config: Dict[str, Any] | None = None
) -> None:
    from urllib.error import HTTPError, URLError

    started = False
    try:
        config = dict(config or _radio_config())
        stream_url = _radio_stream_url(station_id, config)
        with _radio_open_stream(
            stream_url, handler.headers.get("Range"), config
        ) as response:
            content_type = str(
                response.headers.get("Content-Type") or "application/octet-stream"
            ).strip()
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type.startswith("text/") or media_type in {
                "application/json",
                "application/xml",
                "text/xml",
            }:
                raise RuntimeError(
                    f"Radio station returned unsupported content type {media_type}"
                )
            handler.send_response(getattr(response, "status", 200))
            started = True
            allowed_headers = [
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
                "icy-name",
                "icy-genre",
                "icy-br",
                "icy-url",
            ]
            for header in allowed_headers:
                value = response.headers.get(header)
                if value:
                    safe_value = str(value).replace("\r", "").replace("\n", "")[:512]
                    handler.send_header(header, safe_value)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
            handler.end_headers()
            while True:
                chunk = response.read(RADIO_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except ValueError as exc:
        if not started:
            handler.send_error(400, str(exc))
    except PermissionError as exc:
        if not started:
            handler.send_error(403, str(exc))
    except LookupError as exc:
        if not started:
            handler.send_error(404, str(exc))
    except HTTPError as exc:
        if not started:
            handler.send_error(exc.code, f"Radio stream HTTP {exc.code}")
    except (URLError, TimeoutError, RuntimeError, OSError) as exc:
        if not started:
            handler.send_error(502, str(exc))
    except (BrokenPipeError, ConnectionResetError):
        return
