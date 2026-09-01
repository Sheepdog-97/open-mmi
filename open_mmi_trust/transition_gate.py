"""Old-trusted-side Trust Transition Gate v1.

Candidate releases are data until this module has parsed their Trust Manifest
from the candidate Git object database and compared it with local Accepted
Owner Trust State.  This module never imports or executes candidate Python,
shell scripts, hooks, or package metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .accepted_state import (
    DEFAULT_ACCEPTED_STATE_PATH,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    AcceptedTrustStateError,
    _record_acknowledged_expansion,
    _record_accepted_manifest,
    accepted_state_digest,
    compare_trust_manifests,
    read_accepted_state,
)
from .manifest import ManifestError, manifest_digest, validate_manifest
from .lineage import (
    ACK_NONE,
    ACK_TRANSITION,
    DECISION_ALLOWED,
    DECISION_EXPANSION,
    DEFAULT_TRANSITION_LINEAGE_DIR,
    SOURCE_PREPARED_UPDATE,
    TransitionLineageError,
    _record_state_transition,
    require_lineage_current,
)


TRANSITION_AUTHORIZATION_SCHEMA_VERSION = 1
TRANSITION_AUTHORIZATION_ID = "org.open-mmi.trust-transition-authorization"
DEFAULT_TRANSITION_AUTHORIZATION_PATH = Path(
    "/var/lib/open-mmi/trust/transition-authorization.v1.json"
)
CANDIDATE_MANIFEST_GIT_PATH = "open_mmi_trust/data/trust-manifest.v1.json"
MAX_CANDIDATE_MANIFEST_BYTES = 64 * 1024
GIT_TIMEOUT_SECONDS = 10.0

_TRANSACTION_RE = re.compile(r"^prepare-[0-9a-f]{32}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_BLOB_RE = re.compile(r"^[0-9a-f]{40,64}$")
_AUTHORIZATION_KEYS = {
    "schema_version",
    "authorization_id",
    "authorized_at",
    "transaction_id",
    "candidate_commit",
    "accepted_state_digest",
    "accepted_manifest_digest",
    "candidate_manifest_digest",
    "candidate_policy_generation",
}


class TransitionGateError(RuntimeError):
    """A prepared candidate cannot safely pass the trust transition gate."""


@dataclass(frozen=True)
class PreparedTransition:
    transaction_id: str
    candidate_commit: str
    candidate_manifest: dict[str, Any]
    candidate_manifest_digest: str
    candidate_manifest_blob: str
    accepted_state_digest: str
    accepted_manifest_digest: str
    relation: str
    changes: tuple[dict[str, Any], ...]
    allowed: bool
    acknowledgement_required: bool
    acknowledged: bool
    reason: str

    def summary(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "candidate_commit": self.candidate_commit,
            "candidate_manifest_digest": self.candidate_manifest_digest,
            "candidate_policy_generation": self.candidate_manifest["policy_generation"],
            "accepted_state_digest": self.accepted_state_digest,
            "accepted_manifest_digest": self.accepted_manifest_digest,
            "relation": self.relation,
            "changes": [dict(change) for change in self.changes],
            "allowed": self.allowed,
            "acknowledgement_required": self.acknowledgement_required,
            "acknowledged": self.acknowledged,
            "reason": self.reason,
        }


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise TransitionGateError("transition authorization timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TransitionGateError("transition authorization timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise TransitionGateError("transition authorization timestamp is invalid")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise TransitionGateError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise TransitionGateError(f"{label} is missing keys: {', '.join(missing)}")


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TransitionGateError(f"duplicate trust JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise TransitionGateError(f"invalid trust JSON number: {value}")


def _decode_candidate_manifest(data: bytes) -> dict[str, Any]:
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise TransitionGateError("candidate Trust Manifest is not UTF-8") from exc
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except TransitionGateError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TransitionGateError("candidate Trust Manifest is invalid JSON") from exc
    try:
        return validate_manifest(payload)
    except ManifestError as exc:
        raise TransitionGateError(f"candidate Trust Manifest is invalid: {exc}") from exc


def _validated_identity(transaction_id: object, candidate_commit: object) -> tuple[str, str]:
    transaction = str(transaction_id or "")
    candidate = str(candidate_commit or "").lower()
    if not _TRANSACTION_RE.fullmatch(transaction):
        raise TransitionGateError("prepared transaction identity is invalid")
    if not _COMMIT_RE.fullmatch(candidate):
        raise TransitionGateError("prepared candidate commit is invalid")
    return transaction, candidate


def _git(
    stage: Path,
    arguments: Sequence[str],
    *,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={stage}", "-C", str(stage), *arguments],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=text,
            check=False,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TransitionGateError("candidate Git object data could not be inspected") from exc


def load_candidate_manifest_from_git(
    stage: Path,
    candidate_commit: str,
) -> tuple[dict[str, Any], str]:
    """Read the candidate manifest as a regular Git blob without executing candidate code."""

    candidate = str(candidate_commit).lower()
    if not _COMMIT_RE.fullmatch(candidate):
        raise TransitionGateError("prepared candidate commit is invalid")
    tree = _git(
        stage,
        ("ls-tree", "--full-tree", candidate, "--", CANDIDATE_MANIFEST_GIT_PATH),
    )
    if tree.returncode != 0:
        raise TransitionGateError("candidate Trust Manifest could not be located")
    lines = [line for line in tree.stdout.splitlines() if line.strip()]
    if len(lines) != 1 or "\t" not in lines[0]:
        raise TransitionGateError("candidate Trust Manifest is missing or ambiguous")
    metadata, path = lines[0].split("\t", 1)
    parts = metadata.split()
    if len(parts) != 3 or path != CANDIDATE_MANIFEST_GIT_PATH:
        raise TransitionGateError("candidate Trust Manifest Git entry is invalid")
    mode, object_type, blob = parts
    if mode != "100644" or object_type != "blob" or not _BLOB_RE.fullmatch(blob):
        raise TransitionGateError("candidate Trust Manifest must be a non-executable regular Git blob")

    size_result = _git(stage, ("cat-file", "-s", blob))
    try:
        size = int(size_result.stdout.strip()) if size_result.returncode == 0 else -1
    except ValueError:
        size = -1
    if size < 1 or size > MAX_CANDIDATE_MANIFEST_BYTES:
        raise TransitionGateError("candidate Trust Manifest size is invalid")

    blob_result = _git(stage, ("cat-file", "blob", blob), text=False)
    if blob_result.returncode != 0 or len(blob_result.stdout) != size:
        raise TransitionGateError("candidate Trust Manifest blob could not be read exactly")
    return _decode_candidate_manifest(bytes(blob_result.stdout)), blob


def validate_transition_authorization(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TransitionGateError("transition authorization must be an object")
    _require_exact_keys(payload, _AUTHORIZATION_KEYS, "transition authorization")
    if payload["schema_version"] != TRANSITION_AUTHORIZATION_SCHEMA_VERSION:
        raise TransitionGateError("unsupported transition authorization schema_version")
    if payload["authorization_id"] != TRANSITION_AUTHORIZATION_ID:
        raise TransitionGateError("unexpected transition authorization id")
    transaction, candidate = _validated_identity(
        payload["transaction_id"], payload["candidate_commit"]
    )
    digests: dict[str, str] = {}
    for key in (
        "accepted_state_digest",
        "accepted_manifest_digest",
        "candidate_manifest_digest",
    ):
        value = payload[key]
        if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
            raise TransitionGateError(f"transition authorization {key} is invalid")
        digests[key] = value
    generation = payload["candidate_policy_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise TransitionGateError("transition authorization candidate generation is invalid")
    return {
        "schema_version": TRANSITION_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": TRANSITION_AUTHORIZATION_ID,
        "authorized_at": _validate_timestamp(payload["authorized_at"]),
        "transaction_id": transaction,
        "candidate_commit": candidate,
        **digests,
        "candidate_policy_generation": generation,
    }


def canonical_transition_authorization_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = validate_transition_authorization(payload)
    return (
        json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def transition_authorization_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_transition_authorization_bytes(payload)
    ).hexdigest()


def _production_path(path: Path) -> bool:
    try:
        return path.resolve(strict=False) == DEFAULT_TRANSITION_AUTHORIZATION_PATH
    except OSError:
        return path == DEFAULT_TRANSITION_AUTHORIZATION_PATH


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
        raise TransitionGateError("transition authorization directory is unavailable") from exc
    expected_uid = 0 if require_root else os.geteuid()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_mode & 0o077
    ):
        raise TransitionGateError("transition authorization directory is untrusted")


def read_transition_authorization(
    path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
) -> dict[str, Any] | None:
    production = _production_path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TransitionGateError("transition authorization file cannot be inspected") from exc
    if not _trusted_regular_file(path, require_root=production):
        raise TransitionGateError("transition authorization file is untrusted")
    try:
        text = path.read_text(encoding="utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except TransitionGateError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TransitionGateError("transition authorization file is invalid") from exc
    return validate_transition_authorization(payload)


def _write_transition_authorization(
    payload: Mapping[str, Any],
    path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
) -> dict[str, Any]:
    production = _production_path(path)
    if production and os.geteuid() != 0:
        raise TransitionGateError("production transition authorization changes require root")
    validated = validate_transition_authorization(payload)
    _ensure_parent(path, require_root=production)
    if path.exists() and not _trusted_regular_file(path, require_root=production):
        raise TransitionGateError("refusing to replace untrusted transition authorization file")

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
        raise TransitionGateError("could not persist transition authorization") from exc
    return validated


def _clear_transition_authorization(
    path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
) -> bool:
    production = _production_path(path)
    if production and os.geteuid() != 0:
        raise TransitionGateError("production transition authorization changes require root")
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise TransitionGateError("transition authorization file cannot be inspected") from exc
    if not _trusted_regular_file(path, require_root=production):
        raise TransitionGateError("refusing to remove untrusted transition authorization file")
    try:
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as exc:
        raise TransitionGateError("could not clear transition authorization") from exc
    return True


def _authorization_matches(
    authorization: Mapping[str, Any],
    *,
    transaction_id: str,
    candidate_commit: str,
    accepted_state_digest_value: str,
    accepted_manifest_digest: str,
    candidate_manifest_digest: str,
    candidate_policy_generation: int,
) -> bool:
    return (
        authorization["transaction_id"] == transaction_id
        and authorization["candidate_commit"] == candidate_commit
        and authorization["accepted_state_digest"] == accepted_state_digest_value
        and authorization["accepted_manifest_digest"] == accepted_manifest_digest
        and authorization["candidate_manifest_digest"] == candidate_manifest_digest
        and authorization["candidate_policy_generation"] == candidate_policy_generation
    )


def evaluate_prepared_candidate(
    stage: Path,
    *,
    transaction_id: object,
    candidate_commit: object,
    accepted_state_path: Path = DEFAULT_ACCEPTED_STATE_PATH,
    authorization_path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    lineage_path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> PreparedTransition:
    transaction, candidate = _validated_identity(transaction_id, candidate_commit)
    candidate_manifest, blob = load_candidate_manifest_from_git(stage, candidate)
    candidate_digest = "sha256:" + manifest_digest(candidate_manifest)

    try:
        accepted = read_accepted_state(accepted_state_path)
    except AcceptedTrustStateError as exc:
        raise TransitionGateError(f"accepted owner trust state is invalid: {exc}") from exc
    if accepted is None:
        return PreparedTransition(
            transaction,
            candidate,
            candidate_manifest,
            candidate_digest,
            blob,
            "",
            "",
            "not-established",
            tuple(),
            False,
            False,
            False,
            "accepted-owner-trust-not-established",
        )

    try:
        require_lineage_current(accepted, lineage_path)
    except TransitionLineageError as exc:
        raise TransitionGateError(f"transition lineage is not current: {exc}") from exc

    state_digest = accepted_state_digest(accepted)
    comparison = compare_trust_manifests(accepted["manifest"], candidate_manifest)
    relation = str(comparison["relation"])
    changes = tuple(dict(change) for change in comparison["changes"])
    accepted_manifest_digest = str(comparison["accepted_manifest_digest"])

    if relation == TRANSITION_GENERATION_REGRESSION:
        return PreparedTransition(
            transaction,
            candidate,
            candidate_manifest,
            candidate_digest,
            blob,
            state_digest,
            accepted_manifest_digest,
            relation,
            changes,
            False,
            False,
            False,
            "generation-regression",
        )
    if relation != TRANSITION_EXPANSION:
        return PreparedTransition(
            transaction,
            candidate,
            candidate_manifest,
            candidate_digest,
            blob,
            state_digest,
            accepted_manifest_digest,
            relation,
            changes,
            True,
            False,
            False,
            "allowed-without-owner-acknowledgement",
        )

    authorization = read_transition_authorization(authorization_path)
    acknowledged = bool(
        authorization
        and _authorization_matches(
            authorization,
            transaction_id=transaction,
            candidate_commit=candidate,
            accepted_state_digest_value=state_digest,
            accepted_manifest_digest=accepted_manifest_digest,
            candidate_manifest_digest=candidate_digest,
            candidate_policy_generation=candidate_manifest["policy_generation"],
        )
    )
    return PreparedTransition(
        transaction,
        candidate,
        candidate_manifest,
        candidate_digest,
        blob,
        state_digest,
        accepted_manifest_digest,
        relation,
        changes,
        acknowledged,
        True,
        acknowledged,
        (
            "owner-acknowledged-expansion"
            if acknowledged
            else "owner-acknowledgement-required"
        ),
    )


def require_prepared_candidate_allowed(
    stage: Path,
    *,
    transaction_id: object,
    candidate_commit: object,
    accepted_state_path: Path = DEFAULT_ACCEPTED_STATE_PATH,
    authorization_path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    lineage_path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> PreparedTransition:
    transition = evaluate_prepared_candidate(
        stage,
        transaction_id=transaction_id,
        candidate_commit=candidate_commit,
        accepted_state_path=accepted_state_path,
        authorization_path=authorization_path,
        lineage_path=lineage_path,
    )
    if transition.allowed:
        return transition
    if transition.reason == "accepted-owner-trust-not-established":
        raise TransitionGateError(
            "Accepted Owner Trust State is not established; bootstrap the installed boundary before updating"
        )
    if transition.reason == "generation-regression":
        raise TransitionGateError(
            "prepared candidate Trust Manifest regresses the accepted policy generation"
        )
    if transition.reason == "owner-acknowledgement-required":
        raise TransitionGateError(
            "prepared candidate expands accepted owner trust; local transition acknowledgement is required"
        )
    raise TransitionGateError("prepared candidate is blocked by the trust transition gate")


def _authorize_prepared_expansion(
    stage: Path,
    *,
    transaction_id: object,
    candidate_commit: object,
    expected_candidate_manifest_digest: str,
    expected_accepted_state_digest: str,
    accepted_state_path: Path = DEFAULT_ACCEPTED_STATE_PATH,
    authorization_path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    lineage_path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any]:
    transition = evaluate_prepared_candidate(
        stage,
        transaction_id=transaction_id,
        candidate_commit=candidate_commit,
        accepted_state_path=accepted_state_path,
        authorization_path=authorization_path,
        lineage_path=lineage_path,
    )
    if transition.relation != TRANSITION_EXPANSION:
        raise TransitionGateError("prepared candidate does not require expansion acknowledgement")
    if transition.candidate_manifest_digest != expected_candidate_manifest_digest:
        raise TransitionGateError("prepared candidate changed after owner review")
    if transition.accepted_state_digest != expected_accepted_state_digest:
        raise TransitionGateError("accepted owner trust state changed after owner review")
    payload = {
        "schema_version": TRANSITION_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": TRANSITION_AUTHORIZATION_ID,
        "authorized_at": _timestamp(),
        "transaction_id": transition.transaction_id,
        "candidate_commit": transition.candidate_commit,
        "accepted_state_digest": transition.accepted_state_digest,
        "accepted_manifest_digest": transition.accepted_manifest_digest,
        "candidate_manifest_digest": transition.candidate_manifest_digest,
        "candidate_policy_generation": transition.candidate_manifest["policy_generation"],
    }
    return _write_transition_authorization(payload, authorization_path)


def activate_acknowledged_expansion(
    transition: PreparedTransition,
    *,
    accepted_state_path: Path = DEFAULT_ACCEPTED_STATE_PATH,
    authorization_path: Path = DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    lineage_path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any] | None:
    """Commit acknowledged expansion authority and append lineage before candidate execution."""

    if transition.relation != TRANSITION_EXPANSION:
        return None
    if not transition.allowed or not transition.acknowledged:
        raise TransitionGateError("trust-boundary expansion is not owner-acknowledged")
    authorization = read_transition_authorization(authorization_path)
    if authorization is None or not _authorization_matches(
        authorization,
        transaction_id=transition.transaction_id,
        candidate_commit=transition.candidate_commit,
        accepted_state_digest_value=transition.accepted_state_digest,
        accepted_manifest_digest=transition.accepted_manifest_digest,
        candidate_manifest_digest=transition.candidate_manifest_digest,
        candidate_policy_generation=transition.candidate_manifest["policy_generation"],
    ):
        raise TransitionGateError("transition acknowledgement changed before deployment")
    try:
        before = read_accepted_state(accepted_state_path)
        if before is None:
            raise AcceptedTrustStateError("accepted owner trust state disappeared")
        require_lineage_current(before, lineage_path)
        recorded = _record_acknowledged_expansion(
            transition.candidate_manifest,
            expected_accepted_state_digest=transition.accepted_state_digest,
            path=accepted_state_path,
        )
        _record_state_transition(
            before,
            recorded,
            source=SOURCE_PREPARED_UPDATE,
            decision=DECISION_EXPANSION,
            acknowledgement_required=True,
            acknowledgement_method=ACK_TRANSITION,
            transaction_id=transition.transaction_id,
            candidate_commit=transition.candidate_commit,
            authorization_digest=transition_authorization_digest(authorization),
            path=lineage_path,
        )
    except (AcceptedTrustStateError, TransitionLineageError) as exc:
        raise TransitionGateError(f"could not activate acknowledged trust expansion: {exc}") from exc
    try:
        _clear_transition_authorization(authorization_path)
    except TransitionGateError:
        # Once accepted state and lineage have advanced, the old authorization is
        # stale by construction. Failure to remove it cannot re-authorize anything.
        pass
    return recorded

def finalize_successful_transition(
    transition: PreparedTransition,
    *,
    accepted_state_path: Path = DEFAULT_ACCEPTED_STATE_PATH,
    lineage_path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
) -> dict[str, Any] | None:
    """Advance accepted state and append lineage after successful non-expanding deployment."""

    if transition.relation == TRANSITION_EXPANSION:
        return None
    try:
        current = read_accepted_state(accepted_state_path)
        if current is None:
            raise AcceptedTrustStateError("accepted owner trust state disappeared")
        require_lineage_current(current, lineage_path)
        candidate_digest = "sha256:" + manifest_digest(transition.candidate_manifest)
        if current["manifest_digest"] == candidate_digest:
            return current
        recorded = _record_accepted_manifest(
            transition.candidate_manifest,
            accepted_state_path,
        )
        _record_state_transition(
            current,
            recorded,
            source=SOURCE_PREPARED_UPDATE,
            decision=DECISION_ALLOWED,
            acknowledgement_required=False,
            acknowledgement_method=ACK_NONE,
            transaction_id=transition.transaction_id,
            candidate_commit=transition.candidate_commit,
            path=lineage_path,
        )
        return recorded
    except (AcceptedTrustStateError, TransitionLineageError) as exc:
        raise TransitionGateError(
            f"could not finalize accepted owner trust state after deployment: {exc}"
        ) from exc
