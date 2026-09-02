"""One-shot privileged installer for a coordinator-prepared nightly candidate.

The service accepts no arguments.  Every deployment input is re-derived from
root-owned coordinator state, managed source metadata, and channel policy.
"""

from __future__ import annotations

import os
import pwd
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from open_mmi_trust import release_integrity, release_provenance, transition_gate
from open_mmi_trust.accepted_state import DEFAULT_ACCEPTED_STATE_PATH

from ui import update_coordinator, update_policy
from ui.web_dashboard import update_status


INSTALL_TIMEOUT_SECONDS = 300.0
WHEEL_BUILD_TIMEOUT_SECONDS = 180.0
INSTALL_SERVICE = "open-mmi-update-installer.service"


class InstallerError(RuntimeError):
    pass


def _trusted_stage(state: Mapping[str, Any], staging_root: Path) -> Path:
    try:
        return update_coordinator.trusted_prepared_stage(state, staging_root)
    except update_coordinator.CoordinatorError as exc:
        raise InstallerError(str(exc)) from exc


def _revalidate_candidate(
    stage: Path,
    state: Mapping[str, Any],
    source: Mapping[str, str],
    channel: str,
) -> None:
    if channel != "nightly":
        raise InstallerError("Prepared installation is enabled only for nightly updates")
    repository = update_status._repository_snapshot(source, "configured", channel)
    if repository.get("state") != "ready":
        raise InstallerError("Managed update source changed after preparation")
    candidate = str(state["candidate_commit"]).lower()
    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-c", f"safe.directory={stage}", "-C", str(stage), *arguments],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=10.0,
        )

    try:
        head_result = git("rev-parse", "HEAD")
        ancestry_result = git("merge-base", "--is-ancestor", source["installed_commit"], candidate)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallerError("Prepared candidate could not be revalidated") from exc
    head = head_result.stdout.strip().lower() if head_result.returncode == 0 else ""
    if head != candidate:
        raise InstallerError("Prepared candidate identity changed")
    if ancestry_result.returncode != 0:
        raise InstallerError("Prepared candidate is not a proven forward update")


def _deployment_environment(
    stage: Path,
    state: Mapping[str, Any],
    source: Mapping[str, str],
    candidate_wheel: Optional[Path] = None,
) -> Dict[str, str]:
    try:
        owner = Path(source["repository_path"]).stat()
        account = pwd.getpwuid(owner.st_uid)
    except (KeyError, OSError) as exc:
        raise InstallerError("Managed source owner cannot be resolved") from exc
    environment = os.environ.copy()
    environment.update({
        "OPEN_MMI_PREPARED_STAGE": str(stage),
        "OPEN_MMI_PREPARED_TRANSACTION": str(state["transaction_id"]),
        "OPEN_MMI_PREPARED_COMMIT": str(state["candidate_commit"]),
        "OPEN_MMI_PREPARED_VERSION": str(state["target_version"]),
        "OPEN_MMI_PREVIOUS_COMMIT": str(source["installed_commit"]),
        "OPEN_MMI_MANAGED_REPOSITORY": str(source["repository_path"]),
        "OPEN_MMI_MANAGED_BRANCH": str(source["branch"]),
        "OPEN_MMI_MANAGED_UPSTREAM": str(source["upstream"]),
        "OPEN_MMI_REAL_USER": account.pw_name,
        "HOME": "/var/lib/open-mmi/installer-home",
        "USER": "root",
        "LOGNAME": "root",
        "PIP_CONFIG_FILE": "/dev/null",
        "PIP_NO_INDEX": "1",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
    })
    if candidate_wheel is not None:
        environment["OPEN_MMI_PREPARED_WHEEL"] = str(candidate_wheel)
    return environment


