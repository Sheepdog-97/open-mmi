"""Fail-closed local telemetry authorization for Open MMI.

Telemetry Guard v1 deliberately authorizes only session-scoped, local-only
collection.  Durable telemetry retention and remote submission remain separate
trust-boundary changes and are not authorized by this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, TypeVar


SCOPE_SCHEMA_VERSION = 1
AUTHORIZATION_SCHEMA_VERSION = 1
AUTHORIZATION_ID = "org.open-mmi.telemetry-authorization"
DEFAULT_AUTHORIZATION_PATH = Path("/var/lib/open-mmi/trust/telemetry-authorization.v1.json")
VIN_ALGORITHM = "pbkdf2-sha256"
VIN_ITERATIONS = 200_000
VIN_SALT_BYTES = 16
MAX_SCOPE_SIGNALS = 128
MAX_IDENTIFIER_LENGTH = 96

_SCOPE_KEYS = {"schema_version", "purpose", "signals", "retention", "destination"}
_AUTHORIZATION_KEYS = {
    "schema_version",
    "authorization_id",
    "authorized_at",
    "vin_binding",
    "scope",
    "scope_digest",
}
_VIN_BINDING_KEYS = {"algorithm", "iterations", "salt", "fingerprint"}
_IDENTIFIER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,94}[a-z0-9])?$")
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")
_SCOPE_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

T = TypeVar("T")


class TelemetryGuardError(RuntimeError):
    """Telemetry authorization state or input is invalid."""


class TelemetryDenied(TelemetryGuardError):
    """Telemetry collection was denied before sampling began."""

    def __init__(self, reason: str):
        super().__init__(f"telemetry collection denied: {reason}")
        self.reason = reason


@dataclass(frozen=True)
class CollectionDecision:
    """One fail-closed telemetry collection decision."""

    allowed: bool
    reason: str
    scope_digest: str


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TelemetryGuardError(f"duplicate telemetry JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise TelemetryGuardError(f"invalid telemetry JSON number: {value}")


def _decode_json(text: str, label: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except TelemetryGuardError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TelemetryGuardError(f"{label} is invalid JSON") from exc


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise TelemetryGuardError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise TelemetryGuardError(f"{label} is missing keys: {', '.join(missing)}")


def _canonical_identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise TelemetryGuardError(f"{label} is invalid")
    if value.strip() != value or value.lower() != value or not _IDENTIFIER_RE.fullmatch(value):
        raise TelemetryGuardError(f"{label} must be a canonical lowercase ID")
    return value


def normalize_scope(payload: Any) -> dict[str, Any]:
    """Validate a Telemetry Guard v1 scope and return canonical independent data."""

    if not isinstance(payload, Mapping):
        raise TelemetryGuardError("telemetry scope must be an object")
    _require_exact_keys(payload, _SCOPE_KEYS, "telemetry scope")
    if payload["schema_version"] != SCOPE_SCHEMA_VERSION:
        raise TelemetryGuardError("unsupported telemetry scope schema_version")

    purpose = _canonical_identifier(payload["purpose"], "telemetry scope purpose")
    signals = payload["signals"]
    if not isinstance(signals, list) or not signals or len(signals) > MAX_SCOPE_SIGNALS:
        raise TelemetryGuardError("telemetry scope signals must be a non-empty bounded array")
    normalized_signals = [
        _canonical_identifier(signal, "telemetry signal") for signal in signals
    ]
    if normalized_signals != sorted(normalized_signals):
        raise TelemetryGuardError("telemetry scope signals must be sorted")
    if len(normalized_signals) != len(set(normalized_signals)):
        raise TelemetryGuardError("telemetry scope signals must not contain duplicates")

    if payload["retention"] != "session":
        raise TelemetryGuardError("Telemetry Guard v1 permits session retention only")
    if payload["destination"] != "local-only":
        raise TelemetryGuardError("Telemetry Guard v1 permits local-only collection only")

    return {
        "schema_version": SCOPE_SCHEMA_VERSION,
        "purpose": purpose,
        "signals": normalized_signals,
        "retention": "session",
        "destination": "local-only",
    }


def canonical_scope_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = normalize_scope(payload)
    return (
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def scope_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_scope_bytes(payload)).hexdigest()


def normalize_vin(vin: str) -> str:
    if not isinstance(vin, str):
        raise TelemetryGuardError("VIN must be text")
    normalized = vin.strip().upper()
    if not _VIN_RE.fullmatch(normalized):
        raise TelemetryGuardError("VIN must be a 17-character canonical VIN")
    return normalized


def _vin_fingerprint(vin: str, salt: bytes, iterations: int = VIN_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        normalize_vin(vin).encode("ascii"),
        salt,
        iterations,
    )


def _encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_bytes(value: Any, label: str, expected_length: int) -> bytes:
    if not isinstance(value, str):
        raise TelemetryGuardError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise TelemetryGuardError(f"{label} is invalid") from exc
    if len(decoded) != expected_length:
        raise TelemetryGuardError(f"{label} is invalid")
    return decoded


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise TelemetryGuardError("telemetry authorization timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TelemetryGuardError("telemetry authorization timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise TelemetryGuardError("telemetry authorization timestamp is invalid")
    return value


def validate_authorization(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TelemetryGuardError("telemetry authorization must be an object")
    _require_exact_keys(payload, _AUTHORIZATION_KEYS, "telemetry authorization")
    if payload["schema_version"] != AUTHORIZATION_SCHEMA_VERSION:
        raise TelemetryGuardError("unsupported telemetry authorization schema_version")
    if payload["authorization_id"] != AUTHORIZATION_ID:
        raise TelemetryGuardError("unexpected telemetry authorization id")

    vin_binding = payload["vin_binding"]
    if not isinstance(vin_binding, Mapping):
        raise TelemetryGuardError("telemetry VIN binding is invalid")
    _require_exact_keys(vin_binding, _VIN_BINDING_KEYS, "telemetry VIN binding")
    if vin_binding["algorithm"] != VIN_ALGORITHM:
        raise TelemetryGuardError("unsupported telemetry VIN binding algorithm")
    iterations = vin_binding["iterations"]
    if iterations != VIN_ITERATIONS:
        raise TelemetryGuardError("unsupported telemetry VIN binding work factor")
    salt = _decode_bytes(vin_binding["salt"], "telemetry VIN salt", VIN_SALT_BYTES)
    fingerprint = _decode_bytes(vin_binding["fingerprint"], "telemetry VIN fingerprint", 32)

    scope = normalize_scope(payload["scope"])
    digest = payload["scope_digest"]
    if not isinstance(digest, str) or not _SCOPE_DIGEST_RE.fullmatch(digest):
        raise TelemetryGuardError("telemetry scope digest is invalid")
    if digest != scope_digest(scope):
        raise TelemetryGuardError("telemetry scope digest does not match stored scope")

    return {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": AUTHORIZATION_ID,
        "authorized_at": _validate_timestamp(payload["authorized_at"]),
        "vin_binding": {
            "algorithm": VIN_ALGORITHM,
            "iterations": VIN_ITERATIONS,
            "salt": _encode_bytes(salt),
            "fingerprint": _encode_bytes(fingerprint),
        },
        "scope": scope,
        "scope_digest": digest,
    }


def _production_path(path: Path) -> bool:
    try:
        return path.resolve(strict=False) == DEFAULT_AUTHORIZATION_PATH
    except OSError:
        return path == DEFAULT_AUTHORIZATION_PATH


def _trusted_regular_file(path: Path, *, require_root: bool) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    expected_uid = 0 if require_root else os.geteuid()
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == expected_uid
        and metadata.st_nlink == 1
        and not metadata.st_mode & 0o077
    )


def _ensure_parent(path: Path, *, require_root: bool) -> None:
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        metadata = parent.lstat()
    except OSError as exc:
        raise TelemetryGuardError("telemetry authorization directory is unavailable") from exc
    expected_uid = 0 if require_root else os.geteuid()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid or metadata.st_mode & 0o077:
        raise TelemetryGuardError("telemetry authorization directory is untrusted")


def _require_mutation_authority(path: Path) -> bool:
    production = _production_path(path)
    if production and os.geteuid() != 0:
        raise TelemetryGuardError("production telemetry authorization changes require root")
    return production


def read_authorization(path: Path = DEFAULT_AUTHORIZATION_PATH) -> dict[str, Any] | None:
    production = _production_path(path)
    if not path.exists():
        return None
    if not _trusted_regular_file(path, require_root=production):
        raise TelemetryGuardError("telemetry authorization file is untrusted")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TelemetryGuardError("telemetry authorization file cannot be read") from exc
    return validate_authorization(_decode_json(text, "telemetry authorization file"))


def _write_authorization(payload: Mapping[str, Any], path: Path = DEFAULT_AUTHORIZATION_PATH) -> dict[str, Any]:
    production = _require_mutation_authority(path)
    validated = validate_authorization(payload)
    _ensure_parent(path, require_root=production)
    if path.exists() and not _trusted_regular_file(path, require_root=production):
        raise TelemetryGuardError("refusing to replace untrusted telemetry authorization file")

    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(validated, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise TelemetryGuardError("could not persist telemetry authorization") from exc
    return validated


def _create_authorization(vin: str, scope: Mapping[str, Any], path: Path = DEFAULT_AUTHORIZATION_PATH) -> dict[str, Any]:
    normalized_scope = normalize_scope(scope)
    salt = secrets.token_bytes(VIN_SALT_BYTES)
    payload = {
        "schema_version": AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": AUTHORIZATION_ID,
        "authorized_at": _timestamp(),
        "vin_binding": {
            "algorithm": VIN_ALGORITHM,
            "iterations": VIN_ITERATIONS,
            "salt": _encode_bytes(salt),
            "fingerprint": _encode_bytes(_vin_fingerprint(vin, salt)),
        },
        "scope": normalized_scope,
        "scope_digest": scope_digest(normalized_scope),
    }
    return _write_authorization(payload, path)


def _revoke_authorization(path: Path = DEFAULT_AUTHORIZATION_PATH) -> bool:
    production = _require_mutation_authority(path)
    if not path.exists():
        return False
    if not _trusted_regular_file(path, require_root=production):
        raise TelemetryGuardError("refusing to remove untrusted telemetry authorization file")
    try:
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise TelemetryGuardError("could not revoke telemetry authorization") from exc
    return True


def collection_decision(
    vin: str,
    scope: Mapping[str, Any],
    path: Path = DEFAULT_AUTHORIZATION_PATH,
) -> CollectionDecision:
    try:
        normalized_scope = normalize_scope(scope)
        requested_digest = scope_digest(normalized_scope)
        normalized_vin = normalize_vin(vin)
    except TelemetryGuardError:
        return CollectionDecision(False, "invalid-request", "")

    try:
        authorization = read_authorization(path)
    except TelemetryGuardError:
        return CollectionDecision(False, "authorization-invalid", requested_digest)
    if authorization is None:
        return CollectionDecision(False, "not-authorized", requested_digest)
    if authorization["scope_digest"] != requested_digest:
        return CollectionDecision(False, "scope-mismatch", requested_digest)

    binding = authorization["vin_binding"]
    try:
        salt = _decode_bytes(binding["salt"], "telemetry VIN salt", VIN_SALT_BYTES)
        expected = _decode_bytes(binding["fingerprint"], "telemetry VIN fingerprint", 32)
        actual = _vin_fingerprint(normalized_vin, salt, binding["iterations"])
    except TelemetryGuardError:
        return CollectionDecision(False, "authorization-invalid", requested_digest)
    if not hmac.compare_digest(actual, expected):
        return CollectionDecision(False, "vehicle-mismatch", requested_digest)
    return CollectionDecision(True, "authorized", requested_digest)


def require_collection_allowed(
    vin: str,
    scope: Mapping[str, Any],
    path: Path = DEFAULT_AUTHORIZATION_PATH,
) -> CollectionDecision:
    decision = collection_decision(vin, scope, path)
    if not decision.allowed:
        raise TelemetryDenied(decision.reason)
    return decision


def collect_with_guard(
    vin: str,
    scope: Mapping[str, Any],
    samplers: Mapping[str, Callable[[], T]],
    path: Path = DEFAULT_AUTHORIZATION_PATH,
) -> dict[str, T]:
    """Sample exactly the declared signals, only after authorization succeeds."""

    normalized_scope = normalize_scope(scope)
    expected_signals = normalized_scope["signals"]
    if set(samplers) != set(expected_signals):
        raise TelemetryGuardError(
            "telemetry samplers must match the authorized signal set exactly"
        )
    require_collection_allowed(vin, normalized_scope, path)
    return {signal: samplers[signal]() for signal in expected_signals}


def load_scope_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise TelemetryGuardError(f"cannot read telemetry scope: {path}") from exc
    return normalize_scope(_decode_json(text, "telemetry scope"))
