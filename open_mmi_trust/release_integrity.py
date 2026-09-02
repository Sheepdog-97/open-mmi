"""Installed Release/File Integrity v1 for Open MMI.

The integrity state binds the managed Open MMI release source and active package
runtime bytes to an exact Git commit whose Trust Manifest was handled by the
already-installed trust stack.  It deliberately does *not* claim independent release provenance:
that is a separate Release Provenance / Pinned Signer Root trust state and check.

Candidate inventories are derived from Git objects, not from the candidate
worktree.  State mutation is private and confined by CI to the local owner CLI
and the old-trusted update installer.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .accepted_state import accepted_state_digest, read_accepted_state
from .lineage import lineage_summary, read_transition_lineage, require_lineage_current
from .manifest import ManifestError, manifest_digest, validate_manifest


INTEGRITY_SCHEMA_VERSION = 1
INTEGRITY_STATE_ID = "org.open-mmi.installed-release-integrity"
DEFAULT_INTEGRITY_STATE_PATH = Path("/var/lib/open-mmi/trust/installed-release-integrity.v1.json")
DEFAULT_SOURCE_DESCRIPTOR = Path("/opt/open-mmi/.update-source.json")
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
# Backwards-compatible internal name used by existing v1 tests/invariants.
RUNTIME_ROOTS = PACKAGE_RUNTIME_ROOTS
SOURCE_RUNTIME_ROOTS = (
    "actions",
    "bindings",
    "canbusd",
    "powerd",
    "ui",
    "vehicles",
)
SOURCE_RELEASE_ROOTS = (*SOURCE_RUNTIME_ROOTS, "scripts", "packaging", "systemd")
SOURCE_RELEASE_FILES = ("LICENSE", "README.md", "pyproject.toml")
INVENTORY_ROOTS = (*PACKAGE_RUNTIME_ROOTS, "scripts", "packaging", "systemd")
DEFAULT_INSTALL_ROOT = Path("/opt/open-mmi")
DEFAULT_SYSTEMD_UNIT_ROOT = Path("/etc/systemd/system")
DEFAULT_SYSTEMD_USER_UNIT_ROOT = Path("/etc/systemd/user")
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
_PYTHON_LIB_RE = re.compile(r"^python[0-9]+\.[0-9]+$")
PACKAGE_SOURCE_ONLY_PATHS = {"ui/web_dashboard/README.md"}
MAX_INVENTORY_FILES = 4096
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReleaseIntegrityError(RuntimeError):
    """Installed integrity evidence is invalid, unavailable, or contradictory."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ReleaseIntegrityError("integrity timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReleaseIntegrityError("integrity timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseIntegrityError("integrity timestamp is invalid")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ReleaseIntegrityError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise ReleaseIntegrityError(f"{label} is missing keys: {', '.join(missing)}")


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ReleaseIntegrityError("integrity inventory path is invalid")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ReleaseIntegrityError("integrity inventory path is invalid") from exc
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ReleaseIntegrityError("integrity inventory path contains control characters")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value != path.as_posix():
        raise ReleaseIntegrityError("integrity inventory path is unsafe")
    if not path.parts or (
        value not in SOURCE_RELEASE_FILES and path.parts[0] not in INVENTORY_ROOTS
    ):
        raise ReleaseIntegrityError("integrity inventory path is outside managed release roots")
    return value


def _is_package_runtime_path(path: str) -> bool:
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
    if path.startswith("ui/web_dashboard/static/") and suffix in {
        ".css", ".html", ".js", ".md", ".png", ".svg", ".txt"
    }:
        return True
    return False


def _is_inventory_path(path: str) -> bool:
    if path in SOURCE_RELEASE_FILES or path in PACKAGE_SOURCE_ONLY_PATHS:
        return True
    if _is_package_runtime_path(path):
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


def _validate_inventory_entry(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ReleaseIntegrityError("integrity inventory entry must be an object")
    _require_exact_keys(payload, {"path", "sha256", "size"}, "integrity inventory entry")
    path = _safe_relative_path(payload["path"])
    if not _is_inventory_path(path):
        raise ReleaseIntegrityError(f"integrity inventory path is not in the v1 managed release scope: {path}")
    digest = payload["sha256"]
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReleaseIntegrityError("integrity inventory sha256 is invalid")
    size = payload["size"]
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES:
        raise ReleaseIntegrityError("integrity inventory size is invalid")
    return {"path": path, "sha256": digest, "size": size}


def validate_inventory(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload or len(payload) > MAX_INVENTORY_FILES:
        raise ReleaseIntegrityError("integrity inventory is empty or too large")
    entries = [_validate_inventory_entry(entry) for entry in payload]
    paths = [entry["path"] for entry in entries]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ReleaseIntegrityError("integrity inventory paths must be sorted and unique")
    if sum(entry["size"] for entry in entries) > MAX_TOTAL_BYTES:
        raise ReleaseIntegrityError("integrity inventory total size exceeds v1 limit")
    required_manifest = "open_mmi_trust/data/trust-manifest.v1.json"
    if required_manifest not in set(paths):
        raise ReleaseIntegrityError("integrity inventory omits the Trust Manifest")
    return entries


def canonical_inventory_bytes(payload: Sequence[Mapping[str, Any]]) -> bytes:
    entries = validate_inventory(list(payload))
    return (json.dumps(entries, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def inventory_digest(payload: Sequence[Mapping[str, Any]]) -> str:
    return "sha256:" + hashlib.sha256(canonical_inventory_bytes(payload)).hexdigest()


def validate_integrity_state(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ReleaseIntegrityError("installed release integrity state must be an object")
    keys = {
        "schema_version", "state_id", "recorded_at", "record_source",
        "candidate_commit", "trust_manifest", "trust_manifest_digest",
        "inventory", "inventory_digest", "accepted_state_digest_at_recording",
        "lineage_head_record_digest_at_recording",
    }
    _require_exact_keys(payload, keys, "installed release integrity state")
    if payload["schema_version"] != INTEGRITY_SCHEMA_VERSION:
        raise ReleaseIntegrityError("unsupported installed release integrity schema_version")
    if payload["state_id"] != INTEGRITY_STATE_ID:
        raise ReleaseIntegrityError("unexpected installed release integrity state id")
    source = payload["record_source"]
    if source not in {"baseline-existing-state", "prepared-update"}:
        raise ReleaseIntegrityError("installed release integrity source is invalid")
    commit = payload["candidate_commit"]
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise ReleaseIntegrityError("installed release integrity candidate commit is invalid")
    try:
        manifest = validate_manifest(payload["trust_manifest"])
    except ManifestError as exc:
        raise ReleaseIntegrityError(f"integrity Trust Manifest is invalid: {exc}") from exc
    manifest_sha = "sha256:" + manifest_digest(manifest)
    if payload["trust_manifest_digest"] != manifest_sha:
        raise ReleaseIntegrityError("integrity Trust Manifest digest does not match embedded manifest")
    entries = validate_inventory(payload["inventory"])
    digest = inventory_digest(entries)
    if payload["inventory_digest"] != digest:
        raise ReleaseIntegrityError("integrity inventory digest does not match inventory")
    accepted_digest = payload["accepted_state_digest_at_recording"]
    lineage_digest = payload["lineage_head_record_digest_at_recording"]
    if not isinstance(accepted_digest, str) or not _SHA256_RE.fullmatch(accepted_digest):
        raise ReleaseIntegrityError("integrity accepted-state anchor is invalid")
    if not isinstance(lineage_digest, str) or not _SHA256_RE.fullmatch(lineage_digest):
        raise ReleaseIntegrityError("integrity lineage anchor is invalid")
    return {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "state_id": INTEGRITY_STATE_ID,
        "recorded_at": _validate_timestamp(payload["recorded_at"]),
        "record_source": source,
        "candidate_commit": commit,
        "trust_manifest": manifest,
        "trust_manifest_digest": manifest_sha,
        "inventory": entries,
        "inventory_digest": digest,
        "accepted_state_digest_at_recording": accepted_digest,
        "lineage_head_record_digest_at_recording": lineage_digest,
    }


def canonical_integrity_state_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = validate_integrity_state(payload)
    return (json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def integrity_state_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_integrity_state_bytes(payload)).hexdigest()


def _production_path(path: Path) -> bool:
    try:
        return path.resolve(strict=False) == DEFAULT_INTEGRITY_STATE_PATH
    except OSError:
        return path == DEFAULT_INTEGRITY_STATE_PATH


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        metadata = path.parent.lstat()
    except OSError as exc:
        raise ReleaseIntegrityError("installed release integrity directory is unavailable") from exc
    expected_uid = 0 if require_root else os.geteuid()
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != expected_uid or metadata.st_mode & 0o077:
        raise ReleaseIntegrityError("installed release integrity directory is untrusted")


def _decode_json(text: str) -> Any:
    def unique(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseIntegrityError(f"duplicate installed integrity field: {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise ReleaseIntegrityError(f"invalid installed integrity JSON number: {value}")

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=reject)
    except ReleaseIntegrityError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("installed release integrity state is invalid JSON") from exc


def read_integrity_state(path: Path = DEFAULT_INTEGRITY_STATE_PATH) -> dict[str, Any] | None:
    production = _production_path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReleaseIntegrityError("installed release integrity state cannot be inspected") from exc
    if not _trusted_regular_file(path, require_root=production):
        raise ReleaseIntegrityError("installed release integrity state file is untrusted")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseIntegrityError("installed release integrity state cannot be read") from exc
    return validate_integrity_state(_decode_json(text))


def _write_integrity_state(
    payload: Mapping[str, Any],
    path: Path = DEFAULT_INTEGRITY_STATE_PATH,
) -> dict[str, Any]:
    production = _production_path(path)
    if production and os.geteuid() != 0:
        raise ReleaseIntegrityError("production installed integrity changes require root")
    validated = validate_integrity_state(payload)
    _ensure_parent(path, require_root=production)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ReleaseIntegrityError("installed release integrity state cannot be inspected") from exc
    else:
        if not _trusted_regular_file(path, require_root=production):
            raise ReleaseIntegrityError("refusing to replace untrusted installed integrity state")
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
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
        raise ReleaseIntegrityError("could not persist installed release integrity state") from exc
    return validated


def _record_integrity_state(
    *,
    candidate_commit: str,
    trust_manifest: Mapping[str, Any],
    inventory: Sequence[Mapping[str, Any]],
    accepted_state: Mapping[str, Any],
    lineage_head_record_digest: str,
    record_source: str,
    path: Path = DEFAULT_INTEGRITY_STATE_PATH,
) -> dict[str, Any]:
    manifest = validate_manifest(trust_manifest)
    entries = validate_inventory(list(inventory))
    payload = {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "state_id": INTEGRITY_STATE_ID,
        "recorded_at": _timestamp(),
        "record_source": record_source,
        "candidate_commit": candidate_commit,
        "trust_manifest": manifest,
        "trust_manifest_digest": "sha256:" + manifest_digest(manifest),
        "inventory": entries,
        "inventory_digest": inventory_digest(entries),
        "accepted_state_digest_at_recording": accepted_state_digest(accepted_state),
        "lineage_head_record_digest_at_recording": lineage_head_record_digest,
    }
    return _write_integrity_state(payload, path)


def _git(repository: Path, *arguments: str, text: bool = False) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={repository}", "-C", str(repository), *arguments],
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            text=text, timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseIntegrityError("Git object inspection failed") from exc


def _git_blob(repository: Path, object_id: str, expected_size: int) -> bytes:
    if expected_size > MAX_FILE_BYTES:
        raise ReleaseIntegrityError("candidate runtime file exceeds v1 integrity size limit")
    result = _git(repository, "cat-file", "blob", object_id)
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        raise ReleaseIntegrityError("candidate runtime blob could not be read")
    if len(result.stdout) != expected_size:
        raise ReleaseIntegrityError("candidate runtime blob size changed during inspection")
    return result.stdout


def inventory_from_git_commit(repository: Path, commit: str) -> list[dict[str, Any]]:
    commit = str(commit).lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise ReleaseIntegrityError("candidate commit is invalid")
    result = _git(
        repository, "ls-tree", "-r", "-z", "-l", "--full-tree", commit, "--",
        *INVENTORY_ROOTS, *SOURCE_RELEASE_FILES,
    )
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        raise ReleaseIntegrityError("candidate runtime tree could not be enumerated")
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
            raise ReleaseIntegrityError("candidate runtime tree entry is invalid") from exc
        safe = _safe_relative_path(path)
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ReleaseIntegrityError(f"candidate runtime tree contains unsupported object: {safe}")
        if not _is_inventory_path(safe):
            raise ReleaseIntegrityError(f"candidate managed release tree contains unsupported file: {safe}")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ReleaseIntegrityError("candidate runtime inventory exceeds v1 total size limit")
        data = _git_blob(repository, object_id, size)
        entries.append({
            "path": safe,
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "size": size,
        })
        if len(entries) > MAX_INVENTORY_FILES:
            raise ReleaseIntegrityError("candidate runtime inventory contains too many files")
    entries.sort(key=lambda item: item["path"])
    return validate_inventory(entries)


def manifest_from_git_commit(repository: Path, commit: str) -> dict[str, Any]:
    path = "open_mmi_trust/data/trust-manifest.v1.json"
    result = _git(repository, "show", f"{commit}:{path}")
    if result.returncode != 0 or not isinstance(result.stdout, bytes) or len(result.stdout) > MAX_FILE_BYTES:
        raise ReleaseIntegrityError("candidate Trust Manifest could not be read as Git object data")
    try:
        payload = json.loads(result.stdout.decode("utf-8"))
        return validate_manifest(payload)
    except (UnicodeError, json.JSONDecodeError, ManifestError) as exc:
        raise ReleaseIntegrityError(f"candidate Trust Manifest is invalid: {exc}") from exc


def expected_release_from_git(repository: Path, commit: str) -> dict[str, Any]:
    inventory = inventory_from_git_commit(repository, commit)
    manifest = manifest_from_git_commit(repository, commit)
    manifest_path = "open_mmi_trust/data/trust-manifest.v1.json"
    manifest_entry = next(entry for entry in inventory if entry["path"] == manifest_path)
    result = _git(repository, "show", f"{commit}:{manifest_path}")
    assert isinstance(result.stdout, bytes)
    if manifest_entry["sha256"] != "sha256:" + hashlib.sha256(result.stdout).hexdigest():
        raise ReleaseIntegrityError("candidate Trust Manifest is not bound to runtime inventory")
    return {
        "candidate_commit": str(commit).lower(),
        "trust_manifest": manifest,
        "trust_manifest_digest": "sha256:" + manifest_digest(manifest),
        "inventory": inventory,
        "inventory_digest": inventory_digest(inventory),
    }


def _ignored_runtime_file(relative: str) -> bool:
    parts = PurePosixPath(relative).parts
    if "__pycache__" in parts or relative.endswith((".pyc", ".pyo")):
        return True
    if relative in PACKAGE_SOURCE_ONLY_PATHS:
        return True
    return False


def _same_root(left: Path, right: Path) -> bool:
    try:
        return left.resolve(strict=False) == right.resolve(strict=False)
    except OSError:
        return left == right


def production_package_root(install_root: Path = DEFAULT_INSTALL_ROOT) -> Path:
    """Resolve the one production venv site-packages directory without executing it."""

    lib_root = Path(install_root) / "venv" / "lib"
    try:
        metadata = lib_root.lstat()
    except OSError as exc:
        raise ReleaseIntegrityError(
            "production Open MMI venv library root is unavailable"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseIntegrityError("production Open MMI venv library root is untrusted")

    candidates: list[Path] = []
    try:
        children = sorted(lib_root.iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise ReleaseIntegrityError(
            "production Open MMI venv library root cannot be enumerated"
        ) from exc

    for child in children:
        if not _PYTHON_LIB_RE.fullmatch(child.name):
            continue
        try:
            child_metadata = child.lstat()
        except OSError as exc:
            raise ReleaseIntegrityError(
                "production Open MMI Python library path is unavailable"
            ) from exc
        if stat.S_ISLNK(child_metadata.st_mode) or not stat.S_ISDIR(child_metadata.st_mode):
            raise ReleaseIntegrityError(
                "production Open MMI Python library path is untrusted"
            )

        site_packages = child / "site-packages"
        try:
            package_metadata = site_packages.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ReleaseIntegrityError(
                "production Open MMI site-packages path is unavailable"
            ) from exc
        if stat.S_ISLNK(package_metadata.st_mode) or not stat.S_ISDIR(package_metadata.st_mode):
            raise ReleaseIntegrityError(
                "production Open MMI site-packages path is untrusted"
            )
        candidates.append(site_packages)

    if len(candidates) != 1:
        raise ReleaseIntegrityError(
            "production Open MMI site-packages identity is missing or ambiguous"
        )
    return candidates[0]


def _verify_inventory_root(
    entries: Sequence[Mapping[str, Any]],
    root: Path,
    roots: Sequence[str],
    *,
    exact_files: Sequence[str] = (),
    package_only: bool = False,
) -> dict[str, Any]:
    root_set = set(roots)
    exact_set = set(exact_files)
    selected = {
        entry["path"]: entry
        for entry in entries
        if (
            (entry["path"] in exact_set)
            or (
                PurePosixPath(str(entry["path"])).parts[0] in root_set
                and (not package_only or _is_package_runtime_path(str(entry["path"])))
            )
        )
    }
    missing: list[str] = []
    modified: list[str] = []
    extras: list[str] = []
    unsafe: list[str] = []

    for relative, entry in selected.items():
        path = root / relative
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            missing.append(relative)
            continue
        except OSError:
            unsafe.append(relative)
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            unsafe.append(relative)
            continue
        try:
            data = path.read_bytes()
        except OSError:
            unsafe.append(relative)
            continue
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if len(data) != entry["size"] or digest != entry["sha256"]:
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
                try:
                    metadata = path.lstat()
                except OSError:
                    unsafe.append(relative)
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    unsafe.append(relative)
                    continue
                if name == "__pycache__":
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    unsafe.append(relative)
                    continue
                retained.append(name)
            directories[:] = retained
            for name in files:
                path = base / name
                relative = path.relative_to(root).as_posix()
                try:
                    metadata = path.lstat()
                except OSError:
                    unsafe.append(relative)
                    continue
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                    unsafe.append(relative)
                    continue
                if relative in selected or _ignored_runtime_file(relative):
                    continue
                extras.append(relative)

    return {
        "files_expected": len(selected),
        "missing": sorted(set(missing)),
        "modified": sorted(set(modified)),
        "extra": sorted(set(extras)),
        "unsafe": sorted(set(unsafe)),
    }


def verify_runtime_inventory(
    *,
    inventory: Sequence[Mapping[str, Any]],
    trust_manifest_digest: str,
    candidate_commit: str,
    install_root: Path,
    package_root: Path | None = None,
) -> dict[str, Any]:
    entries = validate_inventory(list(inventory))
    if not isinstance(trust_manifest_digest, str) or not _SHA256_RE.fullmatch(trust_manifest_digest):
        raise ReleaseIntegrityError("runtime verification Trust Manifest digest is invalid")
    if not isinstance(candidate_commit, str) or not _COMMIT_RE.fullmatch(candidate_commit):
        raise ReleaseIntegrityError("runtime verification candidate commit is invalid")

    source_root = Path(install_root)
    package_root = Path(package_root) if package_root is not None else source_root
    combined_root = _same_root(source_root, package_root)
    if combined_root:
        # Developer/test installs commonly execute all package roots from one checkout.
        checks = [(
            "runtime",
            _verify_inventory_root(
                entries, source_root, INVENTORY_ROOTS, exact_files=SOURCE_RELEASE_FILES
            ),
        )]
    else:
        checks = [
            (
                "source",
                _verify_inventory_root(
                    entries, source_root, SOURCE_RELEASE_ROOTS, exact_files=SOURCE_RELEASE_FILES
                ),
            ),
            (
                "package",
                _verify_inventory_root(
                    entries, package_root, PACKAGE_RUNTIME_ROOTS, package_only=True
                ),
            ),
        ]

    def collect(field: str) -> list[str]:
        values: list[str] = []
        for label, check in checks:
            for relative in check[field]:
                values.append(relative if combined_root else f"{label}:{relative}")
        return sorted(set(values))

    missing = collect("missing")
    modified = collect("modified")
    extras = collect("extra")
    unsafe = collect("unsafe")

    manifest_path = package_root / "open_mmi_trust" / "data" / "trust-manifest.v1.json"
    try:
        active_manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
        active_manifest_digest = "sha256:" + manifest_digest(active_manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, ManifestError):
        unsafe.append(
            "open_mmi_trust/data/trust-manifest.v1.json"
            if combined_root else "package:open_mmi_trust/data/trust-manifest.v1.json"
        )
        active_manifest_digest = ""
    if active_manifest_digest and active_manifest_digest != trust_manifest_digest:
        modified.append(
            "open_mmi_trust/data/trust-manifest.v1.json"
            if combined_root else "package:open_mmi_trust/data/trust-manifest.v1.json"
        )

    missing = sorted(set(missing))
    modified = sorted(set(modified))
    extras = sorted(set(extras))
    unsafe = sorted(set(unsafe))
    matches = not (missing or modified or extras or unsafe)
    return {
        "matches": matches,
        "files_expected": sum(int(check["files_expected"]) for _label, check in checks),
        "inventory_files": len(entries),
        "inventory_digest": inventory_digest(entries),
        "candidate_commit": candidate_commit,
        "trust_manifest_digest": trust_manifest_digest,
        "source_root": str(source_root),
        "package_root": str(package_root),
        "missing": missing,
        "modified": modified,
        "extra": extras,
        "unsafe": unsafe,
    }


def _verify_privileged_ownership(
    entries: Sequence[Mapping[str, Any]],
    source_root: Path,
    package_root: Path,
) -> dict[str, Any]:
    """Verify root control of the production privileged execution path."""

    if not _same_root(source_root, DEFAULT_INSTALL_ROOT):
        return {"matches": True, "paths_checked": 0, "unsafe": []}

    unsafe: list[str] = []
    checked: set[Path] = set()

    def check_directory(path: Path, label: str) -> None:
        if path in checked:
            return
        checked.add(path)
        try:
            metadata = path.lstat()
        except OSError:
            unsafe.append(label)
            return
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_mode & 0o022
        ):
            unsafe.append(label)

    def check_ancestors(path: Path, label: str) -> None:
        current = path
        index = 0
        while True:
            check_directory(current, label if index == 0 else f"{label}:ancestor:{current}")
            if current == current.parent:
                break
            current = current.parent
            index += 1

    check_ancestors(source_root, "source-root")
    check_ancestors(package_root, "package-root")
    check_directory(source_root / "venv" / "bin", "python-bin-root")

    python_path = source_root / "venv" / "bin" / "python"
    try:
        python_metadata = python_path.lstat()
        resolved_python = python_path.resolve(strict=True)
        resolved_metadata = resolved_python.lstat()
    except OSError:
        unsafe.append("python-executable")
    else:
        if python_metadata.st_uid != 0:
            unsafe.append("python-executable")
        if (
            not stat.S_ISREG(resolved_metadata.st_mode)
            or resolved_metadata.st_uid != 0
            or resolved_metadata.st_mode & 0o022
            or not resolved_metadata.st_mode & 0o111
            or not resolved_python.is_relative_to(Path("/usr/bin"))
        ):
            unsafe.append("python-executable-target")
        else:
            check_ancestors(resolved_python.parent, "python-system-root")

    normalized = validate_inventory(list(entries))
    for entry in normalized:
        relative = str(entry["path"])
        item = PurePosixPath(relative)
        first = item.parts[0] if item.parts else ""
        targets: list[tuple[str, Path]] = []
        if relative in SOURCE_RELEASE_FILES or first in SOURCE_RELEASE_ROOTS:
            targets.append(("source", source_root / relative))
        if _is_package_runtime_path(relative):
            targets.append(("package", package_root / relative))

        for kind, path in targets:
            label = f"{kind}:{relative}"
            try:
                metadata = path.lstat()
            except OSError:
                unsafe.append(label)
                continue
            if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                unsafe.append(label)
            parent = path.parent
            while True:
                check_directory(parent, f"{kind}-directory:{parent}")
                if parent == (source_root if kind == "source" else package_root):
                    break
                if parent == parent.parent:
                    unsafe.append(f"{kind}-directory:outside-root:{path}")
                    break
                parent = parent.parent

    unsafe = sorted(set(unsafe))
    return {
        "matches": not unsafe,
        "paths_checked": len(checked),
        "unsafe": unsafe,
    }


def _verify_privileged_units(
    entries: Sequence[Mapping[str, Any]],
    unit_root: Path,
    *,
    units: Sequence[str] = PRIVILEGED_SYSTEM_UNITS,
    inventory_prefix: str = "systemd/system",
    production_root: Path = DEFAULT_SYSTEMD_UNIT_ROOT,
) -> dict[str, Any]:
    inventory = {
        str(entry["path"]): entry for entry in validate_inventory(list(entries))
    }
    missing: list[str] = []
    modified: list[str] = []
    unsafe: list[str] = []
    production = _same_root(Path(unit_root), production_root)
    if production:
        try:
            root_metadata = Path(unit_root).lstat()
        except OSError as exc:
            raise ReleaseIntegrityError(
                "privileged systemd unit root is unavailable"
            ) from exc
        if (
            stat.S_ISLNK(root_metadata.st_mode)
            or not stat.S_ISDIR(root_metadata.st_mode)
            or root_metadata.st_uid != 0
            or root_metadata.st_mode & 0o022
        ):
            raise ReleaseIntegrityError("privileged systemd unit root is untrusted")

    for unit in units:
        relative = f"{inventory_prefix}/{unit}"
        expected = inventory.get(relative)
        if expected is None:
            raise ReleaseIntegrityError(
                f"integrity inventory omits privileged systemd unit: {unit}"
            )

        path = Path(unit_root) / unit
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            missing.append(unit)
            continue
        except OSError:
            unsafe.append(unit)
            continue

        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or (production and (metadata.st_uid != 0 or metadata.st_mode & 0o022))
        ):
            unsafe.append(unit)
            continue

        try:
            data = path.read_bytes()
        except OSError:
            unsafe.append(unit)
            continue
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if len(data) != expected["size"] or digest != expected["sha256"]:
            modified.append(unit)

    missing = sorted(set(missing))
    modified = sorted(set(modified))
    unsafe = sorted(set(unsafe))
    return {
        "matches": not (missing or modified or unsafe),
        "files_expected": len(units),
        "unit_root": str(unit_root),
        "missing": missing,
        "modified": modified,
        "unsafe": unsafe,
    }


def verify_privileged_runtime_inventory(
    *,
    inventory: Sequence[Mapping[str, Any]],
    trust_manifest_digest: str,
    candidate_commit: str,
    install_root: Path = DEFAULT_INSTALL_ROOT,
    package_root: Path | None = None,
    systemd_unit_root: Path = DEFAULT_SYSTEMD_UNIT_ROOT,
    systemd_user_unit_root: Path | None = None,
) -> dict[str, Any]:
    """Verify the bytes actually used by privileged update execution."""

    entries = validate_inventory(list(inventory))
    source_root = Path(install_root)
    packages = (
        Path(package_root)
        if package_root is not None
        else production_package_root(source_root)
    )
    user_unit_root = (
        DEFAULT_SYSTEMD_USER_UNIT_ROOT
        if systemd_user_unit_root is None
        and _same_root(Path(systemd_unit_root), DEFAULT_SYSTEMD_UNIT_ROOT)
        else Path(systemd_unit_root)
        if systemd_user_unit_root is None
        else Path(systemd_user_unit_root)
    )
    runtime = verify_runtime_inventory(
        inventory=inventory,
        trust_manifest_digest=trust_manifest_digest,
        candidate_commit=candidate_commit,
        install_root=source_root,
        package_root=packages,
    )
    units = _verify_privileged_units(inventory, Path(systemd_unit_root))
    user_units = _verify_privileged_units(
        inventory,
        user_unit_root,
        units=PRIVILEGED_USER_UNITS,
        inventory_prefix="systemd/user",
        production_root=DEFAULT_SYSTEMD_USER_UNIT_ROOT,
    )
    ownership = _verify_privileged_ownership(entries, source_root, packages)

    missing = list(runtime["missing"]) + [
        f"systemd:{item}" for item in units["missing"]
    ] + [
        f"systemd-user:{item}" for item in user_units["missing"]
    ]
    modified = list(runtime["modified"]) + [
        f"systemd:{item}" for item in units["modified"]
    ] + [
        f"systemd-user:{item}" for item in user_units["modified"]
    ]
    unsafe = list(runtime["unsafe"]) + [
        f"systemd:{item}" for item in units["unsafe"]
    ] + [
        f"systemd-user:{item}" for item in user_units["unsafe"]
    ] + [
        f"ownership:{item}" for item in ownership["unsafe"]
    ]
    return {
        "matches": bool(
            runtime["matches"] and units["matches"] and user_units["matches"] and ownership["matches"]
        ),
        "files_expected": (
            int(runtime["files_expected"])
            + int(units["files_expected"])
            + int(user_units["files_expected"])
        ),
        "inventory_files": runtime["inventory_files"],
        "inventory_digest": runtime["inventory_digest"],
        "candidate_commit": candidate_commit,
        "trust_manifest_digest": trust_manifest_digest,
        "source_root": str(source_root),
        "package_root": str(packages),
        "systemd_unit_root": str(systemd_unit_root),
        "systemd_user_unit_root": str(user_unit_root),
        "missing": sorted(set(missing)),
        "modified": sorted(set(modified)),
        "extra": list(runtime["extra"]),
        "unsafe": sorted(set(unsafe)),
        "runtime": runtime,
        "privileged_units": units,
        "privileged_user_units": user_units,
        "privileged_ownership": ownership,
    }


def verify_privileged_installed_runtime(
    state: Mapping[str, Any],
    install_root: Path = DEFAULT_INSTALL_ROOT,
    package_root: Path | None = None,
    systemd_unit_root: Path = DEFAULT_SYSTEMD_UNIT_ROOT,
    systemd_user_unit_root: Path | None = None,
) -> dict[str, Any]:
    normalized = validate_integrity_state(state)
    return verify_privileged_runtime_inventory(
        inventory=normalized["inventory"],
        trust_manifest_digest=normalized["trust_manifest_digest"],
        candidate_commit=normalized["candidate_commit"],
        install_root=install_root,
        package_root=package_root,
        systemd_unit_root=systemd_unit_root,
        systemd_user_unit_root=systemd_user_unit_root,
    )


def require_current_privileged_integrity(
    state_path: Path = DEFAULT_INTEGRITY_STATE_PATH,
    install_root: Path = DEFAULT_INSTALL_ROOT,
    package_root: Path | None = None,
    systemd_unit_root: Path = DEFAULT_SYSTEMD_UNIT_ROOT,
) -> dict[str, Any]:
    state = read_integrity_state(state_path)
    if state is None:
        raise ReleaseIntegrityError(
            "Installed Release/File Integrity is not established; "
            "local bootstrap is required before updates"
        )
    verification = verify_privileged_installed_runtime(
        state,
        install_root=install_root,
        package_root=package_root,
        systemd_unit_root=systemd_unit_root,
    )
    if not verification["matches"]:
        raise ReleaseIntegrityError(
            "privileged Open MMI runtime bytes do not match recorded integrity state"
        )
    return state


def verify_installed_runtime(
    state: Mapping[str, Any],
    install_root: Path,
    package_root: Path | None = None,
) -> dict[str, Any]:
    normalized = validate_integrity_state(state)
    return verify_runtime_inventory(
        inventory=normalized["inventory"],
        trust_manifest_digest=normalized["trust_manifest_digest"],
        candidate_commit=normalized["candidate_commit"],
        install_root=install_root,
        package_root=package_root,
    )


def require_current_integrity(
    state_path: Path,
    install_root: Path,
    package_root: Path | None = None,
) -> dict[str, Any]:
    state = read_integrity_state(state_path)
    if state is None:
        raise ReleaseIntegrityError(
            "Installed Release/File Integrity is not established; local bootstrap is required before updates"
        )
    verification = verify_installed_runtime(state, install_root, package_root)
    if not verification["matches"]:
        raise ReleaseIntegrityError("installed Open MMI runtime bytes do not match recorded integrity state")
    return state


TRUSTED_WHEEL_DISTRIBUTION = "open-mmi"
TRUSTED_WHEEL_VERSION = "0.1.0a1"
TRUSTED_WHEEL_TAG = "py3-none-any"
TRUSTED_WHEEL_ENTRY_POINTS = (
    ("open-mmi-canbusd", "canbusd.core:main"),
    ("open-mmi-config", "ui.config_cli:main"),
    ("open-mmi-dashboard", "ui.web_dashboard.server:main"),
    ("open-mmi-launcher", "ui.launcher:main"),
    ("open-mmi-powerd", "powerd.cli:main"),
    ("open-mmi-status", "ui.dashboard.status_cli:main"),
    ("open-mmi-telemetry", "open_mmi_telemetry.cli:main"),
    ("open-mmi-trust-inspect", "open_mmi_trust.inspector_cli:main"),
    ("open-mmi-trust-integrity", "open_mmi_trust.release_integrity_cli:main"),
    ("open-mmi-trust-provenance", "open_mmi_trust.release_provenance_cli:main"),
    ("open-mmi-trust-lineage", "open_mmi_trust.lineage_cli:main"),
    ("open-mmi-trust-state", "open_mmi_trust.accepted_state_cli:main"),
    ("open-mmi-trust-transition", "open_mmi_trust.transition_gate_cli:main"),
    ("open-mmi-update-coordinator", "ui.update_coordinator:main"),
    ("open-mmi-update-installer", "ui.update_installer:main"),
    ("open-mmi-vehicle-config-coordinator", "ui.vehicle_config_coordinator:main"),
)


def _wheel_record_digest(data: bytes) -> str:
    encoded = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def _trusted_wheel_member(name: str, data: bytes) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info, data


def build_trusted_wheel_from_git_inventory(
    repository: Path,
    commit: str,
    inventory: Sequence[Mapping[str, Any]],
    output_dir: Path,
) -> Path:
    """Construct the Open MMI wheel without importing or executing candidate code.

    The build algorithm and package metadata are supplied by the already-installed
    trusted release. Candidate Git objects provide only the byte payload that was
    already bound into the trusted runtime inventory.
    """

    commit = str(commit).lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise ReleaseIntegrityError("candidate commit is invalid")
    entries = validate_inventory(list(inventory))
    package_entries = [entry for entry in entries if _is_package_runtime_path(entry["path"])]
    if not package_entries:
        raise ReleaseIntegrityError("candidate package runtime inventory is empty")

    try:
        output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = output_dir.lstat()
    except OSError as exc:
        raise ReleaseIntegrityError("trusted wheel output directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_mode & 0o022:
        raise ReleaseIntegrityError("trusted wheel output directory is unsafe")

    normalized = TRUSTED_WHEEL_DISTRIBUTION.replace("-", "_")
    dist_info = f"{normalized}-{TRUSTED_WHEEL_VERSION}.dist-info"
    wheel_name = f"{normalized}-{TRUSTED_WHEEL_VERSION}-{TRUSTED_WHEEL_TAG}.whl"
    wheel_path = output_dir / wheel_name
    if wheel_path.exists():
        raise ReleaseIntegrityError("trusted wheel output already exists")

    members: list[tuple[str, bytes]] = []
    for entry in package_entries:
        path = str(entry["path"])
        result = _git(repository, "show", f"{commit}:{path}")
        if result.returncode != 0 or not isinstance(result.stdout, bytes):
            raise ReleaseIntegrityError(f"candidate package object could not be read: {path}")
        data = result.stdout
        if len(data) != entry["size"] or "sha256:" + hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ReleaseIntegrityError(f"candidate package object does not match trusted inventory: {path}")
        members.append((path, data))

    metadata_body = (
        "Metadata-Version: 2.1\n"
        "Name: open-mmi\n"
        f"Version: {TRUSTED_WHEEL_VERSION}\n"
        "Summary: Open vehicle MMI integration framework for Linux\n"
        "License: GPL-3.0-only\n"
        "Requires-Python: >=3.9\n"
        "Requires-Dist: python-can>=4.3,<5\n"
        "Requires-Dist: evdev>=1.6,<2\n"
        "\n"
    ).encode("utf-8")
    wheel_body = (
        "Wheel-Version: 1.0\n"
        "Generator: open-mmi-trusted-wheel-builder-v1\n"
        "Root-Is-Purelib: true\n"
        f"Tag: {TRUSTED_WHEEL_TAG}\n"
        "\n"
    ).encode("utf-8")
    entry_points_body = (
        "[console_scripts]\n"
        + "".join(f"{name} = {target}\n" for name, target in TRUSTED_WHEEL_ENTRY_POINTS)
    ).encode("utf-8")
    members.extend((
        (f"{dist_info}/METADATA", metadata_body),
        (f"{dist_info}/WHEEL", wheel_body),
        (f"{dist_info}/entry_points.txt", entry_points_body),
    ))

    record_rows: list[tuple[str, str, str]] = [
        (name, _wheel_record_digest(data), str(len(data))) for name, data in members
    ]
    record_name = f"{dist_info}/RECORD"
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    for row in record_rows:
        writer.writerow(row)
    writer.writerow((record_name, "", ""))
    record_body = buffer.getvalue().encode("utf-8")
    members.append((record_name, record_body))

    try:
        with zipfile.ZipFile(wheel_path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(members, key=lambda item: item[0]):
                info, payload = _trusted_wheel_member(name, data)
                archive.writestr(info, payload)
        os.chmod(wheel_path, 0o600)
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        try:
            wheel_path.unlink()
        except OSError:
            pass
        raise ReleaseIntegrityError("trusted candidate wheel could not be constructed") from exc

    verify_wheel_against_inventory(wheel_path, entries)
    return wheel_path


def verify_wheel_against_inventory(
    wheel_path: Path,
    inventory: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    inventory_entries = validate_inventory(list(inventory))
    expected_entries = [entry for entry in inventory_entries if _is_package_runtime_path(entry["path"])]
    expected = {entry["path"]: entry for entry in expected_entries}
    seen: dict[str, tuple[str, int]] = {}
    extras: list[str] = []
    try:
        metadata = wheel_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ReleaseIntegrityError("candidate wheel is not a trusted regular file")
        with zipfile.ZipFile(wheel_path) as archive:
            names = [info.filename for info in archive.infolist()]
            if len(names) != len(set(names)):
                raise ReleaseIntegrityError("candidate wheel contains duplicate members")
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = PurePosixPath(info.filename).as_posix()
                if name.startswith("/") or ".." in PurePosixPath(name).parts:
                    raise ReleaseIntegrityError("candidate wheel contains unsafe member path")
                first = PurePosixPath(name).parts[0] if PurePosixPath(name).parts else ""
                if first not in PACKAGE_RUNTIME_ROOTS:
                    continue
                if not _is_package_runtime_path(name):
                    if name not in PACKAGE_SOURCE_ONLY_PATHS and not _ignored_runtime_file(name):
                        extras.append(name)
                    continue
                if name not in expected:
                    extras.append(name)
                    continue
                if info.file_size > MAX_FILE_BYTES:
                    raise ReleaseIntegrityError("candidate wheel runtime member exceeds size limit")
                data = archive.read(info)
                seen[name] = ("sha256:" + hashlib.sha256(data).hexdigest(), len(data))
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, ReleaseIntegrityError):
            raise
        raise ReleaseIntegrityError("candidate wheel could not be inspected") from exc

    missing = sorted(set(expected) - set(seen))
    modified = sorted(
        path for path, entry in expected.items()
        if path in seen and seen[path] != (entry["sha256"], entry["size"])
    )
    extras = sorted(set(extras))
    if missing or modified or extras:
        raise ReleaseIntegrityError(
            "candidate wheel runtime payload does not match trusted Git-object inventory"
        )
    return {
        "matches": True,
        "files_verified": len(expected),
        "inventory_digest": inventory_digest(inventory_entries),
    }


def current_trust_anchors(accepted_state_path: Path, lineage_path: Path) -> tuple[dict[str, Any], str]:
    accepted = read_accepted_state(accepted_state_path)
    if accepted is None:
        raise ReleaseIntegrityError("Accepted Owner Trust State is not established")
    try:
        head = require_lineage_current(accepted, lineage_path)
        summary = lineage_summary(read_transition_lineage(lineage_path))
    except Exception as exc:
        raise ReleaseIntegrityError(f"Trust Transition Lineage is not current: {exc}") from exc
    digest = summary.get("head_record_digest")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ReleaseIntegrityError("Trust Transition Lineage head digest is invalid")
    if head.get("record_digest") and head["record_digest"] != digest:
        raise ReleaseIntegrityError("Trust Transition Lineage head identity is inconsistent")
    return accepted, digest


def default_install_root() -> Path:
    # Production presence wins over the interpreter that happened to invoke the
    # verifier. This prevents an editable checkout from silently standing in for
    # the separately deployed privileged runtime.
    if DEFAULT_INSTALL_ROOT.is_dir():
        return DEFAULT_INSTALL_ROOT
    return Path(__file__).resolve().parents[1]


def default_package_root() -> Path:
    if DEFAULT_INSTALL_ROOT.is_dir():
        return production_package_root(DEFAULT_INSTALL_ROOT)
    return Path(__file__).resolve().parents[1]
