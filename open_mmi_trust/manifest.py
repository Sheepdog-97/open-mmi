"""Strict parser and deterministic digest for Open MMI Trust Manifest v1."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_MANIFEST_PATH = Path(__file__).with_name("data") / "trust-manifest.v1.json"
MANIFEST_ID = "org.open-mmi.trust-manifest"
SCHEMA_VERSION = 1

CAPABILITY_POLICIES = {
    "vehicle.can.receive": {"allowed", "prohibited"},
    "vehicle.can.transmit": {"allowed", "prohibited"},
    "telemetry.collection": {"prohibited", "local-owner-opt-in"},
    "vehicle.identity.remote-resolution": {"allowed", "prohibited"},
    "network.external-egress": {"prohibited", "declared-purposes-only", "allowed"},
    "vehicle-data.persistence": {"prohibited", "declared-purposes-only", "allowed"},
}

ASSURANCE_LEVELS = {
    "declared",
    "ci-guarded",
    "runtime-guarded",
    "os-enforced",
    "hardware-enforced",
}

PURPOSE_CAPABILITIES = {
    "network.external-egress",
    "vehicle-data.persistence",
}

TOP_LEVEL_KEYS = {
    "schema_version",
    "manifest_id",
    "policy_generation",
    "capabilities",
}
CAPABILITY_KEYS = {"policy", "assurance", "purposes"}


class ManifestError(ValueError):
    """Raised when a trust manifest is malformed or semantically unsupported."""


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ManifestError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise ManifestError(f"{label} is missing keys: {', '.join(missing)}")


def _validate_purposes(capability_id: str, value: Any) -> list[str]:
    if not isinstance(value, list):
        raise ManifestError(f"{capability_id}.purposes must be an array")
    if value != sorted(value):
        raise ManifestError(f"{capability_id}.purposes must be sorted")
    if len(value) != len(set(value)):
        raise ManifestError(f"{capability_id}.purposes must not contain duplicates")
    for purpose in value:
        if not isinstance(purpose, str) or not purpose or len(purpose) > 96:
            raise ManifestError(f"{capability_id}.purposes contains an invalid purpose ID")
        if purpose.strip() != purpose or purpose.lower() != purpose:
            raise ManifestError(f"{capability_id}.purposes must use lowercase canonical IDs")
        if any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for ch in purpose):
            raise ManifestError(f"{capability_id}.purposes contains a non-canonical ID: {purpose!r}")
    return list(value)


def validate_manifest(payload: Any) -> dict[str, Any]:
    """Validate Trust Manifest v1 and return a normalized independent mapping."""

    if not isinstance(payload, Mapping):
        raise ManifestError("trust manifest root must be an object")
    _require_exact_keys(payload, TOP_LEVEL_KEYS, "trust manifest")

    if payload["schema_version"] != SCHEMA_VERSION:
        raise ManifestError(f"unsupported trust manifest schema_version: {payload['schema_version']!r}")
    if payload["manifest_id"] != MANIFEST_ID:
        raise ManifestError(f"unexpected trust manifest id: {payload['manifest_id']!r}")
    generation = payload["policy_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ManifestError("policy_generation must be a positive integer")

    capabilities = payload["capabilities"]
    if not isinstance(capabilities, Mapping):
        raise ManifestError("capabilities must be an object")
    if set(capabilities) != set(CAPABILITY_POLICIES):
        missing = sorted(set(CAPABILITY_POLICIES) - set(capabilities))
        unknown = sorted(set(capabilities) - set(CAPABILITY_POLICIES))
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ManifestError("capability set does not match schema v1 (" + "; ".join(details) + ")")

    normalized_capabilities: dict[str, dict[str, Any]] = {}
    for capability_id in sorted(CAPABILITY_POLICIES):
        entry = capabilities[capability_id]
        if not isinstance(entry, Mapping):
            raise ManifestError(f"{capability_id} must be an object")
        expected_keys = set(CAPABILITY_KEYS)
        if capability_id not in PURPOSE_CAPABILITIES:
            expected_keys.remove("purposes")
        _require_exact_keys(entry, expected_keys, capability_id)

        policy = entry["policy"]
        if policy not in CAPABILITY_POLICIES[capability_id]:
            raise ManifestError(f"unsupported {capability_id} policy: {policy!r}")
        assurance = entry["assurance"]
        if assurance not in ASSURANCE_LEVELS:
            raise ManifestError(f"unsupported {capability_id} assurance: {assurance!r}")

        normalized: dict[str, Any] = {"policy": policy, "assurance": assurance}
        if capability_id in PURPOSE_CAPABILITIES:
            purposes = _validate_purposes(capability_id, entry["purposes"])
            if policy == "declared-purposes-only" and not purposes:
                raise ManifestError(f"{capability_id} must declare at least one purpose")
            if policy != "declared-purposes-only" and purposes:
                raise ManifestError(
                    f"{capability_id}.purposes must be empty unless policy is declared-purposes-only"
                )
            normalized["purposes"] = purposes
        normalized_capabilities[capability_id] = normalized

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": MANIFEST_ID,
        "policy_generation": generation,
        "capabilities": normalized_capabilities,
    }


def load_manifest(path: Path | str | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"trust manifest does not exist: {target}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read trust manifest {target}: {exc}") from exc
    return validate_manifest(payload)


def canonical_manifest_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = validate_manifest(payload)
    return (json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def manifest_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()
