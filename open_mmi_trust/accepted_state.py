"""Accepted Owner Trust State v1 for Open MMI.

This module records the trust boundary that the local owner has accepted.  The
release Trust Manifest remains candidate-supplied evidence; accepted state is
separate root-owned local authority state.

Accepted-state mutation is intentionally private.  Normal runtime code may read
and compare the state, but official code outside the local owner CLI must not
write it.  V1 can bootstrap the currently installed boundary and record later
non-expanding/narrowing current boundaries.  It does not provide a way for
already-installed candidate code to broaden an existing accepted boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .manifest import (
    ASSURANCE_LEVELS,
    CAPABILITY_POLICIES,
    ManifestError,
    manifest_digest,
    validate_manifest,
)


ACCEPTED_STATE_SCHEMA_VERSION = 1
ACCEPTED_STATE_ID = "org.open-mmi.accepted-owner-trust"
DEFAULT_ACCEPTED_STATE_PATH = Path("/var/lib/open-mmi/trust/accepted-owner-trust.v1.json")

TRANSITION_EQUAL = "equal"
TRANSITION_NARROWER = "narrower"
TRANSITION_EXPANSION = "expansion"
TRANSITION_GENERATION_REGRESSION = "generation-regression"
TRANSITION_RELATIONS = {
    TRANSITION_EQUAL,
    TRANSITION_NARROWER,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
}

_STATE_KEYS = {
    "schema_version",
    "state_id",
    "accepted_at",
    "manifest_digest",
    "manifest",
}

_POLICY_RANK = {
    "vehicle.can.receive": {"prohibited": 0, "allowed": 1},
    "vehicle.can.transmit": {"prohibited": 0, "allowed": 1},
    "telemetry.collection": {"prohibited": 0, "local-owner-opt-in": 1},
    "vehicle.identity.remote-resolution": {"prohibited": 0, "allowed": 1},
    "network.external-egress": {
        "prohibited": 0,
        "declared-purposes-only": 1,
        "allowed": 2,
    },
    "vehicle-data.persistence": {
        "prohibited": 0,
        "declared-purposes-only": 1,
        "allowed": 2,
    },
}
_ASSURANCE_RANK = {
    "declared": 0,
    "ci-guarded": 1,
    "runtime-guarded": 2,
    "os-enforced": 3,
    "hardware-enforced": 4,
}
if set(_ASSURANCE_RANK) != set(ASSURANCE_LEVELS):  # defensive schema drift guard
    raise RuntimeError("accepted-state assurance ordering does not match manifest vocabulary")
if set(_POLICY_RANK) != set(CAPABILITY_POLICIES):
    raise RuntimeError("accepted-state policy ordering does not match manifest capabilities")
for _capability_id, _policies in CAPABILITY_POLICIES.items():
    if set(_POLICY_RANK[_capability_id]) != set(_policies):
        raise RuntimeError(
            f"accepted-state policy ordering is incomplete for {_capability_id}"
        )


class AcceptedTrustStateError(RuntimeError):
    """Accepted owner trust state is malformed, unsafe, or unavailable."""


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AcceptedTrustStateError(f"duplicate accepted trust state field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise AcceptedTrustStateError(f"invalid accepted trust state JSON number: {value}")


def _decode_json(text: str) -> Any:
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except AcceptedTrustStateError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AcceptedTrustStateError("accepted owner trust state is invalid JSON") from exc


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise AcceptedTrustStateError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise AcceptedTrustStateError(f"{label} is missing keys: {', '.join(missing)}")


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise AcceptedTrustStateError("accepted trust timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise AcceptedTrustStateError("accepted trust timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise AcceptedTrustStateError("accepted trust timestamp is invalid")
    return value


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return "sha256:" + manifest_digest(manifest)


def validate_accepted_state(payload: Any) -> dict[str, Any]:
    """Validate Accepted Owner Trust State v1 and return independent normalized data."""

    if not isinstance(payload, Mapping):
        raise AcceptedTrustStateError("accepted owner trust state must be an object")
    _require_exact_keys(payload, _STATE_KEYS, "accepted owner trust state")
    if payload["schema_version"] != ACCEPTED_STATE_SCHEMA_VERSION:
        raise AcceptedTrustStateError("unsupported accepted owner trust state schema_version")
    if payload["state_id"] != ACCEPTED_STATE_ID:
        raise AcceptedTrustStateError("unexpected accepted owner trust state id")

    try:
        manifest = validate_manifest(payload["manifest"])
    except ManifestError as exc:
        raise AcceptedTrustStateError(f"accepted manifest is invalid: {exc}") from exc

    digest = payload["manifest_digest"]
    expected_digest = _manifest_sha256(manifest)
    if digest != expected_digest:
        raise AcceptedTrustStateError("accepted manifest digest does not match embedded manifest")

    return {
        "schema_version": ACCEPTED_STATE_SCHEMA_VERSION,
        "state_id": ACCEPTED_STATE_ID,
        "accepted_at": _validate_timestamp(payload["accepted_at"]),
        "manifest_digest": expected_digest,
        "manifest": manifest,
    }


def canonical_accepted_state_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = validate_accepted_state(payload)
    return (
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")


def accepted_state_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_accepted_state_bytes(payload)).hexdigest()


def _production_path(path: Path) -> bool:
    try:
        return path.resolve(strict=False) == DEFAULT_ACCEPTED_STATE_PATH
    except OSError:
        return path == DEFAULT_ACCEPTED_STATE_PATH


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
        raise AcceptedTrustStateError("accepted trust state directory is unavailable") from exc
    expected_uid = 0 if require_root else os.geteuid()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid or metadata.st_mode & 0o077:
        raise AcceptedTrustStateError("accepted trust state directory is untrusted")


def read_accepted_state(path: Path = DEFAULT_ACCEPTED_STATE_PATH) -> dict[str, Any] | None:
    """Read trusted accepted-owner state, returning None when it is not established."""

    production = _production_path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AcceptedTrustStateError("accepted owner trust state file cannot be inspected") from exc
    if not _trusted_regular_file(path, require_root=production):
        raise AcceptedTrustStateError("accepted owner trust state file is untrusted")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AcceptedTrustStateError("accepted owner trust state file cannot be read") from exc
    return validate_accepted_state(_decode_json(text))


def _write_accepted_state(
    payload: Mapping[str, Any],
    path: Path = DEFAULT_ACCEPTED_STATE_PATH,
) -> dict[str, Any]:
    production = _production_path(path)
    if production and os.geteuid() != 0:
        raise AcceptedTrustStateError("production accepted trust state changes require root")

    validated = validate_accepted_state(payload)
    _ensure_parent(path, require_root=production)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise AcceptedTrustStateError("accepted owner trust state file cannot be inspected") from exc
    else:
        if not _trusted_regular_file(path, require_root=production):
            raise AcceptedTrustStateError("refusing to replace untrusted accepted trust state file")

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
        raise AcceptedTrustStateError("could not persist accepted owner trust state") from exc
    return validated


def _record_accepted_manifest(
    manifest: Mapping[str, Any],
    path: Path = DEFAULT_ACCEPTED_STATE_PATH,
) -> dict[str, Any]:
    """Record a bootstrap/equivalent/narrower accepted boundary only.

    This primitive deliberately cannot broaden an existing accepted boundary.
    A future pre-install owner-acknowledged expansion mechanism must be a separate
    old-trusted-side operation with transition-history evidence.
    """

    normalized = validate_manifest(manifest)
    existing = read_accepted_state(path)
    if existing is not None:
        comparison = compare_trust_manifests(existing["manifest"], normalized)
        if comparison["relation"] == TRANSITION_GENERATION_REGRESSION:
            raise AcceptedTrustStateError(
                "refusing accepted trust state generation regression"
            )
        if comparison["relation"] == TRANSITION_EXPANSION:
            raise AcceptedTrustStateError(
                "refusing to broaden existing accepted owner trust state"
            )

    payload = {
        "schema_version": ACCEPTED_STATE_SCHEMA_VERSION,
        "state_id": ACCEPTED_STATE_ID,
        "accepted_at": _timestamp(),
        "manifest_digest": _manifest_sha256(normalized),
        "manifest": normalized,
    }
    return _write_accepted_state(payload, path)


def compare_trust_manifests(
    accepted_manifest: Mapping[str, Any],
    candidate_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify candidate authority relative to an already accepted manifest.

    Equivalent or narrower authority can proceed without owner acknowledgement.
    Policy expansion, newly declared purposes, or weaker assurance is expansion.
    Generation regression is separately blocked because v1 has no trusted
    downgrade-lineage mechanism.
    """

    accepted = validate_manifest(accepted_manifest)
    candidate = validate_manifest(candidate_manifest)
    changes: list[dict[str, Any]] = []

    accepted_generation = accepted["policy_generation"]
    candidate_generation = candidate["policy_generation"]
    if candidate_generation < accepted_generation:
        changes.append(
            {
                "kind": "generation-regression",
                "accepted": accepted_generation,
                "candidate": candidate_generation,
            }
        )

    expansion = False
    narrowing = False
    for capability_id in sorted(accepted["capabilities"]):
        accepted_capability = accepted["capabilities"][capability_id]
        candidate_capability = candidate["capabilities"][capability_id]
        accepted_policy = accepted_capability["policy"]
        candidate_policy = candidate_capability["policy"]
        accepted_rank = _POLICY_RANK[capability_id][accepted_policy]
        candidate_rank = _POLICY_RANK[capability_id][candidate_policy]

        if candidate_rank > accepted_rank:
            expansion = True
            changes.append(
                {
                    "capability": capability_id,
                    "kind": "policy-expansion",
                    "accepted": accepted_policy,
                    "candidate": candidate_policy,
                }
            )
        elif candidate_rank < accepted_rank:
            narrowing = True
            changes.append(
                {
                    "capability": capability_id,
                    "kind": "policy-narrowing",
                    "accepted": accepted_policy,
                    "candidate": candidate_policy,
                }
            )

        if accepted_policy == candidate_policy == "declared-purposes-only":
            accepted_purposes = set(accepted_capability["purposes"])
            candidate_purposes = set(candidate_capability["purposes"])
            added = sorted(candidate_purposes - accepted_purposes)
            removed = sorted(accepted_purposes - candidate_purposes)
            if added:
                expansion = True
                changes.append(
                    {
                        "capability": capability_id,
                        "kind": "purposes-added",
                        "purposes": added,
                    }
                )
            if removed:
                narrowing = True
                changes.append(
                    {
                        "capability": capability_id,
                        "kind": "purposes-removed",
                        "purposes": removed,
                    }
                )

        accepted_assurance = accepted_capability["assurance"]
        candidate_assurance = candidate_capability["assurance"]
        accepted_assurance_rank = _ASSURANCE_RANK[accepted_assurance]
        candidate_assurance_rank = _ASSURANCE_RANK[candidate_assurance]
        if candidate_assurance_rank < accepted_assurance_rank:
            expansion = True
            changes.append(
                {
                    "capability": capability_id,
                    "kind": "assurance-weakened",
                    "accepted": accepted_assurance,
                    "candidate": candidate_assurance,
                }
            )
        elif candidate_assurance_rank > accepted_assurance_rank:
            narrowing = True
            changes.append(
                {
                    "capability": capability_id,
                    "kind": "assurance-strengthened",
                    "accepted": accepted_assurance,
                    "candidate": candidate_assurance,
                }
            )

    if candidate_generation < accepted_generation:
        relation = TRANSITION_GENERATION_REGRESSION
        allowed_without_owner_ack = False
    elif expansion:
        relation = TRANSITION_EXPANSION
        allowed_without_owner_ack = False
    elif narrowing:
        relation = TRANSITION_NARROWER
        allowed_without_owner_ack = True
    else:
        relation = TRANSITION_EQUAL
        allowed_without_owner_ack = True

    return {
        "relation": relation,
        "allowed_without_owner_ack": allowed_without_owner_ack,
        "accepted_generation": accepted_generation,
        "candidate_generation": candidate_generation,
        "accepted_manifest_digest": _manifest_sha256(accepted),
        "candidate_manifest_digest": _manifest_sha256(candidate),
        "changes": changes,
    }
