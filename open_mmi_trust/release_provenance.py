"""Pinned release-signer provenance v1 for Open MMI.

The provenance root is owner-established local trust state.  Candidate Git commit
signatures are verified offline by already-installed code against exactly that
pinned OpenPGP public key in an isolated temporary GnuPG home.  Verification does
not use GitHub signature badges, the caller's keyring, Web-of-Trust decisions, or
network key discovery.

v1 is intentionally create-once: there is no production signer-rotation primitive.
A future key change is a trust-boundary transition and must not be smuggled through
an ordinary software update.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


PROVENANCE_SCHEMA_VERSION = 1
PROVENANCE_ROOT_ID = "org.open-mmi.release-signer-root"
DEFAULT_PROVENANCE_ROOT_PATH = Path("/var/lib/open-mmi/trust/release-signer-root.v1.json")
DEFAULT_SOURCE_DESCRIPTOR = Path("/opt/open-mmi/.update-source.json")
GPG_PROGRAM = Path("/usr/bin/gpg")
GIT_PROGRAM = Path("/usr/bin/git")
MAX_PUBLIC_KEY_BYTES = 512 * 1024
MAX_GPG_OUTPUT_BYTES = 1024 * 1024
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FINGERPRINT_RE = re.compile(r"^(?:[0-9A-F]{40}|[0-9A-F]{64})$")


class ReleaseProvenanceError(RuntimeError):
    """Pinned signer evidence is invalid, unavailable, or contradictory."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ReleaseProvenanceError("release signer root timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ReleaseProvenanceError("release signer root timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ReleaseProvenanceError("release signer root timestamp is invalid")
    return value


def _require_exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    unknown = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unknown:
        raise ReleaseProvenanceError(f"{label} contains unknown keys: {', '.join(unknown)}")
    if missing:
        raise ReleaseProvenanceError(f"{label} is missing keys: {', '.join(missing)}")


def _normalize_fingerprint(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ReleaseProvenanceError(f"{label} is invalid")
    fingerprint = value.upper()
    if value != fingerprint or not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise ReleaseProvenanceError(f"{label} is invalid")
    return fingerprint


def validate_provenance_root(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ReleaseProvenanceError("release signer root must be an object")
    keys = {
        "schema_version",
        "root_id",
        "established_at",
        "root_source",
        "algorithm",
        "primary_fingerprint",
        "signing_fingerprints",
        "public_key_base64",
        "public_key_sha256",
        "baseline_commit",
        "baseline_integrity_state_digest",
        "history_before_baseline",
    }
    _require_exact_keys(payload, keys, "release signer root")
    if payload["schema_version"] != PROVENANCE_SCHEMA_VERSION:
        raise ReleaseProvenanceError("unsupported release signer root schema_version")
    if payload["root_id"] != PROVENANCE_ROOT_ID:
        raise ReleaseProvenanceError("unexpected release signer root id")
    if payload["root_source"] != "owner-pinned-local-key":
        raise ReleaseProvenanceError("release signer root source is invalid")
    if payload["algorithm"] != "openpgp":
        raise ReleaseProvenanceError("release signer root algorithm is invalid")
    if payload["history_before_baseline"] != "unverified":
        raise ReleaseProvenanceError("release signer root historical baseline is invalid")

    primary = _normalize_fingerprint(payload["primary_fingerprint"], "primary fingerprint")
    signing_payload = payload["signing_fingerprints"]
    if not isinstance(signing_payload, list) or not signing_payload:
        raise ReleaseProvenanceError("release signer root has no signing fingerprints")
    signing = [
        _normalize_fingerprint(value, "signing fingerprint") for value in signing_payload
    ]
    if signing != sorted(signing) or len(signing) != len(set(signing)):
        raise ReleaseProvenanceError("release signer fingerprints must be sorted and unique")

    encoded = payload["public_key_base64"]
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_PUBLIC_KEY_BYTES * 2:
        raise ReleaseProvenanceError("release signer public key encoding is invalid")
    try:
        key_bytes = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ReleaseProvenanceError("release signer public key encoding is invalid") from exc
    if not key_bytes or len(key_bytes) > MAX_PUBLIC_KEY_BYTES:
        raise ReleaseProvenanceError("release signer public key is empty or too large")
    key_digest = "sha256:" + hashlib.sha256(key_bytes).hexdigest()
    if payload["public_key_sha256"] != key_digest:
        raise ReleaseProvenanceError("release signer public key digest does not match key material")

    commit = payload["baseline_commit"]
    if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
        raise ReleaseProvenanceError("release signer baseline commit is invalid")
    integrity_digest = payload["baseline_integrity_state_digest"]
    if not isinstance(integrity_digest, str) or not _SHA256_RE.fullmatch(integrity_digest):
        raise ReleaseProvenanceError("release signer baseline integrity-state digest is invalid")

    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "root_id": PROVENANCE_ROOT_ID,
        "established_at": _validate_timestamp(payload["established_at"]),
        "root_source": "owner-pinned-local-key",
        "algorithm": "openpgp",
        "primary_fingerprint": primary,
        "signing_fingerprints": signing,
        "public_key_base64": base64.b64encode(key_bytes).decode("ascii"),
        "public_key_sha256": key_digest,
        "baseline_commit": commit,
        "baseline_integrity_state_digest": integrity_digest,
        "history_before_baseline": "unverified",
    }


def canonical_provenance_root_bytes(payload: Mapping[str, Any]) -> bytes:
    normalized = validate_provenance_root(payload)
    return (json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def provenance_root_digest(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_provenance_root_bytes(payload)).hexdigest()


def _production_path(path: Path) -> bool:
    try:
        return path.resolve(strict=False) == DEFAULT_PROVENANCE_ROOT_PATH
    except OSError:
        return path == DEFAULT_PROVENANCE_ROOT_PATH


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
        raise ReleaseProvenanceError("release signer trust directory is unavailable") from exc
    expected_uid = 0 if require_root else os.geteuid()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_mode & 0o077
    ):
        raise ReleaseProvenanceError("release signer trust directory is untrusted")


def _decode_json(text: str) -> Any:
    def unique(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseProvenanceError(f"duplicate release signer root field: {key}")
            result[key] = value
        return result

    def reject(value: str) -> None:
        raise ReleaseProvenanceError(f"invalid release signer root JSON number: {value}")

    try:
        return json.loads(text, object_pairs_hook=unique, parse_constant=reject)
    except ReleaseProvenanceError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ReleaseProvenanceError("release signer root is invalid JSON") from exc


def read_provenance_root(path: Path = DEFAULT_PROVENANCE_ROOT_PATH) -> dict[str, Any] | None:
    production = _production_path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ReleaseProvenanceError("release signer root cannot be inspected") from exc
    if not _trusted_regular_file(path, require_root=production):
        raise ReleaseProvenanceError("release signer root file is untrusted")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseProvenanceError("release signer root cannot be read") from exc
    return validate_provenance_root(_decode_json(text))


def _write_provenance_root(
    payload: Mapping[str, Any],
    path: Path = DEFAULT_PROVENANCE_ROOT_PATH,
) -> dict[str, Any]:
    """Create the signer root exactly once; official code has no replacement path."""

    production = _production_path(path)
    if production and os.geteuid() != 0:
        raise ReleaseProvenanceError("production release signer root changes require root")
    validated = validate_provenance_root(payload)
    _ensure_parent(path, require_root=production)
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ReleaseProvenanceError("release signer root cannot be inspected") from exc
    else:
        raise ReleaseProvenanceError("release signer root is already established")

    temporary_name = ""
    linked = False
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
        try:
            os.link(temporary_name, path)
        except FileExistsError as exc:
            raise ReleaseProvenanceError("release signer root is already established") from exc
        linked = True
        os.unlink(temporary_name)
        temporary_name = ""
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except ReleaseProvenanceError:
        raise
    except OSError as exc:
        raise ReleaseProvenanceError("could not persist release signer root") from exc
    finally:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        if linked:
            # The temporary hard link has already been removed on the success path.
            # If a crash/error occurred after linking, the fixed path remains visible and
            # strict nlink/JSON validation makes any incomplete state fail closed.
            pass
    return validated


def _require_trusted_system_program(path: Path, label: str) -> None:
    """Require the fixed verifier binary and its containing directory to be root-controlled."""

    try:
        metadata = path.lstat()
        parent = path.parent.lstat()
    except OSError as exc:
        raise ReleaseProvenanceError(f"fixed {label} verifier {path} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
        or not metadata.st_mode & 0o111
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != 0
        or parent.st_mode & 0o022
    ):
        raise ReleaseProvenanceError(f"fixed {label} verifier {path} is untrusted")


def _gpg_environment(home: Path) -> dict[str, str]:
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


def _run_gpg(
    home: Path,
    *arguments: str,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    _require_trusted_system_program(GPG_PROGRAM, "OpenPGP")
    try:
        result = subprocess.run(
            [str(GPG_PROGRAM), "--no-options", "--batch", "--homedir", str(home), *arguments],
            env=_gpg_environment(home),
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30.0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseProvenanceError("OpenPGP key inspection failed") from exc
    if len(result.stdout) + len(result.stderr) > MAX_GPG_OUTPUT_BYTES:
        raise ReleaseProvenanceError("OpenPGP verifier output exceeds v1 limit")
    return result


def _describe_imported_key(home: Path) -> dict[str, Any]:
    result = _run_gpg(home, "--with-colons", "--fixed-list-mode", "--list-keys")
    if result.returncode != 0:
        raise ReleaseProvenanceError("OpenPGP public key could not be listed")
    try:
        lines = result.stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ReleaseProvenanceError("OpenPGP key listing is invalid") from exc

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
        if not _FINGERPRINT_RE.fullmatch(fingerprint):
            raise ReleaseProvenanceError("OpenPGP key fingerprint is invalid")
        if current_validity in {"r", "d", "i"}:
            raise ReleaseProvenanceError("OpenPGP release signer key is revoked or disabled")
        if current_kind == "pub":
            primaries.append(fingerprint)
        if "s" in current_caps.lower():
            signing.append(fingerprint)
        current_kind = ""
        current_caps = ""
        current_validity = ""

    if len(primaries) != 1:
        raise ReleaseProvenanceError("release signer input must contain exactly one OpenPGP primary key")
    if not signing:
        raise ReleaseProvenanceError("release signer OpenPGP key has no signing-capable key")

    secret = _run_gpg(home, "--with-colons", "--list-secret-keys")
    if secret.returncode == 0:
        try:
            secret_text = secret.stdout.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ReleaseProvenanceError("OpenPGP secret-key listing is invalid") from exc
        if any(line.startswith(("sec:", "ssb:")) for line in secret_text.splitlines()):
            raise ReleaseProvenanceError("release signer bootstrap input must contain public key material only")

    exported = _run_gpg(home, "--export", primaries[0])
    if exported.returncode != 0 or not exported.stdout or len(exported.stdout) > MAX_PUBLIC_KEY_BYTES:
        raise ReleaseProvenanceError("canonical OpenPGP public key export failed")
    return {
        "primary_fingerprint": primaries[0],
        "signing_fingerprints": sorted(set(signing)),
        "public_key": exported.stdout,
    }


def describe_public_key(key_bytes: bytes) -> dict[str, Any]:
    if not isinstance(key_bytes, bytes) or not key_bytes or len(key_bytes) > MAX_PUBLIC_KEY_BYTES:
        raise ReleaseProvenanceError("release signer public key is empty or too large")
    with tempfile.TemporaryDirectory(prefix="open-mmi-provenance-key-") as directory:
        home = Path(directory)
        os.chmod(home, 0o700)
        imported = _run_gpg(home, "--import-options", "import-minimal", "--import", input_bytes=key_bytes)
        if imported.returncode != 0:
            raise ReleaseProvenanceError("release signer OpenPGP public key could not be imported")
        return _describe_imported_key(home)


def build_provenance_root(
    *,
    key_bytes: bytes,
    baseline_commit: str,
    baseline_integrity_state_digest: str,
    established_at: str | None = None,
) -> dict[str, Any]:
    commit = str(baseline_commit).lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise ReleaseProvenanceError("release signer baseline commit is invalid")
    if not isinstance(baseline_integrity_state_digest, str) or not _SHA256_RE.fullmatch(
        baseline_integrity_state_digest
    ):
        raise ReleaseProvenanceError("release signer baseline integrity-state digest is invalid")
    description = describe_public_key(key_bytes)
    canonical_key = description["public_key"]
    payload = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "root_id": PROVENANCE_ROOT_ID,
        "established_at": established_at or _timestamp(),
        "root_source": "owner-pinned-local-key",
        "algorithm": "openpgp",
        "primary_fingerprint": description["primary_fingerprint"],
        "signing_fingerprints": description["signing_fingerprints"],
        "public_key_base64": base64.b64encode(canonical_key).decode("ascii"),
        "public_key_sha256": "sha256:" + hashlib.sha256(canonical_key).hexdigest(),
        "baseline_commit": commit,
        "baseline_integrity_state_digest": baseline_integrity_state_digest,
        "history_before_baseline": "unverified",
    }
    return validate_provenance_root(payload)


def _import_pinned_key(home: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_provenance_root(state)
    key_bytes = base64.b64decode(normalized["public_key_base64"], validate=True)
    imported = _run_gpg(home, "--import-options", "import-minimal", "--import", input_bytes=key_bytes)
    if imported.returncode != 0:
        raise ReleaseProvenanceError("pinned OpenPGP public key could not be imported")
    description = _describe_imported_key(home)
    if description["primary_fingerprint"] != normalized["primary_fingerprint"]:
        raise ReleaseProvenanceError("pinned OpenPGP primary fingerprint does not match key material")
    if description["signing_fingerprints"] != normalized["signing_fingerprints"]:
        raise ReleaseProvenanceError("pinned OpenPGP signing fingerprints do not match key material")
    if "sha256:" + hashlib.sha256(description["public_key"]).hexdigest() != normalized["public_key_sha256"]:
        raise ReleaseProvenanceError("pinned OpenPGP canonical key digest does not match state")
    return description


def verify_commit_provenance(
    repository: Path,
    commit: str,
    state: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = validate_provenance_root(state)
    commit = str(commit).lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise ReleaseProvenanceError("release provenance candidate commit is invalid")
    repository = Path(repository)
    if not repository.is_absolute():
        raise ReleaseProvenanceError("release provenance repository path must be absolute")
    _require_trusted_system_program(GIT_PROGRAM, "Git")

    with tempfile.TemporaryDirectory(prefix="open-mmi-provenance-verify-") as directory:
        home = Path(directory)
        os.chmod(home, 0o700)
        _import_pinned_key(home, normalized)
        try:
            result = subprocess.run(
                [
                    str(GIT_PROGRAM),
                    "-c", f"safe.directory={repository}",
                    "-c", "gpg.format=openpgp",
                    "-c", f"gpg.program={GPG_PROGRAM}",
                    "-C", str(repository),
                    "verify-commit", "--raw", commit,
                ],
                env=_gpg_environment(home),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=30.0,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ReleaseProvenanceError("release commit signature verification failed") from exc
        if len(result.stdout) + len(result.stderr) > MAX_GPG_OUTPUT_BYTES:
            raise ReleaseProvenanceError("release commit verifier output exceeds v1 limit")
        try:
            output = (result.stdout + b"\n" + result.stderr).decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ReleaseProvenanceError("release commit verifier output is invalid") from exc

    fatal_status = {
        "BADSIG",
        "ERRSIG",
        "NO_PUBKEY",
        "REVKEYSIG",
        "KEYREVOKED",
        "EXPSIG",
        "EXPKEYSIG",
        "KEYEXPIRED",
        "SIGEXPIRED",
    }
    observed_fatal: list[str] = []
    valid_lines: list[list[str]] = []
    for line in output.splitlines():
        if not line.startswith("[GNUPG:] "):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        status = fields[1]
        if status in fatal_status:
            observed_fatal.append(status)
        if status == "VALIDSIG":
            valid_lines.append(fields)

    if result.returncode != 0 or observed_fatal or len(valid_lines) != 1:
        raise ReleaseProvenanceError("release commit does not have one valid signature from the pinned signer")
    fields = valid_lines[0]
    if len(fields) < 12:
        raise ReleaseProvenanceError("release commit OpenPGP validation evidence is malformed")
    signing_fingerprint = fields[2].upper()
    primary_fingerprint = fields[-1].upper()
    if signing_fingerprint not in normalized["signing_fingerprints"]:
        raise ReleaseProvenanceError("release commit signature was not made by a pinned signing key")
    if primary_fingerprint != normalized["primary_fingerprint"]:
        raise ReleaseProvenanceError("release commit signature does not chain to the pinned primary key")
    try:
        signature_timestamp = int(fields[4])
    except ValueError as exc:
        raise ReleaseProvenanceError("release commit signature timestamp is invalid") from exc
    if signature_timestamp <= 0:
        raise ReleaseProvenanceError("release commit signature timestamp is invalid")

    return {
        "verified": True,
        "candidate_commit": commit,
        "primary_fingerprint": primary_fingerprint,
        "signing_fingerprint": signing_fingerprint,
        "signature_date": fields[3],
        "signature_timestamp": signature_timestamp,
        "provenance_root_digest": provenance_root_digest(normalized),
    }



def verification_repository_for_integrity(
    install_root: Path,
    expected_commit: str,
    descriptor_path: Path = DEFAULT_SOURCE_DESCRIPTOR,
) -> Path:
    """Return the fixed local Git repository that contains an integrity-bound commit."""

    commit = str(expected_commit).lower()
    if not _COMMIT_RE.fullmatch(commit):
        raise ReleaseProvenanceError("integrity-bound release commit is invalid")
    install_root = Path(install_root)
    if (install_root / ".git").is_dir():
        return install_root

    try:
        metadata = descriptor_path.lstat()
    except OSError as exc:
        raise ReleaseProvenanceError("managed update source descriptor is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise ReleaseProvenanceError("managed update source descriptor is untrusted")
    try:
        payload = json.loads(descriptor_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseProvenanceError("managed update source descriptor is invalid") from exc
    expected = {
        "schema_version", "channel", "repository_path", "branch", "upstream",
        "installed_commit", "installed_version",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema_version") != 1:
        raise ReleaseProvenanceError("managed update source descriptor schema is invalid")
    repository = Path(str(payload.get("repository_path") or ""))
    installed_commit = str(payload.get("installed_commit") or "").lower()
    if not repository.is_absolute():
        raise ReleaseProvenanceError("managed update repository path is invalid")
    if installed_commit != commit:
        raise ReleaseProvenanceError(
            "managed update source commit does not match installed integrity state"
        )
    return repository

def require_provenance_root(
    path: Path = DEFAULT_PROVENANCE_ROOT_PATH,
) -> dict[str, Any]:
    state = read_provenance_root(path)
    if state is None:
        raise ReleaseProvenanceError(
            "release signer provenance is not established; local owner bootstrap is required before updates"
        )
    return state


def require_current_release_provenance(
    state_path: Path,
    repository: Path,
    commit: str,
) -> dict[str, Any]:
    state = require_provenance_root(state_path)
    evidence = verify_commit_provenance(repository, commit, state)
    return {"root": state, "verification": evidence}


def require_candidate_release_provenance(
    state_path: Path,
    repository: Path,
    commit: str,
) -> dict[str, Any]:
    state = require_provenance_root(state_path)
    evidence = verify_commit_provenance(repository, commit, state)
    return {"root": state, "verification": evidence}