def _prepare_candidate_wheel(
    stage: Path,
    rollback_root: Path,
    transaction_id: str,
    candidate_commit: str,
    inventory: Sequence[Mapping[str, Any]],
) -> Path:
    wheel_dir = rollback_root / transaction_id / "candidate-wheel"
    try:
        return release_integrity.build_trusted_wheel_from_git_inventory(
            stage, candidate_commit, inventory, wheel_dir
        )
    except release_integrity.ReleaseIntegrityError as exc:
        raise InstallerError(
            "Prepared candidate wheel could not be constructed from trusted Git-object inventory"
        ) from exc


def _run_deployment(command: Sequence[str], environment: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), env=dict(environment), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        check=False, timeout=INSTALL_TIMEOUT_SECONDS,
    )


_DEPLOYMENT_STAGES = {
    "backup", "install-root", "packaging-tools", "repository-head", "repository-clean", "repository-fetch",
    "repository-merge", "package-build", "package-artifact", "files", "package", "system-services",
    "user-services", "vehicle-config-coordinator", "power-manager",
    "service-health", "api-health", "version-health",
}


def _deployment_failure(output: str) -> str:
    matches = re.findall(r"Prepared deployment failed at stage: ([a-z-]+)", output or "")
    stage = matches[-1] if matches else ""
    if stage in _DEPLOYMENT_STAGES:
        failure = f"Prepared deployment failed during {stage}"
        if "Prepared rollback verified" in (output or ""):
            return f"{failure}; rollback verified"
        return f"{failure}; rollback unverified"
    return "Prepared deployment failed"


