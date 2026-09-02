#!/usr/bin/env python3
"""Jellyfin media provider for the Open MMI web dashboard.

This module owns Jellyfin configuration, authentication, scoped catalogue
requests, and same-origin audio/image proxying.  It intentionally has no
dependency on ``DashboardHandler`` so it can be tested and packaged in
isolation.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import time
from typing import Any, Dict, Mapping


# --- Open MMI Jellyfin local audio client start ---
JELLYFIN_TIMEOUT_SECONDS = 4.0
JELLYFIN_ACTIVE_WITHIN_SECONDS = 600
JELLYFIN_AUDIO_CHUNK_BYTES = 64 * 1024
JELLYFIN_JSON_MAX_BYTES = 4 * 1024 * 1024
JELLYFIN_IMAGE_MAX_BYTES = 8 * 1024 * 1024
JELLYFIN_LOGIN_CACHE_SECONDS = 15 * 60
JELLYFIN_IMAGE_CONTENT_TYPES = {
    "image/avif",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
JELLYFIN_CLIENT_NAME = "Open MMI"
JELLYFIN_DEVICE_ID = "open-mmi-dashboard"
JELLYFIN_CLIENT_VERSION = "0.1.0"
_JELLYFIN_LOGIN_CACHE: Dict[str, Dict[str, Any]] = {}


def _mapping_flag(values: Mapping[str, Any], name: str) -> bool:
    return str(values.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _jellyfin_config_from_mapping(values: Mapping[str, Any]) -> Dict[str, Any]:
    url = str(values.get("OPEN_MMI_JELLYFIN_URL", "")).strip().rstrip("/")
    token = str(values.get("OPEN_MMI_JELLYFIN_TOKEN", "")).strip()
    username = str(values.get("OPEN_MMI_JELLYFIN_USERNAME", "")).strip()
    password_set = "OPEN_MMI_JELLYFIN_PASSWORD" in values
    password = str(values.get("OPEN_MMI_JELLYFIN_PASSWORD", ""))
    session_id = str(values.get("OPEN_MMI_JELLYFIN_SESSION_ID", "")).strip()
    device_name = str(values.get("OPEN_MMI_JELLYFIN_DEVICE", "")).strip().casefold()
    user_id = str(values.get("OPEN_MMI_JELLYFIN_USER_ID", "")).strip()
    library_id = str(values.get("OPEN_MMI_JELLYFIN_LIBRARY_ID", "")).strip()
    username_configured = bool(username and password_set)
    return {
        "configured": bool(url and (token or username_configured)),
        "url": url,
        "token": token,
        "username": username,
        "password": password,
        "username_configured": username_configured,
        "auth_mode": "token" if token else "username" if username_configured else "",
        "session_id": session_id,
        "device_name": device_name,
        "user_id": user_id,
        "library_id": library_id,
        "allow_global": _mapping_flag(values, "OPEN_MMI_JELLYFIN_ALLOW_GLOBAL"),
        "insecure_tls": _mapping_flag(values, "OPEN_MMI_JELLYFIN_INSECURE_TLS"),
    }


def _jellyfin_config() -> Dict[str, Any]:
    return _jellyfin_config_from_mapping(os.environ)

def _jellyfin_header_value(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\"')


def _jellyfin_login_auth_header(config: Dict[str, Any]) -> str:
    device_name = os.getenv("OPEN_MMI_JELLYFIN_DEVICE", "").strip() or "Dashboard"
    device_id = os.getenv("OPEN_MMI_JELLYFIN_DEVICE_ID", "").strip() or JELLYFIN_DEVICE_ID
    return (
        f'MediaBrowser Client="{_jellyfin_header_value(JELLYFIN_CLIENT_NAME)}", '
        f'Device="{_jellyfin_header_value(device_name)}", '
        f'DeviceId="{_jellyfin_header_value(device_id)}", '
        f'Version="{_jellyfin_header_value(JELLYFIN_CLIENT_VERSION)}"'
    )


def _jellyfin_login_cache_key(config: Dict[str, Any]) -> str:
    import hashlib

    device_id = os.getenv("OPEN_MMI_JELLYFIN_DEVICE_ID", "").strip() or JELLYFIN_DEVICE_ID
    material = "\0".join(
        [
            str(config.get("url") or ""),
            str(config.get("username") or ""),
            str(config.get("password") or ""),
            device_id,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _jellyfin_invalidate_login(config: Dict[str, Any]) -> None:
    _JELLYFIN_LOGIN_CACHE.pop(_jellyfin_login_cache_key(config), None)
    if config.get("auth_mode") == "username":
        config["token"] = ""


def _jellyfin_prune_login_cache(now: float | None = None) -> None:
    current = time.monotonic() if now is None else now
    for key, cached in list(_JELLYFIN_LOGIN_CACHE.items()):
        cached_at = float(cached.get("cached_at") or 0.0)
        if current - cached_at >= JELLYFIN_LOGIN_CACHE_SECONDS:
            _JELLYFIN_LOGIN_CACHE.pop(key, None)


def _read_bounded_response(response: Any, maximum: int, label: str) -> bytes:
    raw_length = response.headers.get("Content-Length")
    if raw_length:
        try:
            declared = int(raw_length)
        except (TypeError, ValueError):
            declared = -1
        if declared > maximum:
            raise RuntimeError(f"{label} exceeded the {maximum}-byte limit")

    chunks = []
    total = 0
    while total <= maximum:
        chunk = response.read(min(64 * 1024, maximum + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > maximum:
        raise RuntimeError(f"{label} exceeded the {maximum}-byte limit")
    return b"".join(chunks)


def _jellyfin_login(config: Dict[str, Any]) -> Dict[str, Any]:
    from urllib.error import HTTPError, URLError
    from urllib.request import Request

    if not config.get("username_configured"):
        raise RuntimeError("Jellyfin username/password is not configured")

    cache_key = _jellyfin_login_cache_key(config)
    now = time.monotonic()
    _jellyfin_prune_login_cache(now)
    cached = _JELLYFIN_LOGIN_CACHE.get(cache_key)
    if cached and cached.get("token"):
        cached_at = float(cached.get("cached_at") or 0.0)
        if now - cached_at < JELLYFIN_LOGIN_CACHE_SECONDS:
            return {
                key: value for key, value in cached.items() if key != "cached_at"
            }
        _JELLYFIN_LOGIN_CACHE.pop(cache_key, None)

    url = f"{config['url']}/Users/AuthenticateByName"
    body = json.dumps({"Username": config.get("username", ""), "Pw": config.get("password", "")}).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": _jellyfin_login_auth_header(config),
        },
        method="POST",
    )

    try:
        with _jellyfin_urlopen(request, config, timeout=JELLYFIN_TIMEOUT_SECONDS) as response:
            raw = _read_bounded_response(response, JELLYFIN_JSON_MAX_BYTES, "Jellyfin login response")
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Jellyfin login HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"Jellyfin login connection failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Jellyfin login timed out") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Jellyfin login returned invalid JSON") from exc

    if not isinstance(payload, dict) or not payload.get("AccessToken"):
        raise RuntimeError("Jellyfin login did not return an access token")

    user = payload.get("User") if isinstance(payload.get("User"), dict) else {}
    login = {
        "token": str(payload["AccessToken"]),
        "user_id": str(user.get("Id") or ""),
        "user_name": str(user.get("Name") or config.get("username") or ""),
    }
    _JELLYFIN_LOGIN_CACHE[cache_key] = {
        **login,
        "cached_at": now,
    }
    return login


def _jellyfin_access_token(config: Dict[str, Any]) -> str:
    token = str(config.get("token") or "")
    if token:
        return token

    login = _jellyfin_login(config)
    token = str(login.get("token") or "")
    if not token:
        raise RuntimeError("Jellyfin login did not return an access token")

    config["token"] = token
    if login.get("user_id") and not config.get("user_id"):
        config["user_id"] = login["user_id"]
    return token


def _jellyfin_auth_headers(config: Dict[str, Any]) -> Dict[str, str]:
    token = _jellyfin_access_token(config)
    return {
        "Accept": "application/json",
        "Authorization": f"MediaBrowser Token={token}",
        "X-MediaBrowser-Token": token,
        "X-Emby-Token": token,
    }

def _jellyfin_urlopen(request: Any, config: Dict[str, Any], *, timeout: float):
    from urllib.request import urlopen

    if config.get("insecure_tls"):
        context = ssl._create_unverified_context()
        return urlopen(request, timeout=timeout, context=context)
    return urlopen(request, timeout=timeout)


def _jellyfin_authenticated_urlopen(
    config: Dict[str, Any],
    url: str,
    *,
    headers: Dict[str, str] | None = None,
    timeout: float = JELLYFIN_TIMEOUT_SECONDS,
):
    from urllib.error import HTTPError
    from urllib.request import Request

    base_headers = dict(headers or {})
    for attempt in range(2):
        request_headers = _jellyfin_auth_headers(config)
        request_headers.update(base_headers)
        request = Request(url, headers=request_headers)
        try:
            return _jellyfin_urlopen(request, config, timeout=timeout)
        except HTTPError as exc:
            should_retry = (
                attempt == 0
                and exc.code in {401, 403}
                and config.get("auth_mode") == "username"
            )
            if not should_retry:
                raise
            try:
                exc.close()
            except Exception:
                pass
            _jellyfin_invalidate_login(config)
    raise RuntimeError("Jellyfin authentication retry failed")


def _jellyfin_request_json(config: Dict[str, Any], path: str) -> Any:
    from urllib.error import HTTPError, URLError

    url = f"{config['url']}{path}"
    try:
        with _jellyfin_authenticated_urlopen(config, url) as response:
            raw = _read_bounded_response(response, JELLYFIN_JSON_MAX_BYTES, "Jellyfin JSON response")
            return json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        try:
            detail = exc.read(512).decode("utf-8", errors="replace").strip()
        except Exception:
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Jellyfin HTTP {exc.code}{suffix}") from exc
    except URLError as exc:
        raise RuntimeError(f"Jellyfin connection failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("Jellyfin request timed out") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Jellyfin returned invalid JSON") from exc

def _jellyfin_error_details(exc: Exception) -> Dict[str, Any]:
    """Classify provider failures without exposing credentials or raw responses."""

    from urllib.error import HTTPError, URLError

    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, HTTPError):
            if current.code in {401, 403}:
                return {
                    "connection_state": "authentication-error",
                    "retryable": False,
                    "error_code": "authentication",
                }
            return {
                "connection_state": "server-error",
                "retryable": current.code >= 500,
                "error_code": f"http-{current.code}",
            }
        if isinstance(current, (URLError, TimeoutError, ConnectionError, OSError)):
            return {
                "connection_state": "reconnecting",
                "retryable": True,
                "error_code": "unreachable",
            }
        current = current.__cause__

    return {
        "connection_state": "server-error",
        "retryable": False,
        "error_code": "provider-error",
    }


def _jellyfin_test_connection(config: Dict[str, Any]) -> Dict[str, Any]:
    if not config.get("configured"):
        raise RuntimeError("Jellyfin is not fully configured")

    working = dict(config)
    system_info = _jellyfin_request_json(working, "/System/Info")
    if not isinstance(system_info, dict):
        raise RuntimeError("Jellyfin system information response was invalid")

    user_id = _jellyfin_user_id(working)
    return {
        "connected": True,
        "server_name": str(system_info.get("ServerName") or system_info.get("Name") or "Jellyfin"),
        "version": str(system_info.get("Version") or ""),
        "user_id": user_id or "",
        "library_id": str(working.get("library_id") or ""),
        "auth_mode": str(working.get("auth_mode") or ""),
    }


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_jellyfin_id(value: Any, label: str = "item") -> str:
    candidate = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate):
        raise ValueError(f"Invalid Jellyfin {label} id")
    return candidate


def _jellyfin_media_filter(value: str) -> str:
    aliases = {
        "favorite": "favorites",
        "favourite": "favorites",
        "favourites": "favorites",
        "a-z": "az",
        "name": "az",
    }
    candidate = aliases.get(str(value or "recent").strip().lower(), str(value or "recent").strip().lower())
    if candidate not in {"recent", "favorites", "az"}:
        raise ValueError("Unsupported media filter; use recent, favorites, or az")
    return candidate


def _jellyfin_scope_params(config: Dict[str, Any]) -> Dict[str, str]:
    params: Dict[str, str] = {}
    user_id = _jellyfin_user_id(config)
    if user_id:
        params["UserId"] = user_id
    library_id = config.get("library_id")
    if library_id:
        params["ParentId"] = _safe_jellyfin_id(library_id, "library")
    return params

def _jellyfin_user_id(config: Dict[str, Any]) -> str | None:
    configured_user_id = str(config.get("user_id") or "").strip()

    # Assigned-user login returns the authoritative Jellyfin user. Preserve that
    # flow and reject an explicit conflicting scope override.
    if config.get("username_configured") and not config.get("token"):
        login = _jellyfin_login(config)
        login_user_id = _safe_jellyfin_id(login.get("user_id"), "user")
        if configured_user_id:
            explicit_user_id = _safe_jellyfin_id(configured_user_id, "user")
            if explicit_user_id != login_user_id:
                raise RuntimeError(
                    "OPEN_MMI_JELLYFIN_USER_ID does not match the assigned-user login"
                )
        config["user_id"] = login_user_id
        return login_user_id

    if configured_user_id:
        return _safe_jellyfin_id(configured_user_id, "user")

    # A server API key is not a user session, so /Users/Me is not a reliable way
    # to derive scope. Allow an exact username to act as a friendly scope selector.
    if config.get("token"):
        scope_username = str(config.get("username") or "").strip()
        if scope_username:
            users = _jellyfin_request_json(config, "/Users")
            if not isinstance(users, list):
                raise RuntimeError("Jellyfin /Users did not return a user list")
            matches = [
                user
                for user in users
                if isinstance(user, dict)
                and user.get("Id")
                and str(user.get("Name") or "").casefold() == scope_username.casefold()
            ]
            if len(matches) != 1:
                raise RuntimeError(
                    "OPEN_MMI_JELLYFIN_USERNAME must exactly match one Jellyfin user "
                    "when used with OPEN_MMI_JELLYFIN_TOKEN"
                )
            user_id = _safe_jellyfin_id(matches[0]["Id"], "user")
            config["user_id"] = user_id
            return user_id

        if config.get("allow_global"):
            return None
        raise RuntimeError(
            "Jellyfin user scope is required for an API key. Set "
            "OPEN_MMI_JELLYFIN_USER_ID or OPEN_MMI_JELLYFIN_USERNAME, or explicitly "
            "set OPEN_MMI_JELLYFIN_ALLOW_GLOBAL=1 for legacy global access."
        )

    if config.get("allow_global"):
        return None
    raise RuntimeError(
        "Jellyfin user scope is required. Set OPEN_MMI_JELLYFIN_USER_ID or use "
        "assigned-user login."
    )

def _jellyfin_validate_item_access(config: Dict[str, Any], item_id: str) -> str | None:
    from urllib.parse import urlencode

    safe_item_id = _safe_jellyfin_id(item_id)
    scope = _jellyfin_scope_params(config)
    params = {
        **scope,
        "Ids": safe_item_id,
        "IncludeItemTypes": "Audio",
        "Recursive": "true",
        "Limit": "1",
        "EnableImages": "false",
    }
    data = _jellyfin_request_json(config, "/Items?" + urlencode(params))
    items = data.get("Items", []) if isinstance(data, dict) else []
    for item in items:
        if not isinstance(item, dict) or str(item.get("Id", "")) != safe_item_id:
            continue
        if item.get("Type") == "Audio" or item.get("MediaType") == "Audio":
            return scope.get("UserId")
    raise PermissionError("Jellyfin item is outside the configured user/library scope")

def _ticks_to_seconds(value: Any) -> float | None:
    try:
        ticks = float(value)
    except (TypeError, ValueError):
        return None
    return ticks / 10_000_000.0


def _format_jellyfin_item(item: Dict[str, Any]) -> Dict[str, Any]:
    artists = item.get("Artists") or []
    album_artist = item.get("AlbumArtist")
    artist = album_artist or ", ".join(map(str, artists[:3]))
    album = item.get("Album") or item.get("AlbumName")
    runtime_seconds = _ticks_to_seconds(item.get("RunTimeTicks"))
    item_id = str(item.get("Id") or "")
    image_url = None
    if item_id and (item.get("ImageTags", {}) or {}).get("Primary"):
        image_url = f"/api/jellyfin/image/{item_id}"
    return {
        "id": item_id,
        "name": item.get("Name") or "Untitled",
        "artist": artist or "Unknown artist",
        "album": album or "",
        "duration_seconds": runtime_seconds,
        "image_url": image_url,
    }


def _jellyfin_demo_tracks() -> Dict[str, Any]:
    return {
        "configured": True,
        "demo": True,
        "items": [
            {"id": "demo-night-drive", "name": "Night Drive", "artist": "Open MMI Demo", "album": "Synthetic Radio", "duration_seconds": 224, "image_url": None},
            {"id": "demo-morning-commute", "name": "Morning Commute", "artist": "Open MMI Demo", "album": "Synthetic Radio", "duration_seconds": 188, "image_url": None},
            {"id": "demo-road-trip", "name": "Road Trip Mix", "artist": "Open MMI Demo", "album": "Synthetic Radio", "duration_seconds": 256, "image_url": None},
        ],
    }


def _jellyfin_demo_status() -> Dict[str, Any]:
    now = time.time()
    duration = 224.0
    position = now % duration
    return {
        "configured": True,
        "demo": True,
        "status": "idle",
        "state_label": "local player ready",
        "server_name": "demo://jellyfin",
        "title": "Jellyfin demo",
        "subtitle": "Local audio player layout preview",
        "client": "Open MMI",
        "device_name": "Dashboard",
        "user_name": "demo",
        "position_seconds": round(position, 1),
        "runtime_seconds": duration,
        "progress_percent": round((position / duration) * 100.0, 1),
        "image_url": None,
    }


def _pick_jellyfin_session(
    sessions: list[Dict[str, Any]],
    config: Dict[str, Any],
    user_id: str | None = None,
) -> Dict[str, Any] | None:
    # Never expose an arbitrary active session. A session must match an explicit
    # selector, and user-scoped deployments only accept sessions for that user.
    candidates = [session for session in sessions if isinstance(session, dict)]
    if user_id:
        candidates = [
            session
            for session in candidates
            if str(session.get("UserId") or "") == user_id
        ]

    session_id = str(config.get("session_id") or "")
    if session_id:
        return next(
            (session for session in candidates if str(session.get("Id") or "") == session_id),
            None,
        )

    device_name = str(config.get("device_name") or "").casefold()
    if device_name:
        return next(
            (
                session
                for session in candidates
                if str(session.get("DeviceName") or "").casefold() == device_name
                or str(session.get("Client") or "").casefold() == device_name
            ),
            None,
        )

    return None

def _jellyfin_status_payload(
    demo_mode: bool = False, config: Dict[str, Any] | None = None
) -> Dict[str, Any]:
    config = dict(config or _jellyfin_config())
    if not config["configured"]:
        if demo_mode:
            return _jellyfin_demo_status()
        return {
            "configured": False,
            "status": "unconfigured",
            "connection_state": "configuration-missing",
            "retryable": False,
            "state_label": "not configured",
            "title": "Jellyfin not configured",
            "subtitle": "Set Jellyfin URL with token or username/password",
        }

    try:
        user_id = _jellyfin_user_id(config)
        scope = {
            "user_scoped": bool(user_id),
            "library_scoped": bool(config.get("library_id")),
            "global_access": bool(config.get("allow_global")),
        }
        if not config.get("session_id") and not config.get("device_name"):
            return {
                "configured": True,
                "status": "ready",
                "connection_state": "ready",
                "retryable": False,
                "state_label": "local player ready",
                "title": "Jellyfin ready",
                "subtitle": "Pick a track below to play locally",
                "scope": scope,
            }

        sessions = _jellyfin_request_json(
            config,
            f"/Sessions?activeWithinSeconds={JELLYFIN_ACTIVE_WITHIN_SECONDS}",
        )
        if not isinstance(sessions, list):
            sessions = []
        session = _pick_jellyfin_session(sessions, config, user_id)
        if not session:
            return {
                "configured": True,
                "status": "ready",
                "connection_state": "ready",
                "retryable": False,
                "state_label": "local player ready",
                "title": "Jellyfin ready",
                "subtitle": "No matching remote session; local playback is available",
                "scope": scope,
            }

        item = session.get("NowPlayingItem") or {}
        play_state = session.get("PlayState") or {}
        paused = play_state.get("IsPaused") is True
        position_seconds = _ticks_to_seconds(play_state.get("PositionTicks"))
        runtime_seconds = _ticks_to_seconds(item.get("RunTimeTicks"))
        progress = None
        if position_seconds is not None and runtime_seconds and runtime_seconds > 0:
            progress = (position_seconds / runtime_seconds) * 100.0
        formatted = _format_jellyfin_item(item) if item else {}
        status = "paused" if paused else "playing" if item else "ready"
        return {
            "configured": True,
            "status": status,
            "connection_state": "ready",
            "retryable": False,
            "state_label": status,
            "title": formatted.get("name") or "Jellyfin ready",
            "subtitle": " · ".join(
                part for part in [formatted.get("artist"), formatted.get("album")] if part
            )
            or "Pick a track below to play locally",
            "client": session.get("Client"),
            "device_name": session.get("DeviceName"),
            "position_seconds": position_seconds,
            "runtime_seconds": runtime_seconds,
            "progress_percent": progress,
            "image_url": formatted.get("image_url"),
            "scope": scope,
        }
    except Exception as exc:
        details = _jellyfin_error_details(exc)
        return {
            "configured": True,
            "status": "error",
            **details,
            "state_label": "credentials rejected"
            if details["connection_state"] == "authentication-error"
            else "reconnecting"
            if details["connection_state"] == "reconnecting"
            else "server error",
            "title": "Jellyfin unavailable",
            "subtitle": str(exc),
        }

def _jellyfin_search_payload(
    query: str = "",
    limit: int = 24,
    media_filter: str = "recent",
    demo_mode: bool = False,
    config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    config = dict(config or _jellyfin_config())
    if not config["configured"]:
        if demo_mode:
            payload = _jellyfin_demo_tracks()
            payload["filter"] = _jellyfin_media_filter(media_filter)
            return payload
        return {
            "configured": False,
            "connection_state": "configuration-missing",
            "retryable": False,
            "items": [],
            "error": "Jellyfin is not configured",
        }

    try:
        from urllib.parse import urlencode

        selected_filter = _jellyfin_media_filter(media_filter)
        scope = _jellyfin_scope_params(config)
        params = {
            **scope,
            "IncludeItemTypes": "Audio",
            "Recursive": "true",
            "Limit": str(max(1, min(int(limit), 60))),
            "SortBy": "DateCreated,SortName",
            "SortOrder": "Descending",
            "Fields": "PrimaryImageAspectRatio,MediaSources,AlbumArtist,Artists",
            "EnableImages": "true",
            "EnableUserData": "true" if scope.get("UserId") else "false",
        }
        query = (query or "").strip()
        if selected_filter == "favorites":
            params["IsFavorite"] = "true"
            params["SortBy"] = "SortName"
            params["SortOrder"] = "Ascending"
        elif selected_filter == "az":
            params["SortBy"] = "SortName"
            params["SortOrder"] = "Ascending"
        if query:
            params["SearchTerm"] = query
            params["SortBy"] = "SortName"
            params["SortOrder"] = "Ascending"

        data = _jellyfin_request_json(config, "/Items?" + urlencode(params))
        items = data.get("Items", []) if isinstance(data, dict) else []
        return {
            "configured": True,
            "connection_state": "ready",
            "retryable": False,
            "filter": selected_filter,
            "items": [
                _format_jellyfin_item(item)
                for item in items
                if isinstance(item, dict)
                and item.get("Id")
                and (item.get("Type") == "Audio" or item.get("MediaType") == "Audio")
            ],
        }
    except Exception as exc:
        return {
            "configured": True,
            **_jellyfin_error_details(exc),
            "items": [],
            "error": str(exc),
        }

def _jellyfin_proxy_audio(
    handler: Any, item_id: str, config: Dict[str, Any] | None = None
) -> None:
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode

    config = dict(config or _jellyfin_config())
    if not config["configured"]:
        handler.send_error(404, "Jellyfin is not configured")
        return
    try:
        safe_item_id = _safe_jellyfin_id(item_id)
        user_id = _jellyfin_validate_item_access(config, safe_item_id)
    except ValueError as exc:
        handler.send_error(400, str(exc))
        return
    except PermissionError as exc:
        handler.send_error(403, str(exc))
        return
    except Exception as exc:
        handler.send_error(502, str(exc))
        return

    params = {
        "static": "true",
        "deviceId": "open-mmi-dashboard",
        "allowAudioStreamCopy": "true",
        "enableAutoStreamCopy": "true",
    }
    if user_id:
        params["UserId"] = user_id
    url = f"{config['url']}/Audio/{quote(safe_item_id, safe='')}/stream?" + urlencode(params)
    headers = {"Accept": "audio/*,*/*"}
    if handler.headers.get("Range"):
        headers["Range"] = handler.headers.get("Range")
    try:
        with _jellyfin_authenticated_urlopen(config, url, headers=headers) as response:
            status = getattr(response, "status", 200)
            handler.send_response(status)
            for header in [
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
                "Last-Modified",
                "ETag",
            ]:
                value = response.headers.get(header)
                if value:
                    handler.send_header(header, value)
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
            handler.end_headers()
            while True:
                chunk = response.read(JELLYFIN_AUDIO_CHUNK_BYTES)
                if not chunk:
                    break
                try:
                    handler.wfile.write(chunk)
                except ConnectionError:
                    # Browsers routinely close an in-flight media response when
                    # the user changes track/source, reloads, or the audio element
                    # issues a replacement range request.  The response has
                    # already started, so this is a normal end-of-stream condition.
                    return
    except HTTPError as exc:
        handler.send_error(exc.code, f"Jellyfin stream HTTP {exc.code}")
    except (URLError, TimeoutError) as exc:
        handler.send_error(502, str(exc))

def _jellyfin_proxy_image(
    handler: Any, item_id: str, config: Dict[str, Any] | None = None
) -> None:
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote

    config = dict(config or _jellyfin_config())
    if not config["configured"]:
        handler.send_error(404, "Jellyfin is not configured")
        return
    try:
        safe_item_id = _safe_jellyfin_id(item_id)
        _jellyfin_validate_item_access(config, safe_item_id)
    except ValueError as exc:
        handler.send_error(400, str(exc))
        return
    except PermissionError as exc:
        handler.send_error(403, str(exc))
        return
    except Exception as exc:
        handler.send_error(502, str(exc))
        return

    image_url = (
        f"{config['url']}/Items/{quote(safe_item_id, safe='')}/Images/Primary"
        "?maxHeight=480&quality=84"
    )
    try:
        with _jellyfin_authenticated_urlopen(
            config,
            image_url,
            headers={
                "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif"
            },
        ) as response:
            content_type = str(response.headers.get("Content-Type") or "").strip()
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type not in JELLYFIN_IMAGE_CONTENT_TYPES:
                raise RuntimeError(f"Jellyfin returned unsupported image type {media_type or 'unknown'}")
            body = _read_bounded_response(response, JELLYFIN_IMAGE_MAX_BYTES, "Jellyfin image")
            handler.send_response(getattr(response, "status", 200))
            handler.send_header("Content-Type", media_type)
            handler.send_header("Cache-Control", "private, max-age=60")
            handler.send_header("X-Content-Type-Options", "nosniff")
            handler.send_header("Cross-Origin-Resource-Policy", "same-origin")
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
    except HTTPError as exc:
        handler.send_error(exc.code, f"Jellyfin image HTTP {exc.code}")
    except (URLError, TimeoutError, RuntimeError) as exc:
        handler.send_error(502, str(exc))

# --- Open MMI Jellyfin local audio client end ---
