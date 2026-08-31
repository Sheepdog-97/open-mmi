"""User-owned vehicle catalogue creation and revision-safe JSON editing.

The dashboard may create or edit only custom profiles and bindings beneath its
fixed user catalogue root. Maintained catalogue content is immutable and is
accepted solely as a revision-bound starting template.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ui import vehicle_configuration, vehicle_setup


API_VERSION = 1
COPY_ACTION = "copy-maintained-template"
LOAD_ACTION = "load-custom-item"
SAVE_ACTION = "save-custom-item"
MANAGE_ACTION = "manage-custom-item"
IMPORT_ACTION = "import-custom-item"
DEFAULT_LIFECYCLE_LOCK = Path("/run/open-mmi/lifecycle.lock")
MAX_PROVENANCE_BYTES = 64 * 1024
_CATALOGUE_WRITE_LOCK = threading.Lock()
_KIND_LIMITS = {
    "profile": vehicle_setup.MAX_PROFILE_BYTES,
    "bindings": vehicle_setup.MAX_BINDINGS_BYTES,
}


class VehicleCatalogueError(RuntimeError):
    """A custom catalogue request failed closed."""


class VehicleCatalogueConflictError(VehicleCatalogueError):
    """A revision changed or the requested custom destination already exists."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise VehicleCatalogueError(f"Duplicate catalogue JSON field: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise VehicleCatalogueError(f"Invalid catalogue JSON number: {value}")