def install_prepared(
    state_path: Path = update_coordinator.DEFAULT_STATE_FILE,
    lock_path: Path = update_coordinator.DEFAULT_LOCK,
    staging_root: Path = update_coordinator.DEFAULT_STAGING_ROOT,
    command: Optional[Sequence[str]] = None,
    rollback_root: Optional[Path] = None,
    accepted_state_path: Optional[Path] = None,
    transition_authorization_path: Optional[Path] = None,
    transition_lineage_path: Optional[Path] = None,
    integrity_state_path: Optional[Path] = None,
    provenance_state_path: Optional[Path] = None,
    integrity_install_root: Optional[Path] = None,
    integrity_package_root: Optional[Path] = None,
) -> Dict[str, Any]:
    if os.geteuid() != 0 and state_path == update_coordinator.DEFAULT_STATE_FILE:
        raise InstallerError("Prepared installation requires root")
    rollback_root = rollback_root or update_coordinator._artifact_root(
        state_path, update_coordinator.DEFAULT_ROLLBACK_ROOT, "rollback"
    )
    accepted_state_path = accepted_state_path or update_coordinator._trust_artifact_path(
        state_path, DEFAULT_ACCEPTED_STATE_PATH
    )
    transition_authorization_path = (
        transition_authorization_path
        or update_coordinator._trust_artifact_path(
            state_path, transition_gate.DEFAULT_TRANSITION_AUTHORIZATION_PATH
        )
    )
    transition_lineage_path = (
        transition_lineage_path
        or update_coordinator._trust_artifact_path(
            state_path, transition_gate.DEFAULT_TRANSITION_LINEAGE_DIR
        )
    )
    integrity_state_path = (
        integrity_state_path
        or update_coordinator._trust_artifact_path(
            state_path, release_integrity.DEFAULT_INTEGRITY_STATE_PATH
        )
    )
    provenance_state_path = (
        provenance_state_path
        or update_coordinator._trust_artifact_path(
            state_path, release_provenance.DEFAULT_PROVENANCE_ROOT_PATH
        )
    )
    integrity_install_root = integrity_install_root or (
        release_integrity.default_install_root()
        if state_path == update_coordinator.DEFAULT_STATE_FILE
        else state_path.parent / "runtime"
    )
    integrity_package_root = integrity_package_root or (
        release_integrity.default_package_root()
        if state_path == update_coordinator.DEFAULT_STATE_FILE
        else integrity_install_root
    )
    with update_coordinator.TransactionLock(lock_path):
        state = update_coordinator.read_state(state_path)
        source, source_state = update_status._read_source_descriptor()
        policy, _ = update_policy.read_policy()
        if not source or source_state != "configured" or not policy:
            raise InstallerError("Managed update source or policy is unavailable")
        stage = _trusted_stage(state, staging_root)
        _revalidate_candidate(stage, state, source, str(policy["channel"]))
        try:
            # Current byte identity and release provenance are prerequisites for even
            # evaluating a future candidate's trust-boundary transition.  Both current
            # and candidate signatures are verified offline against the owner-pinned
            # signer root before any candidate-controlled build/deployment can begin.
            if state_path == update_coordinator.DEFAULT_STATE_FILE:
                current_integrity = release_integrity.require_current_privileged_integrity(
                    integrity_state_path,
                    install_root=integrity_install_root,
                    package_root=integrity_package_root,
                )
            else:
                current_integrity = release_integrity.require_current_integrity(
                    integrity_state_path, integrity_install_root, integrity_package_root
                )
            if current_integrity["candidate_commit"] != str(source["installed_commit"]).lower():
                raise release_integrity.ReleaseIntegrityError(
                    "installed integrity commit does not match managed update source identity"
                )
            release_provenance.require_current_release_provenance(
                provenance_state_path, stage, current_integrity["candidate_commit"]
            )
            release_provenance.require_candidate_release_provenance(
                provenance_state_path, stage, str(state["candidate_commit"])
            )
            transition = transition_gate.require_prepared_candidate_allowed(
                stage,
                transaction_id=state["transaction_id"],
                candidate_commit=state["candidate_commit"],
                accepted_state_path=accepted_state_path,
                authorization_path=transition_authorization_path,
                lineage_path=transition_lineage_path,
            )
            expected_release = release_integrity.expected_release_from_git(
                stage, str(state["candidate_commit"])
            )
            if expected_release["trust_manifest_digest"] != transition.candidate_manifest_digest:
                raise release_integrity.ReleaseIntegrityError(
                    "candidate runtime inventory Trust Manifest does not match transition-gate manifest"
                )
            transition_gate.activate_acknowledged_expansion(
                transition,
                accepted_state_path=accepted_state_path,
                authorization_path=transition_authorization_path,
                lineage_path=transition_lineage_path,
            )
        except (
            transition_gate.TransitionGateError,
            release_integrity.ReleaseIntegrityError,
            release_provenance.ReleaseProvenanceError,
        ) as exc:
            raise InstallerError(str(exc)) from exc
        transaction_id = str(state["transaction_id"])
        update_coordinator._safe_remove_transaction_tree(
            rollback_root / transaction_id, rollback_root, "rollback"
        )
        update_coordinator._prune_transaction_trees(
            rollback_root,
            keep=set(),
            limit=max(0, update_coordinator.MAX_RETAINED_ROLLBACKS - 1),
            label="rollback",
        )
        state.update({
            "state": "installing", "stage": "installing",
            "updated_at": update_coordinator._timestamp(), "completed_at": None,
            "pip_version_before": "", "pip_version_after": "", "error": "",
        })
        update_coordinator.write_state(state, state_path)
        candidate_wheel: Optional[Path] = None
        if command is None:
            try:
                candidate_wheel = _prepare_candidate_wheel(
                    stage, rollback_root, transaction_id,
                    expected_release["candidate_commit"], expected_release["inventory"]
                )
            except InstallerError as exc:
                state.update({
                    "state": "failed", "stage": "package-build",
                    "updated_at": update_coordinator._timestamp(),
                    "completed_at": update_coordinator._timestamp(),
                    "error": str(exc),
                })
                failed = update_coordinator.write_state(state, state_path)
                update_coordinator._best_effort_artifact_cleanup(failed, staging_root, rollback_root)
                raise
        deployment_command = list(command or (stage / "scripts/manage.sh", "_deploy-prepared"))
        try:
            result = _run_deployment(
                deployment_command,
                _deployment_environment(stage, state, source, candidate_wheel),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = None
            failure = "Prepared deployment could not complete"
        else:
            failure = _deployment_failure(result.stdout)
        if result is None or result.returncode != 0:
            state.update({
                "state": "failed", "stage": "installation",
                "updated_at": update_coordinator._timestamp(),
                "completed_at": update_coordinator._timestamp(), "error": failure,
            })
            failed = update_coordinator.write_state(state, state_path)
            update_coordinator._best_effort_artifact_cleanup(
                failed, staging_root, rollback_root
            )
            raise InstallerError(failure)
        if state_path == update_coordinator.DEFAULT_STATE_FILE:
            verification = release_integrity.verify_privileged_runtime_inventory(
                inventory=expected_release["inventory"],
                trust_manifest_digest=expected_release["trust_manifest_digest"],
                candidate_commit=expected_release["candidate_commit"],
                install_root=integrity_install_root,
                package_root=integrity_package_root,
            )
        else:
            verification = release_integrity.verify_runtime_inventory(
                inventory=expected_release["inventory"],
                trust_manifest_digest=expected_release["trust_manifest_digest"],
                candidate_commit=expected_release["candidate_commit"],
                install_root=integrity_install_root,
                package_root=integrity_package_root,
            )
        if not verification["matches"]:
            state.update({
                "state": "failed", "stage": "integrity-verification",
                "updated_at": update_coordinator._timestamp(),
                "completed_at": update_coordinator._timestamp(),
                "error": "Prepared deployment completed but installed privileged runtime failed byte-integrity verification",
            })
            failed = update_coordinator.write_state(state, state_path)
            update_coordinator._best_effort_artifact_cleanup(failed, staging_root, rollback_root)
            raise InstallerError(state["error"])
        try:
            transition_gate.finalize_successful_transition(
                transition, accepted_state_path=accepted_state_path,
                lineage_path=transition_lineage_path
            )
        except transition_gate.TransitionGateError as exc:
            state.update({
                "state": "failed", "stage": "trust-state-finalization",
                "updated_at": update_coordinator._timestamp(),
                "completed_at": update_coordinator._timestamp(),
                "error": "Prepared deployment completed but accepted trust state could not be finalized",
            })
            failed = update_coordinator.write_state(state, state_path)
            update_coordinator._best_effort_artifact_cleanup(
                failed, staging_root, rollback_root
            )
            raise InstallerError(state["error"]) from exc
        try:
            accepted_now, lineage_head = release_integrity.current_trust_anchors(
                accepted_state_path, transition_lineage_path
            )
            release_integrity._record_integrity_state(
                candidate_commit=expected_release["candidate_commit"],
                trust_manifest=expected_release["trust_manifest"],
                inventory=expected_release["inventory"],
                accepted_state=accepted_now,
                lineage_head_record_digest=lineage_head,
                record_source="prepared-update",
                path=integrity_state_path,
            )
        except release_integrity.ReleaseIntegrityError as exc:
            state.update({
                "state": "failed", "stage": "integrity-finalization",
                "updated_at": update_coordinator._timestamp(),
                "completed_at": update_coordinator._timestamp(),
                "error": "Prepared deployment completed but installed integrity state could not be finalized",
            })
            failed = update_coordinator.write_state(state, state_path)
            update_coordinator._best_effort_artifact_cleanup(failed, staging_root, rollback_root)
            raise InstallerError(state["error"]) from exc
        pip_before, pip_after = update_coordinator._packaging_tool_versions(
            rollback_root, transaction_id
        )
        state.update({
            "state": "complete", "stage": "complete",
            "updated_at": update_coordinator._timestamp(),
            "completed_at": update_coordinator._timestamp(),
            "pip_version_before": pip_before, "pip_version_after": pip_after,
            "error": "",
        })
        completed = update_coordinator.write_state(state, state_path)
        update_coordinator._best_effort_artifact_cleanup(
            completed, staging_root, rollback_root
        )
        return completed


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv:
        raise SystemExit("open-mmi-update-installer accepts no arguments")
    try:
        install_prepared()
    except (InstallerError, update_coordinator.CoordinatorError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
