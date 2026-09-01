"""Append-only Trust Transition Lineage v1.

Lineage is local evidence, not authority.  It records accepted-owner-trust state
changes in a hash-chained sequence of immutable record files.  The current
Accepted Owner Trust State anchors the chain head, so removing the newest
accepted-state transition is detected even though arbitrary root software could
still replace both stores.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .accepted_state import (
    TRANSITION_EQUAL,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    TRANSITION_NARROWER,
    AcceptedTrustStateError,
    accepted_state_digest,
    compare_trust_manifests,
    validate_accepted_state,
)
from .manifest import ManifestError, manifest_digest, validate_manifest


LINEAGE_SCHEMA_VERSION = 1
LINEAGE_RECORD_ID = "org.open-mmi.trust-transition-lineage-record"
DEFAULT_TRANSITION_LINEAGE_DIR = Path(
    "/var/lib/open-mmi/trust/transition-lineage.v1.d"
)
MAX_LINEAGE_RECORDS = 4096
MAX_LINEAGE_RECORD_BYTES = 128 * 1024

SOURCE_BASELINE = "existing-accepted-state"
SOURCE_PREPARED_UPDATE = "prepared-update"
SOURCE_ACCEPTED_STATE_CLI = "accepted-state-cli"
SOURCE_RECONCILE = "lineage-reconcile"
SOURCES = {
    SOURCE_BASELINE,
    SOURCE_PREPARED_UPDATE,
    SOURCE_ACCEPTED_STATE_CLI,
    SOURCE_RECONCILE,
}

DECISION_BASELINE = "baseline-existing-state"
DECISION_ALLOWED = "allowed-without-owner-acknowledgement"
DECISION_EXPANSION = "owner-acknowledged-expansion"
DECISION_LOCAL_OWNER = "local-owner-accepted-state"
DECISION_RECONCILE = "local-owner-lineage-reconcile"
DECISIONS = {
    DECISION_BASELINE,
    DECISION_ALLOWED,
    DECISION_EXPANSION,
    DECISION_LOCAL_OWNER,
    DECISION_RECONCILE,
}

ACK_NONE = "none"
ACK_TRANSITION = "local-interactive-transition"
ACK_ACCEPTED_STATE = "local-interactive-accepted-state"
ACK_BASELINE = "local-interactive-lineage-baseline"
ACK_RECONCILE = "local-interactive-lineage-reconcile"
ACK_METHODS = {
    ACK_NONE,
    ACK_TRANSITION,
    ACK_ACCEPTED_STATE,
    ACK_BASELINE,
    ACK_RECONCILE,
}

RELATION_BASELINE = "baseline"
_ALLOWED_RELATIONS = {
    RELATION_BASELINE,
    TRANSITION_EQUAL,
    TRANSITION_NARROWER,
    TRANSITION_EXPANSION,
}

_RECORD_KEYS = {
    "schema_version",
    "record_id",
    "sequence",
    "recorded_at",
    "previous_record_digest",
    "source",
    "transaction_id",
    "candidate_commit",
    "accepted_state_before_digest",
    "accepted_state_after_digest",
    "accepted_manifest_before_digest",
    "accepted_manifest_after_digest",
    "policy_generation_before",
    "policy_generation_after",
    "accepted_state_after",
    "manifest_after",
    "relation",
    "changes",
    "decision",
    "owner_acknowledgement",
    "authorization_digest",
}
_ACK_KEYS = {"required", "method"}
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TRANSACTION_RE = re.compile(r"^prepare-[0-9a-f]{32}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_RECORD_FILENAME_RE = re.compile(r"^(?P<sequence>[0-9]{8})-(?P<digest>[0-9a-f]{64})\.json$")


class TransitionLineageError(RuntimeError):
    """Transition lineage is malformed, unsafe, divergent, or unavailable."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise TransitionLineageError("transition lineage timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TransitionLineageError("transition lineage timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise TransitionLineageError("transition lineage timestamp is invalid")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise TransitionLineageError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise TransitionLineageError(f"{label} is missing keys: {', '.join(missing)}")


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TransitionLineageError(f"duplicate transition lineage field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise TransitionLineageError(f"invalid transition lineage JSON number: {value}")


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    return "sha256:" + manifest_digest(manifest)


def _validate_digest(value: Any, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise TransitionLineageError(f"{label} is invalid")
    return value


def validate_lineage_record(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TransitionLineageError("transition lineage record must be an object")
    _require_exact_keys(payload, _RECORD_KEYS, "transition lineage record")
    if payload["schema_version"] != LINEAGE_SCHEMA_VERSION:
        raise TransitionLineageError("unsupported transition lineage schema_version")
    if payload["record_id"] != LINEAGE_RECORD_ID:
        raise TransitionLineageError("unexpected transition lineage record id")

    sequence = payload["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise TransitionLineageError("transition lineage sequence is invalid")

    source = payload["source"]
    if source not in SOURCES:
        raise TransitionLineageError("transition lineage source is invalid")
    decision = payload["decision"]
    if decision not in DECISIONS:
        raise TransitionLineageError("transition lineage decision is invalid")
    relation = payload["relation"]
    if relation not in _ALLOWED_RELATIONS:
        raise TransitionLineageError("transition lineage relation is invalid")

    previous = _validate_digest(
        payload["previous_record_digest"], "previous transition lineage digest", nullable=True
    )
    before_state = _validate_digest(
        payload["accepted_state_before_digest"], "accepted state before digest", nullable=True
    )
    after_state = _validate_digest(
        payload["accepted_state_after_digest"], "accepted state after digest"
    )
    before_manifest = _validate_digest(
        payload["accepted_manifest_before_digest"], "accepted manifest before digest", nullable=True
    )
    after_manifest = _validate_digest(
        payload["accepted_manifest_after_digest"], "accepted manifest after digest"
    )
    authorization_digest = _validate_digest(
        payload["authorization_digest"], "transition authorization digest", nullable=True
    )

    transaction_id = payload["transaction_id"]
    candidate_commit = payload["candidate_commit"]
    if transaction_id is not None and (
        not isinstance(transaction_id, str) or not _TRANSACTION_RE.fullmatch(transaction_id)
    ):
        raise TransitionLineageError("transition lineage transaction id is invalid")
    if candidate_commit is not None and (
        not isinstance(candidate_commit, str) or not _COMMIT_RE.fullmatch(candidate_commit)
    ):
        raise TransitionLineageError("transition lineage candidate commit is invalid")
    if (transaction_id is None) != (candidate_commit is None):
        raise TransitionLineageError("transition lineage candidate identity is incomplete")
    if source == SOURCE_PREPARED_UPDATE and transaction_id is None:
        raise TransitionLineageError("prepared-update lineage requires candidate identity")
    if source != SOURCE_PREPARED_UPDATE and transaction_id is not None:
        raise TransitionLineageError("non-update lineage cannot carry candidate identity")

    before_generation = payload["policy_generation_before"]
    after_generation = payload["policy_generation_after"]
    if before_generation is not None and (
        not isinstance(before_generation, int)
        or isinstance(before_generation, bool)
        or before_generation < 1
    ):
        raise TransitionLineageError("transition lineage before generation is invalid")
    if not isinstance(after_generation, int) or isinstance(after_generation, bool) or after_generation < 1:
        raise TransitionLineageError("transition lineage after generation is invalid")

    try:
        accepted_state_after = validate_accepted_state(payload["accepted_state_after"])
    except AcceptedTrustStateError as exc:
        raise TransitionLineageError(f"transition lineage accepted-state snapshot is invalid: {exc}") from exc
    try:
        manifest_after = validate_manifest(payload["manifest_after"])
    except ManifestError as exc:
        raise TransitionLineageError(f"transition lineage manifest is invalid: {exc}") from exc
    if accepted_state_digest(accepted_state_after) != after_state:
        raise TransitionLineageError("transition lineage after accepted-state digest does not match snapshot")
    if accepted_state_after["manifest"] != manifest_after:
        raise TransitionLineageError("transition lineage accepted-state snapshot manifest does not match manifest")
    if accepted_state_after["manifest_digest"] != after_manifest:
        raise TransitionLineageError("transition lineage accepted-state snapshot manifest digest does not match")
    if _manifest_digest(manifest_after) != after_manifest:
        raise TransitionLineageError("transition lineage after manifest digest does not match manifest")
    if manifest_after["policy_generation"] != after_generation:
        raise TransitionLineageError("transition lineage after generation does not match manifest")

    changes = payload["changes"]
    if not isinstance(changes, list) or any(not isinstance(item, Mapping) for item in changes):
        raise TransitionLineageError("transition lineage changes are invalid")
    normalized_changes = [dict(item) for item in changes]

    acknowledgement = payload["owner_acknowledgement"]
    if not isinstance(acknowledgement, Mapping):
        raise TransitionLineageError("transition lineage owner acknowledgement is invalid")
    _require_exact_keys(acknowledgement, _ACK_KEYS, "transition lineage owner acknowledgement")
    required = acknowledgement["required"]
    method = acknowledgement["method"]
    if not isinstance(required, bool) or method not in ACK_METHODS:
        raise TransitionLineageError("transition lineage owner acknowledgement is invalid")
    if required == (method == ACK_NONE):
        raise TransitionLineageError("transition lineage owner acknowledgement method is inconsistent")

    baseline = sequence == 1
    if baseline:
        if relation != RELATION_BASELINE or source != SOURCE_BASELINE or decision != DECISION_BASELINE:
            raise TransitionLineageError("first transition lineage record must be an explicit baseline")
        if any(value is not None for value in (previous, before_state, before_manifest, before_generation)):
            raise TransitionLineageError("transition lineage baseline cannot claim prior history")
        if normalized_changes:
            raise TransitionLineageError("transition lineage baseline cannot contain transition changes")
        if transaction_id is not None or authorization_digest is not None:
            raise TransitionLineageError("transition lineage baseline cannot contain update authorization")
        if not required or method != ACK_BASELINE:
            raise TransitionLineageError("transition lineage baseline requires local owner confirmation")
    else:
        if relation == RELATION_BASELINE:
            raise TransitionLineageError("only the first transition lineage record may be a baseline")
        if previous is None or before_state is None or before_manifest is None or before_generation is None:
            raise TransitionLineageError("transition lineage transition is missing prior-state evidence")
        if relation == TRANSITION_EXPANSION:
            if not required or method not in {ACK_TRANSITION, ACK_RECONCILE}:
                raise TransitionLineageError("trust expansion lineage requires owner acknowledgement")
            if authorization_digest is None:
                raise TransitionLineageError("trust expansion lineage requires authorization evidence")
        if decision == DECISION_ALLOWED and required:
            raise TransitionLineageError("automatic trust transition cannot claim owner acknowledgement")
        if decision == DECISION_EXPANSION and (
            relation != TRANSITION_EXPANSION or method != ACK_TRANSITION
        ):
            raise TransitionLineageError("owner-acknowledged expansion lineage is inconsistent")
        if source == SOURCE_PREPARED_UPDATE and decision not in {DECISION_ALLOWED, DECISION_EXPANSION}:
            raise TransitionLineageError("prepared-update lineage decision is invalid")
        if source == SOURCE_ACCEPTED_STATE_CLI and (
            decision != DECISION_LOCAL_OWNER or not required or method != ACK_ACCEPTED_STATE
        ):
            raise TransitionLineageError("accepted-state CLI lineage decision is invalid")
        if source == SOURCE_RECONCILE and (
            decision != DECISION_RECONCILE or not required or method != ACK_RECONCILE
        ):
            raise TransitionLineageError("lineage reconciliation decision is invalid")

    return {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "record_id": LINEAGE_RECORD_ID,
        "sequence": sequence,
        "recorded_at": _validate_timestamp(payload["recorded_at"]),
        "previous_record_digest": previous,
        "source": source,
        "transaction_id": transaction_id,
        "candidate_commit": candidate_commit,
        "accepted_state_before_digest": before_state,
        "accepted_state_after_digest": after_state,
        "accepted_manifest_before_digest": before_manifest,
        "accepted_manifest_after_digest": after_manifest,
        "policy_generation_before": before_generation,
        "policy_generation_after": after_generation,
        "accepted_state_after": accepted_state_after,
        "manifest_after": manifest_after,
        "relation": relation,
        "changes": normalized_changes,
        "decision": decision,
        "owner_acknowledgement": {"required": required, "method": method},
        "authorization_digest": authorization_digest,
    }


def canonical_lineage_record_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = validate_lineage_record(payload)
    return (
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def lineage_record_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_lineage_record_bytes(payload)).hexdigest()


def _production_path(path: Path) -> bool:
    try:
        return path.resolve(strict=False) == DEFAULT_TRANSITION_LINEAGE_DIR
    except OSError:
        return path == DEFAULT_TRANSITION_LINEAGE_DIR


def _expected_uid(production: bool) -> int:
    return 0 if production else os.geteuid()


def _trusted_directory(path: Path, *, production: bool) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_uid == _expected_uid(production)
        and not metadata.st_mode & 0o077
    )


def _trusted_record_file(path: Path, *, production: bool) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_uid == _expected_uid(production)
        and metadata.st_nlink == 1
        and not metadata.st_mode & 0o077
        and metadata.st_size <= MAX_LINEAGE_RECORD_BYTES
    )


def _ensure_lineage_directory(path: Path, *, production: bool) -> None:
    if production and os.geteuid() != 0:
        raise TransitionLineageError("production transition lineage changes require root")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not _trusted_directory(path, production=production):
        raise TransitionLineageError("transition lineage directory is untrusted")


def _decode_record(path: Path, *, production: bool) -> dict[str, Any]:
    if not _trusted_record_file(path, production=production):
        raise TransitionLineageError("transition lineage record file is untrusted")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise TransitionLineageError("transition lineage record cannot be read") from exc
    if not raw or len(raw) > MAX_LINEAGE_RECORD_BYTES:
        raise TransitionLineageError("transition lineage record size is invalid")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except TransitionLineageError:
        raise
    except (UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TransitionLineageError("transition lineage record is invalid JSON") from exc
    return validate_lineage_record(payload)


def read_transition_lineage(
    path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> list[dict[str, Any]]:
    production = _production_path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise TransitionLineageError("transition lineage directory cannot be inspected") from exc
    if not _trusted_directory(path, production=production):
        raise TransitionLineageError("transition lineage directory is untrusted")
    try:
        entries = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise TransitionLineageError("transition lineage directory cannot be read") from exc
    if len(entries) > MAX_LINEAGE_RECORDS:
        raise TransitionLineageError("transition lineage record limit exceeded")

    records: list[dict[str, Any]] = []
    previous_digest: str | None = None
    previous_record: dict[str, Any] | None = None
    for expected_sequence, entry in enumerate(entries, start=1):
        match = _RECORD_FILENAME_RE.fullmatch(entry.name)
        if match is None:
            raise TransitionLineageError("transition lineage directory contains an unexpected entry")
        if int(match.group("sequence")) != expected_sequence:
            raise TransitionLineageError("transition lineage sequence is not contiguous")
        record = _decode_record(entry, production=production)
        if record["sequence"] != expected_sequence:
            raise TransitionLineageError("transition lineage record sequence does not match filename")
        digest = lineage_record_digest(record)
        if digest != "sha256:" + match.group("digest"):
            raise TransitionLineageError("transition lineage record digest does not match filename")
        if record["previous_record_digest"] != previous_digest:
            raise TransitionLineageError("transition lineage hash chain is broken")

        if previous_record is not None:
            if record["accepted_state_before_digest"] != previous_record["accepted_state_after_digest"]:
                raise TransitionLineageError("transition lineage accepted-state chain is broken")
            if record["accepted_manifest_before_digest"] != previous_record["accepted_manifest_after_digest"]:
                raise TransitionLineageError("transition lineage manifest chain is broken")
            if record["policy_generation_before"] != previous_record["policy_generation_after"]:
                raise TransitionLineageError("transition lineage generation chain is broken")
            comparison = compare_trust_manifests(
                previous_record["accepted_state_after"]["manifest"], record["accepted_state_after"]["manifest"]
            )
            if comparison["relation"] == TRANSITION_GENERATION_REGRESSION:
                raise TransitionLineageError("transition lineage contains a generation regression")
            if record["relation"] != comparison["relation"]:
                raise TransitionLineageError("transition lineage relation does not match manifests")
            if record["changes"] != comparison["changes"]:
                raise TransitionLineageError("transition lineage changes do not match manifests")

        records.append(record)
        previous_digest = digest
        previous_record = record
    return records


def lineage_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not records:
        return {"established": False, "records": 0}
    head = validate_lineage_record(records[-1])
    baseline = validate_lineage_record(records[0])
    return {
        "established": True,
        "records": len(records),
        "baseline_recorded_at": baseline["recorded_at"],
        "head_sequence": head["sequence"],
        "head_record_digest": lineage_record_digest(head),
        "head_accepted_state_digest": head["accepted_state_after_digest"],
        "head_manifest_digest": head["accepted_manifest_after_digest"],
        "head_policy_generation": head["policy_generation_after"],
        "head_relation": head["relation"],
        "head_decision": head["decision"],
        "history_before_baseline": "unverified",
    }


def require_lineage_current(
    accepted_state: Mapping[str, Any],
    path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any]:
    try:
        accepted = validate_accepted_state(accepted_state)
    except AcceptedTrustStateError as exc:
        raise TransitionLineageError(f"accepted owner trust state is invalid: {exc}") from exc
    records = read_transition_lineage(path)
    if not records:
        raise TransitionLineageError(
            "transition lineage is not established; bootstrap the current accepted state before updating"
        )
    head = records[-1]
    if head["accepted_state_after_digest"] != accepted_state_digest(accepted):
        raise TransitionLineageError(
            "transition lineage head does not anchor the current accepted owner trust state"
        )
    if head["accepted_manifest_after_digest"] != accepted["manifest_digest"]:
        raise TransitionLineageError(
            "transition lineage head manifest does not match accepted owner trust state"
        )
    if head["manifest_after"] != accepted["manifest"]:
        raise TransitionLineageError(
            "transition lineage head manifest snapshot does not match accepted owner trust state"
        )
    return head


def _append_lineage_record(
    payload: Mapping[str, Any],
    path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any]:
    production = _production_path(path)
    _ensure_lineage_directory(path, production=production)
    lock_fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    temporary_name = ""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        existing = read_transition_lineage(path)
        record = validate_lineage_record(payload)
        expected_sequence = len(existing) + 1
        if record["sequence"] != expected_sequence:
            raise TransitionLineageError("transition lineage append sequence is invalid")
        expected_previous = lineage_record_digest(existing[-1]) if existing else None
        if record["previous_record_digest"] != expected_previous:
            raise TransitionLineageError("transition lineage append does not extend the current head")

        data = canonical_lineage_record_bytes(record)
        digest = hashlib.sha256(data).hexdigest()
        final = path / f"{expected_sequence:08d}-{digest}.json"
        # Build the immutable-by-interface record outside the lineage directory so
        # concurrent read-only inspection never observes a partial record entry.
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, prefix=".transition-lineage-append-", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o600)
        # link() gives create-if-absent semantics: an existing record is never replaced.
        os.link(temporary_name, final)
        os.unlink(temporary_name)
        temporary_name = ""
        os.fsync(lock_fd)
        return record
    except FileExistsError as exc:
        raise TransitionLineageError("transition lineage append target already exists") from exc
    except OSError as exc:
        raise TransitionLineageError("could not append transition lineage record") from exc
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _record_lineage_baseline(
    accepted_state: Mapping[str, Any],
    path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any]:
    accepted = validate_accepted_state(accepted_state)
    if read_transition_lineage(path):
        raise TransitionLineageError("transition lineage is already established")
    payload = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "record_id": LINEAGE_RECORD_ID,
        "sequence": 1,
        "recorded_at": _timestamp(),
        "previous_record_digest": None,
        "source": SOURCE_BASELINE,
        "transaction_id": None,
        "candidate_commit": None,
        "accepted_state_before_digest": None,
        "accepted_state_after_digest": accepted_state_digest(accepted),
        "accepted_manifest_before_digest": None,
        "accepted_manifest_after_digest": accepted["manifest_digest"],
        "policy_generation_before": None,
        "policy_generation_after": accepted["manifest"]["policy_generation"],
        "accepted_state_after": accepted,
        "manifest_after": accepted["manifest"],
        "relation": RELATION_BASELINE,
        "changes": [],
        "decision": DECISION_BASELINE,
        "owner_acknowledgement": {"required": True, "method": ACK_BASELINE},
        "authorization_digest": None,
    }
    return _append_lineage_record(payload, path)


def _record_state_transition(
    before_state: Mapping[str, Any],
    after_state: Mapping[str, Any],
    *,
    source: str,
    decision: str,
    acknowledgement_required: bool,
    acknowledgement_method: str,
    transaction_id: str | None = None,
    candidate_commit: str | None = None,
    authorization_digest: str | None = None,
    path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any] | None:
    before = validate_accepted_state(before_state)
    after = validate_accepted_state(after_state)
    if accepted_state_digest(before) == accepted_state_digest(after):
        return None
    head = require_lineage_current(before, path)
    comparison = compare_trust_manifests(before["manifest"], after["manifest"])
    if comparison["relation"] == TRANSITION_GENERATION_REGRESSION:
        raise TransitionLineageError("refusing to record accepted-state generation regression")
    payload = {
        "schema_version": LINEAGE_SCHEMA_VERSION,
        "record_id": LINEAGE_RECORD_ID,
        "sequence": int(head["sequence"]) + 1,
        "recorded_at": _timestamp(),
        "previous_record_digest": lineage_record_digest(head),
        "source": source,
        "transaction_id": transaction_id,
        "candidate_commit": candidate_commit,
        "accepted_state_before_digest": accepted_state_digest(before),
        "accepted_state_after_digest": accepted_state_digest(after),
        "accepted_manifest_before_digest": before["manifest_digest"],
        "accepted_manifest_after_digest": after["manifest_digest"],
        "policy_generation_before": before["manifest"]["policy_generation"],
        "policy_generation_after": after["manifest"]["policy_generation"],
        "accepted_state_after": after,
        "manifest_after": after["manifest"],
        "relation": comparison["relation"],
        "changes": comparison["changes"],
        "decision": decision,
        "owner_acknowledgement": {
            "required": acknowledgement_required,
            "method": acknowledgement_method,
        },
        "authorization_digest": authorization_digest,
    }
    return _append_lineage_record(payload, path)