def _validate_identifier(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not vehicle_setup.IDENTIFIER_RE.fullmatch(value):
        raise VehicleCatalogueError(f"{field} is invalid")
    return value


def _normalize_copy_request(payload: object) -> tuple[str, dict[str, str], str]:
    if not isinstance(payload, Mapping) or set(payload) != {
        "kind",
        "id",
        "template_source",
        "template_id",
        "template_revision",
    }:
        raise VehicleCatalogueError("Invalid custom catalogue copy schema")
    kind = payload.get("kind")
    if kind not in _KIND_LIMITS:
        raise VehicleCatalogueError("Custom catalogue kind must be profile or bindings")
    if payload.get("template_source") != "maintained":
        raise VehicleCatalogueError("Only maintained catalogue items may be used as templates")
    template_id = _validate_identifier(
        payload.get("template_id"),
        field="Maintained template id",
    )
    revision = payload.get("template_revision")
    if not isinstance(revision, str) or not vehicle_configuration.REVISION_RE.fullmatch(revision):
        raise VehicleCatalogueError("Maintained template revision is invalid")
    custom_id = _validate_identifier(payload.get("id"), field="Custom catalogue id")
    return str(kind), {
        "source": "maintained",
        "id": template_id,
        "revision": revision,
    }, custom_id


def _normalize_lifecycle_request(
    payload: object,
) -> tuple[str, str, str, str, Optional[str]]:
    if not isinstance(payload, Mapping):
        raise VehicleCatalogueError("Invalid custom catalogue lifecycle schema")
    action = payload.get("action")
    if action not in {"duplicate", "rename", "delete"}:
        raise VehicleCatalogueError("Custom catalogue lifecycle action is invalid")
    required = {"action", "kind", "source", "id", "expected_revision"}
    if action in {"duplicate", "rename"}:
        required.add("new_id")
    if set(payload) != required:
        raise VehicleCatalogueError("Invalid custom catalogue lifecycle schema")
    kind = payload.get("kind")
    if kind not in _KIND_LIMITS:
        raise VehicleCatalogueError("Custom catalogue kind must be profile or bindings")
    if payload.get("source") != "custom":
        raise VehicleCatalogueError("Only custom catalogue items may be managed")
    identifier = _validate_identifier(payload.get("id"), field="Custom catalogue id")
    revision = payload.get("expected_revision")
    if not isinstance(revision, str) or not vehicle_configuration.REVISION_RE.fullmatch(revision):
        raise VehicleCatalogueError("Expected custom catalogue revision is invalid")
    new_id: Optional[str] = None
    if action in {"duplicate", "rename"}:
        new_id = _validate_identifier(payload.get("new_id"), field="New custom catalogue id")
        if new_id == identifier:
            raise VehicleCatalogueError("New custom catalogue id must be different")
    return str(action), str(kind), identifier, revision, new_id


def _normalize_import_request(payload: object) -> tuple[str, str, bytes]:
    if not isinstance(payload, Mapping) or set(payload) != {"kind", "id", "content"}:
        raise VehicleCatalogueError("Invalid custom catalogue import schema")
    kind = payload.get("kind")
    if kind not in _KIND_LIMITS:
        raise VehicleCatalogueError("Custom catalogue kind must be profile or bindings")
    identifier = _validate_identifier(payload.get("id"), field="Custom catalogue id")
    text = payload.get("content")
    if not isinstance(text, str):
        raise VehicleCatalogueError("Imported custom catalogue content must be text")
    try:
        content = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise VehicleCatalogueError("Imported custom catalogue content is not valid UTF-8") from exc
    return str(kind), identifier, content


def _normalize_custom_request(payload: object, *, save: bool) -> tuple[str, str, Optional[str], Optional[str]]:
    required = {"kind", "source", "id"}
    if save:
        required |= {"expected_revision", "content"}
    if not isinstance(payload, Mapping) or set(payload) != required:
        action = "save" if save else "load"
        raise VehicleCatalogueError(f"Invalid custom catalogue {action} schema")
    kind = payload.get("kind")
    if kind not in _KIND_LIMITS:
        raise VehicleCatalogueError("Custom catalogue kind must be profile or bindings")
    if payload.get("source") != "custom":
        raise VehicleCatalogueError("Only custom catalogue items may be edited")
    identifier = _validate_identifier(payload.get("id"), field="Custom catalogue id")
    if not save:
        return str(kind), identifier, None, None
    revision = payload.get("expected_revision")
    if not isinstance(revision, str) or not vehicle_configuration.REVISION_RE.fullmatch(revision):
        raise VehicleCatalogueError("Expected custom catalogue revision is invalid")
    content = payload.get("content")
    if not isinstance(content, str):
        raise VehicleCatalogueError("Custom catalogue content must be text")
    return str(kind), identifier, revision, content


def _parse_catalogue_content(kind: str, content: bytes) -> tuple[Any, dict[str, Any]]:
    if len(content) > _KIND_LIMITS[kind]:
        raise VehicleCatalogueError("Custom catalogue content exceeds the size limit")
    try:
        document = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VehicleCatalogueError("Custom catalogue content is not valid UTF-8 JSON") from exc
    validation = (
        vehicle_setup.validate_profile(document)
        if kind == "profile"
        else vehicle_setup.validate_bindings(document)
    )
    return document, validation


def _raise_invalid_custom(kind: str, validation: Mapping[str, Any]) -> None:
    errors = validation.get("errors")
    first = errors[0] if isinstance(errors, list) and errors else {}
    path = str(first.get("path") or "$") if isinstance(first, Mapping) else "$"
    message = str(first.get("message") or "validation failed") if isinstance(first, Mapping) else "validation failed"
    label = "profile" if kind == "profile" else "bindings"
    raise VehicleCatalogueError(f"Custom {label} is not valid at {path}: {message}")


def _custom_location(
    roots: vehicle_setup.CatalogueRoots,
    kind: str,
    identifier: str,
    *,
    create: bool,
) -> tuple[Path, str]:
    # Resolve first so every existing component is checked for symlinks beneath
    # the fixed custom root before a file descriptor is opened.
    vehicle_setup.resolve_catalogue_path(roots, kind, "custom", identifier)
    _ensure_user_directory(roots.custom, create=create)
    if kind == "profile":
        profiles = roots.custom / "vehicles"
        _ensure_user_directory(profiles, create=create)
        directory = profiles / identifier
        _ensure_user_directory(directory, create=create)
        return directory, "config.json"
    directory = roots.custom / "bindings"
    _ensure_user_directory(directory, create=create)
    return directory, f"{identifier}.json"


def _open_user_directory(path: Path) -> int:
    _ensure_user_directory(path, create=False)
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise VehicleCatalogueError("Custom catalogue directory cannot be opened") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        os.close(descriptor)
        raise VehicleCatalogueError("Custom catalogue directory is untrusted")
    return descriptor


def _read_custom_at(
    directory_fd: int,
    name: str,
    kind: str,
) -> tuple[bytes, str, dict[str, Any], tuple[int, int, int, int]]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise VehicleCatalogueError("Custom catalogue item is untrusted")
        if metadata.st_size > _KIND_LIMITS[kind]:
            raise VehicleCatalogueError("Custom catalogue item exceeds the size limit")
        chunks: list[bytes] = []
        remaining = _KIND_LIMITS[kind] + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
    except FileNotFoundError as exc:
        raise VehicleCatalogueError("Custom catalogue item was not found") from exc
    except VehicleCatalogueError:
        raise
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue item cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > _KIND_LIMITS[kind]:
        raise VehicleCatalogueError("Custom catalogue item exceeds the size limit")
    _document, validation = _parse_catalogue_content(kind, content)
    revision = "sha256:" + hashlib.sha256(content).hexdigest()
    token = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_uid,
        metadata.st_nlink,
        stat.S_IMODE(metadata.st_mode),
    )
    return content, revision, validation, token


