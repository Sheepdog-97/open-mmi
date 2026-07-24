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
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from ui import update_coordinator, update_policy
from ui.web_dashboard import update_status


INSTALL_TIMEOUT_SECONDS = 300.0
INSTALL_SERVICE = "open-mmi-update-installer.service"


class InstallerError(RuntimeError):
    pass


def _trusted_stage(state: Mapping[str, Any], staging_root: Path) -> Path:
    transaction_id = str(state.get("transaction_id") or "")
    candidate = str(state.get("candidate_commit") or "").lower()
    if state.get("state") != "prepared" or state.get("stage") != "prepared":
        raise InstallerError("No prepared candidate is available")
    if not transaction_id.startswith("prepare-") or len(transaction_id) != 40:
        raise InstallerError("Prepared transaction identity is invalid")
    stage = staging_root / transaction_id
    try:
        resolved_root = staging_root.resolve(strict=True)
        resolved_stage = stage.resolve(strict=True)
        root_metadata = staging_root.lstat()
        metadata = stage.lstat()
    except OSError as exc:
        raise InstallerError("Prepared candidate staging is unavailable") from exc
    if resolved_stage.parent != resolved_root or resolved_stage != stage.absolute():
        raise InstallerError("Prepared candidate staging is invalid")
    if not stat.S_ISDIR(root_metadata.st_mode) or root_metadata.st_mode & 0o022:
        raise InstallerError("Prepared staging root is untrusted")
    if staging_root == update_coordinator.DEFAULT_STAGING_ROOT and (
        root_metadata.st_uid != 0 or resolved_root != staging_root.absolute()
    ):
        raise InstallerError("Prepared staging root is untrusted")
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_mode & 0o022:
        raise InstallerError("Prepared candidate staging is untrusted")
    if staging_root == update_coordinator.DEFAULT_STAGING_ROOT and metadata.st_uid != 0:
        raise InstallerError("Prepared candidate staging is untrusted")
    if len(candidate) != 40 or any(character not in "0123456789abcdef" for character in candidate):
        raise InstallerError("Prepared candidate commit is invalid")
    return stage


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
        "PIP_CACHE_DIR": "/var/lib/open-mmi/pip-cache",
    })
    return environment


def _run_deployment(command: Sequence[str], environment: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command), env=dict(environment), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        check=False, timeout=INSTALL_TIMEOUT_SECONDS,
    )


_DEPLOYMENT_STAGES = {
    "backup", "repository-head", "repository-clean", "repository-fetch",
    "repository-merge", "package-build", "files", "package", "system-services",
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
) -> Dict[str, Any]:
    if os.geteuid() != 0 and state_path == update_coordinator.DEFAULT_STATE_FILE:
        raise InstallerError("Prepared installation requires root")
    rollback_root = rollback_root or update_coordinator._artifact_root(
        state_path, update_coordinator.DEFAULT_ROLLBACK_ROOT, "rollback"
    )
    with update_coordinator.TransactionLock(lock_path):
        state = update_coordinator.read_state(state_path)
        source, source_state = update_status._read_source_descriptor()
        policy, _ = update_policy.read_policy()
        if not source or source_state != "configured" or not policy:
            raise InstallerError("Managed update source or policy is unavailable")
        stage = _trusted_stage(state, staging_root)
        _revalidate_candidate(stage, state, source, str(policy["channel"]))
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
            "error": "",
        })
        update_coordinator.write_state(state, state_path)
        deployment_command = list(command or (stage / "scripts/manage.sh", "_deploy-prepared"))
        try:
            result = _run_deployment(deployment_command, _deployment_environment(stage, state, source))
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
        state.update({
            "state": "complete", "stage": "complete",
            "updated_at": update_coordinator._timestamp(),
            "completed_at": update_coordinator._timestamp(), "error": "",
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
