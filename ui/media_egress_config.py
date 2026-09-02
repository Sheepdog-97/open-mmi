"""Root-owned owner configuration for purpose-scoped media network egress.

The dashboard process is deliberately unable to mutate or read this state.
Owner changes are made only by an explicit root CLI invocation and are loaded
into the media-egress service through a systemd credential.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

CONFIG_ID = "org.open-mmi.media-egress-config"
SCHEMA_VERSION = 1
DEFAULT_CONFIG_PATH = Path("/var/lib/open-mmi/network-egress/media.v1.json")
CREDENTIAL_NAME = "media-config"
MAX_VALUE_BYTES = 4096
JELLYFIN_KEYS = (
    "OPEN_MMI_JELLYFIN_URL",
    "OPEN_MMI_JELLYFIN_TOKEN",
    "OPEN_MMI_JELLYFIN_USERNAME",
    "OPEN_MMI_JELLYFIN_PASSWORD",
    "OPEN_MMI_JELLYFIN_USER_ID",
    "OPEN_MMI_JELLYFIN_LIBRARY_ID",
    "OPEN_MMI_JELLYFIN_SESSION_ID",
    "OPEN_MMI_JELLYFIN_DEVICE",
    "OPEN_MMI_JELLYFIN_INSECURE_TLS",
    "OPEN_MMI_JELLYFIN_ALLOW_GLOBAL",
)


class MediaEgressConfigError(RuntimeError):
    """The owner media-egress configuration is malformed or cannot be changed safely."""


def empty_config() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "config_id": CONFIG_ID, "jellyfin": {}}


def _validate_value(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise MediaEgressConfigError(f"{label} must be a string")
    if "\0" in value or "\n" in value or "\r" in value:
        raise MediaEgressConfigError(f"{label} contains an invalid control character")
    if len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise MediaEgressConfigError(f"{label} is too long")
    return value


def _flag(value: str, label: str) -> str:
    normal = value.strip().lower()
    if normal not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
        raise MediaEgressConfigError(f"{label} must be a boolean flag")
    return "1" if normal in {"1", "true", "yes", "on"} else "0"


def normalize_jellyfin(values: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(values, Mapping):
        raise MediaEgressConfigError("Jellyfin configuration must be an object")
    unknown = sorted(set(values) - set(JELLYFIN_KEYS))
    if unknown:
        raise MediaEgressConfigError("unsupported Jellyfin configuration keys: " + ", ".join(unknown))

    cleaned: dict[str, str] = {}
    for key in JELLYFIN_KEYS:
        if key not in values:
            continue
        value = _validate_value(values[key], key)
        if key in {"OPEN_MMI_JELLYFIN_INSECURE_TLS", "OPEN_MMI_JELLYFIN_ALLOW_GLOBAL"}:
            value = _flag(value, key)
        if value != "":
            cleaned[key] = value

    if not cleaned:
        return {}

    url = cleaned.get("OPEN_MMI_JELLYFIN_URL", "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MediaEgressConfigError("Jellyfin URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise MediaEgressConfigError("Jellyfin URL may not contain embedded credentials")
    cleaned["OPEN_MMI_JELLYFIN_URL"] = url

    token = cleaned.get("OPEN_MMI_JELLYFIN_TOKEN", "").strip()
    username = cleaned.get("OPEN_MMI_JELLYFIN_USERNAME", "").strip()
    password = cleaned.get("OPEN_MMI_JELLYFIN_PASSWORD", "")
    if not token and not (username and password):
        raise MediaEgressConfigError("Jellyfin configuration requires a token or username/password")
    return {key: cleaned[key] for key in JELLYFIN_KEYS if key in cleaned}


def validate_config(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise MediaEgressConfigError("media egress configuration root must be an object")
    if set(payload) != {"schema_version", "config_id", "jellyfin"}:
        raise MediaEgressConfigError("media egress configuration fields are invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise MediaEgressConfigError("unsupported media egress configuration schema")
    if payload.get("config_id") != CONFIG_ID:
        raise MediaEgressConfigError("unexpected media egress configuration id")
    return {
        "schema_version": SCHEMA_VERSION,
        "config_id": CONFIG_ID,
        "jellyfin": normalize_jellyfin(payload.get("jellyfin", {})),
    }


def read_config(path: Path = DEFAULT_CONFIG_PATH, *, missing_ok: bool = True) -> dict[str, Any]:
    target = Path(path)
    try:
        metadata = target.lstat()
    except FileNotFoundError:
        if missing_ok:
            return empty_config()
        raise MediaEgressConfigError(f"media egress configuration is missing: {target}")
    except OSError as exc:
        raise MediaEgressConfigError(f"cannot inspect media egress configuration {target}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise MediaEgressConfigError("media egress configuration is not a regular file")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MediaEgressConfigError(f"cannot read media egress configuration {target}: {exc}") from exc
    return validate_config(payload)


def credential_path() -> Path:
    override = os.getenv("OPEN_MMI_MEDIA_EGRESS_CONFIG", "").strip()
    if override:
        return Path(override)
    directory = os.getenv("CREDENTIALS_DIRECTORY", "").strip()
    if not directory:
        raise MediaEgressConfigError("media egress credential directory is unavailable")
    return Path(directory) / CREDENTIAL_NAME


def read_credential_config() -> dict[str, Any]:
    return read_config(credential_path(), missing_ok=False)


def write_config(payload: Mapping[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise MediaEgressConfigError("changing media network authority requires root")
    normalized = validate_config(payload)
    target = Path(path)
    parent = target.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        parent_metadata = parent.lstat()
        if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
            raise MediaEgressConfigError("media egress configuration directory is untrusted")
        os.chown(parent, 0, 0)
        os.chmod(parent, 0o700)
        if target.exists() or target.is_symlink():
            metadata = target.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise MediaEgressConfigError("refusing to replace unsafe media egress configuration")
            if metadata.st_uid != 0 or metadata.st_gid != 0 or metadata.st_mode & 0o077:
                raise MediaEgressConfigError("existing media egress configuration ownership/mode is unsafe")
    except OSError as exc:
        raise MediaEgressConfigError(f"cannot prepare media egress configuration path: {exc}") from exc

    body = json.dumps(normalized, sort_keys=True, indent=2) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=parent, prefix=target.name + ".", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(temporary, 0, 0)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        os.chown(target, 0, 0)
        os.chmod(target, 0o600)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        raise MediaEgressConfigError(f"cannot write media egress configuration: {exc}") from exc
    return normalized


def write_jellyfin(values: Mapping[str, Any], path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    return write_config(
        {"schema_version": SCHEMA_VERSION, "config_id": CONFIG_ID, "jellyfin": dict(values)},
        path,
    )


def jellyfin_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    values = normalize_jellyfin(payload)
    url = values.get("OPEN_MMI_JELLYFIN_URL", "")
    token = bool(values.get("OPEN_MMI_JELLYFIN_TOKEN"))
    username = values.get("OPEN_MMI_JELLYFIN_USERNAME", "")
    password = bool(values.get("OPEN_MMI_JELLYFIN_PASSWORD"))
    auth_mode = "token" if token else "username" if username and password else ""
    return {
        "configured": bool(url and auth_mode),
        "url": url,
        "auth_mode": auth_mode,
        "username": username,
        "user_id": values.get("OPEN_MMI_JELLYFIN_USER_ID", ""),
        "library_id": values.get("OPEN_MMI_JELLYFIN_LIBRARY_ID", ""),
        "session_id": values.get("OPEN_MMI_JELLYFIN_SESSION_ID", ""),
        "device": values.get("OPEN_MMI_JELLYFIN_DEVICE", ""),
        "token_configured": token,
        "password_configured": password,
        "insecure_tls": values.get("OPEN_MMI_JELLYFIN_INSECURE_TLS", "0") == "1",
        "allow_global": values.get("OPEN_MMI_JELLYFIN_ALLOW_GLOBAL", "0") == "1",
        "authority_source": "root-owned-media-egress-config",
    }