def _write_replacement_at(
    directory_fd: int,
    name: str,
    content: bytes,
    expected_token: tuple[int, ...],
) -> None:
    temporary_name = f".open-mmi-save-{uuid.uuid4().hex}.tmp"
    descriptor = -1
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        os.fchmod(descriptor, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short custom catalogue write")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1

        try:
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise VehicleCatalogueConflictError(
                "Custom catalogue item changed; reload it before saving",
                "custom-stale",
            ) from exc
        current_token = (
            current.st_dev,
            current.st_ino,
            current.st_size,
            current.st_mtime_ns,
            current.st_ctime_ns,
            current.st_uid,
            current.st_nlink,
            stat.S_IMODE(current.st_mode),
        )
        if current_token != expected_token:
            raise VehicleCatalogueConflictError(
                "Custom catalogue item changed; reload it before saving",
                "custom-stale",
            )
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except VehicleCatalogueError:
        raise
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue item could not be saved") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except OSError:
            pass


def _read_template(
    roots: vehicle_setup.CatalogueRoots,
    kind: str,
    template: Mapping[str, str],
) -> tuple[bytes, str]:
    path = vehicle_setup.resolve_catalogue_path(
        roots,
        kind,
        "maintained",
        template["id"],
    )
    descriptor = -1
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        maximum = _KIND_LIMITS[kind]
        if not stat.S_ISREG(metadata.st_mode):
            raise VehicleCatalogueError("Maintained template must be a regular file")
        if (
            metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_nlink != 1
            or metadata.st_mode & 0o022
        ):
            raise VehicleCatalogueError("Maintained template is untrusted")
        if metadata.st_size > maximum:
            raise VehicleCatalogueError("Maintained template exceeds the size limit")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
    except OSError as exc:
        raise VehicleCatalogueError("Maintained template cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > _KIND_LIMITS[kind]:
        raise VehicleCatalogueError("Maintained template exceeds the size limit")
    actual_revision = "sha256:" + hashlib.sha256(content).hexdigest()
    if actual_revision != template["revision"]:
        raise VehicleCatalogueConflictError(
            "Maintained template changed; refresh Vehicle Setup before copying",
            "template-stale",
        )
    try:
        document = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VehicleCatalogueError("Maintained template is not valid UTF-8 JSON") from exc
    validation = (
        vehicle_setup.validate_profile(document)
        if kind == "profile"
        else vehicle_setup.validate_bindings(document, maintained=True)
    )
    if validation.get("valid") is not True:
        raise VehicleCatalogueError("Maintained template is not valid")
    return content, actual_revision


def _ensure_user_directory(path: Path, *, create: bool = True) -> None:
    if not path.is_absolute():
        raise VehicleCatalogueError("Custom catalogue root must be absolute")
    try:
        if create:
            existed = path.exists()
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not existed:
                path.chmod(0o700)
        metadata = path.lstat()
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise VehicleCatalogueError("Custom catalogue directory is untrusted")


def _remove_new_file(directory: Path, name: str) -> None:
    directory_fd = -1
    try:
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        os.unlink(name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def _display_name(identifier: str) -> str:
    words = identifier.replace("-", "_").split("_")
    return " ".join(word.capitalize() for word in words if word) or identifier


def _provenance_content(
    kind: str,
    custom_id: str,
    template: Mapping[str, str],
) -> bytes:
    try:
        from ui.web_dashboard import versioning

        build_id = versioning.resolve_build_id()
    except Exception:
        build_id = "unknown"
    payload = {
        "schema_version": 1,
        "kind": kind,
        "id": custom_id,
        "display_name": _display_name(custom_id),
        "template": {
            "source": "maintained",
            "id": template["id"],
            "open_mmi_version": build_id,
            "revision": template["revision"],
        },
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _import_provenance_content(kind: str, custom_id: str) -> bytes:
    payload = {
        "schema_version": 1,
        "kind": kind,
        "id": custom_id,
        "display_name": _display_name(custom_id),
        "origin": {"type": "import"},
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _provenance_destination(
    root: Path,
    kind: str,
) -> Path:
    provenance_root = root / ".open-mmi-provenance"
    _ensure_user_directory(provenance_root)
    directory = provenance_root / kind
    _ensure_user_directory(directory)
    return directory


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue destination cannot be inspected") from exc
    return True


def _write_new_file(directory: Path, name: str, content: bytes) -> None:
    _ensure_user_directory(directory)
    directory_fd = -1
    temporary_name = f".open-mmi-copy-{uuid.uuid4().hex}.tmp"
    try:
        directory_fd = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            existing = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            raise VehicleCatalogueConflictError(
                "A custom catalogue item with that id already exists",
                "custom-exists",
            )
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short custom catalogue write")
                view = view[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError as exc:
            raise VehicleCatalogueConflictError(
                "A custom catalogue item with that id already exists",
                "custom-exists",
            ) from exc
        os.unlink(temporary_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    except VehicleCatalogueError:
        raise
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue item could not be created") from exc
    finally:
        if directory_fd >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
            os.close(directory_fd)


def copy_maintained_template(
    payload: object,
    *,
    roots: Optional[vehicle_setup.CatalogueRoots] = None,
) -> dict[str, Any]:
    """Create one revision-bound custom copy without modifying maintained files."""

    kind, template, custom_id = _normalize_copy_request(payload)
    selected_roots = roots or vehicle_setup.default_roots()
    content, revision = _read_template(selected_roots, kind, template)
    _ensure_user_directory(selected_roots.custom)

    provenance = _provenance_content(kind, custom_id, template)
    provenance_name = f"{custom_id}.json"
    provenance_path = (
        selected_roots.custom
        / ".open-mmi-provenance"
        / kind
        / provenance_name
    )
    if kind == "profile":
        destination = selected_roots.custom / "vehicles" / custom_id
        content_path = destination / "config.json"
    else:
        destination = selected_roots.custom / "bindings"
        content_path = destination / f"{custom_id}.json"
    if _path_present(content_path) or _path_present(provenance_path) or (
        kind == "profile" and _path_present(destination)
    ):
        raise VehicleCatalogueConflictError(
            "A custom catalogue item with that id already exists",
            "custom-exists",
        )

    provenance_directory = _provenance_destination(selected_roots.custom, kind)

    if kind == "profile":
        profiles = selected_roots.custom / "vehicles"
        _ensure_user_directory(profiles)
        destination = profiles / custom_id
        try:
            destination.mkdir(mode=0o700)
            destination.chmod(0o700)
        except FileExistsError as exc:
            raise VehicleCatalogueConflictError(
                "A custom catalogue item with that id already exists",
                "custom-exists",
            ) from exc
        except OSError as exc:
            raise VehicleCatalogueError("Custom profile directory could not be created") from exc
        try:
            _ensure_user_directory(destination, create=False)
            _write_new_file(destination, "config.json", content)
            _write_new_file(provenance_directory, provenance_name, provenance)
        except BaseException:
            _remove_new_file(destination, "config.json")
            _remove_new_file(provenance_directory, provenance_name)
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
    else:
        bindings = selected_roots.custom / "bindings"
        _ensure_user_directory(bindings)
        binding_name = f"{custom_id}.json"
        try:
            _write_new_file(bindings, binding_name, content)
            _write_new_file(provenance_directory, provenance_name, provenance)
        except BaseException:
            _remove_new_file(bindings, binding_name)
            _remove_new_file(provenance_directory, provenance_name)
            raise

    return {
        "ok": True,
        "api_version": API_VERSION,
        "action": COPY_ACTION,
        "kind": kind,
        "template": dict(template),
        "custom": {
            "source": "custom",
            "id": custom_id,
            "revision": revision,
        },
    }


def import_custom_item(
    payload: object,
    *,
    roots: Optional[vehicle_setup.CatalogueRoots] = None,
) -> dict[str, Any]:
    """Validate and create one new custom JSON item without applying it."""

    kind, custom_id, content = _normalize_import_request(payload)
    _document, validation = _parse_catalogue_content(kind, content)
    if validation.get("valid") is not True:
        _raise_invalid_custom(kind, validation)
    selected_roots = roots or vehicle_setup.default_roots()
    _ensure_user_directory(selected_roots.custom)

    provenance = _import_provenance_content(kind, custom_id)
    provenance_name = f"{custom_id}.json"
    provenance_path = (
        selected_roots.custom
        / ".open-mmi-provenance"
        / kind
        / provenance_name
    )
    if kind == "profile":
        destination = selected_roots.custom / "vehicles" / custom_id
        content_path = destination / "config.json"
    else:
        destination = selected_roots.custom / "bindings"
        content_path = destination / f"{custom_id}.json"
    if _path_present(content_path) or _path_present(provenance_path) or (
        kind == "profile" and _path_present(destination)
    ):
        raise VehicleCatalogueConflictError(
            "A custom catalogue item with that id already exists",
            "custom-exists",
        )

    provenance_directory = _provenance_destination(selected_roots.custom, kind)
    with _CATALOGUE_WRITE_LOCK:
        if kind == "profile":
            profiles = selected_roots.custom / "vehicles"
            _ensure_user_directory(profiles)
            destination = profiles / custom_id
            try:
                destination.mkdir(mode=0o700)
                destination.chmod(0o700)
            except FileExistsError as exc:
                raise VehicleCatalogueConflictError(
                    "A custom catalogue item with that id already exists",
                    "custom-exists",
                ) from exc
            except OSError as exc:
                raise VehicleCatalogueError("Custom profile directory could not be created") from exc
            try:
                _ensure_user_directory(destination, create=False)
                _write_new_file(destination, "config.json", content)
                _write_new_file(provenance_directory, provenance_name, provenance)
            except BaseException:
                _remove_new_file(destination, "config.json")
                _remove_new_file(provenance_directory, provenance_name)
                try:
                    destination.rmdir()
                except OSError:
                    pass
                raise
        else:
            bindings = selected_roots.custom / "bindings"
            _ensure_user_directory(bindings)
            binding_name = f"{custom_id}.json"
            try:
                _write_new_file(bindings, binding_name, content)
                _write_new_file(provenance_directory, provenance_name, provenance)
            except BaseException:
                _remove_new_file(bindings, binding_name)
                _remove_new_file(provenance_directory, provenance_name)
                raise

    revision = "sha256:" + hashlib.sha256(content).hexdigest()
    return {
        "ok": True,
        "api_version": API_VERSION,
        "action": IMPORT_ACTION,
        "kind": kind,
        "custom": {"source": "custom", "id": custom_id, "revision": revision},
        "validation": validation,
        "applied": False,
    }


def load_custom_item(
    payload: object,
    *,
    roots: Optional[vehicle_setup.CatalogueRoots] = None,
) -> dict[str, Any]:
    """Load exact user-owned JSON for editing; maintained items are never exposed."""

    kind, identifier, _revision, _content = _normalize_custom_request(payload, save=False)
    selected_roots = roots or vehicle_setup.default_roots()
    directory, name = _custom_location(selected_roots, kind, identifier, create=False)
    directory_fd = _open_user_directory(directory)
    try:
        content, revision, validation, _token = _read_custom_at(directory_fd, name, kind)
    finally:
        os.close(directory_fd)
    return {
        "ok": True,
        "api_version": API_VERSION,
        "action": LOAD_ACTION,
        "kind": kind,
        "custom": {"source": "custom", "id": identifier, "revision": revision},
        "content": content.decode("utf-8"),
        "validation": validation,
    }


def save_custom_item(
    payload: object,
    *,
    roots: Optional[vehicle_setup.CatalogueRoots] = None,
) -> dict[str, Any]:
    """Validate and atomically save one exact-revision custom catalogue item."""

    kind, identifier, expected_revision, text = _normalize_custom_request(payload, save=True)
    assert expected_revision is not None and text is not None
    try:
        content = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise VehicleCatalogueError("Custom catalogue content is not valid UTF-8") from exc
    _document, validation = _parse_catalogue_content(kind, content)
    if validation.get("valid") is not True:
        _raise_invalid_custom(kind, validation)

    selected_roots = roots or vehicle_setup.default_roots()
    directory, name = _custom_location(selected_roots, kind, identifier, create=False)
    with _CATALOGUE_WRITE_LOCK:
        directory_fd = _open_user_directory(directory)
        try:
            _current, current_revision, _current_validation, token = _read_custom_at(
                directory_fd, name, kind
            )
            if current_revision != expected_revision:
                raise VehicleCatalogueConflictError(
                    "Custom catalogue item changed; reload it before saving",
                    "custom-stale",
                )
            _write_replacement_at(directory_fd, name, content, token)
        finally:
            os.close(directory_fd)

    revision = "sha256:" + hashlib.sha256(content).hexdigest()
    return {
        "ok": True,
        "api_version": API_VERSION,
        "action": SAVE_ACTION,
        "kind": kind,
        "custom": {"source": "custom", "id": identifier, "revision": revision},
        "validation": validation,
        "applied": False,
    }


def _metadata_token(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_uid,
        metadata.st_nlink,
        stat.S_IMODE(metadata.st_mode),
    )


def _verify_token_at(directory_fd: int, name: str, expected: tuple[int, ...]) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise VehicleCatalogueConflictError(
            "Custom catalogue item changed; refresh Vehicle Setup and try again",
            "custom-stale",
        ) from exc
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue item cannot be inspected") from exc
    if _metadata_token(metadata) != expected:
        raise VehicleCatalogueConflictError(
            "Custom catalogue item changed; refresh Vehicle Setup and try again",
            "custom-stale",
        )


def _read_private_file_at(
    directory_fd: int,
    name: str,
    *,
    maximum_bytes: int,
    missing_ok: bool = False,
) -> tuple[Optional[bytes], Optional[tuple[int, ...]]]:
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise VehicleCatalogueError("Custom catalogue provenance is untrusted")
        if metadata.st_size > maximum_bytes:
            raise VehicleCatalogueError("Custom catalogue provenance exceeds the size limit")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
    except FileNotFoundError:
        if missing_ok:
            return None, None
        raise VehicleCatalogueError("Custom catalogue provenance was not found")
    except VehicleCatalogueError:
        raise
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue provenance cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > maximum_bytes:
        raise VehicleCatalogueError("Custom catalogue provenance exceeds the size limit")
    return content, _metadata_token(metadata)


def _provenance_directory_optional(
    root: Path,
    kind: str,
) -> Optional[Path]:
    provenance_root = root / ".open-mmi-provenance"
    kind_directory = provenance_root / kind
    if not _path_present(provenance_root):
        return None
    _ensure_user_directory(provenance_root, create=False)
    if not _path_present(kind_directory):
        return None
    _ensure_user_directory(kind_directory, create=False)
    return kind_directory


def _read_provenance_optional(
    root: Path,
    kind: str,
    identifier: str,
) -> tuple[Optional[dict[str, Any]], Optional[tuple[int, ...]]]:
    directory = _provenance_directory_optional(root, kind)
    if directory is None:
        return None, None
    directory_fd = _open_user_directory(directory)
    try:
        content, token = _read_private_file_at(
            directory_fd,
            f"{identifier}.json",
            maximum_bytes=MAX_PROVENANCE_BYTES,
            missing_ok=True,
        )
    finally:
        os.close(directory_fd)
    if content is None:
        return None, None
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VehicleCatalogueError("Custom catalogue provenance is invalid") from exc
    if not isinstance(value, Mapping):
        raise VehicleCatalogueError("Custom catalogue provenance is invalid")
    return dict(value), token


def _lifecycle_provenance(
    kind: str,
    identifier: str,
    *,
    source_id: str,
    source_revision: str,
    existing: Optional[Mapping[str, Any]],
    action: str,
) -> bytes:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload = dict(existing or {})
    payload.update(
        {
            "schema_version": 1,
            "kind": kind,
            "id": identifier,
            "display_name": _display_name(identifier),
        }
    )
    if action == "duplicate":
        payload["created_at"] = now
        payload["derived_from"] = {
            "source": "custom",
            "id": source_id,
            "revision": source_revision,
        }
        payload.pop("renamed_at", None)
        payload.pop("previous_ids", None)
    elif action == "rename":
        previous = payload.get("previous_ids")
        previous_ids = [item for item in previous if isinstance(item, str)] if isinstance(previous, list) else []
        if source_id not in previous_ids:
            previous_ids.append(source_id)
        payload["previous_ids"] = previous_ids[-16:]
        payload["renamed_at"] = now
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


@contextmanager
def _lifecycle_transaction(path: Path):
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise VehicleCatalogueError("Open MMI lifecycle lock is unavailable") from exc
    require_root = path == DEFAULT_LIFECYCLE_LOCK
    trusted_owner = metadata.st_uid == 0 if require_root else metadata.st_uid in {0, os.geteuid()}
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or not trusted_owner
        or metadata.st_mode & 0o022
    ):
        os.close(descriptor)
        raise VehicleCatalogueError("Open MMI lifecycle lock is untrusted")
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(descriptor)
        raise VehicleCatalogueConflictError(
            "Another Open MMI lifecycle operation is active",
            "lifecycle-busy",
        ) from exc
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _active_catalogue_identity(
    kind: str,
    roots: vehicle_setup.CatalogueRoots,
    active: Optional[Mapping[str, Any]],
) -> dict[str, str]:
    value: Mapping[str, Any]
    if active is None:
        status = vehicle_setup.status_payload(roots)
        candidate = status.get("active")
        if not isinstance(candidate, Mapping):
            raise VehicleCatalogueError("Active vehicle setup cannot be verified")
        value = candidate
    else:
        value = active
    key = "vehicle" if kind == "profile" else "bindings"
    identity = value.get(key)
    if not isinstance(identity, Mapping):
        raise VehicleCatalogueError("Active vehicle setup cannot be verified")
    source = identity.get("source")
    identifier = identity.get("id")
    if not isinstance(source, str) or not isinstance(identifier, str):
        raise VehicleCatalogueError("Active vehicle setup cannot be verified")
    return {"source": source, "id": identifier}


def _assert_inactive(
    kind: str,
    identifier: str,
    roots: vehicle_setup.CatalogueRoots,
    active: Optional[Mapping[str, Any]],
) -> None:
    identity = _active_catalogue_identity(kind, roots, active)
    if identity == {"source": "custom", "id": identifier}:
        raise VehicleCatalogueConflictError(
            "Active custom catalogue items cannot be renamed or deleted",
            "custom-active",
        )


def _assert_profile_directory_exact(directory_fd: int) -> None:
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise VehicleCatalogueError("Custom profile directory cannot be inspected") from exc
    if names != ["config.json"]:
        raise VehicleCatalogueError("Custom profile directory contains unsupported files")


def _custom_destination_exists(
    roots: vehicle_setup.CatalogueRoots,
    kind: str,
    identifier: str,
) -> bool:
    if kind == "profile":
        destination = roots.custom / "vehicles" / identifier
    else:
        destination = roots.custom / "bindings" / f"{identifier}.json"
    provenance = roots.custom / ".open-mmi-provenance" / kind / f"{identifier}.json"
    return _path_present(destination) or _path_present(provenance)


def _duplicate_custom_item(
    roots: vehicle_setup.CatalogueRoots,
    kind: str,
    identifier: str,
    new_id: str,
    content: bytes,
    revision: str,
    provenance: Optional[Mapping[str, Any]],
) -> None:
    if _custom_destination_exists(roots, kind, new_id):
        raise VehicleCatalogueConflictError(
            "A custom catalogue item with that id already exists",
            "custom-exists",
        )
    provenance_content = _lifecycle_provenance(
        kind,
        new_id,
        source_id=identifier,
        source_revision=revision,
        existing=provenance,
        action="duplicate",
    )
    provenance_directory = _provenance_destination(roots.custom, kind)
    provenance_name = f"{new_id}.json"
    if kind == "profile":
        profiles = roots.custom / "vehicles"
        _ensure_user_directory(profiles)
        destination = profiles / new_id
        try:
            destination.mkdir(mode=0o700)
            destination.chmod(0o700)
        except FileExistsError as exc:
            raise VehicleCatalogueConflictError(
                "A custom catalogue item with that id already exists",
                "custom-exists",
            ) from exc
        except OSError as exc:
            raise VehicleCatalogueError("Custom profile directory could not be created") from exc
        try:
            _ensure_user_directory(destination, create=False)
            _write_new_file(destination, "config.json", content)
            _write_new_file(provenance_directory, provenance_name, provenance_content)
        except BaseException:
            _remove_new_file(destination, "config.json")
            _remove_new_file(provenance_directory, provenance_name)
            try:
                destination.rmdir()
            except OSError:
                pass
            raise
    else:
        bindings = roots.custom / "bindings"
        _ensure_user_directory(bindings)
        binding_name = f"{new_id}.json"
        try:
            _write_new_file(bindings, binding_name, content)
            _write_new_file(provenance_directory, provenance_name, provenance_content)
        except BaseException:
            _remove_new_file(bindings, binding_name)
            _remove_new_file(provenance_directory, provenance_name)
            raise


def _rename_custom_item(
    roots: vehicle_setup.CatalogueRoots,
    kind: str,
    identifier: str,
    new_id: str,
    token: tuple[int, ...],
    provenance: Optional[Mapping[str, Any]],
) -> None:
    if _custom_destination_exists(roots, kind, new_id):
        raise VehicleCatalogueConflictError(
            "A custom catalogue item with that id already exists",
            "custom-exists",
        )
    if kind == "profile":
        source_directory = roots.custom / "vehicles" / identifier
        source_fd = _open_user_directory(source_directory)
        parent = roots.custom / "vehicles"
        old_name = identifier
        new_name = new_id
        content_name = "config.json"
    else:
        source_fd = -1
        parent = roots.custom / "bindings"
        old_name = f"{identifier}.json"
        new_name = f"{new_id}.json"
        content_name = old_name
    parent_fd = _open_user_directory(parent)
    provenance_directory = _provenance_directory_optional(roots.custom, kind)
    provenance_fd = _open_user_directory(provenance_directory) if provenance_directory else -1
    item_moved = False
    provenance_moved = False
    try:
        if kind == "profile":
            _assert_profile_directory_exact(source_fd)
            _verify_token_at(source_fd, content_name, token)
        else:
            _verify_token_at(parent_fd, content_name, token)
        try:
            os.stat(new_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise VehicleCatalogueConflictError(
                "A custom catalogue item with that id already exists",
                "custom-exists",
            )
        os.rename(old_name, new_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        item_moved = True
        os.fsync(parent_fd)

        if provenance is not None:
            assert provenance_fd >= 0
            old_provenance = f"{identifier}.json"
            new_provenance = f"{new_id}.json"
            os.rename(
                old_provenance,
                new_provenance,
                src_dir_fd=provenance_fd,
                dst_dir_fd=provenance_fd,
            )
            provenance_moved = True
            os.fsync(provenance_fd)
            updated = _lifecycle_provenance(
                kind,
                new_id,
                source_id=identifier,
                source_revision="",
                existing=provenance,
                action="rename",
            )
            _current, provenance_token = _read_private_file_at(
                provenance_fd,
                new_provenance,
                maximum_bytes=MAX_PROVENANCE_BYTES,
            )
            assert provenance_token is not None
            _write_replacement_at(provenance_fd, new_provenance, updated, provenance_token)
    except BaseException:
        if provenance_moved and provenance_fd >= 0:
            try:
                os.rename(
                    f"{new_id}.json",
                    f"{identifier}.json",
                    src_dir_fd=provenance_fd,
                    dst_dir_fd=provenance_fd,
                )
                os.fsync(provenance_fd)
            except OSError:
                pass
        if item_moved:
            try:
                os.rename(new_name, old_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        raise
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if provenance_fd >= 0:
            os.close(provenance_fd)
        os.close(parent_fd)


def _delete_custom_item(
    roots: vehicle_setup.CatalogueRoots,
    kind: str,
    identifier: str,
    token: tuple[int, ...],
    provenance: Optional[Mapping[str, Any]],
) -> None:
    suffix = uuid.uuid4().hex
    if kind == "profile":
        source_directory = roots.custom / "vehicles" / identifier
        source_fd = _open_user_directory(source_directory)
        parent = roots.custom / "vehicles"
        old_name = identifier
        hidden_name = f".open-mmi-delete-{suffix}"
        content_name = "config.json"
    else:
        source_fd = -1
        parent = roots.custom / "bindings"
        old_name = f"{identifier}.json"
        hidden_name = f".open-mmi-delete-{suffix}.json"
        content_name = old_name
    parent_fd = _open_user_directory(parent)
    provenance_directory = _provenance_directory_optional(roots.custom, kind)
    provenance_fd = _open_user_directory(provenance_directory) if provenance_directory else -1
    item_moved = False
    provenance_moved = False
    hidden_provenance = f".open-mmi-delete-{suffix}.json"
    try:
        if kind == "profile":
            _assert_profile_directory_exact(source_fd)
            _verify_token_at(source_fd, content_name, token)
        else:
            _verify_token_at(parent_fd, content_name, token)
        os.rename(old_name, hidden_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        item_moved = True
        os.fsync(parent_fd)
        if provenance is not None:
            assert provenance_fd >= 0
            os.rename(
                f"{identifier}.json",
                hidden_provenance,
                src_dir_fd=provenance_fd,
                dst_dir_fd=provenance_fd,
            )
            provenance_moved = True
            os.fsync(provenance_fd)
    except BaseException:
        if provenance_moved and provenance_fd >= 0:
            try:
                os.rename(
                    hidden_provenance,
                    f"{identifier}.json",
                    src_dir_fd=provenance_fd,
                    dst_dir_fd=provenance_fd,
                )
                os.fsync(provenance_fd)
            except OSError:
                pass
        if item_moved:
            try:
                os.rename(hidden_name, old_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        raise

    try:
        if kind == "profile":
            os.unlink("config.json", dir_fd=source_fd)
            os.fsync(source_fd)
            os.close(source_fd)
            source_fd = -1
            os.rmdir(hidden_name, dir_fd=parent_fd)
        else:
            os.unlink(hidden_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        if provenance_moved and provenance_fd >= 0:
            os.unlink(hidden_provenance, dir_fd=provenance_fd)
            os.fsync(provenance_fd)
    except OSError as exc:
        raise VehicleCatalogueError("Custom catalogue item could not be deleted") from exc
    finally:
        if source_fd >= 0:
            os.close(source_fd)
        if provenance_fd >= 0:
            os.close(provenance_fd)
        os.close(parent_fd)


def manage_custom_item(
    payload: object,
    *,
    roots: Optional[vehicle_setup.CatalogueRoots] = None,
    active: Optional[Mapping[str, Any]] = None,
    lifecycle_lock: Path = DEFAULT_LIFECYCLE_LOCK,
) -> dict[str, Any]:
    """Duplicate, rename or delete one exact-revision custom catalogue item."""

    action, kind, identifier, expected_revision, new_id = _normalize_lifecycle_request(payload)
    selected_roots = roots or vehicle_setup.default_roots()
    with _CATALOGUE_WRITE_LOCK, _lifecycle_transaction(lifecycle_lock):
        directory, name = _custom_location(selected_roots, kind, identifier, create=False)
        directory_fd = _open_user_directory(directory)
        try:
            content, revision, validation, token = _read_custom_at(directory_fd, name, kind)
            if kind == "profile":
                _assert_profile_directory_exact(directory_fd)
        finally:
            os.close(directory_fd)
        if revision != expected_revision:
            raise VehicleCatalogueConflictError(
                "Custom catalogue item changed; refresh Vehicle Setup and try again",
                "custom-stale",
            )
        if action == "duplicate" and validation.get("valid") is not True:
            _raise_invalid_custom(kind, validation)
        provenance, _provenance_token = _read_provenance_optional(
            selected_roots.custom,
            kind,
            identifier,
        )

        if action == "duplicate":
            assert new_id is not None
            verify_directory, verify_name = _custom_location(
                selected_roots, kind, identifier, create=False
            )
            verify_fd = _open_user_directory(verify_directory)
            try:
                if kind == "profile":
                    _assert_profile_directory_exact(verify_fd)
                _verify_token_at(verify_fd, verify_name, token)
            finally:
                os.close(verify_fd)
            _duplicate_custom_item(
                selected_roots,
                kind,
                identifier,
                new_id,
                content,
                revision,
                provenance,
            )
            return {
                "ok": True,
                "api_version": API_VERSION,
                "action": MANAGE_ACTION,
                "operation": "duplicate",
                "kind": kind,
                "source": {"source": "custom", "id": identifier, "revision": revision},
                "custom": {"source": "custom", "id": new_id, "revision": revision},
                "applied": False,
            }

        _assert_inactive(kind, identifier, selected_roots, active)
        if action == "rename":
            assert new_id is not None
            _rename_custom_item(
                selected_roots,
                kind,
                identifier,
                new_id,
                token,
                provenance,
            )
            return {
                "ok": True,
                "api_version": API_VERSION,
                "action": MANAGE_ACTION,
                "operation": "rename",
                "kind": kind,
                "source": {"source": "custom", "id": identifier, "revision": revision},
                "custom": {"source": "custom", "id": new_id, "revision": revision},
                "applied": False,
            }

        _delete_custom_item(
            selected_roots,
            kind,
            identifier,
            token,
            provenance,
        )
        return {
            "ok": True,
            "api_version": API_VERSION,
            "action": MANAGE_ACTION,
            "operation": "delete",
            "kind": kind,
            "deleted": {"source": "custom", "id": identifier, "revision": revision},
            "applied": False,
        }
