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


def _inspect_updater_transition_gate_source(root: Path) -> dict[str, Any]:
    installer = root / "ui" / "update_installer.py"
    coordinator = root / "ui" / "update_coordinator.py"
    gate = root / "open_mmi_trust" / "transition_gate.py"
    paths = (installer, coordinator, gate)
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
    installer_activate = call_lines(installer_tree, "activate_acknowledged_expansion")
    installer_deploy = call_lines(installer_tree, "_run_deployment")
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
        and installer_activate
        and installer_deploy
        and min(installer_gate) < min(installer_activate) < min(installer_deploy)
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
            installer_activation_lines=installer_activate,
            installer_deployment_lines=installer_deploy,
            coordinator_gate_lines=coordinator_gate,
            coordinator_installer_lines=coordinator_systemctl,
            candidate_manifest_data_only=data_only,
            forbidden_gate_surfaces=forbidden,
        )
    return _check(
        "updater.preinstallation-trust-gate",
        PASS,
        "Installed updater gates the prepared candidate before candidate-controlled deployment executes.",
        installer_gate_line=min(installer_gate),
        installer_deployment_line=min(installer_deploy),
        coordinator_gate_line=min(coordinator_gate),
        candidate_manifest_source="git-object-data",
        note=(
            "This is source-level evidence for the installed Open MMI updater. It is not an OS sandbox against arbitrary privileged replacement code."
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
    install_root: Path | None = None,
) -> dict[str, Any]:
    """Inspect local trust evidence without mutating trust state or contacting a network."""

    root = Path(install_root) if install_root is not None else _default_install_root()
    manifest, manifest_summary, manifest_check = _inspect_manifest(Path(manifest_path))
    telemetry_summary, telemetry_check = _inspect_telemetry_authorization(Path(authorization_path))
    accepted_check = _inspect_accepted_owner_state(Path(accepted_state_path), manifest)

    checks: list[dict[str, Any]] = [manifest_check, telemetry_check, accepted_check]
    checks.append(_inspect_telemetry_default_deny())
    checks.append(_inspect_telemetry_self_authorization_source(root))
    checks.append(_inspect_accepted_state_self_authorization_source(root))
    checks.append(_inspect_can_transmit_source(root / "canbusd", manifest))
    checks.extend(_inspect_dashboard_render(root / "ui" / "web_dashboard" / "static"))
    checks.extend(_declared_assurance_checks(manifest))

    checks.extend(
        [
            _check(
                "release.file-integrity",
                UNVERIFIED,
                "No independently signed installed-file integrity index is defined yet.",
                note="The manifest self-digest is not a substitute for release-file integrity.",
            ),
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
