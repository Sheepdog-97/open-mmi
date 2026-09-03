"""Read-only Trust Inspector v1 for Open MMI installations.

The inspector reports evidence.  It does not authorize capabilities, mutate trust
state, contact a network service, or upgrade a release's declared assurance level.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from open_mmi_telemetry.guard import (
    DEFAULT_AUTHORIZATION_PATH,
    TelemetryDenied,
    TelemetryGuardError,
    collect_with_guard,
    read_authorization,
)

from .accepted_state import (
    DEFAULT_ACCEPTED_STATE_PATH,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    AcceptedTrustStateError,
    accepted_state_digest,
    compare_trust_manifests,
    read_accepted_state,
)
from .manifest import DEFAULT_MANIFEST_PATH, ManifestError, load_manifest, manifest_digest
from .release_integrity import (
    DEFAULT_INSTALL_ROOT as INTEGRITY_DEFAULT_INSTALL_ROOT,
    DEFAULT_INTEGRITY_STATE_PATH,
    ReleaseIntegrityError,
    default_install_root as integrity_default_install_root,
    default_package_root as integrity_default_package_root,
    integrity_state_digest,
    read_integrity_state,
    verify_installed_runtime,
    verify_privileged_installed_runtime,
)
from .release_provenance import (
    DEFAULT_PROVENANCE_ROOT_PATH,
    ReleaseProvenanceError,
    provenance_root_digest,
    read_provenance_root,
    verification_repository_for_integrity,
    verify_commit_provenance,
)
from .lineage import (
    DEFAULT_TRANSITION_LINEAGE_DIR,
    TransitionLineageError,
    lineage_summary,
    read_transition_lineage,
    require_lineage_current,
)


INSPECTION_SCHEMA_VERSION = 1
PASS = "PASS"
UNVERIFIED = "UNVERIFIED"
FAIL = "FAIL"
STATUSES = {PASS, UNVERIFIED, FAIL}
BOOTSTRAP_SHA384_BASE64 = "sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"
_REMOTE_DEPENDENCY_RE = re.compile(
    r"<(?:script|link)\b[^>]*(?:src|href)=[\"'](?P<url>(?:https?:)?//[^\"']+)",
    re.IGNORECASE,
)


def _check(check_id: str, status: str, summary: str, **evidence: Any) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"invalid trust inspection status: {status}")
    return {
        "id": check_id,
        "status": status,
        "summary": summary,
        "evidence": evidence,
    }


def _overall_status(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(check.get("status")) for check in checks}
    if FAIL in statuses:
        return FAIL
    if UNVERIFIED in statuses:
        return UNVERIFIED
    return PASS


def _default_install_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _inspect_manifest(path: Path) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    try:
        manifest = load_manifest(path)
    except ManifestError as exc:
        return (
            None,
            {
                "available": False,
                "path": str(path),
                "error": str(exc),
            },
            _check(
                "manifest.valid",
                FAIL,
                "Trust Manifest is missing, malformed, or unsupported.",
                path=str(path),
                error=str(exc),
            ),
        )

    digest = "sha256:" + manifest_digest(manifest)
    summary = {
        "available": True,
        "path": str(path),
        "manifest_id": manifest["manifest_id"],
        "schema_version": manifest["schema_version"],
        "policy_generation": manifest["policy_generation"],
        "digest": digest,
        "capabilities": manifest["capabilities"],
    }
    return (
        manifest,
        summary,
        _check(
            "manifest.valid",
            PASS,
            "Trust Manifest parsed strictly and has a deterministic self-digest.",
            path=str(path),
            policy_generation=manifest["policy_generation"],
            digest=digest,
            note="This proves internal manifest consistency, not release provenance or file integrity.",
        ),
    )


def _inspect_accepted_owner_state(
    path: Path,
    manifest: Mapping[str, Any] | None,
) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _check(
            "owner.accepted-release-state",
            UNVERIFIED,
            "Accepted Owner Trust State is not established yet.",
            path=str(path),
            established=False,
            note="Telemetry authorization is feature-specific consent, not accepted release authority.",
        )
    except PermissionError:
        return _check(
            "owner.accepted-release-state",
            UNVERIFIED,
            "Accepted Owner Trust State exists behind a permission boundary and could not be inspected.",
            path=str(path),
            established=None,
            suggestion="Run the inspector with local read authority for the root-owned trust state.",
        )
    except OSError as exc:
        return _check(
            "owner.accepted-release-state",
            UNVERIFIED,
            "Accepted Owner Trust State could not be inspected.",
            path=str(path),
            established=None,
            error=str(exc),
        )

    if not os.access(path, os.R_OK):
        return _check(
            "owner.accepted-release-state",
            UNVERIFIED,
            "Accepted Owner Trust State is present but not readable by this inspector process.",
            path=str(path),
            established=None,
            mode=oct(metadata.st_mode & 0o777),
            uid=metadata.st_uid,
            suggestion="Run the inspector with local read authority for the root-owned trust state.",
        )

    try:
        state = read_accepted_state(path)
    except AcceptedTrustStateError as exc:
        return _check(
            "owner.accepted-release-state",
            FAIL,
            "Accepted Owner Trust State is present but fails strict validation.",
            path=str(path),
            established=None,
            error=str(exc),
        )

    if state is None:
        return _check(
            "owner.accepted-release-state",
            UNVERIFIED,
            "Accepted Owner Trust State is not established yet.",
            path=str(path),
            established=False,
        )

    evidence = {
        "path": str(path),
        "established": True,
        "accepted_at": state["accepted_at"],
        "accepted_state_digest": accepted_state_digest(state),
        "accepted_manifest_digest": state["manifest_digest"],
        "accepted_generation": state["manifest"]["policy_generation"],
        "accepted_capabilities": state["manifest"]["capabilities"],
    }
    if manifest is None:
        evidence["current_relation"] = "unverified"
        return _check(
            "owner.accepted-release-state",
            UNVERIFIED,
            "Accepted Owner Trust State is valid, but the installed Trust Manifest is unavailable for comparison.",
            **evidence,
        )

    comparison = compare_trust_manifests(state["manifest"], manifest)
    evidence["current_relation"] = comparison["relation"]
    evidence["current_manifest_digest"] = comparison["candidate_manifest_digest"]
    evidence["comparison"] = comparison
    if comparison["relation"] in {TRANSITION_EXPANSION, TRANSITION_GENERATION_REGRESSION}:
        return _check(
            "owner.accepted-release-state",
            FAIL,
            "Installed Trust Manifest exceeds or regresses the locally accepted owner trust boundary.",
            **evidence,
        )

    return _check(
        "owner.accepted-release-state",
        PASS,
        "Installed Trust Manifest does not exceed the locally accepted owner trust boundary.",
        **evidence,
    )



def _inspect_release_integrity(
    path: Path,
    install_root: Path,
    package_root: Path,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Return the integrity check plus validated state when available."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return (
            _check(
                "release.file-integrity",
                UNVERIFIED,
                "Installed Release/File Integrity is not established yet.",
                path=str(path),
                established=False,
                note=(
                    "Integrity v1 binds installed runtime bytes to an exact accepted Git candidate; "
                    "release provenance is evaluated separately."
                ),
            ),
            None,
        )
    except PermissionError:
        return (
            _check(
                "release.file-integrity",
                UNVERIFIED,
                "Installed Release/File Integrity exists behind a permission boundary and could not be inspected.",
                path=str(path),
                suggestion="Run the inspector with local read authority for the root-owned trust state.",
            ),
            None,
        )
    except OSError as exc:
        return (
            _check(
                "release.file-integrity", UNVERIFIED,
                "Installed Release/File Integrity could not be inspected.",
                path=str(path), error=str(exc),
            ),
            None,
        )

    if not os.access(path, os.R_OK):
        return (
            _check(
                "release.file-integrity", UNVERIFIED,
                "Installed Release/File Integrity is present but not readable by this inspector process.",
                path=str(path), mode=oct(metadata.st_mode & 0o777), uid=metadata.st_uid,
                suggestion="Run the inspector with local read authority for the root-owned trust state.",
            ),
            None,
        )

    try:
        state = read_integrity_state(path)
    except ReleaseIntegrityError as exc:
        return (
            _check(
                "release.file-integrity", FAIL,
                "Installed Release/File Integrity state is present but fails strict validation.",
                path=str(path), error=str(exc),
            ),
            None,
        )
    if state is None:
        return (
            _check(
                "release.file-integrity", UNVERIFIED,
                "Installed Release/File Integrity is not established yet.",
                path=str(path), established=False,
            ),
            None,
        )

    verification = verify_installed_runtime(state, install_root, package_root)
    evidence = {
        "path": str(path),
        "install_root": str(install_root),
        "package_root": str(package_root),
        "candidate_commit": state["candidate_commit"],
        "trust_manifest_digest": state["trust_manifest_digest"],
        "inventory_digest": state["inventory_digest"],
        "integrity_state_digest": integrity_state_digest(state),
        "recorded_at": state["recorded_at"],
        "record_source": state["record_source"],
        "files_expected": verification["files_expected"],
    }
    if not verification["matches"]:
        return (
            _check(
                "release.file-integrity", FAIL,
                "Installed Open MMI runtime bytes do not match the recorded accepted-candidate inventory.",
                **evidence,
                missing=verification["missing"],
                modified=verification["modified"],
                extra=verification["extra"],
                unsafe=verification["unsafe"],
            ),
            state,
        )
    return (
        _check(
            "release.file-integrity", PASS,
            "Installed Open MMI runtime bytes match the exact recorded Git-candidate inventory.",
            **evidence,
            note=(
                "This proves local byte identity to the accepted candidate Git tree. "
                "Signer authentication is reported separately by release.provenance."
            ),
        ),
        state,
    )


def _inspect_privileged_runtime_integrity(
    integrity_state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if integrity_state is None:
        return _check(
            "release.privileged-runtime-integrity",
            UNVERIFIED,
            "Privileged runtime binding cannot be evaluated without "
            "Installed Release/File Integrity.",
            established=False,
        )

    try:
        verification = verify_privileged_installed_runtime(integrity_state)
    except ReleaseIntegrityError as exc:
        return _check(
            "release.privileged-runtime-integrity",
            FAIL,
            "The production privileged Open MMI runtime could not be bound "
            "to the recorded release.",
            error=str(exc),
        )

    evidence = {
        "candidate_commit": integrity_state["candidate_commit"],
        "inventory_digest": integrity_state["inventory_digest"],
        "source_root": verification["source_root"],
        "package_root": verification["package_root"],
        "systemd_unit_root": verification["systemd_unit_root"],
        "files_expected": verification["files_expected"],
    }
    if not verification["matches"]:
        return _check(
            "release.privileged-runtime-integrity",
            FAIL,
            "The production source, installed Python package, or privileged "
            "update units differ from the recorded release.",
            **evidence,
            missing=verification["missing"],
            modified=verification["modified"],
            extra=verification["extra"],
            unsafe=verification["unsafe"],
        )

    return _check(
        "release.privileged-runtime-integrity",
        PASS,
        "The production source, installed Python package, and privileged "
        "update units match the recorded release.",
        **evidence,
        note=(
            "The privileged systemd units execute the verified installed Python "
            "modules directly; pip-generated console wrappers are not part of "
            "this trust handoff."
        ),
    )


def _inspect_release_provenance(
    path: Path,
    integrity_state: Mapping[str, Any] | None,
    install_root: Path,
) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _check(
            "release.provenance",
            UNVERIFIED,
            "No independently owner-pinned release-signing identity is established yet.",
            path=str(path),
            signer_root="not-established",
        )
    except PermissionError:
        return _check(
            "release.provenance",
            UNVERIFIED,
            "Pinned release-signer state exists behind a permission boundary and could not be inspected.",
            path=str(path),
            suggestion="Run the inspector with local read authority for the root-owned trust state.",
        )
    except OSError as exc:
        return _check(
            "release.provenance",
            UNVERIFIED,
            "Pinned release-signer state could not be inspected.",
            path=str(path), error=str(exc),
        )

    if not os.access(path, os.R_OK):
        return _check(
            "release.provenance",
            UNVERIFIED,
            "Pinned release-signer state is present but not readable by this inspector process.",
            path=str(path), mode=oct(metadata.st_mode & 0o777), uid=metadata.st_uid,
            suggestion="Run the inspector with local read authority for the root-owned trust state.",
        )
    try:
        root = read_provenance_root(path)
    except ReleaseProvenanceError as exc:
        return _check(
            "release.provenance",
            FAIL,
            "Pinned release-signer state is present but fails strict validation.",
            path=str(path), error=str(exc),
        )
    if root is None:
        return _check(
            "release.provenance",
            UNVERIFIED,
            "No independently owner-pinned release-signing identity is established yet.",
            path=str(path), signer_root="not-established",
        )
    root_evidence = {
        "path": str(path),
        "provenance_root_digest": provenance_root_digest(root),
        "root_source": root["root_source"],
        "primary_fingerprint": root["primary_fingerprint"],
        "signing_fingerprints": root["signing_fingerprints"],
        "public_key_sha256": root["public_key_sha256"],
        "baseline_commit": root["baseline_commit"],
        "baseline_integrity_state_digest": root["baseline_integrity_state_digest"],
        "history_before_baseline": root["history_before_baseline"],
    }
    if integrity_state is None:
        return _check(
            "release.provenance",
            UNVERIFIED,
            "Pinned release-signer root is valid, but current installed integrity evidence is unavailable for commit binding.",
            **root_evidence,
        )
    if (
        integrity_state["candidate_commit"] == root["baseline_commit"]
        and integrity_state_digest(integrity_state) != root["baseline_integrity_state_digest"]
    ):
        return _check(
            "release.provenance",
            FAIL,
            "Pinned release-signer baseline no longer binds the integrity state it was established against.",
            current_integrity_state_digest=integrity_state_digest(integrity_state),
            **root_evidence,
        )
    try:
        repository = verification_repository_for_integrity(
            install_root, integrity_state["candidate_commit"]
        )
        verification = verify_commit_provenance(
            repository, integrity_state["candidate_commit"], root
        )
    except ReleaseProvenanceError as exc:
        return _check(
            "release.provenance",
            FAIL,
            "Current integrity-bound Git commit does not verify against the owner-pinned release signer root.",
            candidate_commit=integrity_state["candidate_commit"],
            error=str(exc),
            **root_evidence,
        )
    return _check(
        "release.provenance",
        PASS,
        "Current integrity-bound Git commit has a valid offline OpenPGP signature from the owner-pinned release signer root.",
        candidate_commit=integrity_state["candidate_commit"],
        verification_repository=str(repository),
        signing_fingerprint=verification["signing_fingerprint"],
        signature_date=verification["signature_date"],
        signature_timestamp=verification["signature_timestamp"],
        **root_evidence,
        note=(
            "Verification uses an isolated temporary GnuPG home containing only the pinned public key. "
            "It does not rely on GitHub's verified badge, a user keyring, Web-of-Trust decisions, or network key discovery."
        ),
    )

def _inspect_transition_lineage(
    path: Path,
    accepted_state_path: Path,
) -> dict[str, Any]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Trust Transition Lineage is not established yet.",
            path=str(path),
            established=False,
            note="A locally confirmed baseline does not retroactively prove transitions before Lineage v1.",
        )
    except PermissionError:
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Trust Transition Lineage exists behind a permission boundary and could not be inspected.",
            path=str(path),
            established=None,
            suggestion="Run the inspector with local read authority for the root-owned trust state.",
        )
    except OSError as exc:
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Trust Transition Lineage could not be inspected.",
            path=str(path),
            established=None,
            error=str(exc),
        )

    if not os.access(path, os.R_OK | os.X_OK):
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Trust Transition Lineage is present but not readable by this inspector process.",
            path=str(path),
            mode=oct(metadata.st_mode & 0o777),
            uid=metadata.st_uid,
            suggestion="Run the inspector with local read authority for the root-owned trust state.",
        )
    try:
        records = read_transition_lineage(path)
    except TransitionLineageError as exc:
        return _check(
            "release.transition-lineage",
            FAIL,
            "Trust Transition Lineage is present but its append-only chain fails validation.",
            path=str(path),
            error=str(exc),
        )
    if not records:
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Trust Transition Lineage directory exists but no baseline is established.",
            path=str(path),
            established=False,
        )

    try:
        accepted_metadata = accepted_state_path.lstat()
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Transition lineage is valid, but current Accepted Owner Trust State is unavailable for head anchoring.",
            path=str(path),
            error=f"{type(exc).__name__}: {exc}",
            **lineage_summary(records),
        )
    if not os.access(accepted_state_path, os.R_OK):
        return _check(
            "release.transition-lineage",
            UNVERIFIED,
            "Transition lineage is valid, but current Accepted Owner Trust State is unreadable for head anchoring.",
            path=str(path),
            accepted_state_mode=oct(accepted_metadata.st_mode & 0o777),
            **lineage_summary(records),
        )
    try:
        accepted = read_accepted_state(accepted_state_path)
        if accepted is None:
            raise TransitionLineageError("accepted owner trust state is not established")
        head = require_lineage_current(accepted, path)
    except (AcceptedTrustStateError, TransitionLineageError) as exc:
        return _check(
            "release.transition-lineage",
            FAIL,
            "Trust Transition Lineage does not anchor the current Accepted Owner Trust State.",
            path=str(path),
            error=str(exc),
            **lineage_summary(records),
        )
    return _check(
        "release.transition-lineage",
        PASS,
        "Trust Transition Lineage is hash-chained and anchors the current Accepted Owner Trust State.",
        path=str(path),
        head_record_digest=lineage_summary(records)["head_record_digest"],
        head_sequence=head["sequence"],
        records=len(records),
        baseline_recorded_at=records[0]["recorded_at"],
        latest_relation=head["relation"],
        latest_decision=head["decision"],
        history_before_baseline="unverified",
        note="Hash chaining and accepted-state anchoring detect local record edits/reordering and missing authority-changing tail records; arbitrary root replacement still requires an external integrity anchor to detect independently.",
    )

def _inspect_telemetry_authorization(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return (
            {"authorized": False, "state": "not-authorized"},
            _check(
                "telemetry.authorization-state",
                PASS,
                "No local telemetry authorization is present.",
                path=str(path),
                authorized=False,
            ),
        )
    except PermissionError:
        return (
            {"authorized": None, "state": "inaccessible"},
            _check(
                "telemetry.authorization-state",
                UNVERIFIED,
                "Telemetry authorization state exists behind a permission boundary and could not be inspected.",
                path=str(path),
                suggestion="Run the inspector with local read authority for the root-owned trust state.",
            ),
        )
    except OSError as exc:
        return (
            {"authorized": None, "state": "unreadable"},
            _check(
                "telemetry.authorization-state",
                UNVERIFIED,
                "Telemetry authorization state could not be inspected.",
                path=str(path),
                error=str(exc),
            ),
        )

    if not os.access(path, os.R_OK):
        return (
            {"authorized": None, "state": "inaccessible"},
            _check(
                "telemetry.authorization-state",
                UNVERIFIED,
                "Telemetry authorization state is present but not readable by this inspector process.",
                path=str(path),
                mode=oct(metadata.st_mode & 0o777),
                uid=metadata.st_uid,
                suggestion="Run the inspector with local read authority for the root-owned trust state.",
            ),
        )

    try:
        authorization = read_authorization(path)
    except TelemetryGuardError as exc:
        return (
            {"authorized": None, "state": "invalid"},
            _check(
                "telemetry.authorization-state",
                FAIL,
                "Telemetry authorization state is present but fails Telemetry Guard validation.",
                path=str(path),
                error=str(exc),
            ),
        )

    if authorization is None:
        return (
            {"authorized": False, "state": "not-authorized"},
            _check(
                "telemetry.authorization-state",
                PASS,
                "No local telemetry authorization is present.",
                path=str(path),
                authorized=False,
            ),
        )

    redacted = {
        "authorized": True,
        "state": "authorized",
        "authorized_at": authorization["authorized_at"],
        "scope": authorization["scope"],
        "scope_digest": authorization["scope_digest"],
        "vin_binding": {
            "algorithm": authorization["vin_binding"]["algorithm"],
            "iterations": authorization["vin_binding"]["iterations"],
        },
    }
    return (
        redacted,
        _check(
            "telemetry.authorization-state",
            PASS,
            "Telemetry authorization state is structurally trusted and scope-bound.",
            path=str(path),
            authorized=True,
            scope_digest=authorization["scope_digest"],
            note="VIN salt and fingerprint are intentionally omitted from inspection output.",
        ),
    )


def _inspect_telemetry_default_deny() -> dict[str, Any]:
    scope = {
        "schema_version": 1,
        "purpose": "trust-inspector-self-test",
        "signals": ["trust-inspector.synthetic-signal"],
        "retention": "session",
        "destination": "local-only",
    }
    sampled = []

    def sampler() -> int:
        sampled.append(True)
        return 1

    # /proc is read-only for ordinary file creation and this exact child is not a
    # Telemetry Guard production path.  The guard only checks for existence here;
    # the inspector never creates or removes anything.
    absent_authorization = Path("/proc/open-mmi-trust-inspector-no-authorization")
    try:
        collect_with_guard(
            "WVWZZZ1KZ6W000001",
            scope,
            {"trust-inspector.synthetic-signal": sampler},
            absent_authorization,
        )
    except TelemetryDenied as exc:
        if exc.reason == "not-authorized" and not sampled:
            return _check(
                "telemetry.default-deny-runtime",
                PASS,
                "Telemetry Guard denied an unauthorized synthetic collection before its sampler ran.",
                denial_reason=exc.reason,
                sampler_calls=0,
            )
        return _check(
            "telemetry.default-deny-runtime",
            FAIL,
            "Telemetry Guard denied the self-test, but not with the expected pre-sampling default-deny behavior.",
            denial_reason=exc.reason,
            sampler_calls=len(sampled),
        )
    except Exception as exc:  # fail closed for an inspector self-test
        return _check(
            "telemetry.default-deny-runtime",
            FAIL,
            "Telemetry Guard default-deny self-test could not be completed safely.",
            error=f"{type(exc).__name__}: {exc}",
            sampler_calls=len(sampled),
        )

    return _check(
        "telemetry.default-deny-runtime",
        FAIL,
        "Telemetry Guard allowed collection without authorization.",
        sampler_calls=len(sampled),
    )



def _inspect_telemetry_self_authorization_source(root: Path) -> dict[str, Any]:
    mutation_names = {"_create_authorization", "_write_authorization", "_revoke_authorization"}
    package_roots = (
        "actions",
        "bindings",
        "canbusd",
        "open_mmi_telemetry",
        "open_mmi_trust",
        "powerd",
        "ui",
        "vehicles",
    )
    allowed = {
        root / "open_mmi_telemetry" / "guard.py",
        root / "open_mmi_telemetry" / "cli.py",
    }
    offenders: list[str] = []
    scanned = 0
    try:
        for package_name in package_roots:
            package_root = root / package_name
            if not package_root.exists():
                continue
            for path in sorted(package_root.rglob("*.py")):
                if path in allowed or "__pycache__" in path.parts:
                    continue
                scanned += 1
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            if alias.name in mutation_names:
                                offenders.append(
                                    f"{path.relative_to(root)}:{node.lineno}:import:{alias.name}"
                                )
                    if not isinstance(node, ast.Call):
                        continue
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    else:
                        name = ""
                    if name in mutation_names:
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}:{name}")
    except (OSError, UnicodeError, SyntaxError) as exc:
        return _check(
            "telemetry.self-authorization-source-tripwire",
            FAIL,
            "Installed production source could not be inspected for silent Telemetry Guard authorization calls.",
            error=f"{type(exc).__name__}: {exc}",
        )

    if scanned == 0:
        return _check(
            "telemetry.self-authorization-source-tripwire",
            FAIL,
            "No installed Open MMI Python source was available to reproduce the telemetry self-authorization tripwire.",
        )
    if offenders:
        return _check(
            "telemetry.self-authorization-source-tripwire",
            FAIL,
            "Production code contains Telemetry Guard authorization mutation calls outside the supported owner CLI.",
            offenders=offenders,
            files_scanned=scanned,
        )
    return _check(
        "telemetry.self-authorization-source-tripwire",
        PASS,
        "Installed production source reproduces the no-silent-self-authorization CI tripwire.",
        files_scanned=scanned,
        note="This constrains official Open MMI code; it does not prevent arbitrary root software from modifying the machine.",
    )

def _inspect_accepted_state_self_authorization_source(root: Path) -> dict[str, Any]:
    mutation_names = {
        "_record_accepted_manifest",
        "_record_acknowledged_expansion",
        "_write_accepted_state",
    }
    package_roots = (
        "actions",
        "bindings",
        "canbusd",
        "open_mmi_telemetry",
        "open_mmi_trust",
        "powerd",
        "ui",
        "vehicles",
    )
    state_module = root / "open_mmi_trust" / "accepted_state.py"
    owner_cli = root / "open_mmi_trust" / "accepted_state_cli.py"
    transition_module = root / "open_mmi_trust" / "transition_gate.py"
    allowed = {
        owner_cli: {"_record_accepted_manifest"},
        transition_module: {
            "_record_accepted_manifest",
            "_record_acknowledged_expansion",
        },
    }
    offenders: list[str] = []
    scanned = 0
    try:
        for package_name in package_roots:
            package_root = root / package_name
            if not package_root.exists():
                continue
            for path in sorted(package_root.rglob("*.py")):
                if path == state_module or "__pycache__" in path.parts:
                    continue
                scanned += 1
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            if alias.name not in mutation_names:
                                continue
                            if alias.name in allowed.get(path, set()):
                                continue
                            offenders.append(
                                f"{path.relative_to(root)}:{node.lineno}:import:{alias.name}"
                            )
                    if not isinstance(node, ast.Call):
                        continue
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    else:
                        name = ""
                    if name not in mutation_names:
                        continue
                    if name in allowed.get(path, set()):
                        continue
                    offenders.append(f"{path.relative_to(root)}:{node.lineno}:{name}")
    except (OSError, UnicodeError, SyntaxError) as exc:
        return _check(
            "owner.accepted-state-self-authorization-source-tripwire",
            FAIL,
            "Installed production source could not be inspected for silent accepted-state mutation calls.",
            error=f"{type(exc).__name__}: {exc}",
        )

    if scanned == 0:
        return _check(
            "owner.accepted-state-self-authorization-source-tripwire",
            FAIL,
            "No installed Open MMI Python source was available to reproduce the accepted-state mutation tripwire.",
        )
    if offenders:
        return _check(
            "owner.accepted-state-self-authorization-source-tripwire",
            FAIL,
            "Production code contains Accepted Owner Trust State mutation calls outside the owner/transition authority surfaces.",
            offenders=offenders,
            files_scanned=scanned,
        )
    return _check(
        "owner.accepted-state-self-authorization-source-tripwire",
        PASS,
        "Installed production source reproduces the accepted-state mutation tripwire.",
        files_scanned=scanned,
        note=(
            "The owner CLI may use only the monotonic record primitive; the transition gate "
            "may additionally record an exact owner-acknowledged expansion. The raw writer remains module-internal."
        ),
    )


def _inspect_provenance_root_mutation_source(root: Path) -> dict[str, Any]:
    provenance_module = root / "open_mmi_trust" / "release_provenance.py"
    provenance_cli = root / "open_mmi_trust" / "release_provenance_cli.py"
    missing = [
        str(path.relative_to(root)) for path in (provenance_module, provenance_cli) if not path.is_file()
    ]
    if missing:
        return _check(
            "release.provenance-root-mutation-source-tripwire",
            UNVERIFIED,
            "Installed release-provenance source is not fully available for mutation-tripwire reproduction.",
            missing=missing,
        )
    mutation_names = {"_write_provenance_root"}
    offenders: list[str] = []
    scanned = 0
    ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
    try:
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(root)
            if ignored_roots.intersection(relative.parts) or path == provenance_module:
                continue
            scanned += 1
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in mutation_names and path != provenance_cli:
                            offenders.append(f"{relative}:{node.lineno}:import:{alias.name}")
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name in mutation_names and path != provenance_cli:
                    offenders.append(f"{relative}:{node.lineno}:{name}")
    except (OSError, UnicodeError, SyntaxError) as exc:
        return _check(
            "release.provenance-root-mutation-source-tripwire",
            FAIL,
            "Installed production source could not be inspected for release-signer root mutation calls.",
            error=f"{type(exc).__name__}: {exc}",
        )
    if scanned == 0:
        return _check(
            "release.provenance-root-mutation-source-tripwire",
            FAIL,
            "No installed Open MMI Python source was available to reproduce the release-signer root mutation tripwire.",
        )
    if offenders:
        return _check(
            "release.provenance-root-mutation-source-tripwire",
            FAIL,
            "Production code contains release-signer root mutation outside the local bootstrap CLI.",
            offenders=offenders,
            files_scanned=scanned,
        )
    return _check(
        "release.provenance-root-mutation-source-tripwire",
        PASS,
        "Installed production source reproduces the create-once release-signer root mutation tripwire.",
        files_scanned=scanned,
        note="Release Provenance v1 has no signer-rotation or candidate-controlled root-replacement primitive.",
    )

def _inspect_updater_transition_gate_source(root: Path) -> dict[str, Any]:
    installer = root / "ui" / "update_installer.py"
    coordinator = root / "ui" / "update_coordinator.py"
    gate = root / "open_mmi_trust" / "transition_gate.py"
    integrity = root / "open_mmi_trust" / "release_integrity.py"
    provenance = root / "open_mmi_trust" / "release_provenance.py"
    paths = (installer, coordinator, gate, integrity, provenance)
    missing = [str(path.relative_to(root)) for path in paths if not path.is_file()]
    if missing:
        return _check(
            "updater.preinstallation-trust-gate",
            UNVERIFIED,
            "Installed updater trust-gate source is not fully available for inspection.",
            missing=missing,
        )

    try:
        installer_source = installer.read_text(encoding="utf-8")
        coordinator_source = coordinator.read_text(encoding="utf-8")
        gate_source = gate.read_text(encoding="utf-8")
        integrity_source = integrity.read_text(encoding="utf-8")
        provenance_source = provenance.read_text(encoding="utf-8")
        installer_tree = ast.parse(installer_source, filename=str(installer))
        coordinator_tree = ast.parse(coordinator_source, filename=str(coordinator))
        gate_tree = ast.parse(gate_source, filename=str(gate))
    except (OSError, UnicodeError, SyntaxError) as exc:
        return _check(
            "updater.preinstallation-trust-gate",
            FAIL,
            "Installed updater trust-gate source could not be inspected reproducibly.",
            error=f"{type(exc).__name__}: {exc}",
        )

    def call_lines(tree: ast.AST, name: str) -> list[int]:
        lines: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                called = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called = node.func.attr
            else:
                called = ""
            if called == name:
                lines.append(node.lineno)
        return sorted(lines)

    installer_gate = call_lines(installer_tree, "require_prepared_candidate_allowed")
    installer_integrity = call_lines(
        installer_tree, "require_current_privileged_integrity"
    )
    installer_current_provenance = call_lines(installer_tree, "require_current_release_provenance")
    installer_candidate_provenance = call_lines(installer_tree, "require_candidate_release_provenance")
    installer_activate = call_lines(installer_tree, "activate_acknowledged_expansion")
    installer_wheel = call_lines(installer_tree, "_prepare_candidate_wheel")
    installer_deploy = call_lines(installer_tree, "_run_deployment")
    installer_runtime_verify = call_lines(installer_tree, "verify_runtime_inventory")
    installer_finalize = call_lines(installer_tree, "finalize_successful_transition")
    installer_integrity_record = call_lines(installer_tree, "_record_integrity_state")
    coordinator_gate = call_lines(coordinator_tree, "require_prepared_candidate_allowed")
    coordinator_systemctl: list[int] = []
    for node in ast.walk(coordinator_tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "run" or not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            continue
        strings = [
            element.value
            for element in node.args[0].elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if "open-mmi-update-installer.service" in strings:
            coordinator_systemctl.append(node.lineno)

    forbidden_gate_imports = {"importlib", "runpy"}
    forbidden: list[str] = []
    for node in ast.walk(gate_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] in forbidden_gate_imports:
                    forbidden.append(f"import:{alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".", 1)[0] in forbidden_gate_imports:
                forbidden.append(f"import:{node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in {"exec", "eval", "compile", "__import__"}:
                forbidden.append(f"call:{node.func.id}")

    ordered = bool(
        installer_gate
        and installer_integrity
        and installer_current_provenance
        and installer_candidate_provenance
        and installer_activate
        and installer_wheel
        and installer_deploy
        and installer_runtime_verify
        and installer_finalize
        and installer_integrity_record
        and min(installer_integrity)
        < min(installer_current_provenance)
        < min(installer_candidate_provenance)
        < min(installer_gate)
        < min(installer_activate)
        < min(installer_wheel)
        < min(installer_deploy)
        < min(installer_runtime_verify)
        < min(installer_finalize)
        < min(installer_integrity_record)
        and coordinator_gate
        and coordinator_systemctl
        and min(coordinator_gate) < min(coordinator_systemctl)
    )
    data_only = (
        'open_mmi_trust/data/trust-manifest.v1.json' in gate_source
        and '"ls-tree"' in gate_source
        and '"cat-file"' in gate_source
        and "scripts/manage.sh" not in gate_source
        and not forbidden
    )
    if not ordered or not data_only:
        return _check(
            "updater.preinstallation-trust-gate",
            FAIL,
            "Installed updater source does not preserve the reviewed pre-execution Trust Transition Gate ordering.",
            installer_gate_lines=installer_gate,
            installer_integrity_lines=installer_integrity,
            installer_current_provenance_lines=installer_current_provenance,
            installer_candidate_provenance_lines=installer_candidate_provenance,
            installer_activation_lines=installer_activate,
            installer_wheel_lines=installer_wheel,
            installer_deployment_lines=installer_deploy,
            installer_runtime_verification_lines=installer_runtime_verify,
            installer_finalize_lines=installer_finalize,
            installer_integrity_record_lines=installer_integrity_record,
            coordinator_gate_lines=coordinator_gate,
            coordinator_installer_lines=coordinator_systemctl,
            candidate_manifest_data_only=data_only,
            forbidden_gate_surfaces=forbidden,
        )
    return _check(
        "updater.preinstallation-trust-gate",
        PASS,
        "Installed updater verifies the current privileged runtime plus pinned-signer provenance for both current and candidate commits before trust transition evaluation or candidate-controlled build/deployment begins, then verifies the built artifact and installed privileged runtime.",
        installer_gate_line=min(installer_gate),
        installer_integrity_line=min(installer_integrity),
        installer_current_provenance_line=min(installer_current_provenance),
        installer_candidate_provenance_line=min(installer_candidate_provenance),
        installer_wheel_line=min(installer_wheel),
        installer_deployment_line=min(installer_deploy),
        installer_runtime_verification_line=min(installer_runtime_verify),
        coordinator_gate_line=min(coordinator_gate),
        candidate_manifest_source="git-object-data",
        note=(
            "The trusted installer checks the production source, installed package, and privileged update-unit byte binding, verifies current and candidate commit signatures offline against the owner-pinned signer root, and only then evaluates the Trust Transition Gate. After trust acceptance, pip may execute the candidate PEP 517 build backend; the resulting wheel is verified against the Git-object inventory before manage.sh deployment, and the privileged runtime binding is rechecked afterward. This is still local byte-integrity evidence, not an OS sandbox against arbitrary privileged replacement code."
        ),
    )


def _inspect_can_transmit_source(canbus_root: Path, manifest: Mapping[str, Any] | None) -> dict[str, Any]:
    policy = None
    assurance = None
    if manifest is not None:
        capability = manifest["capabilities"].get("vehicle.can.transmit", {})
        policy = capability.get("policy")
        assurance = capability.get("assurance")
    if policy != "prohibited":
        return _check(
            "can.transmit-source-tripwire",
            UNVERIFIED,
            "CAN transmit source tripwire is not applicable because the manifest does not prohibit transmission.",
            policy=policy,
            assurance=assurance,
        )
    if not canbus_root.is_dir():
        return _check(
            "can.transmit-source-tripwire",
            FAIL,
            "Installed CAN runtime source directory is missing, so the declared CI tripwire cannot be reproduced.",
            path=str(canbus_root),
            policy=policy,
            assurance=assurance,
        )

    offenders: list[str] = []
    try:
        paths = sorted(canbus_root.glob("*.py"))
        if not paths:
            raise OSError("no Python runtime source files found")
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr in {"send", "send_periodic"}:
                    offenders.append(f"{path.name}:{node.lineno}:{node.func.attr}")
    except (OSError, UnicodeError, SyntaxError) as exc:
        return _check(
            "can.transmit-source-tripwire",
            FAIL,
            "Installed CAN runtime source could not be inspected reproducibly.",
            path=str(canbus_root),
            error=f"{type(exc).__name__}: {exc}",
            policy=policy,
            assurance=assurance,
        )

    if offenders:
        return _check(
            "can.transmit-source-tripwire",
            FAIL,
            "Transmit-like calls were found in installed CAN runtime source while transmission is prohibited.",
            offenders=offenders,
            policy=policy,
            assurance=assurance,
        )
    return _check(
        "can.transmit-source-tripwire",
        PASS,
        "Installed CAN runtime source reproduces the current no-send CI tripwire.",
        files_scanned=len(paths),
        policy=policy,
        assurance=assurance,
        note="This is source-level evidence, not proof that SocketCAN transmission is impossible at the OS or hardware layer.",
    )



_CAN_TRANSMIT_ASSURANCE = "os-enforced"
_CAN_UDEV_RULE_PATH = Path("/etc/udev/rules.d/80-canbus.rules")


def _can_transmit_unit_contract(
    root: Path,
) -> tuple[list[str], dict[str, Any]]:
    required = {
        "systemd/user/canbusd.service": (
            "RestrictAddressFamilies=AF_CAN AF_UNIX",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
        ),
        "systemd/system/open-mmi-vehicle-can-provision.service": (
            "RestrictAddressFamilies=AF_NETLINK AF_UNIX",
            "CapabilityBoundingSet=CAP_NET_ADMIN CAP_DAC_READ_SEARCH",
        ),
    }
    forbidden = {
        "systemd/user/canbusd.service": (
            "CAP_NET_ADMIN",
            "AF_INET",
            "AF_INET6",
        ),
        "systemd/system/open-mmi-vehicle-can-provision.service": (
            "AF_CAN",
        ),
    }

    failures: list[str] = []
    evidence: dict[str, Any] = {}

    for relative, required_lines in required.items():
        path = root / relative
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(
                f"{relative}:unreadable:{type(exc).__name__}"
            )
            continue

        lines = {
            line.strip()
            for line in source.splitlines()
            if line.strip()
            and not line.lstrip().startswith("#")
        }

        missing = [
            line
            for line in required_lines
            if line not in lines
        ]
        forbidden_hits = [
            fragment
            for fragment in forbidden.get(relative, ())
            if any(fragment in line for line in lines)
        ]

        failures.extend(
            f"{relative}:missing:{line}"
            for line in missing
        )
        failures.extend(
            f"{relative}:forbidden:{fragment}"
            for fragment in forbidden_hits
        )

        evidence[relative] = {
            "required_lines": list(required_lines),
            "forbidden_fragments": list(
                forbidden.get(relative, ())
            ),
        }

    return sorted(set(failures)), evidence


def _can_transmit_source_contract(root: Path) -> list[str]:
    required = {
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

    failures: list[str] = []

    for relative, fragments in required.items():
        path = root / relative
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(
                f"{relative}:unreadable:{type(exc).__name__}"
            )
            continue

        for fragment in fragments:
            if fragment not in source:
                failures.append(
                    f"{relative}:missing:{fragment}"
                )

    return sorted(set(failures))


def _can_transmit_udev_contract(
    path: Path,
    *,
    expected_uid: int = 0,
) -> tuple[list[str], dict[str, Any]]:
    evidence: dict[str, Any] = {
        "path": str(path),
        "expected_uid": expected_uid,
    }
    failures: list[str] = []

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ["udev-rule:missing"], evidence
    except OSError as exc:
        return [
            f"udev-rule:unreadable:{type(exc).__name__}"
        ], evidence

    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != expected_uid
        or metadata.st_mode & 0o022
    ):
        return ["udev-rule:unsafe-metadata"], evidence

    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [
            f"udev-rule:unreadable:{type(exc).__name__}"
        ], evidence

    physical_rules = [
        line.strip()
        for line in source.splitlines()
        if "RUN+=" in line
        and " type can bitrate " in line
    ]
    evidence["physical_can_rules"] = physical_rules

    if not physical_rules:
        failures.append("udev-rule:no-physical-can-rule")

    for rule in physical_rules:
        if "listen-only on" not in rule:
            failures.append(
                "udev-rule:"
                "physical-can-rule-not-listen-only"
            )

    return sorted(set(failures)), evidence


def _can_transmit_user_shadow_paths() -> list[str]:
    home = Path.home()
    runtime = os.getenv("XDG_RUNTIME_DIR", "").strip()

    bases = [
        home / ".config" / "systemd" / "user",
        home / ".local" / "share" / "systemd" / "user",
    ]
    if runtime:
        bases.extend(
            [
                Path(runtime) / "systemd" / "user",
                Path(runtime) / "systemd" / "user.control",
            ]
        )

    forbidden_directives = (
        "ExecStart=",
        "RestrictAddressFamilies=",
        "CapabilityBoundingSet=",
        "AmbientCapabilities=",
    )

    offenders: list[str] = []

    for base in bases:
        full = base / "canbusd.service"
        if full.exists() or full.is_symlink():
            offenders.append(str(full))

        dropin = base / "canbusd.service.d"
        if not dropin.is_dir():
            continue

        for path in sorted(dropin.glob("*.conf")):
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                offenders.append(str(path) + ":unreadable")
                continue

            for raw in source.splitlines():
                line = raw.strip()
                if any(
                    line.startswith(prefix)
                    for prefix in forbidden_directives
                ):
                    offenders.append(f"{path}:{line}")

    return sorted(set(offenders))


def _inspect_can_transmit_os_enforcement(
    root: Path,
    manifest: Mapping[str, Any] | None,
    privileged_runtime_check: Mapping[str, Any] | None,
    *,
    production: bool,
    udev_rule_path: Path = _CAN_UDEV_RULE_PATH,
    udev_expected_uid: int = 0,
) -> dict[str, Any] | None:
    if manifest is None:
        return None

    capability = manifest["capabilities"]["vehicle.can.transmit"]

    if capability != {
        "policy": "prohibited",
        "assurance": _CAN_TRANSMIT_ASSURANCE,
    }:
        return _check(
            "capability.vehicle.can.transmit.enforcement",
            FAIL,
            (
                "CAN transmit manifest semantics do not match "
                "the enforced passive-CAN boundary."
            ),
            capability=capability,
        )

    unit_failures, unit_evidence = (
        _can_transmit_unit_contract(root)
    )
    source_failures = _can_transmit_source_contract(root)

    if unit_failures or source_failures:
        return _check(
            "capability.vehicle.can.transmit.enforcement",
            FAIL if production else UNVERIFIED,
            (
                "Installed source does not reproduce the "
                "declared CAN transmit prohibition."
                if production
                else
                "CAN transmit OS enforcement cannot be "
                "reproduced from this non-production fixture."
            ),
            unit_failures=unit_failures,
            source_failures=source_failures,
            units=unit_evidence,
        )

    if not production:
        return _check(
            "capability.vehicle.can.transmit.enforcement",
            UNVERIFIED,
            (
                "CAN passive-enforcement source is present, "
                "but effective production unit and udev state "
                "were not inspected."
            ),
            assurance=capability["assurance"],
        )

    if (
        privileged_runtime_check is None
        or privileged_runtime_check.get("status") != PASS
    ):
        return _check(
            "capability.vehicle.can.transmit.enforcement",
            UNVERIFIED,
            (
                "CAN passive-enforcement source is present, "
                "but deployed privileged unit integrity is "
                "not currently proven."
            ),
            privileged_runtime_status=(
                privileged_runtime_check or {}
            ).get("status"),
        )

    shadow_paths = _can_transmit_user_shadow_paths()
    if shadow_paths:
        return _check(
            "capability.vehicle.can.transmit.enforcement",
            FAIL,
            (
                "Owner-writable user-unit state can shadow "
                "or weaken the passive CAN daemon boundary."
            ),
            shadow_paths=shadow_paths,
        )

    udev_failures, udev_evidence = (
        _can_transmit_udev_contract(
            Path(udev_rule_path),
            expected_uid=udev_expected_uid,
        )
    )
    if udev_failures:
        return _check(
            "capability.vehicle.can.transmit.enforcement",
            FAIL,
            (
                "Deployed physical CAN provisioning does not "
                "enforce listen-only mode."
            ),
            udev_failures=udev_failures,
            udev=udev_evidence,
        )

    return _check(
        "capability.vehicle.can.transmit.enforcement",
        PASS,
        (
            "Physical CAN provisioning is listen-only and CAN "
            "receive authority is separated from interface-"
            "administration authority."
        ),
        assurance=capability["assurance"],
        canbusd_can_socket_authority=True,
        canbusd_net_admin_authority=False,
        provisioner_can_socket_authority=False,
        provisioner_net_admin_authority=True,
        owner_unit_shadows=[],
        udev=udev_evidence,
        note=(
            "Live controller LISTEN-ONLY and fresh challenge-"
            "bound receive behavior are independently measured "
            "by the separate CAN trust test."
        ),
    )

def _inspect_dashboard_render(static_root: Path) -> list[dict[str, Any]]:
    index = static_root / "index.html"
    bootstrap = static_root / "vendor" / "bootstrap-5.3.8.min.css"
    checks: list[dict[str, Any]] = []

    try:
        html = index.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        checks.append(
            _check(
                "dashboard.render-egress",
                FAIL,
                "Dashboard index could not be inspected for remote render dependencies.",
                path=str(index),
                error=f"{type(exc).__name__}: {exc}",
            )
        )
    else:
        urls = sorted({match.group("url") for match in _REMOTE_DEPENDENCY_RE.finditer(html)})
        local_bootstrap = 'href="/vendor/bootstrap-5.3.8.min.css"' in html
        no_icon_font = "bootstrap-icons" not in html
        if urls or not local_bootstrap or not no_icon_font:
            checks.append(
                _check(
                    "dashboard.render-egress",
                    FAIL,
                    "Dashboard render dependency state does not match the reviewed local-only static contract.",
                    path=str(index),
                    remote_dependencies=urls,
                    local_bootstrap=local_bootstrap,
                    bootstrap_icons_absent=no_icon_font,
                )
            )
        else:
            checks.append(
                _check(
                    "dashboard.render-egress",
                    PASS,
                    "Dashboard index has no remote script/stylesheet dependency and uses local Bootstrap.",
                    path=str(index),
                    remote_dependencies=[],
                )
            )

    try:
        data = bootstrap.read_bytes()
    except OSError as exc:
        checks.append(
            _check(
                "dashboard.bootstrap-integrity",
                FAIL,
                "Reviewed local Bootstrap asset is missing or unreadable.",
                path=str(bootstrap),
                error=f"{type(exc).__name__}: {exc}",
            )
        )
    else:
        digest = base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
        if digest != BOOTSTRAP_SHA384_BASE64:
            checks.append(
                _check(
                    "dashboard.bootstrap-integrity",
                    FAIL,
                    "Local Bootstrap asset does not match the reviewed Bootstrap 5.3.8 SHA-384 digest.",
                    path=str(bootstrap),
                    sha384_base64=digest,
                )
            )
        else:
            checks.append(
                _check(
                    "dashboard.bootstrap-integrity",
                    PASS,
                    "Local Bootstrap asset matches the reviewed Bootstrap 5.3.8 SHA-384 digest.",
                    path=str(bootstrap),
                    sha384_base64=digest,
                )
            )
    return checks



def _network_egress_unit_contract(root: Path) -> tuple[list[str], dict[str, Any]]:
    contracts = {
        "systemd/system/open-mmi-update-installer.service": (
            "IPAddressDeny=any",
            "IPAddressAllow=localhost",
            "ReadOnlyPaths=/var/lib/open-mmi/network-egress",
        ),
        "systemd/system/open-mmi-update-coordinator.service": (
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            "ReadOnlyPaths=/var/lib/open-mmi/network-egress",
            "ExecStart=/opt/open-mmi/venv/bin/python -I -m ui.update_coordinator serve",
        ),
        "systemd/system/open-mmi-media-egress.service": (
            "DynamicUser=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "CapabilityBoundingSet=",
            "LoadCredential=media-config:/var/lib/open-mmi/network-egress/media.v1.json",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        ),
        "systemd/user/open-mmi-dashboard.service": (
            "IPAddressDeny=any",
            "IPAddressAllow=localhost",
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        ),
        "systemd/user/canbusd.service": (
            "RestrictAddressFamilies=AF_CAN AF_UNIX",
        ),
        "systemd/user/open-mmi-owner-config.service": (
            "ProtectSystem=strict",
            "ProtectHome=read-only",
            "RestrictAddressFamilies=AF_UNIX",
        ),
    }
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    for relative, required in contracts.items():
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        missing = [line for line in required if line not in text]
        if relative.endswith("canbusd.service") and ("AF_INET" in text or "AF_INET6" in text):
            missing.append("must-not-authorize-AF_INET")
        if relative.endswith("open-mmi-dashboard.service") and "EnvironmentFile=" in text:
            missing.append("must-not-load-owner-network-secrets")
        if missing:
            failures.extend(f"{relative}:{item}" for item in missing)
        evidence[relative] = {"required_contract": list(required), "matches": not missing}
    return failures, evidence


def _network_egress_source_contract(root: Path) -> list[str]:
    required_fragments = {
        "ui/launcher.py": (
            "web_url must target the local loopback interface",
            "--property=IPAddressDeny=any",
            "--property=IPAddressAllow=localhost",
            '"ui.launcher"',
        ),
        "ui/media_egress.py": (
            "RADIO_BROWSER_DEFAULT_URL",
            "read_credential_config",
            '"/v1/jellyfin/test-candidate"',
            "_require_root_peer",
        ),
        "ui/egress_client.py": (
            '"/v1/media/proxy"',
            '"/v1/jellyfin/test-candidate"',
        ),
        "ui/update_coordinator.py": (
            'RELEASE_FETCH_PURPOSE = "updates.release-fetch"',
            "def _release_fetch_check()",
            "return update_status.check_for_updates()",
            "def _release_fetch_candidate(",
            "update_status._fetch_remote_candidate(source, candidate_commit)",
        ),
    }
    failures: list[str] = []
    for relative, required in required_fragments.items():
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        for fragment in required:
            if fragment not in text:
                failures.append(f"{relative}:missing:{fragment}")
    return failures


def _network_egress_user_shadow_paths() -> list[str]:
    home = Path.home()
    runtime = os.getenv("XDG_RUNTIME_DIR", "").strip()
    bases = [home / ".config" / "systemd" / "user", home / ".local" / "share" / "systemd" / "user"]
    if runtime:
        bases.extend(
            [
                Path(runtime) / "systemd" / "user",
                Path(runtime) / "systemd" / "user.control",
            ]
        )
    protected = (
        "canbusd.service",
        "open-mmi-dashboard.service",
        "open-mmi-owner-config.service",
    )
    forbidden_directives = (
        "ExecStart=",
        "IPAddressDeny=",
        "IPAddressAllow=",
        "RestrictAddressFamilies=",
        "NoNewPrivileges=",
        "PrivateDevices=",
        "RestrictNamespaces=",
    )
    offenders: list[str] = []
    for base in bases:
        for unit in protected:
            full = base / unit
            if full.exists() or full.is_symlink():
                offenders.append(str(full))
            dropin = base / f"{unit}.d"
            if not dropin.is_dir():
                continue
            for path in sorted(dropin.glob("*.conf")):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    offenders.append(str(path) + ":unreadable")
                    continue
                for raw in text.splitlines():
                    line = raw.strip()
                    if any(line.startswith(prefix) for prefix in forbidden_directives):
                        offenders.append(f"{path}:{line}")
    return sorted(set(offenders))


def _inspect_network_egress_enforcement(
    root: Path,
    manifest: Mapping[str, Any] | None,
    privileged_runtime_check: Mapping[str, Any] | None,
    *,
    production: bool,
) -> dict[str, Any] | None:
    if manifest is None:
        return None
    capability = manifest["capabilities"]["network.external-egress"]
    if capability["assurance"] == "declared":
        return None
    expected_purposes = ["media.internet-radio", "media.jellyfin", "updates.release-fetch"]
    if (
        capability["policy"] != "declared-purposes-only"
        or capability["assurance"] != "os-enforced"
        or capability.get("purposes") != expected_purposes
    ):
        return _check(
            "capability.network.external-egress.enforcement",
            FAIL,
            "Network egress manifest semantics do not match the enforced purpose boundary.",
            capability=capability,
            expected_purposes=expected_purposes,
        )

    unit_failures, unit_evidence = _network_egress_unit_contract(root)
    source_failures = _network_egress_source_contract(root)
    if unit_failures or source_failures:
        return _check(
            "capability.network.external-egress.enforcement",
            FAIL if production else UNVERIFIED,
            (
                "Installed source does not reproduce the declared network egress enforcement contract."
                if production
                else "Network egress enforcement cannot be reproduced from this non-production fixture."
            ),
            unit_failures=unit_failures,
            source_failures=source_failures,
            units=unit_evidence,
        )
    if not production:
        return _check(
            "capability.network.external-egress.enforcement",
            UNVERIFIED,
            "Network egress enforcement source is present, but effective production unit ownership/runtime state was not inspected.",
            purposes=expected_purposes,
            assurance=capability["assurance"],
        )
    if privileged_runtime_check is None or privileged_runtime_check.get("status") != PASS:
        return _check(
            "capability.network.external-egress.enforcement",
            UNVERIFIED,
            "Network policy source is present, but root-owned deployed unit integrity is not currently proven.",
            purposes=expected_purposes,
            privileged_runtime_status=(privileged_runtime_check or {}).get("status"),
        )
    shadow_paths = _network_egress_user_shadow_paths()
    if shadow_paths:
        return _check(
            "capability.network.external-egress.enforcement",
            FAIL,
            "Owner-writable user-unit state can shadow or weaken a root-owned Open MMI network sandbox.",
            shadow_paths=shadow_paths,
        )
    return _check(
        "capability.network.external-egress.enforcement",
        PASS,
        "External network authority is confined to the declared updater and media actors by root-owned OS service boundaries.",
        purposes=expected_purposes,
        assurance=capability["assurance"],
        root_owned_units=True,
        owner_unit_shadows=[],
        launcher_ui_target="loopback-only",
    )


_IDENTITY_ASSURANCE = "runtime-guarded"
_IDENTITY_AUTHORIZATION_PATH = "/var/lib/open-mmi/trust/telemetry-authorization.v1.json"
_IDENTITY_NETWORK_DENY_PATHS = (
    _IDENTITY_AUTHORIZATION_PATH,
    "/var/lib/open-mmi/vehicle-data",
)


def _vehicle_identity_unit_contract(root: Path) -> tuple[list[str], dict[str, Any]]:
    inaccessible = (
        "InaccessiblePaths=-/var/lib/open-mmi/trust/telemetry-authorization.v1.json "
        "-/var/lib/open-mmi/vehicle-data"
    )
    required: dict[str, tuple[str, ...]] = {
        "systemd/system/open-mmi-media-egress.service": (
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            inaccessible,
        ),
        "systemd/system/open-mmi-update-coordinator.service": (
            "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
            inaccessible,
        ),
        "systemd/user/open-mmi-dashboard.service": (
            "IPAddressDeny=any",
            "IPAddressAllow=localhost",
        ),
        "systemd/user/canbusd.service": (
            "RestrictAddressFamilies=AF_CAN AF_UNIX",
        ),
        "systemd/user/open-mmi-owner-config.service": (
            "RestrictAddressFamilies=AF_UNIX",
        ),
        "systemd/system/open-mmi-vehicle-store.service": (
            "RestrictAddressFamilies=AF_UNIX",
        ),
    }
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    for relative, fragments in required.items():
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        missing = [fragment for fragment in fragments if fragment not in text]
        if relative.endswith("canbusd.service") and ("AF_INET" in text or "AF_INET6" in text):
            missing.append("must-not-authorize-AF_INET")
        if missing:
            failures.extend(f"{relative}:missing:{fragment}" for fragment in missing)
        evidence[relative] = {"required_fragments": list(fragments)}
    return sorted(set(failures)), evidence


def _vehicle_identity_source_contract(root: Path) -> list[str]:
    required_fragments = {
        "open_mmi_telemetry/guard.py": (
            '"destination": "local-only"',
            "def normalize_vin(vin: str)",
            "def _vin_fingerprint(vin: str, salt: bytes",
        ),
        "open_mmi_trust/vehicle_identity.py": (
            "class RemoteVehicleIdentityDenied",
            "def contains_vehicle_identity_material",
            "def require_remote_identity_safe",
        ),
        "ui/media_egress.py": (
            "RemoteVehicleIdentityDenied",
            "require_remote_identity_safe(",
            "except RemoteVehicleIdentityDenied as exc:",
        ),
        "ui/update_coordinator.py": (
            "no caller-selected path, ref, repository, command, or service exists",
        ),
    }
    failures: list[str] = []
    for relative, fragments in required_fragments.items():
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        for fragment in fragments:
            if fragment not in text:
                failures.append(f"{relative}:missing:{fragment}")

    # The local telemetry package is the only production component that accepts
    # a raw VIN.  It must remain free of network client primitives.
    network_import_roots = {
        "socket", "urllib", "http", "requests", "httpx", "aiohttp", "ftplib", "smtplib",
    }
    telemetry_root = root / "open_mmi_telemetry"
    if telemetry_root.is_dir():
        for path in sorted(telemetry_root.glob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except (OSError, UnicodeError, SyntaxError) as exc:
                failures.append(f"{path.relative_to(root)}:unreadable:{type(exc).__name__}")
                continue
            for node in ast.walk(tree):
                module = ""
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in network_import_roots:
                            failures.append(
                                f"{path.relative_to(root)}:{node.lineno}:network-import:{alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    module = node.module
                    if module.split(".", 1)[0] in network_import_roots:
                        failures.append(
                            f"{path.relative_to(root)}:{node.lineno}:network-import:{module}"
                        )

    # Network-capable actors must not import vehicle/telemetry data providers.
    forbidden_egress_import_fragments = (
        "open_mmi_telemetry",
        "canbusd",
        "vehicle_store",
        "status_bus",
        "service_reminder",
        "trip_distance",
        "trip_a",
        "trip_b",
    )
    for relative in ("ui/media_egress.py", "ui/update_coordinator.py"):
        path = root / relative
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if any(fragment in module for fragment in forbidden_egress_import_fragments):
                    failures.append(f"{relative}:{node.lineno}:identity-source-import:{module}")

    # Adding a new production VIN/identity consumer is an explicit trust-boundary
    # change.  Keep the small reviewed local set closed by default.
    allowed_identity_python = {
        "open_mmi_telemetry/guard.py",
        "open_mmi_telemetry/cli.py",
        "open_mmi_trust/vehicle_identity.py",
        "open_mmi_trust/inspector.py",
        "open_mmi_trust/accepted_state.py",
        "open_mmi_trust/manifest.py",
        "ui/media_egress.py",
    }
    ignored_roots = {
        "tests", "tools", "independent_checker", ".git", ".venv", "venv",
        "__pycache__", "build", "dist",
    }
    identity_re = re.compile(
        r"\bvin\b|vin_binding|normalize_vin|_vin_fingerprint|vehicle\.identity\.remote-resolution",
        re.IGNORECASE,
    )
    resolver_markers = (
        "vpic", "nhtsa", "vindecoder", "vin-decoder", "vehiclehistory", "vehicle-history",
    )
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        relative_text = relative.as_posix()
        if ignored_roots.intersection(relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative_text}:unreadable:{type(exc).__name__}")
            continue
        if identity_re.search(text) and relative_text not in allowed_identity_python:
            failures.append(f"{relative_text}:undeclared-identity-consumer")
        lowered = text.lower()
        if relative_text not in {"open_mmi_trust/inspector.py"}:
            for marker in resolver_markers:
                if marker in lowered:
                    failures.append(f"{relative_text}:remote-resolver-marker:{marker}")

    return sorted(set(failures))


def _inspect_vehicle_identity_remote_resolution_enforcement(
    root: Path,
    manifest: Mapping[str, Any] | None,
    network_check: Mapping[str, Any] | None,
    *,
    production: bool,
) -> dict[str, Any] | None:
    if manifest is None:
        return None
    capability = manifest["capabilities"]["vehicle.identity.remote-resolution"]
    if capability["assurance"] == "declared":
        return None
    if capability["policy"] != "prohibited" or capability["assurance"] != _IDENTITY_ASSURANCE:
        return _check(
            "capability.vehicle.identity.remote-resolution.enforcement",
            FAIL,
            "Remote vehicle-identity manifest semantics do not match the enforced local-only boundary.",
            capability=capability,
            expected_policy="prohibited",
            expected_assurance=_IDENTITY_ASSURANCE,
        )

    network_capability = manifest["capabilities"]["network.external-egress"]
    if (
        network_capability["policy"] != "declared-purposes-only"
        or network_capability["assurance"] != "os-enforced"
        or network_capability.get("purposes")
        != ["media.internet-radio", "media.jellyfin", "updates.release-fetch"]
    ):
        return _check(
            "capability.vehicle.identity.remote-resolution.enforcement",
            FAIL,
            "Remote identity prohibition depends on the exact OS-enforced external-egress purpose boundary.",
            network_capability=network_capability,
        )

    unit_failures, unit_evidence = _vehicle_identity_unit_contract(root)
    source_failures = _vehicle_identity_source_contract(root)
    if unit_failures or source_failures:
        return _check(
            "capability.vehicle.identity.remote-resolution.enforcement",
            FAIL if production else UNVERIFIED,
            (
                "Installed source does not reproduce the remote vehicle-identity prohibition contract."
                if production
                else "Remote vehicle-identity prohibition cannot be reproduced from this non-production fixture."
            ),
            unit_failures=unit_failures,
            source_failures=source_failures,
            units=unit_evidence,
        )
    if not production:
        return _check(
            "capability.vehicle.identity.remote-resolution.enforcement",
            UNVERIFIED,
            "Local-only identity guards are present, but effective production network isolation was not inspected.",
            assurance=capability["assurance"],
        )
    if network_check is None or network_check.get("status") != PASS:
        status = FAIL if (network_check or {}).get("status") == FAIL else UNVERIFIED
        return _check(
            "capability.vehicle.identity.remote-resolution.enforcement",
            status,
            "Remote identity prohibition cannot be proven while the external network boundary is not proven.",
            network_enforcement_status=(network_check or {}).get("status"),
        )
    return _check(
        "capability.vehicle.identity.remote-resolution.enforcement",
        PASS,
        "Vehicle identity remains local: VIN handling is local-only, identity-bearing media egress is rejected, and network actors cannot read Open MMI identity state.",
        policy="prohibited",
        assurance=capability["assurance"],
        authorized_remote_identity_purposes=[],
        local_vin_use="telemetry-authorization-binding-only",
        inaccessible_identity_paths=list(_IDENTITY_NETWORK_DENY_PATHS),
        network_enforcement_status=network_check.get("status"),
    )


_PERSISTENCE_DURABLE_PURPOSES = (
    "service-reminder",
    "trip-a",
    "trip-b",
    "trip-distance",
)
_PERSISTENCE_PURPOSES = [*_PERSISTENCE_DURABLE_PURPOSES, "vehicle-runtime-status"]
_PERSISTENCE_STORAGE_ROOT = Path("/var/lib/open-mmi/vehicle-data")


def _vehicle_persistence_unit_contract(root: Path) -> tuple[list[str], dict[str, Any]]:
    required: dict[str, tuple[str, ...]] = {
        "systemd/system/open-mmi-vehicle-store.service": (
            "User=root",
            "StateDirectory=open-mmi/vehicle-data",
            "StateDirectoryMode=0700",
            "ProtectHome=yes",
            "ProtectSystem=strict",
            "RestrictAddressFamilies=AF_UNIX",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
        ),
        "systemd/user/open-mmi-dashboard.service": (
            "ProtectHome=read-only",
            "ProtectSystem=strict",
        ),
        "systemd/user/canbusd.service": (
            "RuntimeDirectory=open-mmi",
            "RuntimeDirectoryMode=0700",
            "ProtectHome=read-only",
            "ProtectSystem=strict",
            "ReadWritePaths=%t/open-mmi",
        ),
        "systemd/user/open-mmi-owner-config.service": (
            "ProtectHome=read-only",
            "ProtectSystem=strict",
            "ReadWritePaths=%h/.config/open-mmi %h/.config/autostart",
            "RestrictAddressFamilies=AF_UNIX",
        ),
        "systemd/system/open-mmi-update-coordinator.service": (
            "ReadOnlyPaths=/var/lib/open-mmi/network-egress",
            "InaccessiblePaths=-/var/lib/open-mmi/trust/telemetry-authorization.v1.json -/var/lib/open-mmi/vehicle-data",
        ),
        "systemd/system/open-mmi-update-installer.service": (
            "ReadOnlyPaths=/var/lib/open-mmi/network-egress /var/lib/open-mmi/vehicle-data",
        ),
        "systemd/system/open-mmi-vehicle-config-coordinator.service": (
            "ReadOnlyPaths=/var/lib/open-mmi/vehicle-data",
        ),
    }
    forbidden: dict[str, tuple[str, ...]] = {
        "systemd/user/open-mmi-dashboard.service": ("ReadWritePaths=",),
        "systemd/user/canbusd.service": ("/var/lib", "/home"),
        "systemd/user/open-mmi-owner-config.service": ("/var/lib/open-mmi/vehicle-data",),
    }
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    for relative, fragments in required.items():
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        missing = [fragment for fragment in fragments if fragment not in text]
        present_forbidden = [fragment for fragment in forbidden.get(relative, ()) if fragment in text]
        if missing:
            failures.extend(f"{relative}:missing:{fragment}" for fragment in missing)
        if present_forbidden:
            failures.extend(f"{relative}:forbidden:{fragment}" for fragment in present_forbidden)
        evidence[relative] = {
            "required_fragments": list(fragments),
            "forbidden_fragments": list(forbidden.get(relative, ())),
        }
    return failures, evidence


def _vehicle_persistence_source_contract(root: Path) -> list[str]:
    required_fragments = {
        "ui/vehicle_store.py": (
            'DEFAULT_STORAGE_ROOT = Path("/var/lib/open-mmi/vehicle-data")',
            'PURPOSES = frozenset({"service-reminder", "trip-a", "trip-b", "trip-distance"})',
            'raise ValueError("vehicle-data persistence operation is not declared")',
        ),
        "ui/vehicle_store_client.py": (
            'return request_json("/v1/service-reminder/status")',
            'return request_json("/v1/trip-a/status")',
            'return request_json("/v1/trip-b/status")',
            'return request_json("/v1/trip-distance/status")',
        ),
        "ui/web_dashboard/system_settings.py": (
            "vehicle_store_client.service_reminder_status",
            "vehicle_store_client.trip_a_status",
            "vehicle_store_client.trip_b_status",
            "vehicle_store_client.trip_distance_status",
        ),
        "canbusd/status_bus.py": (
            'Path(runtime_dir) / "open-mmi" / "status.json"',
        ),
    }
    failures: list[str] = []
    for relative, fragments in required_fragments.items():
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            failures.append(f"{relative}:unreadable:{type(exc).__name__}")
            continue
        for fragment in fragments:
            if fragment not in text:
                failures.append(f"{relative}:missing:{fragment}")

    state_modules = {
        "ui/web_dashboard/service_reminder.py",
        "ui/web_dashboard/trip_a.py",
        "ui/web_dashboard/trip_b.py",
        "ui/web_dashboard/trip_distance.py",
    }
    allowed_direct = {*state_modules, "ui/vehicle_store.py"}
    legacy_names = {
        "service-reminder.json",
        "trip-a.json",
        "trip-b.json",
        "trip-distance.json",
    }
    roots = [root / name for name in ("ui", "canbusd", "actions", "powerd", "scripts")]
    for source_root in roots:
        if not source_root.exists():
            continue
        for path in sorted(source_root.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                failures.append(f"{relative}:unreadable:{type(exc).__name__}")
                continue
            if relative not in allowed_direct:
                if "/var/lib/open-mmi/vehicle-data" in text:
                    failures.append(f"{relative}:direct-durable-root-reference")
                for name in legacy_names:
                    if name in text:
                        failures.append(f"{relative}:legacy-vehicle-state-reference:{name}")
            if relative in state_modules or relative == "ui/vehicle_store.py":
                continue
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                failures.append(f"{relative}:syntax-error")
                continue
            for node in ast.walk(tree):
                modules: list[str] = []
                if isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                    if node.module == "ui.web_dashboard":
                        modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
                for module in modules:
                    if module in {
                        "ui.web_dashboard.service_reminder",
                        "ui.web_dashboard.trip_a",
                        "ui.web_dashboard.trip_b",
                        "ui.web_dashboard.trip_distance",
                    }:
                        failures.append(f"{relative}:direct-vehicle-state-import:{module}")
    return sorted(set(failures))


def _vehicle_persistence_storage_contract(
    root: Path, *, expected_uid: int = 0
) -> tuple[list[str], dict[str, Any]]:
    failures: list[str] = []
    evidence: dict[str, Any] = {
        "root": str(root),
        "expected_uid": expected_uid,
        "durable_purposes": list(_PERSISTENCE_DURABLE_PURPOSES),
    }
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return ["storage-root:missing"], evidence
    except OSError as exc:
        return [f"storage-root:unreadable:{type(exc).__name__}"], evidence
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        failures.append("storage-root:not-trusted-directory")
        return failures, evidence
    if metadata.st_uid != expected_uid:
        failures.append(f"storage-root:uid:{metadata.st_uid}")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        failures.append("storage-root:group-or-world-writable")

    try:
        children = {path.name: path for path in root.iterdir()}
    except OSError as exc:
        failures.append(f"storage-root:cannot-list:{type(exc).__name__}")
        return failures, evidence
    unexpected = sorted(set(children) - set(_PERSISTENCE_DURABLE_PURPOSES))
    missing = sorted(set(_PERSISTENCE_DURABLE_PURPOSES) - set(children))
    failures.extend(f"storage-root:unexpected:{name}" for name in unexpected)
    failures.extend(f"storage-root:missing-purpose:{name}" for name in missing)

    purpose_evidence: dict[str, Any] = {}
    for purpose in _PERSISTENCE_DURABLE_PURPOSES:
        directory = children.get(purpose)
        if directory is None:
            continue
        try:
            item = directory.lstat()
        except OSError as exc:
            failures.append(f"{purpose}:unreadable:{type(exc).__name__}")
            continue
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            failures.append(f"{purpose}:not-trusted-directory")
            continue
        if item.st_uid != expected_uid:
            failures.append(f"{purpose}:uid:{item.st_uid}")
        if item.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            failures.append(f"{purpose}:group-or-world-writable")
        try:
            entries = list(directory.iterdir())
        except OSError as exc:
            failures.append(f"{purpose}:cannot-list:{type(exc).__name__}")
            continue
        unexpected_entries = [entry for entry in entries if entry.name != "state.json"]
        failures.extend(f"{purpose}:unexpected:{entry.name}" for entry in unexpected_entries)
        state_path = directory / "state.json"
        if state_path.exists() or state_path.is_symlink():
            try:
                state_meta = state_path.lstat()
            except OSError as exc:
                failures.append(f"{purpose}:state-unreadable:{type(exc).__name__}")
            else:
                if stat.S_ISLNK(state_meta.st_mode) or not stat.S_ISREG(state_meta.st_mode):
                    failures.append(f"{purpose}:state-not-regular")
                if state_meta.st_uid != expected_uid:
                    failures.append(f"{purpose}:state-uid:{state_meta.st_uid}")
                if state_meta.st_nlink != 1:
                    failures.append(f"{purpose}:state-hardlinked")
                if state_meta.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                    failures.append(f"{purpose}:state-group-or-world-writable")
        purpose_evidence[purpose] = {
            "path": str(directory),
            "state_present": state_path.exists(),
        }
    evidence["purposes"] = purpose_evidence
    return sorted(set(failures)), evidence




def _vehicle_persistence_user_shadow_paths() -> list[str]:
    home = Path.home()
    runtime = os.getenv("XDG_RUNTIME_DIR", "").strip()
    bases = [home / ".config" / "systemd" / "user", home / ".local" / "share" / "systemd" / "user"]
    if runtime:
        bases.extend(
            [
                Path(runtime) / "systemd" / "user",
                Path(runtime) / "systemd" / "user.control",
            ]
        )
    protected = (
        "canbusd.service",
        "open-mmi-dashboard.service",
        "open-mmi-owner-config.service",
    )
    forbidden_directives = (
        "ExecStart=",
        "ProtectHome=",
        "ProtectSystem=",
        "ReadWritePaths=",
        "ReadOnlyPaths=",
        "BindPaths=",
        "BindReadOnlyPaths=",
        "RuntimeDirectory=",
        "StateDirectory=",
    )
    offenders: list[str] = []
    for base in bases:
        for unit in protected:
            full = base / unit
            if full.exists() or full.is_symlink():
                offenders.append(str(full))
            dropin = base / f"{unit}.d"
            if not dropin.is_dir():
                continue
            for path in sorted(dropin.glob("*.conf")):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    offenders.append(str(path) + ":unreadable")
                    continue
                for raw in text.splitlines():
                    line = raw.strip()
                    if any(line.startswith(prefix) for prefix in forbidden_directives):
                        offenders.append(f"{path}:{line}")
    return sorted(set(offenders))


def _inspect_vehicle_data_persistence_enforcement(
    root: Path,
    manifest: Mapping[str, Any] | None,
    privileged_runtime_check: Mapping[str, Any] | None,
    *,
    production: bool,
    storage_root: Path = _PERSISTENCE_STORAGE_ROOT,
    storage_expected_uid: int = 0,
) -> dict[str, Any] | None:
    if manifest is None:
        return None
    capability = manifest["capabilities"]["vehicle-data.persistence"]
    if capability["assurance"] == "declared":
        return None
    if (
        capability["policy"] != "declared-purposes-only"
        or capability["assurance"] != "os-enforced"
        or capability.get("purposes") != _PERSISTENCE_PURPOSES
    ):
        return _check(
            "capability.vehicle-data.persistence.enforcement",
            FAIL,
            "Vehicle-data persistence manifest semantics do not match the enforced purpose boundary.",
            capability=capability,
            expected_purposes=_PERSISTENCE_PURPOSES,
        )

    unit_failures, unit_evidence = _vehicle_persistence_unit_contract(root)
    source_failures = _vehicle_persistence_source_contract(root)
    if unit_failures or source_failures:
        return _check(
            "capability.vehicle-data.persistence.enforcement",
            FAIL if production else UNVERIFIED,
            (
                "Installed source does not reproduce the declared vehicle-data persistence enforcement contract."
                if production
                else "Vehicle-data persistence enforcement cannot be reproduced from this non-production fixture."
            ),
            unit_failures=unit_failures,
            source_failures=source_failures,
            units=unit_evidence,
        )
    if not production:
        return _check(
            "capability.vehicle-data.persistence.enforcement",
            UNVERIFIED,
            "Vehicle-data persistence enforcement source is present, but effective production filesystem/runtime state was not inspected.",
            purposes=_PERSISTENCE_PURPOSES,
            assurance=capability["assurance"],
            runtime_status="ephemeral-/run-only",
        )
    if privileged_runtime_check is None or privileged_runtime_check.get("status") != PASS:
        return _check(
            "capability.vehicle-data.persistence.enforcement",
            UNVERIFIED,
            "Persistence policy source is present, but root-owned deployed unit integrity is not currently proven.",
            purposes=_PERSISTENCE_PURPOSES,
            privileged_runtime_status=(privileged_runtime_check or {}).get("status"),
        )
    shadow_paths = _vehicle_persistence_user_shadow_paths()
    if shadow_paths:
        return _check(
            "capability.vehicle-data.persistence.enforcement",
            FAIL,
            "Owner-writable user-unit state can shadow or weaken an Open MMI persistence sandbox.",
            shadow_paths=shadow_paths,
        )
    storage_failures, storage_evidence = _vehicle_persistence_storage_contract(
        Path(storage_root), expected_uid=storage_expected_uid
    )
    if storage_failures:
        return _check(
            "capability.vehicle-data.persistence.enforcement",
            FAIL,
            "The durable vehicle-data store does not match the declared root-owned purpose boundary.",
            storage_failures=storage_failures,
            storage=storage_evidence,
        )
    return _check(
        "capability.vehicle-data.persistence.enforcement",
        PASS,
        "Durable vehicle-derived state is confined to root-owned declared-purpose storage; runtime status remains ephemeral under /run.",
        purposes=_PERSISTENCE_PURPOSES,
        durable_purposes=list(_PERSISTENCE_DURABLE_PURPOSES),
        runtime_status="vehicle-runtime-status:/run-only",
        assurance=capability["assurance"],
        root_owned_units=True,
        owner_unit_shadows=[],
        storage=storage_evidence,
    )

def _declared_assurance_checks(manifest: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if manifest is None:
        return []
    capabilities = manifest["capabilities"]
    checks: list[dict[str, Any]] = []
    for capability_id in (
        "network.external-egress",
        "vehicle-data.persistence",
        "vehicle.identity.remote-resolution",
    ):
        capability = capabilities[capability_id]
        if capability["assurance"] == "declared":
            checks.append(
                _check(
                    f"capability.{capability_id}.enforcement",
                    UNVERIFIED,
                    "Capability is currently declaration-level; Trust Inspector v1 has no generic runtime/OS enforcement proof for it.",
                    capability=capability_id,
                    policy=capability["policy"],
                    assurance=capability["assurance"],
                )
            )
    return checks


def inspect_system(
    *,
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    authorization_path: Path = DEFAULT_AUTHORIZATION_PATH,
    accepted_state_path: Path = DEFAULT_ACCEPTED_STATE_PATH,
    lineage_path: Path = DEFAULT_TRANSITION_LINEAGE_DIR,
    integrity_path: Path = DEFAULT_INTEGRITY_STATE_PATH,
    provenance_path: Path = DEFAULT_PROVENANCE_ROOT_PATH,
    install_root: Path | None = None,
    package_root: Path | None = None,
) -> dict[str, Any]:
    """Inspect local trust evidence without mutating trust state or contacting a network."""

    root = Path(install_root) if install_root is not None else _default_install_root()
    integrity_source_root = (
        Path(install_root) if install_root is not None else integrity_default_install_root()
    )
    integrity_package_root = (
        Path(package_root)
        if package_root is not None
        else (root if install_root is not None else integrity_default_package_root())
    )
    manifest, manifest_summary, manifest_check = _inspect_manifest(Path(manifest_path))
    telemetry_summary, telemetry_check = _inspect_telemetry_authorization(Path(authorization_path))
    accepted_check = _inspect_accepted_owner_state(Path(accepted_state_path), manifest)

    checks: list[dict[str, Any]] = [manifest_check, telemetry_check, accepted_check]
    checks.append(_inspect_telemetry_default_deny())
    checks.append(_inspect_telemetry_self_authorization_source(root))
    checks.append(_inspect_accepted_state_self_authorization_source(root))
    checks.append(_inspect_provenance_root_mutation_source(root))
    checks.append(_inspect_can_transmit_source(root / "canbusd", manifest))
    checks.extend(_inspect_dashboard_render(root / "ui" / "web_dashboard" / "static"))
    checks.extend(_declared_assurance_checks(manifest))

    integrity_check, integrity_state = _inspect_release_integrity(
        Path(integrity_path), integrity_source_root, integrity_package_root
    )
    production = (
        install_root is None
        and package_root is None
        and integrity_source_root == INTEGRITY_DEFAULT_INSTALL_ROOT
    )
    privileged_runtime_check = (
        _inspect_privileged_runtime_integrity(integrity_state)
        if production
        else None
    )
    network_check = _inspect_network_egress_enforcement(
        integrity_source_root,
        manifest,
        privileged_runtime_check,
        production=production,
    )
    if network_check is not None:
        checks.append(network_check)
    persistence_check = _inspect_vehicle_data_persistence_enforcement(
        integrity_source_root,
        manifest,
        privileged_runtime_check,
        production=production,
    )
    if persistence_check is not None:
        checks.append(persistence_check)
    can_transmit_check = _inspect_can_transmit_os_enforcement(
        integrity_source_root,
        manifest,
        privileged_runtime_check,
        production=production,
    )
    if can_transmit_check is not None:
        checks.append(can_transmit_check)
    identity_check = _inspect_vehicle_identity_remote_resolution_enforcement(
        integrity_package_root,
        manifest,
        network_check,
        production=production,
    )
    if identity_check is not None:
        checks.append(identity_check)
    provenance_check = _inspect_release_provenance(
        Path(provenance_path), integrity_state, integrity_source_root
    )
    checks.extend(
        [
            integrity_check,
            *([privileged_runtime_check] if privileged_runtime_check is not None else []),
            provenance_check,
            _inspect_transition_lineage(Path(lineage_path), Path(accepted_state_path)),
            _inspect_updater_transition_gate_source(root),
        ]
    )

    return {
        "schema_version": INSPECTION_SCHEMA_VERSION,
        "status": _overall_status(checks),
        "manifest": manifest_summary,
        "telemetry_authorization": telemetry_summary,
        "checks": checks,
    }


def canonical_report_bytes(report: Mapping[str, Any]) -> bytes:
    """Return deterministic JSON bytes for an already-produced inspection report."""

    return (
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode("utf-8")
