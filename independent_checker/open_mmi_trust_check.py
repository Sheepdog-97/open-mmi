#!/usr/bin/env python3
"""Standalone, read-only Open MMI trust checker.

This checker is intentionally independent from the installed Open MMI Python
runtime.  It uses only the Python standard library plus fixed system ``git`` and
``gpg`` executables for signature verification.  It never imports or executes
``open_mmi_trust`` (or any other installed Open MMI package).
"""

from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

PASS = "PASS"
UNVERIFIED = "UNVERIFIED"
FAIL = "FAIL"
STATUSES = {PASS, UNVERIFIED, FAIL}

MANIFEST_ID = "org.open-mmi.trust-manifest"
MANIFEST_SCHEMA_VERSION = 1
ACCEPTED_STATE_ID = "org.open-mmi.accepted-owner-trust"
ACCEPTED_STATE_SCHEMA_VERSION = 1
LINEAGE_RECORD_ID = "org.open-mmi.trust-transition-lineage-record"
LINEAGE_SCHEMA_VERSION = 1
INTEGRITY_STATE_ID = "org.open-mmi.installed-release-integrity"
INTEGRITY_SCHEMA_VERSION = 1
PROVENANCE_ROOT_ID = "org.open-mmi.release-signer-root"
PROVENANCE_SCHEMA_VERSION = 1

MAX_INVENTORY_FILES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_LINEAGE_RECORDS = 4096
MAX_LINEAGE_RECORD_BYTES = 128 * 1024
MAX_PUBLIC_KEY_BYTES = 512 * 1024
MAX_VERIFIER_OUTPUT_BYTES = 1024 * 1024

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
PURPOSE_CAPABILITIES = {"network.external-egress", "vehicle-data.persistence"}
POLICY_RANK = {
    "vehicle.can.receive": {"prohibited": 0, "allowed": 1},
    "vehicle.can.transmit": {"prohibited": 0, "allowed": 1},
    "telemetry.collection": {"prohibited": 0, "local-owner-opt-in": 1},
    "vehicle.identity.remote-resolution": {"prohibited": 0, "allowed": 1},
    "network.external-egress": {"prohibited": 0, "declared-purposes-only": 1, "allowed": 2},
    "vehicle-data.persistence": {"prohibited": 0, "declared-purposes-only": 1, "allowed": 2},
}
ASSURANCE_RANK = {
    "declared": 0,
    "ci-guarded": 1,
    "runtime-guarded": 2,
    "os-enforced": 3,
    "hardware-enforced": 4,
}

PACKAGE_RUNTIME_ROOTS = (
    "actions",
    "bindings",
    "canbusd",
    "powerd",
    "open_mmi_telemetry",
    "open_mmi_trust",
    "ui",
    "vehicles",
)
SOURCE_RUNTIME_ROOTS = ("actions", "bindings", "canbusd", "powerd", "ui", "vehicles")
SOURCE_RELEASE_ROOTS = (*SOURCE_RUNTIME_ROOTS, "scripts", "packaging", "systemd")
SOURCE_RELEASE_FILES = ("LICENSE", "README.md", "pyproject.toml")
INVENTORY_ROOTS = (*PACKAGE_RUNTIME_ROOTS, "scripts", "packaging", "systemd")
PACKAGE_SOURCE_ONLY_PATHS = {"ui/web_dashboard/README.md"}
PRIVILEGED_SYSTEM_UNITS = (
    "open-mmi-update-coordinator.service",
    "open-mmi-update-installer.service",
    "open-mmi-media-egress.service",
    "open-mmi-vehicle-store.service",
    "open-mmi-vehicle-can-provision.service",
)
PRIVILEGED_USER_UNITS = (
    "canbusd.service",
    "open-mmi-dashboard.service",
    "open-mmi-owner-config.service",
)

KNOWN_NETWORK_PURPOSES = ["media.internet-radio", "media.jellyfin", "updates.release-fetch"]
KNOWN_PERSISTENCE_PURPOSES = [
    "service-reminder",
    "trip-a",
    "trip-b",
    "trip-distance",
    "vehicle-runtime-status",
]

COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FINGERPRINT_RE = re.compile(r"^(?:[0-9A-F]{40}|[0-9A-F]{64})$")
PYTHON_LIB_RE = re.compile(r"^python[0-9]+\.[0-9]+$")
RECORD_FILENAME_RE = re.compile(r"^(?P<sequence>[0-9]{8})-(?P<digest>[0-9a-f]{64})\.json$")
TRANSACTION_RE = re.compile(r"^prepare-[0-9a-f]{32}$")

RELATION_EQUAL = "equal"
RELATION_NARROWER = "narrower"
RELATION_EXPANSION = "expansion"
RELATION_GENERATION_REGRESSION = "generation-regression"
RELATION_BASELINE = "baseline"

LINEAGE_SOURCES = {
    "existing-accepted-state",
    "prepared-update",
    "accepted-state-cli",
    "lineage-reconcile",
}
LINEAGE_DECISIONS = {
    "baseline-existing-state",
    "allowed-without-owner-acknowledgement",
    "owner-acknowledged-expansion",
    "local-owner-accepted-state",
    "local-owner-lineage-reconcile",
}
ACK_METHODS = {
    "none",
    "local-interactive-transition",
    "local-interactive-accepted-state",
    "local-interactive-lineage-baseline",
    "local-interactive-lineage-reconcile",
}

TARGET_PATHS = {
    "accepted": "/var/lib/open-mmi/trust/accepted-owner-trust.v1.json",
    "integrity": "/var/lib/open-mmi/trust/installed-release-integrity.v1.json",
    "provenance": "/var/lib/open-mmi/trust/release-signer-root.v1.json",
    "lineage": "/var/lib/open-mmi/trust/transition-lineage.v1.d",
    "install_root": "/opt/open-mmi",
    "source_descriptor": "/opt/open-mmi/.update-source.json",
    "system_units": "/etc/systemd/system",
    "user_units": "/etc/systemd/user",
    "udev_rules": "/etc/udev/rules.d/80-canbus.rules",
}


class CheckerError(RuntimeError):
    """Evidence is malformed, contradictory, or unsafe."""


class EvidenceUnavailable(CheckerError):
    """Evidence exists or is expected, but this checker cannot currently read it."""


def check(check_id: str, status: str, summary: str, **evidence: Any) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(status)
    return {"id": check_id, "status": status, "summary": summary, "evidence": evidence}


def overall_status(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(item.get("status")) for item in checks}
    if FAIL in statuses:
        return FAIL
    if UNVERIFIED in statuses:
        return UNVERIFIED
    return PASS


def target_path(target_root: Path, absolute: str | Path) -> Path:
    value = Path(absolute)
    if not value.is_absolute():
        raise CheckerError("target path must be absolute")
    return target_root / value.relative_to("/")


def timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise CheckerError(f"{label} timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CheckerError(f"{label} timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise CheckerError(f"{label} timestamp is invalid")
    return value


def exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise CheckerError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise CheckerError(f"{label} is missing keys: {', '.join(missing)}")


def strict_json_bytes(raw: bytes, label: str) -> Any:
    if not raw:
        raise CheckerError(f"{label} is empty")

    def unique(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CheckerError(f"{label} contains duplicate JSON field: {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise CheckerError(f"{label} contains invalid JSON number: {value}")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=reject)
    except CheckerError:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CheckerError(f"{label} is invalid JSON") from exc


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def validate_digest(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise CheckerError(f"{label} is invalid")
    return value


def trusted_file(path: Path, uid: int, *, private: bool = False, max_size: int | None = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CheckerError(f"cannot inspect {path}") from exc
    bad_mode = 0o077 if private else 0o022
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != uid
        or metadata.st_mode & bad_mode
        or (max_size is not None and metadata.st_size > max_size)
    ):
        raise CheckerError(f"untrusted file metadata: {path}")
    return metadata


def trusted_directory(path: Path, uid: int, *, private: bool = False) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise CheckerError(f"cannot inspect {path}") from exc
    bad_mode = 0o077 if private else 0o022
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != uid or metadata.st_mode & bad_mode:
        raise CheckerError(f"untrusted directory metadata: {path}")
    return metadata


def read_trusted_json(path: Path, uid: int, label: str, *, private: bool = True, max_size: int = 1024 * 1024) -> Any:
    trusted_file(path, uid, private=private, max_size=max_size)
    try:
        raw = path.read_bytes()
    except PermissionError as exc:
        raise EvidenceUnavailable(f"cannot read {label}: permission denied") from exc
    except OSError as exc:
        raise CheckerError(f"cannot read {label}") from exc
    if len(raw) > max_size:
        raise CheckerError(f"{label} exceeds size limit")
    return strict_json_bytes(raw, label)


def validate_manifest(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise CheckerError("trust manifest root must be an object")
    exact_keys(payload, {"schema_version", "manifest_id", "policy_generation", "capabilities"}, "trust manifest")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise CheckerError("unsupported trust manifest schema_version")
    if payload["manifest_id"] != MANIFEST_ID:
        raise CheckerError("unexpected trust manifest id")
    generation = payload["policy_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise CheckerError("trust manifest policy_generation is invalid")
    capabilities = payload["capabilities"]
    if not isinstance(capabilities, Mapping) or set(capabilities) != set(CAPABILITY_POLICIES):
        raise CheckerError("trust manifest capability set does not match checker v1 vocabulary")
    normalized: dict[str, dict[str, Any]] = {}
    for capability_id in sorted(CAPABILITY_POLICIES):
        entry = capabilities[capability_id]
        if not isinstance(entry, Mapping):
            raise CheckerError(f"{capability_id} must be an object")
        keys = {"policy", "assurance", "purposes"} if capability_id in PURPOSE_CAPABILITIES else {"policy", "assurance"}
        exact_keys(entry, keys, capability_id)
        policy = entry["policy"]
        assurance = entry["assurance"]
        if policy not in CAPABILITY_POLICIES[capability_id]:
            raise CheckerError(f"unsupported {capability_id} policy")
        if assurance not in ASSURANCE_LEVELS:
            raise CheckerError(f"unsupported {capability_id} assurance")
        item: dict[str, Any] = {"policy": policy, "assurance": assurance}
        if capability_id in PURPOSE_CAPABILITIES:
            purposes = entry["purposes"]
            if not isinstance(purposes, list) or purposes != sorted(purposes) or len(purposes) != len(set(purposes)):
                raise CheckerError(f"{capability_id}.purposes is not sorted and unique")
            for purpose in purposes:
                if (
                    not isinstance(purpose, str)
                    or not purpose
                    or len(purpose) > 96
                    or purpose.strip() != purpose
                    or purpose.lower() != purpose
                    or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for ch in purpose)
                ):
                    raise CheckerError(f"{capability_id}.purposes contains an invalid id")
            if policy == "declared-purposes-only" and not purposes:
                raise CheckerError(f"{capability_id} requires at least one purpose")
            if policy != "declared-purposes-only" and purposes:
                raise CheckerError(f"{capability_id}.purposes must be empty for this policy")
            item["purposes"] = list(purposes)
        normalized[capability_id] = item
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "manifest_id": MANIFEST_ID,
        "policy_generation": generation,
        "capabilities": normalized,
    }


def manifest_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(validate_manifest(payload)))


def validate_accepted_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise CheckerError("accepted owner trust state must be an object")
    exact_keys(payload, {"schema_version", "state_id", "accepted_at", "manifest_digest", "manifest"}, "accepted owner trust state")
    if payload["schema_version"] != ACCEPTED_STATE_SCHEMA_VERSION or payload["state_id"] != ACCEPTED_STATE_ID:
        raise CheckerError("accepted owner trust state schema/id is invalid")
    manifest = validate_manifest(payload["manifest"])
    digest = manifest_digest(manifest)
    if payload["manifest_digest"] != digest:
        raise CheckerError("accepted owner trust manifest digest does not match embedded manifest")
    return {
        "schema_version": ACCEPTED_STATE_SCHEMA_VERSION,
        "state_id": ACCEPTED_STATE_ID,
        "accepted_at": timestamp(payload["accepted_at"], "accepted owner trust state"),
        "manifest_digest": digest,
        "manifest": manifest,
    }


def accepted_state_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(validate_accepted_state(payload)))


def compare_manifests(accepted_payload: Mapping[str, Any], candidate_payload: Mapping[str, Any]) -> dict[str, Any]:
    accepted = validate_manifest(accepted_payload)
    candidate = validate_manifest(candidate_payload)
    changes: list[dict[str, Any]] = []
    expansion = False
    narrowing = False
    accepted_generation = accepted["policy_generation"]
    candidate_generation = candidate["policy_generation"]
    if candidate_generation < accepted_generation:
        changes.append({"kind": "generation-regression", "accepted": accepted_generation, "candidate": candidate_generation})
    for capability_id in sorted(accepted["capabilities"]):
        before = accepted["capabilities"][capability_id]
        after = candidate["capabilities"][capability_id]
        before_policy = before["policy"]
        after_policy = after["policy"]
        if POLICY_RANK[capability_id][after_policy] > POLICY_RANK[capability_id][before_policy]:
            expansion = True
            changes.append({"capability": capability_id, "kind": "policy-expansion", "accepted": before_policy, "candidate": after_policy})
        elif POLICY_RANK[capability_id][after_policy] < POLICY_RANK[capability_id][before_policy]:
            narrowing = True
            changes.append({"capability": capability_id, "kind": "policy-narrowing", "accepted": before_policy, "candidate": after_policy})
        if before_policy == after_policy == "declared-purposes-only":
            before_p = set(before["purposes"])
            after_p = set(after["purposes"])
            added = sorted(after_p - before_p)
            removed = sorted(before_p - after_p)
            if added:
                expansion = True
                changes.append({"capability": capability_id, "kind": "purposes-added", "purposes": added})
            if removed:
                narrowing = True
                changes.append({"capability": capability_id, "kind": "purposes-removed", "purposes": removed})
        before_a = before["assurance"]
        after_a = after["assurance"]
        if ASSURANCE_RANK[after_a] < ASSURANCE_RANK[before_a]:
            expansion = True
            changes.append({"capability": capability_id, "kind": "assurance-weakened", "accepted": before_a, "candidate": after_a})
        elif ASSURANCE_RANK[after_a] > ASSURANCE_RANK[before_a]:
            narrowing = True
            changes.append({"capability": capability_id, "kind": "assurance-strengthened", "accepted": before_a, "candidate": after_a})
    if candidate_generation < accepted_generation:
        relation = RELATION_GENERATION_REGRESSION
    elif expansion:
        relation = RELATION_EXPANSION
    elif narrowing:
        relation = RELATION_NARROWER
    else:
        relation = RELATION_EQUAL
    return {"relation": relation, "changes": changes}


def validate_lineage_record(payload: Any) -> dict[str, Any]:
    keys = {
        "schema_version", "record_id", "sequence", "recorded_at", "previous_record_digest", "source",
        "transaction_id", "candidate_commit", "accepted_state_before_digest", "accepted_state_after_digest",
        "accepted_manifest_before_digest", "accepted_manifest_after_digest", "policy_generation_before",
        "policy_generation_after", "accepted_state_after", "manifest_after", "relation", "changes", "decision",
        "owner_acknowledgement", "authorization_digest",
    }
    if not isinstance(payload, Mapping):
        raise CheckerError("transition lineage record must be an object")
    exact_keys(payload, keys, "transition lineage record")
    if payload["schema_version"] != LINEAGE_SCHEMA_VERSION or payload["record_id"] != LINEAGE_RECORD_ID:
        raise CheckerError("transition lineage schema/id is invalid")
    sequence = payload["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise CheckerError("transition lineage sequence is invalid")
    if payload["source"] not in LINEAGE_SOURCES or payload["decision"] not in LINEAGE_DECISIONS:
        raise CheckerError("transition lineage source/decision is invalid")
    if payload["relation"] not in {RELATION_BASELINE, RELATION_EQUAL, RELATION_NARROWER, RELATION_EXPANSION}:
        raise CheckerError("transition lineage relation is invalid")
    previous = validate_digest(payload["previous_record_digest"], "previous lineage digest", nullable=True)
    before_state = validate_digest(payload["accepted_state_before_digest"], "accepted state before digest", nullable=True)
    after_state = validate_digest(payload["accepted_state_after_digest"], "accepted state after digest")
    before_manifest = validate_digest(payload["accepted_manifest_before_digest"], "accepted manifest before digest", nullable=True)
    after_manifest = validate_digest(payload["accepted_manifest_after_digest"], "accepted manifest after digest")
    authorization = validate_digest(payload["authorization_digest"], "transition authorization digest", nullable=True)
    transaction_id = payload["transaction_id"]
    candidate_commit = payload["candidate_commit"]
    if transaction_id is not None and (not isinstance(transaction_id, str) or not TRANSACTION_RE.fullmatch(transaction_id)):
        raise CheckerError("transition lineage transaction id is invalid")
    if candidate_commit is not None and (not isinstance(candidate_commit, str) or not COMMIT_RE.fullmatch(candidate_commit)):
        raise CheckerError("transition lineage candidate commit is invalid")
    if (transaction_id is None) != (candidate_commit is None):
        raise CheckerError("transition lineage candidate identity is incomplete")
    before_generation = payload["policy_generation_before"]
    after_generation = payload["policy_generation_after"]
    if before_generation is not None and (not isinstance(before_generation, int) or isinstance(before_generation, bool) or before_generation < 1):
        raise CheckerError("transition lineage prior generation is invalid")
    if not isinstance(after_generation, int) or isinstance(after_generation, bool) or after_generation < 1:
        raise CheckerError("transition lineage generation is invalid")
    accepted_after = validate_accepted_state(payload["accepted_state_after"])
    manifest_after = validate_manifest(payload["manifest_after"])
    if accepted_state_digest(accepted_after) != after_state:
        raise CheckerError("transition lineage accepted-state snapshot digest mismatch")
    if accepted_after["manifest"] != manifest_after or accepted_after["manifest_digest"] != after_manifest or manifest_digest(manifest_after) != after_manifest:
        raise CheckerError("transition lineage manifest snapshot mismatch")
    if manifest_after["policy_generation"] != after_generation:
        raise CheckerError("transition lineage generation does not match manifest")
    changes = payload["changes"]
    if not isinstance(changes, list) or any(not isinstance(item, Mapping) for item in changes):
        raise CheckerError("transition lineage changes are invalid")
    ack = payload["owner_acknowledgement"]
    if not isinstance(ack, Mapping):
        raise CheckerError("transition lineage acknowledgement is invalid")
    exact_keys(ack, {"required", "method"}, "transition lineage owner acknowledgement")
    if not isinstance(ack["required"], bool) or ack["method"] not in ACK_METHODS or ack["required"] == (ack["method"] == "none"):
        raise CheckerError("transition lineage acknowledgement is inconsistent")
    if sequence == 1:
        if payload["relation"] != RELATION_BASELINE or payload["source"] != "existing-accepted-state" or payload["decision"] != "baseline-existing-state":
            raise CheckerError("first lineage record is not a baseline")
        if any(value is not None for value in (previous, before_state, before_manifest, before_generation)) or changes or transaction_id is not None or authorization is not None:
            raise CheckerError("lineage baseline claims prior/update history")
        if not ack["required"] or ack["method"] != "local-interactive-lineage-baseline":
            raise CheckerError("lineage baseline lacks owner confirmation")
    else:
        if payload["relation"] == RELATION_BASELINE or previous is None or before_state is None or before_manifest is None or before_generation is None:
            raise CheckerError("transition lineage record is missing prior-state evidence")
        if payload["relation"] == RELATION_EXPANSION and (not ack["required"] or ack["method"] not in {"local-interactive-transition", "local-interactive-lineage-reconcile"} or authorization is None):
            raise CheckerError("lineage expansion lacks owner authorization evidence")
        if payload["decision"] == "allowed-without-owner-acknowledgement" and ack["required"]:
            raise CheckerError("automatic lineage decision claims owner acknowledgement")
    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "record_id": LINEAGE_RECORD_ID,
        "sequence": sequence,
        "recorded_at": timestamp(payload["recorded_at"], "transition lineage"),
        "previous_record_digest": previous,
        "source": payload["source"],
        "transaction_id": transaction_id,
        "candidate_commit": candidate_commit,
        "accepted_state_before_digest": before_state,
        "accepted_state_after_digest": after_state,
        "accepted_manifest_before_digest": before_manifest,
        "accepted_manifest_after_digest": after_manifest,
        "policy_generation_before": before_generation,
        "policy_generation_after": after_generation,
        "accepted_state_after": accepted_after,
        "manifest_after": manifest_after,
        "relation": payload["relation"],
        "changes": [dict(item) for item in changes],
        "decision": payload["decision"],
        "owner_acknowledgement": {"required": ack["required"], "method": ack["method"]},
        "authorization_digest": authorization,
    }


def lineage_record_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(validate_lineage_record(payload)))


def read_lineage(path: Path, uid: int) -> list[dict[str, Any]]:
    trusted_directory(path, uid, private=True)
    entries = sorted(path.iterdir(), key=lambda item: item.name)
    if not entries:
        raise CheckerError("transition lineage is not established")
    if len(entries) > MAX_LINEAGE_RECORDS:
        raise CheckerError("transition lineage record limit exceeded")
    records: list[dict[str, Any]] = []
    previous_digest: str | None = None
    previous_record: dict[str, Any] | None = None
    for expected_sequence, entry in enumerate(entries, start=1):
        match = RECORD_FILENAME_RE.fullmatch(entry.name)
        if match is None or int(match.group("sequence")) != expected_sequence:
            raise CheckerError("transition lineage filenames are not contiguous/canonical")
        trusted_file(entry, uid, private=True, max_size=MAX_LINEAGE_RECORD_BYTES)
        try:
            raw = entry.read_bytes()
        except PermissionError as exc:
            raise EvidenceUnavailable("transition lineage record is not readable") from exc
        except OSError as exc:
            raise CheckerError("transition lineage record cannot be read") from exc
        record = validate_lineage_record(strict_json_bytes(raw, "transition lineage record"))
        if record["sequence"] != expected_sequence:
            raise CheckerError("transition lineage record sequence does not match filename")
        digest = lineage_record_digest(record)
        if digest != "sha256:" + match.group("digest"):
            raise CheckerError("transition lineage record digest does not match filename")
        if record["previous_record_digest"] != previous_digest:
            raise CheckerError("transition lineage hash chain is broken")
        if previous_record is not None:
            if record["accepted_state_before_digest"] != previous_record["accepted_state_after_digest"]:
                raise CheckerError("transition lineage accepted-state chain is broken")
            if record["accepted_manifest_before_digest"] != previous_record["accepted_manifest_after_digest"]:
                raise CheckerError("transition lineage manifest chain is broken")
            if record["policy_generation_before"] != previous_record["policy_generation_after"]:
                raise CheckerError("transition lineage generation chain is broken")
            comparison = compare_manifests(previous_record["manifest_after"], record["manifest_after"])
            if comparison["relation"] == RELATION_GENERATION_REGRESSION:
                raise CheckerError("transition lineage contains generation regression")
            if record["relation"] != comparison["relation"] or record["changes"] != comparison["changes"]:
                raise CheckerError("transition lineage relation/change evidence does not match manifests")
        records.append(record)
        previous_digest = digest
        previous_record = record
    return records


def safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise CheckerError("integrity inventory path is invalid")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise CheckerError("integrity inventory path is invalid") from exc
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise CheckerError("integrity inventory path contains control characters")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise CheckerError("integrity inventory path is unsafe")
    if not path.parts or (value not in SOURCE_RELEASE_FILES and path.parts[0] not in INVENTORY_ROOTS):
        raise CheckerError("integrity inventory path is outside managed roots")
    return value


def is_package_runtime_path(path: str) -> bool:
    item = PurePosixPath(path)
    if not item.parts or item.parts[0] not in PACKAGE_RUNTIME_ROOTS:
        return False
    suffix = item.suffix.lower()
    if suffix == ".py":
        return True
    if item.parts[0] == "bindings" and len(item.parts) == 2 and suffix == ".json":
        return True
    if path.startswith("canbusd/data/") and suffix == ".json":
        return True
    if path.startswith("open_mmi_trust/data/") and suffix == ".json":
        return True
    if path.startswith("vehicles/") and suffix in {".json", ".md"}:
        return True
    if path.startswith("ui/web_dashboard/static/") and suffix in {".css", ".html", ".js", ".md", ".png", ".svg", ".txt"}:
        return True
    return False


def is_inventory_path(path: str) -> bool:
    if path in SOURCE_RELEASE_FILES or path in PACKAGE_SOURCE_ONLY_PATHS:
        return True
    if is_package_runtime_path(path):
        return True
    item = PurePosixPath(path)
    suffix = item.suffix.lower()
    if path.startswith("scripts/"):
        return suffix in {".py", ".sh"} or item.name == "open-mmi-desktop"
    if path.startswith("systemd/"):
        return suffix == ".service"
    if path.startswith("packaging/"):
        return suffix in {".conf", ".desktop", ".png", ".rules", ".svg"}
    return False


def validate_inventory(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload or len(payload) > MAX_INVENTORY_FILES:
        raise CheckerError("integrity inventory is empty or too large")
    entries: list[dict[str, Any]] = []
    total = 0
    for raw in payload:
        if not isinstance(raw, Mapping):
            raise CheckerError("integrity inventory entry must be an object")
        exact_keys(raw, {"path", "sha256", "size"}, "integrity inventory entry")
        path = safe_relative_path(raw["path"])
        if not is_inventory_path(path):
            raise CheckerError(f"integrity inventory path is outside checker v1 scope: {path}")
        digest = raw["sha256"]
        size = raw["size"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise CheckerError("integrity inventory sha256 is invalid")
        if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES:
            raise CheckerError("integrity inventory size is invalid")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise CheckerError("integrity inventory total size exceeds limit")
        entries.append({"path": path, "sha256": digest, "size": size})
    paths = [entry["path"] for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise CheckerError("integrity inventory paths must be sorted and unique")
    if "open_mmi_trust/data/trust-manifest.v1.json" not in set(paths):
        raise CheckerError("integrity inventory omits Trust Manifest")
    return entries


def inventory_digest(payload: Sequence[Mapping[str, Any]]) -> str:
    return sha256_bytes(canonical_json(validate_inventory(list(payload))))


def validate_integrity_state(payload: Any) -> dict[str, Any]:
    keys = {
        "schema_version", "state_id", "recorded_at", "record_source", "candidate_commit", "trust_manifest",
        "trust_manifest_digest", "inventory", "inventory_digest", "accepted_state_digest_at_recording",
        "lineage_head_record_digest_at_recording",
    }
    if not isinstance(payload, Mapping):
        raise CheckerError("installed integrity state must be an object")
    exact_keys(payload, keys, "installed integrity state")
    if payload["schema_version"] != INTEGRITY_SCHEMA_VERSION or payload["state_id"] != INTEGRITY_STATE_ID:
        raise CheckerError("installed integrity state schema/id is invalid")
    if payload["record_source"] not in {"baseline-existing-state", "prepared-update"}:
        raise CheckerError("installed integrity record source is invalid")
    commit = payload["candidate_commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise CheckerError("installed integrity candidate commit is invalid")
    manifest = validate_manifest(payload["trust_manifest"])
    m_digest = manifest_digest(manifest)
    if payload["trust_manifest_digest"] != m_digest:
        raise CheckerError("integrity Trust Manifest digest mismatch")
    inventory = validate_inventory(payload["inventory"])
    i_digest = inventory_digest(inventory)
    if payload["inventory_digest"] != i_digest:
        raise CheckerError("integrity inventory digest mismatch")
    accepted_anchor = validate_digest(payload["accepted_state_digest_at_recording"], "integrity accepted-state anchor")
    lineage_anchor = validate_digest(payload["lineage_head_record_digest_at_recording"], "integrity lineage anchor")
    return {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "state_id": INTEGRITY_STATE_ID,
        "recorded_at": timestamp(payload["recorded_at"], "installed integrity"),
        "record_source": payload["record_source"],
        "candidate_commit": commit,
        "trust_manifest": manifest,
        "trust_manifest_digest": m_digest,
        "inventory": inventory,
        "inventory_digest": i_digest,
        "accepted_state_digest_at_recording": accepted_anchor,
        "lineage_head_record_digest_at_recording": lineage_anchor,
    }


def integrity_state_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(validate_integrity_state(payload)))


def validate_provenance_root(payload: Any) -> dict[str, Any]:
    keys = {
        "schema_version", "root_id", "established_at", "root_source", "algorithm", "primary_fingerprint",
        "signing_fingerprints", "public_key_base64", "public_key_sha256", "baseline_commit",
        "baseline_integrity_state_digest", "history_before_baseline",
    }
    if not isinstance(payload, Mapping):
        raise CheckerError("release signer root must be an object")
    exact_keys(payload, keys, "release signer root")
    if payload["schema_version"] != PROVENANCE_SCHEMA_VERSION or payload["root_id"] != PROVENANCE_ROOT_ID:
        raise CheckerError("release signer root schema/id is invalid")
    if payload["root_source"] != "owner-pinned-local-key" or payload["algorithm"] != "openpgp" or payload["history_before_baseline"] != "unverified":
        raise CheckerError("release signer root semantics are invalid")
    primary = payload["primary_fingerprint"]
    signing = payload["signing_fingerprints"]
    if not isinstance(primary, str) or primary != primary.upper() or not FINGERPRINT_RE.fullmatch(primary):
        raise CheckerError("release signer primary fingerprint is invalid")
    if not isinstance(signing, list) or not signing:
        raise CheckerError("release signer has no signing fingerprints")
    if any(not isinstance(item, str) or item != item.upper() or not FINGERPRINT_RE.fullmatch(item) for item in signing):
        raise CheckerError("release signer signing fingerprint is invalid")
    if signing != sorted(signing) or len(signing) != len(set(signing)):
        raise CheckerError("release signer signing fingerprints are not sorted/unique")
    encoded = payload["public_key_base64"]
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_PUBLIC_KEY_BYTES * 2:
        raise CheckerError("release signer key encoding is invalid")
    try:
        key_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise CheckerError("release signer key encoding is invalid") from exc
    if not key_bytes or len(key_bytes) > MAX_PUBLIC_KEY_BYTES:
        raise CheckerError("release signer key is empty or too large")
    key_digest = sha256_bytes(key_bytes)
    if payload["public_key_sha256"] != key_digest:
        raise CheckerError("release signer key digest mismatch")
    commit = payload["baseline_commit"]
    if not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit):
        raise CheckerError("release signer baseline commit is invalid")
    baseline_integrity = validate_digest(payload["baseline_integrity_state_digest"], "release signer baseline integrity digest")
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "root_id": PROVENANCE_ROOT_ID,
        "established_at": timestamp(payload["established_at"], "release signer root"),
        "root_source": "owner-pinned-local-key",
        "algorithm": "openpgp",
        "primary_fingerprint": primary,
        "signing_fingerprints": list(signing),
        "public_key_base64": base64.b64encode(key_bytes).decode("ascii"),
        "public_key_sha256": key_digest,
        "baseline_commit": commit,
        "baseline_integrity_state_digest": baseline_integrity,
        "history_before_baseline": "unverified",
    }


def provenance_root_digest(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json(validate_provenance_root(payload)))


def ignored_runtime_file(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    return "__pycache__" in parts or relative.endswith((".pyc", ".pyo")) or relative in PACKAGE_SOURCE_ONLY_PATHS


def verify_inventory_root(entries: Sequence[Mapping[str, Any]], root: Path, roots: Sequence[str], uid: int, *, exact_files: Sequence[str] = (), package_only: bool = False) -> dict[str, Any]:
    root_set = set(roots)
    exact_set = set(exact_files)
    selected = {
        entry["path"]: entry
        for entry in entries
        if entry["path"] in exact_set
        or (PurePosixPath(str(entry["path"])).parts[0] in root_set and (not package_only or is_package_runtime_path(str(entry["path"]))))
    }
    missing: list[str] = []
    modified: list[str] = []
    extra: list[str] = []
    unsafe: list[str] = []
    try:
        trusted_directory(root, uid)
    except (FileNotFoundError, CheckerError):
        return {"matches": False, "files_expected": len(selected), "missing": sorted(selected), "modified": [], "extra": [], "unsafe": ["root"]}
    for relative, entry in selected.items():
        path = root / relative
        try:
            trusted_file(path, uid)
            raw = path.read_bytes()
        except FileNotFoundError:
            missing.append(relative)
            continue
        except (CheckerError, OSError):
            unsafe.append(relative)
            continue
        if len(raw) != entry["size"] or sha256_bytes(raw) != entry["sha256"]:
            modified.append(relative)
    for root_name in roots:
        tree_root = root / root_name
        if not tree_root.exists():
            continue
        for directory, directories, files in os.walk(tree_root, topdown=True, followlinks=False):
            base = Path(directory)
            retained: list[str] = []
            for name in directories:
                path = base / name
                relative = path.relative_to(root).as_posix()
                if name == "__pycache__":
                    continue
                try:
                    trusted_directory(path, uid)
                except (FileNotFoundError, CheckerError):
                    unsafe.append(relative)
                    continue
                retained.append(name)
            directories[:] = retained
            for name in files:
                path = base / name
                relative = path.relative_to(root).as_posix()
                try:
                    trusted_file(path, uid)
                except (FileNotFoundError, CheckerError):
                    unsafe.append(relative)
                    continue
                if relative in selected or ignored_runtime_file(relative):
                    continue
                extra.append(relative)
    return {
        "matches": not (missing or modified or extra or unsafe),
        "files_expected": len(selected),
        "missing": sorted(set(missing)),
        "modified": sorted(set(modified)),
        "extra": sorted(set(extra)),
        "unsafe": sorted(set(unsafe)),
    }


def discover_package_root(target_root: Path, uid: int) -> Path:
    lib_root = target_path(target_root, "/opt/open-mmi/venv/lib")
    trusted_directory(lib_root, uid)
    candidates: list[Path] = []
    for child in sorted(lib_root.iterdir(), key=lambda item: item.name):
        if not PYTHON_LIB_RE.fullmatch(child.name):
            continue
        trusted_directory(child, uid)
        site = child / "site-packages"
        if site.exists():
            trusted_directory(site, uid)
            candidates.append(site)
    if len(candidates) != 1:
        raise CheckerError("production site-packages path is missing or ambiguous")
    return candidates[0]


def verify_unit_set(entries: Sequence[Mapping[str, Any]], root: Path, uid: int, units: Sequence[str], prefix: str) -> dict[str, Any]:
    inventory = {str(item["path"]): item for item in entries}
    try:
        trusted_directory(root, uid)
    except (FileNotFoundError, CheckerError):
        return {"matches": False, "missing": list(units), "modified": [], "unsafe": ["unit-root"]}
    missing: list[str] = []
    modified: list[str] = []
    unsafe: list[str] = []
    for unit in units:
        expected = inventory.get(f"{prefix}/{unit}")
        if expected is None:
            raise CheckerError(f"integrity inventory omits privileged unit: {unit}")
        path = root / unit
        try:
            trusted_file(path, uid)
            raw = path.read_bytes()
        except FileNotFoundError:
            missing.append(unit)
            continue
        except (CheckerError, OSError):
            unsafe.append(unit)
            continue
        if len(raw) != expected["size"] or sha256_bytes(raw) != expected["sha256"]:
            modified.append(unit)
    return {"matches": not (missing or modified or unsafe), "missing": missing, "modified": modified, "unsafe": unsafe}


def parse_unit_directives(path: Path) -> dict[str, list[str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CheckerError(f"cannot read deployed unit: {path}") from exc
    directives: dict[str, list[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(('#', ';', '[')) or '=' not in line:
            continue
        key, value = line.split('=', 1)
        directives.setdefault(key.strip(), []).append(value.strip())
    return directives


def verify_static_enforcement(
    target_root: Path,
    manifest: Mapping[str, Any],
    inventory: Sequence[Mapping[str, Any]],
    uid: int,
) -> tuple[str, dict[str, Any]]:
    capabilities = manifest["capabilities"]
    unsupported: list[str] = []
    failures: list[str] = []
    network = capabilities["network.external-egress"]
    persistence = capabilities["vehicle-data.persistence"]
    identity = capabilities["vehicle.identity.remote-resolution"]
    can_transmit = capabilities["vehicle.can.transmit"]
    if network != {"policy": "declared-purposes-only", "assurance": "os-enforced", "purposes": KNOWN_NETWORK_PURPOSES}:
        unsupported.append("network.external-egress")
    if persistence != {"policy": "declared-purposes-only", "assurance": "os-enforced", "purposes": KNOWN_PERSISTENCE_PURPOSES}:
        unsupported.append("vehicle-data.persistence")
    if identity != {"policy": "prohibited", "assurance": "runtime-guarded"}:
        unsupported.append("vehicle.identity.remote-resolution")

    if can_transmit != {
        "policy": "prohibited",
        "assurance": "os-enforced",
    }:
        unsupported.append("vehicle.can.transmit")

    contracts: dict[str, dict[str, str | None]] = {
        "system/open-mmi-media-egress.service": {
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
            "ProtectSystem": "strict",
            "InaccessiblePaths": "-/var/lib/open-mmi/trust/telemetry-authorization.v1.json -/var/lib/open-mmi/vehicle-data",
            "ReadWritePaths": None,
        },
        "system/open-mmi-update-coordinator.service": {
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
            "ProtectSystem": "strict",
            "ReadOnlyPaths": "/var/lib/open-mmi/network-egress",
            "InaccessiblePaths": "-/var/lib/open-mmi/trust/telemetry-authorization.v1.json -/var/lib/open-mmi/vehicle-data",
            "ReadWritePaths": "/var/lib/open-mmi /run/open-mmi",
        },
        "system/open-mmi-update-installer.service": {
            "IPAddressDeny": "any",
            "IPAddressAllow": "localhost",
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
            "ProtectSystem": "strict",
            "ReadOnlyPaths": "/var/lib/open-mmi/network-egress /var/lib/open-mmi/vehicle-data",
        },
        "system/open-mmi-vehicle-store.service": {
            "StateDirectory": "open-mmi/vehicle-data",
            "ProtectSystem": "strict",
            "RestrictAddressFamilies": "AF_UNIX",
        },
        "system/open-mmi-vehicle-config-coordinator.service": {
            "ProtectSystem": "strict",
            "ReadOnlyPaths": "/var/lib/open-mmi/vehicle-data",
            "RestrictAddressFamilies": "AF_UNIX",
        },
        "system/open-mmi-vehicle-can-provision.service": {
            "ProtectSystem": "strict",
            "RestrictAddressFamilies": "AF_NETLINK AF_UNIX",
            "CapabilityBoundingSet": "CAP_NET_ADMIN CAP_DAC_READ_SEARCH",
        },
        "user/open-mmi-dashboard.service": {
            "IPAddressDeny": "any",
            "IPAddressAllow": "localhost",
            "ProtectHome": "read-only",
            "ProtectSystem": "strict",
            "ReadWritePaths": None,
        },
        "user/canbusd.service": {
            "ProtectHome": "read-only",
            "ProtectSystem": "strict",
            "ReadWritePaths": "%t/open-mmi",
            "RestrictAddressFamilies": "AF_CAN AF_UNIX",
            "CapabilityBoundingSet": "",
            "AmbientCapabilities": "",
        },
        "user/open-mmi-owner-config.service": {
            "ProtectHome": "read-only",
            "ProtectSystem": "strict",
            "ReadWritePaths": "%h/.config/open-mmi %h/.config/autostart",
            "RestrictAddressFamilies": "AF_UNIX",
        },
    }
    inventory_map = {str(entry["path"]): entry for entry in inventory}
    for relative, expected_directives in contracts.items():
        inventory_path = f"systemd/{relative}"
        expected_file = inventory_map.get(inventory_path)
        path = target_path(target_root, "/etc/systemd") / relative
        if expected_file is None:
            failures.append(f"{relative}:missing-signed-inventory-entry")
            continue
        try:
            trusted_file(path, uid)
            raw = path.read_bytes()
        except (FileNotFoundError, OSError, CheckerError):
            failures.append(f"{relative}:untrusted-or-missing")
            continue
        if len(raw) != expected_file["size"] or sha256_bytes(raw) != expected_file["sha256"]:
            failures.append(f"{relative}:deployed-bytes-do-not-match-signed-release")
            continue
        try:
            directives = parse_unit_directives(path)
        except CheckerError:
            failures.append(f"{relative}:unreadable")
            continue
        for key, expected_value in expected_directives.items():
            observed = directives.get(key, [])
            if expected_value is None:
                if observed:
                    failures.append(f"{relative}:{key}:must-be-absent")
            elif observed != [expected_value]:
                failures.append(f"{relative}:{key}:expected-single:{expected_value}")


    # generation-6-can-static-enforcement
    #
    # CAN TX prohibition is not established merely by the manifest.  Bind the
    # claim to the signed provisioning implementation plus the deployed,
    # root-controlled udev rule.  Live controller state is measured separately
    # by open_mmi_can_trust_test.py.
    can_source_contracts = {
        "scripts/profile_provision.py": (
            "listen-only on",
            "physical CAN interfaces require bitrate and ",
            "udev listen-only provisioning",
        ),
        "ui/vehicle_config_apply.py": (
            "listen-only on",
            "Physical CAN activation requires bitrate and ",
            "udev listen-only provisioning",
            '"listen-only",',
        ),
    }

    install_root = target_path(
        target_root,
        TARGET_PATHS["install_root"],
    )

    for relative, required_fragments in can_source_contracts.items():
        expected_file = inventory_map.get(relative)
        path = install_root / relative

        if expected_file is None:
            failures.append(
                f"{relative}:missing-signed-inventory-entry"
            )
            continue

        try:
            trusted_file(path, uid)
            raw = path.read_bytes()
            source = raw.decode("utf-8")
        except (
            FileNotFoundError,
            OSError,
            UnicodeError,
            CheckerError,
        ):
            failures.append(
                f"{relative}:untrusted-or-missing"
            )
            continue

        if (
            len(raw) != expected_file["size"]
            or sha256_bytes(raw) != expected_file["sha256"]
        ):
            failures.append(
                f"{relative}:deployed-bytes-do-not-match-signed-release"
            )
            continue

        for fragment in required_fragments:
            if fragment not in source:
                failures.append(
                    f"{relative}:missing-can-contract:{fragment}"
                )

    udev_path = target_path(
        target_root,
        TARGET_PATHS["udev_rules"],
    )
    try:
        trusted_file(udev_path, uid)
        udev_source = udev_path.read_text(encoding="utf-8")
    except (
        FileNotFoundError,
        OSError,
        UnicodeError,
        CheckerError,
    ):
        failures.append(
            "udev/80-canbus.rules:untrusted-or-missing"
        )
    else:
        physical_can_rules = [
            line.strip()
            for line in udev_source.splitlines()
            if "RUN+=" in line
            and " type can bitrate " in line
        ]

        if not physical_can_rules:
            failures.append(
                "udev/80-canbus.rules:no-physical-can-rule"
            )

        for rule in physical_can_rules:
            if "listen-only on" not in rule:
                failures.append(
                    "udev/80-canbus.rules:"
                    "physical-can-rule-not-listen-only"
                )


    if failures:
        return FAIL, {"failures": sorted(failures), "unsupported_capabilities": unsupported}
    if unsupported:
        return UNVERIFIED, {"failures": [], "unsupported_capabilities": unsupported}
    return PASS, {"failures": [], "unsupported_capabilities": []}


def verifier_program(path: Path) -> None:
    metadata = path.lstat()
    parent = path.parent.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or not metadata.st_mode & 0o111
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_mode & 0o022
    ):
        raise CheckerError(f"checker verifier program is untrusted: {path}")


def gpg_env(home: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "GNUPGHOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
    }


def run_bounded(arguments: Sequence[str], *, env: Mapping[str, str], input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(list(arguments), env=dict(env), input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckerError("external cryptographic verifier failed to run") from exc
    if len(result.stdout) + len(result.stderr) > MAX_VERIFIER_OUTPUT_BYTES:
        raise CheckerError("external verifier output exceeds limit")
    return result


def describe_key(key_bytes: bytes) -> dict[str, Any]:
    gpg = Path("/usr/bin/gpg")
    verifier_program(gpg)
    with tempfile.TemporaryDirectory(prefix="open-mmi-independent-key-") as directory:
        home = Path(directory)
        home.chmod(0o700)
        imported = run_bounded([str(gpg), "--no-options", "--batch", "--homedir", str(home), "--import-options", "import-minimal", "--import"], env=gpg_env(home), input_bytes=key_bytes)
        if imported.returncode != 0:
            raise CheckerError("pinned OpenPGP public key cannot be imported")
        listing = run_bounded([str(gpg), "--no-options", "--batch", "--homedir", str(home), "--with-colons", "--fixed-list-mode", "--list-keys"], env=gpg_env(home))
        if listing.returncode != 0:
            raise CheckerError("pinned OpenPGP public key cannot be listed")
        lines = listing.stdout.decode("utf-8", errors="strict").splitlines()
        primaries: list[str] = []
        signing: list[str] = []
        current_kind = ""
        current_caps = ""
        current_validity = ""
        for line in lines:
            fields = line.split(":")
            kind = fields[0] if fields else ""
            if kind in {"pub", "sub"}:
                current_kind = kind
                current_validity = fields[1] if len(fields) > 1 else ""
                current_caps = fields[11] if len(fields) > 11 else ""
                continue
            if kind != "fpr" or not current_kind:
                continue
            fingerprint = fields[9].upper() if len(fields) > 9 else ""
            if not FINGERPRINT_RE.fullmatch(fingerprint) or current_validity in {"r", "d", "i"}:
                raise CheckerError("pinned OpenPGP key metadata is invalid")
            if current_kind == "pub":
                primaries.append(fingerprint)
            if "s" in current_caps.lower():
                signing.append(fingerprint)
            current_kind = ""
        if len(primaries) != 1 or not signing:
            raise CheckerError("pinned key must contain one primary and a signing-capable key")
        exported = run_bounded([str(gpg), "--no-options", "--batch", "--homedir", str(home), "--export", primaries[0]], env=gpg_env(home))
        if exported.returncode != 0 or not exported.stdout or len(exported.stdout) > MAX_PUBLIC_KEY_BYTES:
            raise CheckerError("canonical pinned OpenPGP key export failed")
        return {"primary_fingerprint": primaries[0], "signing_fingerprints": sorted(set(signing)), "public_key": exported.stdout}


def verify_commit_signature(repository: Path, commit: str, provenance: Mapping[str, Any]) -> dict[str, Any]:
    git = Path("/usr/bin/git")
    gpg = Path("/usr/bin/gpg")
    verifier_program(git)
    verifier_program(gpg)
    if not repository.is_absolute() or not COMMIT_RE.fullmatch(commit):
        raise CheckerError("Git provenance input is invalid")
    key_bytes = base64.b64decode(provenance["public_key_base64"], validate=True)
    with tempfile.TemporaryDirectory(prefix="open-mmi-independent-provenance-") as directory:
        home = Path(directory)
        home.chmod(0o700)
        imported = run_bounded([str(gpg), "--no-options", "--batch", "--homedir", str(home), "--import-options", "import-minimal", "--import"], env=gpg_env(home), input_bytes=key_bytes)
        if imported.returncode != 0:
            raise CheckerError("pinned OpenPGP public key cannot be imported")
        result = run_bounded([
            str(git), "-c", f"safe.directory={repository}", "-c", "gpg.format=openpgp", "-c", f"gpg.program={gpg}",
            "-C", str(repository), "verify-commit", "--raw", commit,
        ], env=gpg_env(home))
        output = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="strict")
    fatal = {"BADSIG", "ERRSIG", "NO_PUBKEY", "REVKEYSIG", "KEYREVOKED", "EXPSIG", "EXPKEYSIG", "KEYEXPIRED", "SIGEXPIRED"}
    observed_fatal: list[str] = []
    valid: list[list[str]] = []
    for line in output.splitlines():
        if not line.startswith("[GNUPG:] "):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        if fields[1] in fatal:
            observed_fatal.append(fields[1])
        if fields[1] == "VALIDSIG":
            valid.append(fields)
    if result.returncode != 0 or observed_fatal or len(valid) != 1 or len(valid[0]) < 12:
        raise CheckerError("release commit does not have one valid signature from the pinned signer")
    fields = valid[0]
    signing = fields[2].upper()
    primary = fields[-1].upper()
    if signing not in provenance["signing_fingerprints"] or primary != provenance["primary_fingerprint"]:
        raise CheckerError("release commit signature does not match pinned signer root")
    return {"candidate_commit": commit, "primary_fingerprint": primary, "signing_fingerprint": signing}



def git_run(repository: Path, *arguments: str, text: bool = False, max_output: int = MAX_VERIFIER_OUTPUT_BYTES) -> subprocess.CompletedProcess[Any]:
    git = Path("/usr/bin/git")
    verifier_program(git)
    try:
        result = subprocess.run(
            [str(git), "-c", f"safe.directory={repository}", "-C", str(repository), *arguments],
            env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30.0,
            text=text,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CheckerError("Git object inspection failed") from exc
    stdout_size = len(result.stdout.encode() if isinstance(result.stdout, str) else result.stdout)
    stderr_size = len(result.stderr.encode() if isinstance(result.stderr, str) else result.stderr)
    if stdout_size + stderr_size > max_output:
        raise CheckerError("Git object inspection output exceeds limit")
    return result


def git_blob(repository: Path, object_id: str, expected_size: int) -> bytes:
    result = git_run(repository, "cat-file", "blob", object_id, max_output=MAX_FILE_BYTES + MAX_VERIFIER_OUTPUT_BYTES)
    if result.returncode != 0 or not isinstance(result.stdout, bytes) or len(result.stdout) != expected_size:
        raise CheckerError("signed Git blob could not be read exactly")
    return result.stdout


def inventory_from_git_commit(repository: Path, commit: str) -> list[dict[str, Any]]:
    if not COMMIT_RE.fullmatch(commit):
        raise CheckerError("signed release commit is invalid")
    result = git_run(repository, "ls-tree", "-r", "-z", "-l", "--full-tree", commit, "--", *INVENTORY_ROOTS, *SOURCE_RELEASE_FILES)
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        raise CheckerError("signed release tree cannot be enumerated")
    entries: list[dict[str, Any]] = []
    total = 0
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode_b, kind_b, object_b, size_b = metadata.split(b" ", 3)
            path = raw_path.decode("utf-8", errors="strict")
            mode = mode_b.decode("ascii")
            kind = kind_b.decode("ascii")
            object_id = object_b.decode("ascii")
            size = int(size_b.decode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise CheckerError("signed release tree entry is invalid") from exc
        safe = safe_relative_path(path)
        if kind != "blob" or mode not in {"100644", "100755"} or not is_inventory_path(safe):
            raise CheckerError(f"signed release tree contains unsupported managed object: {safe}")
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES or len(entries) >= MAX_INVENTORY_FILES:
            raise CheckerError("signed release tree exceeds checker v1 limits")
        data = git_blob(repository, object_id, size)
        entries.append({"path": safe, "sha256": sha256_bytes(data), "size": size})
    entries.sort(key=lambda item: item["path"])
    return validate_inventory(entries)


def manifest_from_git_commit(repository: Path, commit: str) -> dict[str, Any]:
    relative = "open_mmi_trust/data/trust-manifest.v1.json"
    result = git_run(repository, "show", f"{commit}:{relative}", max_output=MAX_FILE_BYTES + MAX_VERIFIER_OUTPUT_BYTES)
    if result.returncode != 0 or not isinstance(result.stdout, bytes) or len(result.stdout) > MAX_FILE_BYTES:
        raise CheckerError("signed release Trust Manifest cannot be read")
    return validate_manifest(strict_json_bytes(result.stdout, "signed release Trust Manifest"))


def verify_interpreter(target_root: Path, uid: int) -> dict[str, Any]:
    start = target_path(target_root, "/opt/open-mmi/venv/bin/python")
    current = start
    seen: set[Path] = set()
    for _ in range(8):
        if current in seen:
            raise CheckerError("privileged Python executable symlink loop")
        seen.add(current)
        metadata = current.lstat()
        if metadata.st_uid != uid:
            raise CheckerError("privileged Python executable is not target-owner controlled")
        if stat.S_ISLNK(metadata.st_mode):
            link = Path(os.readlink(current))
            if link.is_absolute():
                current = target_path(target_root, link)
            else:
                current = Path(os.path.normpath(str(current.parent / link)))
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022 or not metadata.st_mode & 0o111:
            raise CheckerError("privileged Python executable target is unsafe")
        expected_usr_bin = target_path(target_root, "/usr/bin")
        try:
            current.relative_to(expected_usr_bin)
        except ValueError as exc:
            raise CheckerError("privileged Python executable does not resolve under target /usr/bin") from exc
        trusted_directory(expected_usr_bin, uid)
        return {"path": str(start), "resolved": str(current)}
    raise CheckerError("privileged Python executable has excessive symlink depth")


def source_repository(target_root: Path, uid: int, expected_commit: str, explicit: Path | None) -> tuple[Path | None, str | None]:
    if explicit is not None:
        return explicit.resolve(), None
    descriptor = target_path(target_root, TARGET_PATHS["source_descriptor"])
    if not descriptor.exists():
        return None, "managed update source descriptor is missing"
    payload = read_trusted_json(descriptor, uid, "managed update source descriptor", private=False)
    expected = {"schema_version", "channel", "repository_path", "branch", "upstream", "installed_commit", "installed_version"}
    if not isinstance(payload, Mapping) or set(payload) != expected or payload.get("schema_version") != 1:
        raise CheckerError("managed update source descriptor schema is invalid")
    if str(payload.get("installed_commit") or "").lower() != expected_commit:
        raise CheckerError("managed update source descriptor commit does not match integrity state")
    repository_value = Path(str(payload.get("repository_path") or ""))
    if not repository_value.is_absolute():
        raise CheckerError("managed update source repository path is invalid")
    return target_path(target_root, repository_value), None


def checker_independence() -> dict[str, Any]:
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=__file__)
    forbidden = {"open_mmi_trust", "open_mmi_telemetry", "ui", "canbusd", "powerd", "actions", "bindings", "vehicles"}
    imports: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        for module in modules:
            if module.split(".", 1)[0] in forbidden:
                imports.append(module)
    if imports:
        raise CheckerError("checker imports Open MMI runtime modules")
    return {"open_mmi_imports": [], "self_sha256": sha256_bytes(Path(__file__).read_bytes())}


def inspect(args: argparse.Namespace) -> dict[str, Any]:
    target_root = Path(args.target_root).resolve()
    uid = args.expected_owner_uid
    checks: list[dict[str, Any]] = []

    try:
        independence = checker_independence()
        if args.expected_checker_sha256:
            if args.expected_checker_sha256 != independence["self_sha256"]:
                raise CheckerError("checker executable hash does not match external anchor")
            independence["expected_self_sha256"] = args.expected_checker_sha256
        checks.append(check("checker.independence", PASS, "Checker uses no Open MMI runtime imports.", **independence))
    except (OSError, UnicodeError, SyntaxError, CheckerError) as exc:
        checks.append(check("checker.independence", FAIL, "Checker independence/self-integrity could not be established.", error=str(exc)))

    accepted: dict[str, Any] | None = None
    lineage: list[dict[str, Any]] | None = None
    integrity: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    package_root: Path | None = None

    accepted_path = target_path(target_root, TARGET_PATHS["accepted"])
    try:
        accepted = validate_accepted_state(read_trusted_json(accepted_path, uid, "accepted owner trust state"))
        checks.append(check("owner.accepted-state", PASS, "Accepted owner trust state is structurally valid and self-digesting.", digest=accepted_state_digest(accepted), manifest_digest=accepted["manifest_digest"], generation=accepted["manifest"]["policy_generation"]))
    except FileNotFoundError:
        checks.append(check("owner.accepted-state", UNVERIFIED, "Accepted owner trust state is missing.", path=str(accepted_path)))
    except EvidenceUnavailable as exc:
        checks.append(check("owner.accepted-state", UNVERIFIED, "Accepted owner trust state is not readable by this checker process.", error=str(exc)))
    except CheckerError as exc:
        checks.append(check("owner.accepted-state", FAIL, "Accepted owner trust state is malformed or unsafe.", error=str(exc)))

    lineage_path = target_path(target_root, TARGET_PATHS["lineage"])
    try:
        lineage = read_lineage(lineage_path, uid)
        head = lineage[-1]
        head_digest = lineage_record_digest(head)
        if args.expected_lineage_head and args.expected_lineage_head != head_digest:
            raise CheckerError("lineage head does not match external expected anchor")
        if accepted is None:
            checks.append(check("owner.transition-lineage", UNVERIFIED, "Transition lineage is internally valid, but its current accepted-state anchor cannot be checked because accepted state is unavailable.", records=len(lineage), head_record_digest=head_digest, externally_anchored=bool(args.expected_lineage_head)))
        else:
            if head["accepted_state_after_digest"] != accepted_state_digest(accepted) or head["accepted_manifest_after_digest"] != accepted["manifest_digest"] or head["manifest_after"] != accepted["manifest"]:
                raise CheckerError("lineage head does not anchor current accepted owner state")
            checks.append(check("owner.transition-lineage", PASS, "Transition lineage is contiguous, hash-chained, and anchored to current accepted state.", records=len(lineage), head_record_digest=head_digest, externally_anchored=bool(args.expected_lineage_head)))
    except FileNotFoundError:
        checks.append(check("owner.transition-lineage", UNVERIFIED, "Transition lineage is missing.", path=str(lineage_path)))
    except EvidenceUnavailable as exc:
        checks.append(check("owner.transition-lineage", UNVERIFIED, "Transition lineage is not readable by this checker process.", error=str(exc)))
    except CheckerError as exc:
        checks.append(check("owner.transition-lineage", FAIL, "Transition lineage is malformed, unsafe, or divergent.", error=str(exc)))

    integrity_path = target_path(target_root, TARGET_PATHS["integrity"])
    try:
        integrity = validate_integrity_state(read_trusted_json(integrity_path, uid, "installed release integrity state"))
        if lineage is None:
            checks.append(check("release.integrity-state", UNVERIFIED, "Installed release integrity state is structurally valid, but its recorded lineage anchor is unavailable.", digest=integrity_state_digest(integrity), candidate_commit=integrity["candidate_commit"], inventory_digest=integrity["inventory_digest"]))
        else:
            matching = [record for record in lineage if lineage_record_digest(record) == integrity["lineage_head_record_digest_at_recording"]]
            if len(matching) != 1 or matching[0]["accepted_state_after_digest"] != integrity["accepted_state_digest_at_recording"]:
                raise CheckerError("integrity recording anchors do not identify one lineage state")
            relation = compare_manifests(matching[0]["accepted_state_after"]["manifest"], integrity["trust_manifest"])["relation"]
            if relation in {RELATION_EXPANSION, RELATION_GENERATION_REGRESSION}:
                raise CheckerError("installed manifest exceeded accepted owner boundary at integrity recording")
            checks.append(check("release.integrity-state", PASS, "Installed release integrity state is self-consistent and bound to recorded owner/lineage evidence.", digest=integrity_state_digest(integrity), candidate_commit=integrity["candidate_commit"], inventory_digest=integrity["inventory_digest"], accepted_relation_at_recording=relation))
    except FileNotFoundError:
        checks.append(check("release.integrity-state", UNVERIFIED, "Installed release integrity state is missing.", path=str(integrity_path)))
    except EvidenceUnavailable as exc:
        checks.append(check("release.integrity-state", UNVERIFIED, "Installed release integrity state is not readable by this checker process.", error=str(exc)))
    except CheckerError as exc:
        checks.append(check("release.integrity-state", FAIL, "Installed release integrity state is malformed, unsafe, or contradicts lineage evidence.", error=str(exc)))

    provenance_path = target_path(target_root, TARGET_PATHS["provenance"])
    try:
        provenance = validate_provenance_root(read_trusted_json(provenance_path, uid, "release signer root"))
        description = describe_key(base64.b64decode(provenance["public_key_base64"], validate=True))
        if description["primary_fingerprint"] != provenance["primary_fingerprint"] or description["signing_fingerprints"] != provenance["signing_fingerprints"] or sha256_bytes(description["public_key"]) != provenance["public_key_sha256"]:
            raise CheckerError("release signer root fields do not match independently parsed key material")
        expected = str(args.expected_signer_fingerprint).upper()
        if not FINGERPRINT_RE.fullmatch(expected) or provenance["primary_fingerprint"] != expected:
            raise CheckerError("release signer root does not match externally supplied primary fingerprint")
        checks.append(check("release.signer-root", PASS, "Pinned signer root matches the external owner-supplied fingerprint and key material.", primary_fingerprint=provenance["primary_fingerprint"], signing_fingerprints=provenance["signing_fingerprints"], root_digest=provenance_root_digest(provenance)))
    except FileNotFoundError:
        checks.append(check("release.signer-root", UNVERIFIED, "Pinned release signer root is missing.", path=str(provenance_path)))
    except EvidenceUnavailable as exc:
        checks.append(check("release.signer-root", UNVERIFIED, "Pinned release signer root is not readable by this checker process.", error=str(exc)))
    except (CheckerError, UnicodeError) as exc:
        checks.append(check("release.signer-root", FAIL, "Pinned release signer root is malformed, unsafe, or does not match the external anchor.", error=str(exc)))

    if integrity is not None:
        install_root = target_path(target_root, TARGET_PATHS["install_root"])
        try:
            package_root = discover_package_root(target_root, uid)
            source = verify_inventory_root(integrity["inventory"], install_root, SOURCE_RELEASE_ROOTS, uid, exact_files=SOURCE_RELEASE_FILES)
            package = verify_inventory_root(integrity["inventory"], package_root, PACKAGE_RUNTIME_ROOTS, uid, package_only=True)
            if not source["matches"] or not package["matches"]:
                raise CheckerError("installed source/package inventory does not match recorded release")
            active_manifest_path = package_root / "open_mmi_trust/data/trust-manifest.v1.json"
            active_manifest = validate_manifest(strict_json_bytes(active_manifest_path.read_bytes(), "active Trust Manifest"))
            if manifest_digest(active_manifest) != integrity["trust_manifest_digest"] or active_manifest != integrity["trust_manifest"]:
                raise CheckerError("active Trust Manifest does not match integrity state")
            interpreter = verify_interpreter(target_root, uid)
            checks.append(check("release.runtime-inventory", PASS, "Installed source, active site-packages, and privileged interpreter ownership match the recorded release.", source_root=str(install_root), package_root=str(package_root), source_files=source["files_expected"], package_files=package["files_expected"], manifest_digest=integrity["trust_manifest_digest"], interpreter=interpreter))
        except (FileNotFoundError, OSError, UnicodeError, CheckerError) as exc:
            checks.append(check("release.runtime-inventory", FAIL, "Installed source/package bytes or ownership do not match recorded release.", error=str(exc)))

        system_root = target_path(target_root, TARGET_PATHS["system_units"])
        user_root = target_path(target_root, TARGET_PATHS["user_units"])
        try:
            system_units = verify_unit_set(integrity["inventory"], system_root, uid, PRIVILEGED_SYSTEM_UNITS, "systemd/system")
            user_units = verify_unit_set(integrity["inventory"], user_root, uid, PRIVILEGED_USER_UNITS, "systemd/user")
            if not system_units["matches"] or not user_units["matches"]:
                raise CheckerError("deployed privileged systemd units do not match recorded release")
            checks.append(check("release.privileged-units", PASS, "Deployed privileged system and user units match recorded release bytes and ownership.", system_units=list(PRIVILEGED_SYSTEM_UNITS), user_units=list(PRIVILEGED_USER_UNITS)))
        except (FileNotFoundError, CheckerError) as exc:
            checks.append(check("release.privileged-units", FAIL, "Privileged systemd unit bytes or ownership are not trusted.", error=str(exc)))

        try:
            status, evidence = verify_static_enforcement(target_root, integrity["trust_manifest"], integrity["inventory"], uid)
            summary = {
                PASS: "Externally measurable network, persistence, identity, and CAN static contracts match checker v1.",
                UNVERIFIED: "Installed manifest uses a capability contract this checker version does not understand.",
                FAIL: "Externally measurable systemd enforcement contract is missing or weakened.",
            }[status]
            checks.append(check("capability.static-enforcement", status, summary, **evidence))
        except CheckerError as exc:
            checks.append(check("capability.static-enforcement", FAIL, "Static enforcement evidence could not be evaluated.", error=str(exc)))

    if integrity is not None and provenance is not None:
        try:
            repository, unavailable = source_repository(target_root, uid, integrity["candidate_commit"], Path(args.repository).resolve() if args.repository else None)
            if repository is None:
                checks.append(check("release.provenance", UNVERIFIED, "Signed Git commit cannot be verified because the local repository evidence is unavailable.", reason=unavailable))
            elif not repository.exists():
                checks.append(check("release.provenance", UNVERIFIED, "Signed Git commit cannot be verified because the referenced repository is unavailable.", repository=str(repository)))
            else:
                evidence = verify_commit_signature(repository, integrity["candidate_commit"], provenance)
                signed_inventory = inventory_from_git_commit(repository, integrity["candidate_commit"])
                signed_manifest = manifest_from_git_commit(repository, integrity["candidate_commit"])
                if signed_inventory != integrity["inventory"] or inventory_digest(signed_inventory) != integrity["inventory_digest"]:
                    raise CheckerError("integrity inventory does not match the signed Git commit tree")
                if signed_manifest != integrity["trust_manifest"] or manifest_digest(signed_manifest) != integrity["trust_manifest_digest"]:
                    raise CheckerError("integrity Trust Manifest does not match the signed Git commit")
                checks.append(check("release.provenance", PASS, "Installed integrity state and bytes are bound to one offline-signed Git commit from the externally anchored signer.", repository=str(repository), signed_inventory_digest=integrity["inventory_digest"], **evidence))
        except (OSError, UnicodeError, CheckerError) as exc:
            checks.append(check("release.provenance", FAIL, "Installed release provenance verification failed.", error=str(exc)))

    return {
        "checker": "open-mmi-independent-trust-checker-v1",
        "target_root": str(target_root),
        "overall_status": overall_status(checks),
        "checks": checks,
        "note": "CAN challenge-bound external observation is intentionally deferred to the separate CAN trust test.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Independent read-only Open MMI trust checker")
    parser.add_argument("--target-root", default="/", help="Mounted target root (default: /)")
    parser.add_argument("--expected-signer-fingerprint", required=True, help="Owner-supplied OpenPGP primary fingerprint")
    parser.add_argument("--expected-checker-sha256", help="Optional external sha256:<hex> checker hash anchor")
    parser.add_argument("--expected-lineage-head", help="Optional external sha256:<hex> lineage-head anchor")
    parser.add_argument("--repository", help="Optional explicit local Git repository containing the integrity-bound commit")
    parser.add_argument("--expected-owner-uid", type=int, default=0, help="Expected uid for target privileged evidence (default: 0)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.expected_owner_uid < 0:
        raise SystemExit("--expected-owner-uid must be non-negative")
    for value, label in ((args.expected_checker_sha256, "--expected-checker-sha256"), (args.expected_lineage_head, "--expected-lineage-head")):
        if value and not SHA256_RE.fullmatch(value):
            raise SystemExit(f"{label} must use sha256:<64 lowercase hex>")
    report = inspect(args)
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"Overall: {report['overall_status']}")
        for item in report["checks"]:
            print(f"{item['status']:10s} {item['id']}: {item['summary']}")
        print(report["note"])
    return {PASS: 0, FAIL: 1, UNVERIFIED: 2}[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
