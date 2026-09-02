#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Open-MMI Installation Manager
# =============================================================================
# Unified script for install, update, uninstall, and management operations
# =============================================================================

APP_NAME="open-mmi"
INSTALL_DIR="/opt/open-mmi"
BACKUP_DIR="/opt/open-mmi-backups"
VERSION_FILE="$INSTALL_DIR/.version"
UPDATE_POLICY_FILE="/etc/open-mmi/update-policy.json"
UPDATE_COORDINATOR_GROUP="open-mmi-update"
UPDATE_COORDINATOR_UNIT="open-mmi-update-coordinator.service"
UPDATE_INSTALLER_UNIT="open-mmi-update-installer.service"
MEDIA_EGRESS_UNIT="open-mmi-media-egress.service"
MEDIA_EGRESS_GROUP="open-mmi"
MEDIA_EGRESS_CONFIG_DIR="/var/lib/open-mmi/network-egress"
MEDIA_EGRESS_CONFIG="$MEDIA_EGRESS_CONFIG_DIR/media.v1.json"
VEHICLE_STORE_UNIT="open-mmi-vehicle-store.service"
VEHICLE_STORE_ROOT="/var/lib/open-mmi/vehicle-data"
OWNER_CONFIG_UNIT="open-mmi-owner-config.service"
SYSTEMD_USER_UNIT_ROOT="/etc/systemd/user"
VEHICLE_CONFIG_COORDINATOR_GROUP="open-mmi-config"
VEHICLE_CONFIG_COORDINATOR_UNIT="open-mmi-vehicle-config-coordinator.service"
VEHICLE_CAN_PROVISION_UNIT="open-mmi-vehicle-can-provision.service"
POWERD_UNIT="open-mmi-powerd.service"
POWER_POLICY_FILE="/etc/open-mmi/power-policy.json"
POWERD_WAKE_UDEV_RULE="90-open-mmi-can-wake.rules"
POWERD_WAKE_UDEV_RULE_PATH="/etc/udev/rules.d/$POWERD_WAKE_UDEV_RULE"
OPEN_MMI_TMPFILES_CONFIG="open-mmi.conf"
OPEN_MMI_TMPFILES_CONFIG_PATH="/usr/lib/tmpfiles.d/$OPEN_MMI_TMPFILES_CONFIG"
VEHICLE_CONFIG_COORDINATOR_ENV="/etc/open-mmi/vehicle-config-coordinator.env"
VEHICLE_CONFIG_UI_QUALIFICATION_GATE="/etc/open-mmi/vehicle-configuration-ui-qualification"
VEHICLE_CONFIG_COORDINATOR_OVERRIDE_DIR="/etc/systemd/system/$VEHICLE_CONFIG_COORDINATOR_UNIT.d"
VEHICLE_CONFIG_COORDINATOR_SANDBOX="$VEHICLE_CONFIG_COORDINATOR_OVERRIDE_DIR/10-write-paths.conf"
VEHICLE_CONFIG_COORDINATOR_SOCKET="/run/open-mmi/vehicle-configuration-coordinator.sock"
UPDATE_COORDINATOR_STATE_DIR="/var/lib/open-mmi"
UPDATE_COORDINATOR_RUNTIME_DIR="/run/open-mmi"

# Color output
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
NC=$'\033[0m' # No Color

# Get real user (accounting for sudo)
REAL_USER="${OPEN_MMI_REAL_USER:-${SUDO_USER:-${USER:-root}}}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)
USER_ID=$(id -u "$REAL_USER")
USER_CONFIG_DIR="$REAL_HOME/.config/open-mmi"
LOGIN_AUTOSTART_ENTRY="$REAL_HOME/.config/autostart/open-mmi.desktop"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_ENTRY_SOURCE="$REPO_ROOT/packaging/linux-desktop/open-mmi-status.desktop"
CHOOSER_ENTRY_SOURCE="$REPO_ROOT/packaging/linux-desktop/open-mmi-chooser.desktop"
DESKTOP_ICON_SOURCE="$REPO_ROOT/packaging/linux-desktop/icons"
APPLICATIONS_DIR="$REAL_HOME/.local/share/applications"
APPLICATION_ENTRY="$APPLICATIONS_DIR/open-mmi.desktop"
CHOOSER_APPLICATION_ENTRY="$APPLICATIONS_DIR/open-mmi-chooser.desktop"
ICON_THEME_DIR="$REAL_HOME/.local/share/icons"
DESKTOP_ENTRY_NAME="Open MMI.desktop"
COMMAND_LINK_DIR="${OPEN_MMI_COMMAND_LINK_DIR:-/usr/local/bin}"
OPEN_MMI_COMMANDS=(
    open-mmi-canbusd
    open-mmi-config
    open-mmi-dashboard
    open-mmi-launcher
    open-mmi-powerd
    open-mmi-status
    open-mmi-update-coordinator
    open-mmi-update-installer
    open-mmi-vehicle-config-coordinator
)

# =============================================================================
# UTILITIES
# =============================================================================

log_info() {
    echo -e "${BLUE}[info]${NC} $*"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $*"
}

log_error() {
    echo -e "${RED}[✗]${NC} $*" >&2
}

log_warn() {
    echo -e "${YELLOW}[!]${NC} $*"
}

confirm() {
    local prompt="$1"
    local response
    read -p "$(echo -e ${YELLOW}$prompt${NC}) (y/N) " -r response
    [[ "$response" =~ ^[Yy]$ ]]
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run with sudo"
        exit 1
    fi
}

harden_install_root_ownership() {
    [ -e "$INSTALL_DIR" ] || return 0
    [[ -d "$INSTALL_DIR" && ! -L "$INSTALL_DIR" ]] || {
        log_error "Installed Open MMI root is not a trusted directory"
        return 1
    }
    chown root:root "$INSTALL_DIR"
    chmod 0755 "$INSTALL_DIR"
}

check_dependencies() {
    local missing=()
    
    for cmd in git python3 pip; do
        if ! command -v "$cmd" &> /dev/null; then
            missing+=("$cmd")
        fi
    done
    
    if [ ${#missing[@]} -gt 0 ]; then
        log_error "Missing required commands: ${missing[*]}"
        log_info "Install with: sudo apt install ${missing[*]}"
        return 1
    fi
    return 0
}

is_installed() {
    [ -d "$INSTALL_DIR" ] && [ -f "$VERSION_FILE" ]
}

get_installed_version() {
    if [ -f "$VERSION_FILE" ]; then
        cat "$VERSION_FILE"
    else
        echo "unknown"
    fi
}

get_current_version() {
    sudo -u "$REAL_USER" git -C "$REPO_ROOT" describe --tags --always 2>/dev/null \
        || echo "dev-$(sudo -u "$REAL_USER" git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo 'local')"
}

get_repo_commit() {
    sudo -u "$REAL_USER" git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || true
}

daemon_running() {
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"
    sudo -u "$REAL_USER" \
        XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        systemctl --user is-active canbusd > /dev/null 2>&1
}

get_repo_branch() {
    sudo -u "$REAL_USER" git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main"
}

get_repo_upstream() {
    local upstream
    upstream=$(sudo -u "$REAL_USER" git -C "$REPO_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)

    if [ -n "$upstream" ]; then
        echo "$upstream"
    else
        echo "origin/$(get_repo_branch)"
    fi
}

write_update_source_metadata() {
    local branch upstream commit version repository destination
    branch="${1:-${OPEN_MMI_MANAGED_BRANCH:-$(get_repo_branch)}}"
    upstream="${2:-${OPEN_MMI_MANAGED_UPSTREAM:-$(get_repo_upstream)}}"
    commit="${3:-${OPEN_MMI_PREPARED_COMMIT:-$(get_repo_commit)}}"
    version="${4:-${OPEN_MMI_PREPARED_VERSION:-$(get_current_version)}}"
    repository="${5:-${OPEN_MMI_MANAGED_REPOSITORY:-$REPO_ROOT}}"
    destination="$INSTALL_DIR/.update-source.json"

    if [[ ! "$commit" =~ ^[0-9a-fA-F]{40}$ ]]; then
        log_warn "Could not record managed update source metadata"
        return 0
    fi

    python3 - "$destination" "$repository" "$branch" "$upstream" "$commit" "$version" "$UPDATE_POLICY_FILE" <<'PY_UPDATE_SOURCE'
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

metadata_path = Path(sys.argv[1])
policy_path = Path(sys.argv[7])
approved_channels = {"stable", "beta", "nightly"}


def timestamp():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def atomic_json(path, payload, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
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
            json.dump(payload, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise


production_policy = policy_path == Path("/etc/open-mmi/update-policy.json")
if policy_path.is_symlink():
    raise RuntimeError("update policy must not be a symlink")
if production_policy and policy_path.parent.is_symlink():
    raise RuntimeError("update policy directory must not be a symlink")
if production_policy and policy_path.parent.exists():
    parent_metadata = policy_path.parent.lstat()
    if not stat.S_ISDIR(parent_metadata.st_mode) or parent_metadata.st_uid != 0:
        raise RuntimeError("production update policy directory must be root owned")
    if parent_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("production update policy directory must not be group/world writable")
if policy_path.exists():
    metadata = policy_path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("update policy must be a regular file")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("update policy must not be group/world writable")
    if policy_path == Path("/etc/open-mmi/update-policy.json") and metadata.st_uid != 0:
        raise RuntimeError("production update policy must be root owned")
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or set(policy) - {"schema_version", "channel", "updated_at"}:
        raise RuntimeError("update policy contains unsupported fields")
    if policy.get("schema_version") != 1:
        raise RuntimeError("update policy is invalid")
    if policy.get("channel") == "development":
        policy["channel"] = "nightly"
        policy["updated_at"] = timestamp()
        atomic_json(policy_path, policy, 0o644)
    elif policy.get("channel") not in approved_channels:
        raise RuntimeError("update policy is invalid")
else:
    policy = {
        "schema_version": 1,
        "channel": "nightly",
        "updated_at": timestamp(),
    }
    if production_policy:
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(policy_path.parent, 0o755)
    atomic_json(policy_path, policy, 0o644)

payload = {
    "schema_version": 1,
    "channel": policy["channel"],
    "repository_path": str(Path(sys.argv[2]).resolve()),
    "branch": sys.argv[3],
    "upstream": sys.argv[4],
    "installed_commit": sys.argv[5].lower(),
    "installed_version": sys.argv[6],
}
atomic_json(metadata_path, payload, 0o644)
PY_UPDATE_SOURCE
    log_success "Recorded managed update source and channel policy"
}

write_checkout_update_source_metadata() {
    local branch upstream commit version
    branch=$(get_repo_branch)
    upstream=$(get_repo_upstream)
    commit=$(get_repo_commit)
    version=$(get_current_version)

    # Interactive install/update operations must describe the checkout being
    # deployed, even if prepared-installer variables remain in the caller's
    # environment. Prepared deployments call the lower-level writer directly.
    write_update_source_metadata "$branch" "$upstream" "$commit" "$version" "$REPO_ROOT"
}

copy_if_missing() {
    local src="$1"
    local dst="$2"

    if [ -e "$dst" ]; then
        log_warn "Keeping existing user file: $dst"
        return 0
    fi

    install -d -m 0700 -o "$REAL_USER" -g "$REAL_USER" "$(dirname "$dst")"
    install -m 0600 -o "$REAL_USER" -g "$REAL_USER" "$src" "$dst"
    log_success "Created $dst"
}

open_editor_as_user() {
    local file="$1"
    local editor="${EDITOR:-nano}"

    sudo -u "$REAL_USER" \
        HOME="$REAL_HOME" \
        XDG_RUNTIME_DIR="/run/user/$USER_ID" \
        "$editor" "$file"
}


get_desktop_dir() {
    local desktop_dir=""

    if command -v xdg-user-dir > /dev/null 2>&1; then
        desktop_dir=$(sudo -u "$REAL_USER" env HOME="$REAL_HOME" xdg-user-dir DESKTOP 2>/dev/null || true)
    fi

    if [[ -z "$desktop_dir" || "$desktop_dir" != /* || "$desktop_dir" = "$REAL_HOME" ]]; then
        desktop_dir="$REAL_HOME/Desktop"
    fi

    printf '%s\n' "$desktop_dir"
}

refresh_desktop_caches() {
    if command -v update-desktop-database > /dev/null 2>&1; then
        sudo -u "$REAL_USER" env HOME="$REAL_HOME" update-desktop-database "$APPLICATIONS_DIR" > /dev/null 2>&1 || true
    fi

    if command -v gtk-update-icon-cache > /dev/null 2>&1 && [ -d "$ICON_THEME_DIR/hicolor" ]; then
        sudo -u "$REAL_USER" env HOME="$REAL_HOME" gtk-update-icon-cache -f -t "$ICON_THEME_DIR/hicolor" > /dev/null 2>&1 || true
    fi
}

install_desktop_icons() {
    if [ ! -d "$DESKTOP_ICON_SOURCE" ]; then
        log_error "Desktop icon source not found: $DESKTOP_ICON_SOURCE"
        return 1
    fi

    while IFS= read -r -d '' source_icon; do
        local relative_path target_icon target_dir
        relative_path="${source_icon#"$DESKTOP_ICON_SOURCE"/}"
        target_icon="$ICON_THEME_DIR/$relative_path"
        target_dir=$(dirname "$target_icon")
        install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$target_dir"
        install -m 0644 -o "$REAL_USER" -g "$REAL_USER" "$source_icon" "$target_icon"
    done < <(find "$DESKTOP_ICON_SOURCE" -type f -print0)
}

remove_desktop_icons() {
    if [ ! -d "$DESKTOP_ICON_SOURCE" ]; then
        return 0
    fi

    while IFS= read -r -d '' source_icon; do
        local relative_path
        relative_path="${source_icon#"$DESKTOP_ICON_SOURCE"/}"
        rm -f "$ICON_THEME_DIR/$relative_path"
    done < <(find "$DESKTOP_ICON_SOURCE" -type f -print0)
}

install_desktop_entry() {
    local desktop_dir
    desktop_dir=$(get_desktop_dir)

    if [ ! -f "$DESKTOP_ENTRY_SOURCE" ]; then
        log_error "Desktop entry source not found: $DESKTOP_ENTRY_SOURCE"
        return 1
    fi
    if [ ! -f "$CHOOSER_ENTRY_SOURCE" ]; then
        log_error "Interface chooser entry source not found: $CHOOSER_ENTRY_SOURCE"
        return 1
    fi

    log_info "Installing desktop launcher and icons..."
    install_desktop_icons
    install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$APPLICATIONS_DIR" "$desktop_dir"
    install -m 0644 -o "$REAL_USER" -g "$REAL_USER" "$DESKTOP_ENTRY_SOURCE" "$APPLICATION_ENTRY"
    install -m 0644 -o "$REAL_USER" -g "$REAL_USER" "$CHOOSER_ENTRY_SOURCE" "$CHOOSER_APPLICATION_ENTRY"
    install -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$DESKTOP_ENTRY_SOURCE" "$desktop_dir/$DESKTOP_ENTRY_NAME"

    if command -v gio > /dev/null 2>&1; then
        sudo -u "$REAL_USER" env HOME="$REAL_HOME" gio set "$desktop_dir/$DESKTOP_ENTRY_NAME" metadata::trusted true > /dev/null 2>&1 || true
    fi

    refresh_desktop_caches
}

remove_desktop_entry() {
    local desktop_dir
    desktop_dir=$(get_desktop_dir)

    log_info "Removing desktop launcher and icons..."
    rm -f "$APPLICATION_ENTRY" "$CHOOSER_APPLICATION_ENTRY" "$desktop_dir/$DESKTOP_ENTRY_NAME"
    remove_desktop_icons
    refresh_desktop_caches
}

verify_console_commands() {
    local command wrapper

    for command in "${OPEN_MMI_COMMANDS[@]}"; do
        wrapper="$INSTALL_DIR/venv/bin/$command"
        if [ ! -x "$wrapper" ]; then
            log_error "Installed command wrapper is missing or not executable: $wrapper"
            return 1
        fi
    done
}

upgrade_python_packaging_tools() {
    local python="${1:-$INSTALL_DIR/venv/bin/python}"
    local result_dir="${2:-}"
    local before after

    if [ ! -x "$python" ]; then
        log_error "Deployment Python is missing or not executable: $python"
        return 1
    fi

    before=$(env -u PYTHONPATH "$python" -m pip --version 2>/dev/null | awk 'NR == 1 {print $2}')
    if [ -z "$before" ]; then
        log_error "Could not determine installed pip version"
        return 1
    fi

    log_info "Preparing Python packaging tools (pip $before)..."
    if ! env -u PYTHONPATH "$python" -m pip install --upgrade pip; then
        log_error "Failed to upgrade pip"
        return 1
    fi

    after=$(env -u PYTHONPATH "$python" -m pip --version 2>/dev/null | awk 'NR == 1 {print $2}')
    if [ -z "$after" ]; then
        log_error "Could not verify pip after upgrade"
        return 1
    fi

    if [ -n "$result_dir" ]; then
        printf '%s\n' "$before" > "$result_dir/pip-version-before"
        printf '%s\n' "$after" > "$result_dir/pip-version-after"
        chmod 0600 "$result_dir/pip-version-before" "$result_dir/pip-version-after"
    fi

    if [ "$before" = "$after" ]; then
        log_success "Python packaging tools ready (pip $after · up to date)"
    else
        log_success "Python packaging tools updated (pip $before → $after)"
    fi
}

record_python_packaging_tool_version() {
    local python="${1:-$INSTALL_DIR/venv/bin/python}"
    local result_dir="${2:-}"
    local version

    version=$(env -u PYTHONPATH "$python" -m pip --version 2>/dev/null | awk 'NR == 1 {print $2}')
    if [ -z "$version" ]; then
        log_error "Could not determine installed pip version"
        return 1
    fi
    if [ -n "$result_dir" ]; then
        printf '%s\n' "$version" > "$result_dir/pip-version-before"
        printf '%s\n' "$version" > "$result_dir/pip-version-after"
        chmod 0600 "$result_dir/pip-version-before" "$result_dir/pip-version-after"
    fi
    log_success "Python packaging tool fixed for offline deployment (pip $version)"
}

install_open_mmi_package() {
    local python="$INSTALL_DIR/venv/bin/python"
    local package_source="${1:-$INSTALL_DIR}"

    if [ ! -x "$python" ]; then
        log_error "Deployment Python is missing or not executable: $python"
        return 1
    fi

    log_info "Installing Open MMI package and console commands..."
    local pip_arguments=(install --upgrade --force-reinstall)
    local pip_environment=(env -u PYTHONPATH)
    if [[ "$package_source" == *.whl ]]; then
        pip_arguments+=(--no-index --no-deps --no-cache-dir)
        pip_environment+=(PIP_CONFIG_FILE=/dev/null PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1)
    fi
    if ! ( umask 0022; "${pip_environment[@]}" "$python" -m pip "${pip_arguments[@]}" "$package_source" ); then
        log_error "Failed to install Open MMI package"
        return 1
    fi

    verify_console_commands
    env -u PYTHONPATH "$python" -I -c 'import canbusd.core, ui.config_cli, ui.web_dashboard.server'
    if [[ $EUID -eq 0 && "$REAL_USER" != root ]]; then
        sudo -u "$REAL_USER" env -u PYTHONPATH "$python" -I \
            -c 'import canbusd.core, ui.config_cli, ui.web_dashboard.server'
    fi
}

configure_maintained_catalogue_permissions() {
    local catalogue_root

    for catalogue_root in "$INSTALL_DIR/vehicles" "$INSTALL_DIR/bindings"; do
        [ -d "$catalogue_root" ] || continue

        # Prepared updates run with UMask=0027 and preserve staged modes.  The
        # maintained catalogue is non-secret installed product data and must be
        # readable by the unprivileged dashboard and canbusd services.
        find "$catalogue_root" -type d \
            -exec chown root:root {} + \
            -exec chmod 0755 {} +
        find "$catalogue_root" -type f \
            -exec chown root:root {} + \
            -exec chmod 0644 {} +
    done
}


harden_custom_catalogue_permissions() {
    local user_group_id
    user_group_id=$(id -g "$REAL_USER")

    python3 - \
        "$REAL_HOME" \
        "$USER_CONFIG_DIR" \
        "$USER_ID" \
        "$user_group_id" <<'PY_CUSTOM_CATALOGUE_PERMISSIONS'
import os
import stat
import sys
from pathlib import Path

home = Path(sys.argv[1])
custom_root = Path(sys.argv[2])
user_uid = int(sys.argv[3])
user_gid = int(sys.argv[4])
expected_root = home / ".config" / "open-mmi"
allowed_owners = {0, user_uid}
maximum_items = 10_000

if not home.is_absolute() or custom_root != expected_root:
    raise SystemExit("custom catalogue path is not the fixed user configuration root")


def metadata(path: Path):
    try:
        return path.lstat()
    except OSError as exc:
        raise SystemExit(f"custom catalogue path cannot be inspected: {path}") from exc


def validate_directory(path: Path, *, owner_must_be_user: bool = False) -> os.stat_result:
    item = metadata(path)
    if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
        raise SystemExit(f"custom catalogue directory is untrusted: {path}")
    expected_owners = {user_uid} if owner_must_be_user else allowed_owners
    if item.st_uid not in expected_owners:
        raise SystemExit(f"custom catalogue directory has an untrusted owner: {path}")
    return item


def create_private_directory(path: Path) -> None:
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        return
    except OSError as exc:
        raise SystemExit(f"custom catalogue directory cannot be created: {path}") from exc
    os.chown(path, user_uid, user_gid, follow_symlinks=False)
    os.chmod(path, 0o700)


home_metadata = validate_directory(home, owner_must_be_user=True)
if home_metadata.st_mode & 0o022:
    raise SystemExit("user home directory must not be group or world writable")

config_root = home / ".config"
if not config_root.exists():
    create_private_directory(config_root)
config_metadata = validate_directory(config_root, owner_must_be_user=True)
if config_metadata.st_mode & 0o022:
    raise SystemExit("user configuration directory must not be group or world writable")

if not custom_root.exists():
    create_private_directory(custom_root)
root_metadata = validate_directory(custom_root)

catalogue_roots = [
    custom_root / "vehicles",
    custom_root / "bindings",
    custom_root / ".open-mmi-provenance",
]
provenance_children = [
    custom_root / ".open-mmi-provenance" / "profile",
    custom_root / ".open-mmi-provenance" / "bindings",
]

# Inspect every existing targeted tree before changing ownership or modes. This
# prevents an update from following a user-created symlink or touching an inode
# linked outside the fixed custom catalogue.
collected: dict[Path, tuple[int, int, bool]] = {
    custom_root: (root_metadata.st_dev, root_metadata.st_ino, True),
}
for root in catalogue_roots:
    try:
        root_metadata = root.lstat()
    except FileNotFoundError:
        continue
    except OSError as exc:
        raise SystemExit(f"custom catalogue path cannot be inspected: {root}") from exc
    if stat.S_ISLNK(root_metadata.st_mode):
        raise SystemExit(f"custom catalogue symlinks are not trusted: {root}")
    if not stat.S_ISDIR(root_metadata.st_mode):
        raise SystemExit(f"custom catalogue directory is untrusted: {root}")
    if root_metadata.st_uid not in allowed_owners:
        raise SystemExit(f"custom catalogue directory has an untrusted owner: {root}")
    collected[root] = (root_metadata.st_dev, root_metadata.st_ino, True)
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise SystemExit(f"custom catalogue directory cannot be read: {directory}") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                item = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SystemExit(f"custom catalogue item cannot be inspected: {path}") from exc
            if stat.S_ISLNK(item.st_mode):
                raise SystemExit(f"custom catalogue symlinks are not trusted: {path}")
            if item.st_uid not in allowed_owners:
                raise SystemExit(f"custom catalogue item has an untrusted owner: {path}")
            if stat.S_ISDIR(item.st_mode):
                collected[path] = (item.st_dev, item.st_ino, True)
                stack.append(path)
            elif stat.S_ISREG(item.st_mode) and item.st_nlink == 1:
                collected[path] = (item.st_dev, item.st_ino, False)
            else:
                raise SystemExit(f"custom catalogue item is untrusted: {path}")
            if len(collected) > maximum_items:
                raise SystemExit("custom catalogue contains too many items")

for root in catalogue_roots:
    if not root.exists():
        create_private_directory(root)
for directory in provenance_children:
    if not directory.exists():
        create_private_directory(directory)

# Include directories created after the no-follow preflight.
for path in [*catalogue_roots, *provenance_children]:
    item = validate_directory(path)
    collected[path] = (item.st_dev, item.st_ino, True)

# Files first, then deepest directories, so no unrelated file in the shared
# open-mmi settings root is ever traversed or modified.
ordered = sorted(
    collected.items(),
    key=lambda pair: (pair[1][2], -len(pair[0].parts)),
)
for path, (device, inode, is_directory) in ordered:
    item = metadata(path)
    if item.st_dev != device or item.st_ino != inode:
        raise SystemExit(f"custom catalogue changed during permission repair: {path}")
    if is_directory != stat.S_ISDIR(item.st_mode):
        raise SystemExit(f"custom catalogue changed during permission repair: {path}")
    if not is_directory and (not stat.S_ISREG(item.st_mode) or item.st_nlink != 1):
        raise SystemExit(f"custom catalogue changed during permission repair: {path}")
    os.chown(path, user_uid, user_gid, follow_symlinks=False)
    os.chmod(path, 0o700 if is_directory else 0o600)

for path, (_device, _inode, is_directory) in collected.items():
    item = metadata(path)
    expected_mode = 0o700 if is_directory else 0o600
    if (
        item.st_uid != user_uid
        or item.st_gid != user_gid
        or stat.S_IMODE(item.st_mode) != expected_mode
    ):
        raise SystemExit(f"custom catalogue permission repair could not be verified: {path}")
PY_CUSTOM_CATALOGUE_PERMISSIONS

    log_success "Verified private user-owned custom vehicle catalogue"
}

install_command_links() {
    local command wrapper link current_target

    install -d -m 0755 "$COMMAND_LINK_DIR"

    for command in "${OPEN_MMI_COMMANDS[@]}"; do
        wrapper="$INSTALL_DIR/venv/bin/$command"
        link="$COMMAND_LINK_DIR/$command"

        if [ -e "$link" ] || [ -L "$link" ]; then
            if [ -L "$link" ]; then
                current_target=$(readlink "$link")
                if [ "$current_target" = "$wrapper" ]; then
                    continue
                fi
            fi

            log_error "Refusing to replace unrelated command: $link"
            return 1
        fi
    done

    for command in "${OPEN_MMI_COMMANDS[@]}"; do
        wrapper="$INSTALL_DIR/venv/bin/$command"
        link="$COMMAND_LINK_DIR/$command"
        if [ ! -L "$link" ]; then
            ln -s "$wrapper" "$link"
        fi
    done
}

remove_command_links() {
    local command wrapper link

    for command in "${OPEN_MMI_COMMANDS[@]}"; do
        wrapper="$INSTALL_DIR/venv/bin/$command"
        link="$COMMAND_LINK_DIR/$command"
        if [ -L "$link" ] && [ "$(readlink "$link")" = "$wrapper" ]; then
            rm -f "$link"
        fi
    done
}

migrate_legacy_dashboard_startup() {
    local config_file="$USER_CONFIG_DIR/launcher.json"

    if [ ! -f "$config_file" ]; then
        return 0
    fi

    if sudo -u "$REAL_USER" env HOME="$REAL_HOME" python3 - "$config_file" <<'PY_CONFIG'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(1)

if not isinstance(payload, dict) or "start_at_login" not in payload:
    raise SystemExit(1)

payload.pop("start_at_login", None)
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.chmod(temporary, 0o600)
temporary.replace(path)
PY_CONFIG
    then
        log_info "Migrating legacy dashboard-service startup preference..."
        export XDG_RUNTIME_DIR="/run/user/$USER_ID"
        sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user disable open-mmi-dashboard.service >/dev/null 2>&1 || true
    fi
}

configure_install_service_defaults() {
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user enable canbusd.service "$OWNER_CONFIG_UNIT"
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user disable open-mmi-dashboard.service >/dev/null 2>&1 || true
    migrate_legacy_dashboard_startup
}

configure_update_service_defaults() {
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user enable canbusd.service "$OWNER_CONFIG_UNIT"
    migrate_legacy_dashboard_startup
}

install_update_coordinator() {
    local authorization_added=false
    if ! getent group "$UPDATE_COORDINATOR_GROUP" >/dev/null 2>&1; then
        groupadd --system "$UPDATE_COORDINATOR_GROUP"
    fi
    if ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -Fqx "$UPDATE_COORDINATOR_GROUP"; then
        usermod -aG "$UPDATE_COORDINATOR_GROUP" "$REAL_USER"
        authorization_added=true
    fi
    install -d -m 0755 -o root -g root /etc/systemd/system
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$UPDATE_COORDINATOR_UNIT" \
        "/etc/systemd/system/$UPDATE_COORDINATOR_UNIT"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$UPDATE_INSTALLER_UNIT" \
        "/etc/systemd/system/$UPDATE_INSTALLER_UNIT"
    install -d -m 0755 -o root -g root "$UPDATE_COORDINATOR_STATE_DIR"
    systemctl daemon-reload
    systemctl enable "$UPDATE_COORDINATOR_UNIT"
    if [ "${OPEN_MMI_PREPARED_DEPLOYMENT:-0}" != 1 ]; then
        systemctl restart "$UPDATE_COORDINATOR_UNIT"
    fi
    if [ "$authorization_added" = true ]; then
        log_warn "Log out and back in before using update actions without sudo."
    fi
}

install_media_egress_service() {
    local authorization_added=false
    if ! getent group "$MEDIA_EGRESS_GROUP" >/dev/null 2>&1; then
        groupadd --system "$MEDIA_EGRESS_GROUP"
    fi
    if ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -Fqx "$MEDIA_EGRESS_GROUP"; then
        usermod -aG "$MEDIA_EGRESS_GROUP" "$REAL_USER"
        authorization_added=true
    fi
    install -d -m 0755 -o root -g root /etc/systemd/system
    install -d -m 0700 -o root -g root "$MEDIA_EGRESS_CONFIG_DIR"
    if [ ! -e "$MEDIA_EGRESS_CONFIG" ]; then
        printf '%s\n' \
            '{"config_id":"org.open-mmi.media-egress-config","jellyfin":{},"schema_version":1}' \
            > "$MEDIA_EGRESS_CONFIG"
        chown root:root "$MEDIA_EGRESS_CONFIG"
        chmod 0600 "$MEDIA_EGRESS_CONFIG"
    fi
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$MEDIA_EGRESS_UNIT" \
        "/etc/systemd/system/$MEDIA_EGRESS_UNIT"
    systemctl daemon-reload
    systemctl enable "$MEDIA_EGRESS_UNIT"
    if [ "${OPEN_MMI_PREPARED_DEPLOYMENT:-0}" != 1 ]; then
        systemctl restart "$MEDIA_EGRESS_UNIT"
    fi
    if [ "$authorization_added" = true ]; then
        log_warn "Log out and back in before using media network integrations."
    fi
}

install_vehicle_store_service() {
    if ! getent group "$MEDIA_EGRESS_GROUP" >/dev/null 2>&1; then
        groupadd --system "$MEDIA_EGRESS_GROUP"
    fi
    if ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -Fqx "$MEDIA_EGRESS_GROUP"; then
        usermod -aG "$MEDIA_EGRESS_GROUP" "$REAL_USER"
    fi
    install -d -m 0755 -o root -g root /etc/systemd/system
    install -d -m 0700 -o root -g root "$VEHICLE_STORE_ROOT"
    if ! env -u PYTHONPATH "$INSTALL_DIR/venv/bin/python" -I -m ui.vehicle_store migrate-legacy \
        --legacy-root "$USER_CONFIG_DIR" \
        --legacy-uid "$USER_ID" \
        --storage-root "$VEHICLE_STORE_ROOT"; then
        log_error "Legacy vehicle-data migration failed closed"
        return 1
    fi
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$VEHICLE_STORE_UNIT" \
        "/etc/systemd/system/$VEHICLE_STORE_UNIT"
    systemctl daemon-reload
    systemctl enable "$VEHICLE_STORE_UNIT"
    if [ "${OPEN_MMI_PREPARED_DEPLOYMENT:-0}" != 1 ]; then
        systemctl restart "$VEHICLE_STORE_UNIT"
    fi
}

install_trusted_user_services() {
    install -d -m 0755 -o root -g root "$SYSTEMD_USER_UNIT_ROOT"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/user/canbusd.service" \
        "$SYSTEMD_USER_UNIT_ROOT/canbusd.service"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/user/open-mmi-dashboard.service" \
        "$SYSTEMD_USER_UNIT_ROOT/open-mmi-dashboard.service"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/user/$OWNER_CONFIG_UNIT" \
        "$SYSTEMD_USER_UNIT_ROOT/$OWNER_CONFIG_UNIT"

    # These were historically installed as owner-writable full units.  Remove
    # the managed copies so they cannot shadow the root-owned policy units.
    rm -f \
        "$REAL_HOME/.config/systemd/user/canbusd.service" \
        "$REAL_HOME/.config/systemd/user/open-mmi-dashboard.service" \
        "$REAL_HOME/.config/systemd/user/$OWNER_CONFIG_UNIT"
    install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" \
        "$REAL_HOME/.config/systemd/user"
    install -d -m 0700 -o "$REAL_USER" -g "$REAL_USER" "$USER_CONFIG_DIR"
    install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$REAL_HOME/.config/autostart"
}

install_open_mmi_transaction_locks() {
    install -d -m 0755 -o root -g root "$(dirname "$OPEN_MMI_TMPFILES_CONFIG_PATH")"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/packaging/tmpfiles/$OPEN_MMI_TMPFILES_CONFIG" \
        "$OPEN_MMI_TMPFILES_CONFIG_PATH"
    systemd-tmpfiles --create "$OPEN_MMI_TMPFILES_CONFIG_PATH"
    install -d -m 0755 -o root -g root "$UPDATE_COORDINATOR_RUNTIME_DIR"
    python3 - "$UPDATE_COORDINATOR_RUNTIME_DIR" 0 0 <<'PY_OPEN_MMI_TRANSACTION_LOCKS'
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected_uid = int(sys.argv[2])
expected_gid = int(sys.argv[3])
names = (
    "lifecycle.lock",
    "update.lock",
    "vehicle-configuration.lock",
)

root_metadata = root.lstat()
if (
    not stat.S_ISDIR(root_metadata.st_mode)
    or root_metadata.st_uid != expected_uid
    or root_metadata.st_gid != expected_gid
    or root_metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
):
    raise SystemExit("Open MMI runtime directory is untrusted")

existing = []
for name in names:
    path = root / name
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        continue
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or metadata.st_gid != expected_gid
        or metadata.st_nlink != 1
    ):
        raise SystemExit(f"Open MMI transaction lock is untrusted: {path}")
    existing.append(path)

for path in existing:
    os.chmod(path, 0o644, follow_symlinks=False)

for name in names:
    path = root / name
    if path in existing:
        continue
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        os.fchown(descriptor, expected_uid, expected_gid)
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY_OPEN_MMI_TRANSACTION_LOCKS
}


write_vehicle_config_coordinator_environment() {
    local runtime_dropin status_path
    runtime_dropin="$REAL_HOME/.config/systemd/user/canbusd.service.d/10-can-runtime.conf"
    status_path="/run/user/$USER_ID/open-mmi/status.json"

    install -d -m 0755 -o root -g root "$(dirname "$VEHICLE_CONFIG_COORDINATOR_ENV")"
    python3 - \
        "$VEHICLE_CONFIG_COORDINATOR_ENV" \
        "$INSTALL_DIR" \
        "$USER_CONFIG_DIR" \
        "$runtime_dropin" \
        "$status_path" <<'PY_VEHICLE_CONFIG_COORDINATOR_ENV'
import json
import os
import sys
from pathlib import Path

destination = Path(sys.argv[1])
values = {
    "OPEN_MMI_INSTALL_DIR": sys.argv[2],
    "OPEN_MMI_CONFIG_DIR": sys.argv[3],
    "OPEN_MMI_RUNTIME_DROPIN": sys.argv[4],
    "OPEN_MMI_STATUS_PATH": sys.argv[5],
}

for key, value in values.items():
    if not value.startswith("/") or "\n" in value or "\r" in value or "\x00" in value:
        raise SystemExit(f"invalid coordinator path for {key}")

temporary = destination.with_name(f".{destination.name}.tmp")
temporary.write_text(
    "".join(f"{key}={json.dumps(value)}\n" for key, value in values.items()),
    encoding="utf-8",
)
os.chmod(temporary, 0o644)
os.replace(temporary, destination)
PY_VEHICLE_CONFIG_COORDINATOR_ENV
    chown root:root "$VEHICLE_CONFIG_COORDINATOR_ENV"
    chmod 0644 "$VEHICLE_CONFIG_COORDINATOR_ENV"
}

write_vehicle_config_coordinator_sandbox() {
    local runtime_directory
    runtime_directory="$REAL_HOME/.config/systemd/user/canbusd.service.d"
    install -d -m 0755 -o "$REAL_USER" -g "$REAL_USER" "$runtime_directory"
    install -d -m 0755 -o root -g root "$VEHICLE_CONFIG_COORDINATOR_OVERRIDE_DIR"
    python3 - "$VEHICLE_CONFIG_COORDINATOR_SANDBOX" "$runtime_directory" <<'PY_VEHICLE_CONFIG_COORDINATOR_SANDBOX'
import os
import sys
from pathlib import Path

destination = Path(sys.argv[1])
runtime_directory = sys.argv[2]
path = Path(runtime_directory)
if (
    not path.is_absolute()
    or ".." in path.parts
    or any(ord(character) < 32 for character in runtime_directory)
):
    raise SystemExit("invalid coordinator writable path")
quoted = runtime_directory.replace("\\", "\\\\").replace('"', '\\"')
temporary = destination.with_name(f".{destination.name}.tmp")
temporary.write_text(
    "[Service]\n"
    f'ReadWritePaths="-{quoted}"\n',
    encoding="utf-8",
)
os.chmod(temporary, 0o644)
os.replace(temporary, destination)
PY_VEHICLE_CONFIG_COORDINATOR_SANDBOX
    chown root:root "$VEHICLE_CONFIG_COORDINATOR_SANDBOX"
    chmod 0644 "$VEHICLE_CONFIG_COORDINATOR_SANDBOX"
}

wait_for_vehicle_config_coordinator() {
    local coordinator_cli="$INSTALL_DIR/venv/bin/open-mmi-config"
    local attempts="${OPEN_MMI_COORDINATOR_HEALTH_ATTEMPTS:-15}"
    local delay="${OPEN_MMI_COORDINATOR_HEALTH_DELAY:-1}"
    local attempt

    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if systemctl is-active --quiet "$VEHICLE_CONFIG_COORDINATOR_UNIT" \
            && [ -S "$VEHICLE_CONFIG_COORDINATOR_SOCKET" ] \
            && [ -x "$coordinator_cli" ] \
            && env -u PYTHONPATH "$coordinator_cli" vehicle-setup coordinator >/dev/null 2>&1; then
            return 0
        fi
        sleep "$delay"
    done

    log_error "Vehicle configuration coordinator failed its post-install health check"
    systemctl --no-pager --full status "$VEHICLE_CONFIG_COORDINATOR_UNIT" >&2 || true
    return 1
}


install_vehicle_config_coordinator() {
    local authorization_added=false
    harden_custom_catalogue_permissions
    if ! getent group "$VEHICLE_CONFIG_COORDINATOR_GROUP" >/dev/null 2>&1; then
        groupadd --system "$VEHICLE_CONFIG_COORDINATOR_GROUP"
    fi
    if ! id -nG "$REAL_USER" | tr ' ' '\n' | grep -Fqx "$VEHICLE_CONFIG_COORDINATOR_GROUP"; then
        usermod -aG "$VEHICLE_CONFIG_COORDINATOR_GROUP" "$REAL_USER"
        authorization_added=true
    fi
    write_vehicle_config_coordinator_environment
    write_vehicle_config_coordinator_sandbox
    install_open_mmi_transaction_locks
    install -d -m 0755 -o root -g root /etc/systemd/system
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$VEHICLE_CONFIG_COORDINATOR_UNIT" \
        "/etc/systemd/system/$VEHICLE_CONFIG_COORDINATOR_UNIT"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$VEHICLE_CAN_PROVISION_UNIT" \
        "/etc/systemd/system/$VEHICLE_CAN_PROVISION_UNIT"
    install -d -m 0755 -o root -g root "$UPDATE_COORDINATOR_STATE_DIR"
    systemctl daemon-reload
    systemctl enable "$VEHICLE_CONFIG_COORDINATOR_UNIT"
    systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT"
    wait_for_vehicle_config_coordinator
    if [ "$authorization_added" = true ]; then
        log_warn "Log out and back in before inspecting vehicle configuration coordinator status."
    fi
}

remove_login_autostart() {
    if [ -f "$LOGIN_AUTOSTART_ENTRY" ] && grep -Fqx "Exec=/usr/local/bin/open-mmi-launcher" "$LOGIN_AUTOSTART_ENTRY"; then
        rm -f "$LOGIN_AUTOSTART_ENTRY"
    fi
}


power_policy_enabled() {
    python3 - "$POWER_POLICY_FILE" <<'PY_POWER_POLICY_ENABLED'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    value = json.loads(path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit(1)
if not isinstance(value, dict) or value.get("schema_version") != 1:
    raise SystemExit(1)
raise SystemExit(0 if value.get("enabled") is True else 1)
PY_POWER_POLICY_ENABLED
}

reconcile_power_manager() {
    if [ ! -f "/etc/systemd/system/$POWERD_UNIT" ]; then
        systemctl disable --now "$POWERD_UNIT" >/dev/null 2>&1 || true
        return 0
    fi

    if power_policy_enabled; then
        systemctl enable "$POWERD_UNIT" >/dev/null
        systemctl restart "$POWERD_UNIT"
    else
        systemctl disable --now "$POWERD_UNIT" >/dev/null 2>&1 || true
    fi
}

install_power_manager() {
    install -d -m 0755 -o root -g root \
        /etc/open-mmi /etc/systemd/system /etc/udev/rules.d
    if [ ! -f "$POWER_POLICY_FILE" ]; then
        cat > "$POWER_POLICY_FILE" <<'EOF_POWER_POLICY'
{
  "schema_version": 1,
  "enabled": false,
  "trigger": "can_bus_silence",
  "silence_seconds": 60,
  "require_remote_wake": true,
  "resume_guard_seconds": 30
}
EOF_POWER_POLICY
    fi
    chown root:root "$POWER_POLICY_FILE"
    chmod 0644 "$POWER_POLICY_FILE"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/systemd/system/$POWERD_UNIT" \
        "/etc/systemd/system/$POWERD_UNIT"
    install -m 0644 -o root -g root \
        "$REPO_ROOT/packaging/udev/$POWERD_WAKE_UDEV_RULE" \
        "$POWERD_WAKE_UDEV_RULE_PATH"
    udevadm control --reload-rules
    udevadm trigger \
        --subsystem-match=net \
        --sysname-match='can*' \
        --action=change || true
    systemctl daemon-reload
    reconcile_power_manager
}

cmd_power() {
    local subcommand="${1:-status}"
    local seconds="${2:-60}"
    local command="$INSTALL_DIR/venv/bin/open-mmi-powerd"

    if [ ! -x "$command" ] || [ ! -f "/etc/systemd/system/$POWERD_UNIT" ]; then
        log_error "Power management is not installed; update the nightly installation first"
        return 1
    fi

    case "$subcommand" in
        enable)
            if ! [[ "$seconds" =~ ^[0-9]+$ ]] || [ "$seconds" -lt 10 ] || [ "$seconds" -gt 86400 ]; then
                log_error "Suspend silence must be an integer from 10 to 86400 seconds"
                return 1
            fi
            "$command" policy enable \
                --policy "$POWER_POLICY_FILE" \
                --silence-seconds "$seconds" >/dev/null
            chown root:root "$POWER_POLICY_FILE"
            chmod 0644 "$POWER_POLICY_FILE"
            systemctl enable --now "$POWERD_UNIT"
            log_success "Automatic suspend enabled after ${seconds}s of healthy CAN silence"
            ;;
        disable)
            "$command" policy disable --policy "$POWER_POLICY_FILE" >/dev/null
            chown root:root "$POWER_POLICY_FILE"
            chmod 0644 "$POWER_POLICY_FILE"
            systemctl disable --now "$POWERD_UNIT" >/dev/null 2>&1 || true
            log_success "Automatic suspend disabled"
            ;;
        status)
            systemctl --no-pager --full status "$POWERD_UNIT" || true
            echo
            "$command" policy show --policy "$POWER_POLICY_FILE" || true
            ;;
        *)
            echo "Usage: sudo $0 power enable [silence-seconds] | disable | status"
            return 1
            ;;
    esac
}

# =============================================================================
# PROFILE-DRIVEN PROVISIONING
# =============================================================================
resolve_maintained_profile_source() {
    local vehicle="$1"
    PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 - \
        "$REPO_ROOT" "$INSTALL_DIR" "$vehicle" <<'PYRESOLVE'
import sys
from pathlib import Path
from canbusd import profile_catalogue

repo_root = Path(sys.argv[1])
install_dir = Path(sys.argv[2])
vehicle = sys.argv[3]
for root in (install_dir, repo_root):
    try:
        resolved = profile_catalogue.resolve_profile(root, vehicle)
    except profile_catalogue.VehicleProfileCatalogueError:
        continue
    if Path(resolved["path"]).is_file():
        print(resolved["path"])
        raise SystemExit(0)
raise SystemExit(f"Vehicle profile not found: {vehicle}")
PYRESOLVE
}

apply_profile_provisioning() {
    local vehicle="${1:-seat-leon-1p-pq35}"
    local bindings="${2:-default}"

    log_info "Applying profile-driven provisioning: vehicle=$vehicle bindings=$bindings"

    python3 "$REPO_ROOT/scripts/profile_provision.py" \
        --repo-root "$REPO_ROOT" \
        --install-dir "$INSTALL_DIR" \
        --user-config-dir "$USER_CONFIG_DIR" \
        --systemd-user-dir "$REAL_HOME/.config/systemd/user" \
        --vehicle "$vehicle" \
        --bindings "$bindings" \
        --real-user "$REAL_USER"

    harden_custom_catalogue_permissions
    chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/systemd/user" || true
}

reload_profile_provisioning() {
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"

    log_info "Reloading systemd user service files..."
    sudo -u "$REAL_USER" \
        HOME="$REAL_HOME" \
        XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        systemctl --user daemon-reload

    log_info "Reloading udev rules..."
    sudo udevadm control --reload-rules
    sudo udevadm trigger

    if daemon_running; then
        log_info "Restarting daemon..."
        sudo -u "$REAL_USER" \
            HOME="$REAL_HOME" \
            XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
            systemctl --user restart canbusd.service
    fi
}

# =============================================================================
# EXPLICIT LOCAL DEPLOYMENT
# =============================================================================

validate_local_deploy_checkout() {
    local repository_status

    repository_status=$(
        sudo -u "$REAL_USER" git -C "$REPO_ROOT" \
            status --porcelain=v1 --untracked-files=normal
    ) || {
        log_error "Could not inspect local deployment checkout"
        return 1
    }

    if [ -n "$repository_status" ]; then
        log_error "Local deployment requires a completely clean Git checkout"
        log_info "Commit, remove, or stash tracked and untracked changes first"
        return 1
    fi
}

validate_local_deploy_build_environment() {
    local build_python="$1"

    if [ ! -x "$build_python" ]; then
        log_error "Local deployment build Python is unavailable: $build_python"
        log_info "Create the repository .venv and provision setuptools>=77 first"
        return 1
    fi

    if ! sudo -u "$REAL_USER" \
        env -u PYTHONPATH "$build_python" -I - <<'PY_LOCAL_BUILD_ENV'
import re
import sys
from importlib.metadata import version

try:
    import pip  # noqa: F401
    import setuptools.build_meta  # noqa: F401
    setuptools_version = version("setuptools")
except Exception as exc:
    print(f"local build environment unavailable: {exc}", file=sys.stderr)
    raise SystemExit(1)

match = re.match(
    r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(.*)$",
    setuptools_version,
)
if match is None:
    print(
        f"unsupported setuptools version: {setuptools_version}",
        file=sys.stderr,
    )
    raise SystemExit(1)

major = int(match.group(1))
suffix = match.group(4).lower()
prerelease = bool(
    re.match(r"^(?:a|b|rc)\d*|^\.dev\d*", suffix)
)

if major < 77 or (major == 77 and prerelease):
    print(
        f"setuptools>=77 required, found {setuptools_version}",
        file=sys.stderr,
    )
    raise SystemExit(1)
PY_LOCAL_BUILD_ENV
    then
        log_error "Local deployment build environment is not ready"
        log_info "Provision $REPO_ROOT/.venv with setuptools>=77 first"
        return 1
    fi
}

cmd_deploy_local() {
    local runtime_python="$INSTALL_DIR/venv/bin/python"
    local build_python="$REPO_ROOT/.venv/bin/python"
    local commit
    local branch
    local upstream
    local version
    local transaction
    local stage
    local rollback_root
    local candidate_wheel_dir
    local candidate_wheel
    local build_root
    local build_repo
    local build_wheel_dir
    local local_wheel
    local -a local_wheels=()

    [[ $EUID -eq 0 ]] || { log_error "Local deployment requires root"; return 1; }

    if ! is_installed; then
        log_error "$APP_NAME not installed"
        log_info "Use './manage.sh install' for the initial installation"
        return 1
    fi

    if [ ! -x "$runtime_python" ]; then
        log_error "Installed Python runtime is unavailable: $runtime_python"
        return 1
    fi

    # Reject any tracked or untracked checkout change before creating build
    # or root-owned staging state.
    validate_local_deploy_checkout

    # Candidate code is built with the repository-local developer environment,
    # never with the installed production runtime. Validate it before creating
    # temporary build or root-owned staging state.
    validate_local_deploy_build_environment "$build_python"

    commit=$(
        sudo -u "$REAL_USER" git -C "$REPO_ROOT" rev-parse HEAD
    ) || {
        log_error "Could not identify local deployment commit"
        return 1
    }

    [[ "$commit" =~ ^[0-9a-fA-F]{40}$ ]] || {
        log_error "Local deployment commit is invalid"
        return 1
    }

    branch=$(
        sudo -u "$REAL_USER" git -C "$REPO_ROOT" \
            rev-parse --abbrev-ref HEAD
    ) || {
        log_error "Could not identify local deployment branch"
        return 1
    }

    if [[ -z "$branch" || "$branch" == "HEAD" ]]; then
        log_error "Local deployment requires a named Git branch"
        return 1
    fi

    upstream=$(get_repo_upstream)
    version=$(get_current_version)

    [[ -n "$upstream" && -n "$version" ]] || {
        log_error "Local deployment metadata is incomplete"
        return 1
    }

    transaction="prepare-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
    stage="/var/lib/open-mmi/staging/$transaction"
    rollback_root="/var/lib/open-mmi/rollback/$transaction"
    candidate_wheel_dir="$rollback_root/candidate-wheel"

    log_warn "Explicit administrator local deployment selected"
    log_info "Source branch: $branch"
    log_info "Source commit: $commit"
    log_info "Source version: $version"
    log_info "No remote release discovery will be performed"

    # Build candidate code without executing the candidate build backend as
    # root. The temporary workspace belongs only to the invoking real user.
    build_root=$(
        sudo -u "$REAL_USER" \
            mktemp -d /var/tmp/open-mmi-local-deploy.XXXXXXXX
    ) || {
        log_error "Could not create local deployment build workspace"
        return 1
    }

    build_repo="$build_root/source"
    build_wheel_dir="$build_root/wheel"

    if ! sudo -u "$REAL_USER" \
        git -c protocol.file.allow=always \
            clone --no-hardlinks --no-checkout -- \
            "$REPO_ROOT" "$build_repo"; then
        rm -rf -- "$build_root"
        log_error "Could not stage local deployment source"
        return 1
    fi

    if ! sudo -u "$REAL_USER" \
        git -C "$build_repo" checkout --detach "$commit"; then
        rm -rf -- "$build_root"
        log_error "Could not select local deployment commit"
        return 1
    fi

    if [[ $(
        sudo -u "$REAL_USER" git -C "$build_repo" rev-parse HEAD
    ) != "$commit" ]]; then
        rm -rf -- "$build_root"
        log_error "Local build source does not match selected commit"
        return 1
    fi

    sudo -u "$REAL_USER" mkdir -m 0700 "$build_wheel_dir"

    if ! PIP_CONFIG_FILE=/dev/null \
        PIP_NO_INDEX=1 \
        PIP_NO_CACHE_DIR=1 \
        PIP_DISABLE_PIP_VERSION_CHECK=1 \
        sudo -u "$REAL_USER" \
            env -u PYTHONPATH "$build_python" -I -m pip wheel \
                --no-deps \
                --no-index \
                --no-build-isolation \
                --wheel-dir "$build_wheel_dir" \
                "$build_repo"; then
        rm -rf -- "$build_root"
        log_error "Local deployment wheel build failed"
        return 1
    fi

    while IFS= read -r -d '' wheel; do
        local_wheels+=("$wheel")
    done < <(
        find "$build_wheel_dir" \
            -maxdepth 1 \
            -type f \
            -name 'open_mmi-*.whl' \
            -print0
    )

    if (( ${#local_wheels[@]} != 1 )); then
        rm -rf -- "$build_root"
        log_error "Local deployment did not produce exactly one Open MMI wheel"
        return 1
    fi

    local_wheel="${local_wheels[0]}"

    # Run the repository's wheel structure verifier without root authority.
    if ! sudo -u "$REAL_USER" \
        env -u PYTHONPATH "$build_python" -I \
            "$build_repo/tools/verify_wheel.py" \
            "$local_wheel"; then
        rm -rf -- "$build_root"
        log_error "Local deployment wheel verification failed"
        return 1
    fi

    # The prepared deployment engine requires a root-owned stage whose Git
    # identity is independently pinned to the selected commit.
    install -d -m 0700 -o root -g root \
        "$(dirname "$stage")" \
        "$(dirname "$rollback_root")"

    if ! git -c protocol.file.allow=always \
        clone --no-hardlinks --no-checkout -- \
        "$REPO_ROOT" "$stage"; then
        rm -rf -- "$build_root" "$stage"
        log_error "Could not create trusted local deployment stage"
        return 1
    fi

    if ! git -c safe.directory="$stage" -C "$stage" \
        checkout --detach "$commit"; then
        rm -rf -- "$build_root" "$stage"
        log_error "Could not select trusted staged commit"
        return 1
    fi

    if [[ $(git -c safe.directory="$stage" -C "$stage" rev-parse HEAD) != "$commit" ]]; then
        rm -rf -- "$build_root" "$stage"
        log_error "Trusted stage does not match selected commit"
        return 1
    fi

    [[ ! -L "$stage" && $(stat -c '%u' "$stage") -eq 0 ]] || {
        rm -rf -- "$build_root" "$stage"
        log_error "Local deployment stage is untrusted"
        return 1
    }

    install -d -m 0700 -o root -g root "$candidate_wheel_dir"

    candidate_wheel="$candidate_wheel_dir/$(basename "$local_wheel")"
    install -m 0644 -o root -g root \
        "$local_wheel" \
        "$candidate_wheel"

    # The user-owned build workspace is no longer needed once the selected
    # candidate has crossed into the private root-owned transaction.
    rm -rf -- "$build_root"

    # Reuse the hardened prepared deployment engine for /opt backup, package
    # replacement, service installation, health checks, and rollback. D1
    # explicitly withholds permission to mutate the developer repository.
    OPEN_MMI_PREPARED_STAGE="$stage" \
    OPEN_MMI_PREPARED_TRANSACTION="$transaction" \
    OPEN_MMI_PREPARED_COMMIT="$commit" \
    OPEN_MMI_PREVIOUS_COMMIT="$commit" \
    OPEN_MMI_PREPARED_VERSION="$version" \
    OPEN_MMI_PREPARED_WHEEL="$candidate_wheel" \
    OPEN_MMI_MANAGED_REPOSITORY="$REPO_ROOT" \
    OPEN_MMI_MANAGED_BRANCH="$branch" \
    OPEN_MMI_MANAGED_UPSTREAM="$upstream" \
    OPEN_MMI_PRESERVE_MANAGED_REPOSITORY=1 \
        cmd_deploy_prepared

    rm -rf -- "$stage" "$rollback_root"

    log_success "Explicit local deployment complete → $version"
}

# =============================================================================
# INSTALL
# =============================================================================

cmd_install() {
    log_info "Installing $APP_NAME..."
    
    # Pre-flight checks
    if is_installed; then
        log_error "$APP_NAME already installed at $INSTALL_DIR"
        log_info "Use './manage.sh update' to update"
        return 1
    fi
    
    if ! check_dependencies; then
        log_info "Install missing dependencies first: sudo apt install git python3 python3-pip"
        return 1
    fi
    
    # Install system dependencies
    log_info "Installing system dependencies..."
    if ! sudo apt update; then
        log_error "Failed to update package list"
        return 1
    fi
    
    if ! sudo apt install -y \
        python3 \
        python3-pip \
        python3-venv \
        can-utils \
        udev \
        dbus-x11 \
        zenity; then
        log_error "Failed to install system dependencies"
        return 1
    fi
    
    # Create install directory
    log_info "Creating install directory..."
    sudo install -d -m 0755 -o root -g root "$INSTALL_DIR"
    
    # Create Python virtual environment
    log_info "Creating Python virtual environment..."
    if ! python3 -m venv "$INSTALL_DIR/venv"; then
        log_error "Failed to create virtual environment"
        return 1
    fi
    
    if ! upgrade_python_packaging_tools "$INSTALL_DIR/venv/bin/python"; then
        return 1
    fi
    
    # Copy application files
    log_info "Copying application files..."
    cp -r "$REPO_ROOT/canbusd" "$INSTALL_DIR/"
    cp -r "$REPO_ROOT/vehicles" "$INSTALL_DIR/"
    cp -r "$REPO_ROOT/bindings" "$INSTALL_DIR/"
    cp -r "$REPO_ROOT/actions" "$INSTALL_DIR/"
    cp -r "$REPO_ROOT/powerd" "$INSTALL_DIR/"

    if [ -d "$REPO_ROOT/ui" ]; then
        cp -r "$REPO_ROOT/ui" "$INSTALL_DIR/"
    fi

    cp -r "$REPO_ROOT/scripts" "$INSTALL_DIR/"
    cp -r "$REPO_ROOT/packaging" "$INSTALL_DIR/"
    cp "$REPO_ROOT/pyproject.toml" "$INSTALL_DIR/"
    cp "$REPO_ROOT/README.md" "$INSTALL_DIR/"
    cp "$REPO_ROOT/LICENSE" "$INSTALL_DIR/"

    configure_maintained_catalogue_permissions

    if ! install_open_mmi_package; then
        return 1
    fi
    if ! install_command_links; then
        return 1
    fi
    install_update_coordinator
    install_media_egress_service
    install_vehicle_store_service
    install_vehicle_config_coordinator
    install_power_manager
    
    # Store version and the managed source descriptor used by read-only checks.
    get_current_version > "$VERSION_FILE"
    write_checkout_update_source_metadata
    
    # Install systemd service
    log_info "Installing systemd user service..."
    install_trusted_user_services
    install_desktop_entry
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"
    mkdir -p "$REAL_HOME/.config/systemd/user/default.target.wants"
    chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/systemd/user"

    sudo -u "$REAL_USER" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user daemon-reload
    configure_install_service_defaults
    # Apply default profile-driven CAN provisioning.
    # This creates user config if missing, writes the daemon runtime drop-in,
    # and generates udev rules from the selected vehicle profile metadata.
    apply_profile_provisioning "seat-leon-1p-pq35" "default"
    reload_profile_provisioning
    # The generated runtime drop-in directory may not have existed when the
    # coordinator first started. Restart once so its mount namespace receives
    # the now-present exact writable-path exception used only for recovery.
    systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT"
    wait_for_vehicle_config_coordinator
    
    # Set permissions
    log_info "Configuring user permissions..."
    sudo usermod -aG video,input "$REAL_USER"
    
    # Try to fix backlight immediately
    if [ -e /sys/class/backlight/intel_backlight/brightness ]; then
        sudo chgrp video /sys/class/backlight/intel_backlight/brightness || true
        sudo chmod 664 /sys/class/backlight/intel_backlight/brightness || true
    fi
    
    # Start services
    log_info "Starting Open MMI services..."
    sudo -u "$REAL_USER" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user restart canbusd "$OWNER_CONFIG_UNIT" open-mmi-dashboard
    
    # Verify
    sleep 1
    if daemon_running; then
        log_success "Installation complete!"
        cmd_status
    else
        log_warn "Installation complete but daemon failed to start"
        log_info "Check logs: journalctl --user -u canbusd -f"
        return 1
    fi
}

# =============================================================================
# UPDATE
# =============================================================================

cmd_update() {
    log_info "Updating $APP_NAME through the trusted coordinator..."

    if ! is_installed; then
        log_error "$APP_NAME not installed"
        return 1
    fi

    local python="$INSTALL_DIR/venv/bin/python"
    if [ ! -x "$python" ]; then
        log_error "Installed Python runtime is unavailable: $python"
        return 1
    fi

    # External release discovery/fetch authority belongs only to the root-owned
    # update coordinator.  The management shell never performs git fetch/pull
    # or package-index access during an installed-system update.
    env -u PYTHONPATH "$python" -I -m ui.config_cli updates check
    env -u PYTHONPATH "$python" -I -m ui.config_cli updates prepare
    env -u PYTHONPATH "$python" -I -m ui.config_cli updates install
}

cmd_deploy_prepared() {
    local stage="${OPEN_MMI_PREPARED_STAGE:-}"
    local transaction="${OPEN_MMI_PREPARED_TRANSACTION:-}"
    local commit="${OPEN_MMI_PREPARED_COMMIT:-}"
    local previous_commit="${OPEN_MMI_PREVIOUS_COMMIT:-}"
    local version="${OPEN_MMI_PREPARED_VERSION:-}"
    local rollback_root="/var/lib/open-mmi/rollback/$transaction"
    local deployment_stage="backup"
    local candidate_wheel="${OPEN_MMI_PREPARED_WHEEL:-}"
    local resolved_stage
    local resolved_wheel

    [[ $EUID -eq 0 ]] || { log_error "Prepared deployment requires root"; return 1; }
    [[ "$transaction" =~ ^prepare-[0-9a-f]{32}$ ]] || { log_error "Invalid prepared transaction"; return 1; }
    [[ "$commit" =~ ^[0-9a-fA-F]{40}$ ]] || { log_error "Invalid prepared commit"; return 1; }
    [[ "$previous_commit" =~ ^[0-9a-fA-F]{40}$ ]] || { log_error "Invalid previous commit"; return 1; }
    [[ -n "$version" && -n "${OPEN_MMI_MANAGED_REPOSITORY:-}" && -n "${OPEN_MMI_MANAGED_BRANCH:-}" && -n "${OPEN_MMI_MANAGED_UPSTREAM:-}" ]] || {
        log_error "Prepared deployment metadata is incomplete"; return 1;
    }
    resolved_stage=$(realpath -e -- "$stage") || { log_error "Prepared stage is unavailable"; return 1; }
    [[ "$resolved_stage" == "/var/lib/open-mmi/staging/$transaction" ]] || {
        log_error "Prepared stage is outside managed staging"; return 1;
    }
    [[ ! -L "$resolved_stage" && $(stat -c '%u' "$resolved_stage") -eq 0 ]] || {
        log_error "Prepared stage is untrusted"; return 1;
    }
    [[ $(git -c safe.directory="$resolved_stage" -C "$resolved_stage" rev-parse HEAD) == "$commit" ]] || {
        log_error "Prepared commit identity changed"; return 1;
    }
    resolved_wheel=$(realpath -e -- "$candidate_wheel") || { log_error "Prepared wheel is unavailable"; return 1; }
    [[ "$resolved_wheel" == "$rollback_root/candidate-wheel/"open_mmi-*.whl ]] || {
        log_error "Prepared wheel is outside trusted transaction artifacts"; return 1;
    }
    [[ -f "$resolved_wheel" && ! -L "$resolved_wheel" && $(stat -c '%u' "$resolved_wheel") -eq 0 ]] || {
        log_error "Prepared wheel is untrusted"; return 1;
    }
    (( (8#$(stat -c '%a' "$resolved_wheel") & 8#022) == 0 )) || {
        log_error "Prepared wheel permissions are untrusted"; return 1;
    }
    candidate_wheel="$resolved_wheel"

    # Failures before the rollback function is installed still need a
    # user-visible, allowlisted stage instead of a generic deployment error.
    trap 'log_error "Prepared deployment failed at stage: $deployment_stage"' ERR

    deployment_stage="install-root"
    harden_install_root_ownership
    deployment_stage="backup"

    install -d -m 0700 -o root -g root "$rollback_root"
    if [ -e "$INSTALL_DIR" ]; then
        cp -a -- "$INSTALL_DIR" "$rollback_root/installation"
        env -u PYTHONPATH "$rollback_root/installation/venv/bin/python" -I -c 'import ui.config_cli'
    fi
    install -d -m 0700 -o root -g root \
        "$rollback_root/system-units" \
        "$rollback_root/system-files" \
        "$rollback_root/user-units" \
        "$rollback_root/trusted-user-units"
    for unit in "$UPDATE_COORDINATOR_UNIT" "$UPDATE_INSTALLER_UNIT" "$MEDIA_EGRESS_UNIT" "$VEHICLE_STORE_UNIT" "$VEHICLE_CONFIG_COORDINATOR_UNIT" "$VEHICLE_CAN_PROVISION_UNIT" "$POWERD_UNIT"; do
        if [ -e "/etc/systemd/system/$unit" ]; then
            cp -a -- "/etc/systemd/system/$unit" "$rollback_root/system-units/$unit"
        else
            : > "$rollback_root/system-units/$unit.absent"
        fi
    done
    for unit in canbusd.service open-mmi-dashboard.service "$OWNER_CONFIG_UNIT"; do
        if [ -e "$SYSTEMD_USER_UNIT_ROOT/$unit" ]; then
            cp -a -- "$SYSTEMD_USER_UNIT_ROOT/$unit" "$rollback_root/trusted-user-units/$unit"
        else
            : > "$rollback_root/trusted-user-units/$unit.absent"
        fi
    done
    if [ -e "$MEDIA_EGRESS_CONFIG" ]; then
        cp -a -- "$MEDIA_EGRESS_CONFIG" "$rollback_root/system-files/media-egress-config.json"
    else
        : > "$rollback_root/system-files/media-egress-config.json.absent"
    fi
    if [ -e "$VEHICLE_CONFIG_COORDINATOR_ENV" ]; then
        cp -a -- "$VEHICLE_CONFIG_COORDINATOR_ENV" \
            "$rollback_root/system-files/vehicle-config-coordinator.env"
    else
        : > "$rollback_root/system-files/vehicle-config-coordinator.env.absent"
    fi
    if [ -e "$VEHICLE_CONFIG_COORDINATOR_SANDBOX" ]; then
        cp -a -- "$VEHICLE_CONFIG_COORDINATOR_SANDBOX" \
            "$rollback_root/system-files/vehicle-config-coordinator-sandbox.conf"
    else
        : > "$rollback_root/system-files/vehicle-config-coordinator-sandbox.conf.absent"
    fi
    if [ -e "$POWER_POLICY_FILE" ]; then
        cp -a -- "$POWER_POLICY_FILE" \
            "$rollback_root/system-files/power-policy.json"
    else
        : > "$rollback_root/system-files/power-policy.json.absent"
    fi
    if [ -e "$POWERD_WAKE_UDEV_RULE_PATH" ]; then
        cp -a -- "$POWERD_WAKE_UDEV_RULE_PATH" \
            "$rollback_root/system-files/$POWERD_WAKE_UDEV_RULE"
    else
        : > "$rollback_root/system-files/$POWERD_WAKE_UDEV_RULE.absent"
    fi
    if [ -e "$OPEN_MMI_TMPFILES_CONFIG_PATH" ]; then
        cp -a -- "$OPEN_MMI_TMPFILES_CONFIG_PATH" \
            "$rollback_root/system-files/$OPEN_MMI_TMPFILES_CONFIG"
    else
        : > "$rollback_root/system-files/$OPEN_MMI_TMPFILES_CONFIG.absent"
    fi
    for unit in canbusd.service open-mmi-dashboard.service; do
        if [ -e "$REAL_HOME/.config/systemd/user/$unit" ]; then
            cp -a -- "$REAL_HOME/.config/systemd/user/$unit" "$rollback_root/user-units/$unit"
        else
            : > "$rollback_root/user-units/$unit.absent"
        fi
    done

    rollback_prepared_deployment() {
        trap - ERR
        log_error "Prepared deployment failed at stage: $deployment_stage"
        log_error "Restoring previous installation"
        if [ -d "$rollback_root/installation" ]; then
            local failed_install="$INSTALL_DIR.failed-$transaction"
            local restored_install="$INSTALL_DIR.restore-$transaction"
            rm -rf -- "$failed_install" "$restored_install"
            cp -a -- "$rollback_root/installation" "$restored_install"
            mv -- "$INSTALL_DIR" "$failed_install"
            mv -- "$restored_install" "$INSTALL_DIR"
            if env -u PYTHONPATH "$INSTALL_DIR/venv/bin/python" -I -c 'import ui.config_cli' >/dev/null 2>&1; then
                log_success "Prepared rollback verified"
                rm -rf -- "$failed_install"
            else
                log_error "Previous Python installation could not be verified after restoration"
            fi
        fi
        if [[ "${OPEN_MMI_PRESERVE_MANAGED_REPOSITORY:-0}" != "1" ]] && \
           [ -d "${OPEN_MMI_MANAGED_REPOSITORY:-}/.git" ]; then
            sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" reset --hard "$previous_commit" >/dev/null 2>&1 || true
        fi
        for unit in "$UPDATE_COORDINATOR_UNIT" "$UPDATE_INSTALLER_UNIT" "$MEDIA_EGRESS_UNIT" "$VEHICLE_STORE_UNIT" "$VEHICLE_CONFIG_COORDINATOR_UNIT" "$VEHICLE_CAN_PROVISION_UNIT" "$POWERD_UNIT"; do
            if [ -e "$rollback_root/system-units/$unit" ]; then
                cp -a -- "$rollback_root/system-units/$unit" "/etc/systemd/system/$unit"
            elif [ -e "$rollback_root/system-units/$unit.absent" ]; then
                rm -f -- "/etc/systemd/system/$unit"
            fi
        done
        install -d -m 0755 -o root -g root "$SYSTEMD_USER_UNIT_ROOT"
        for unit in canbusd.service open-mmi-dashboard.service "$OWNER_CONFIG_UNIT"; do
            if [ -e "$rollback_root/trusted-user-units/$unit" ]; then
                cp -a -- "$rollback_root/trusted-user-units/$unit" "$SYSTEMD_USER_UNIT_ROOT/$unit"
            elif [ -e "$rollback_root/trusted-user-units/$unit.absent" ]; then
                rm -f -- "$SYSTEMD_USER_UNIT_ROOT/$unit"
            fi
        done
        if [ -e "$rollback_root/system-files/media-egress-config.json" ]; then
            install -d -m 0700 -o root -g root "$MEDIA_EGRESS_CONFIG_DIR"
            cp -a -- "$rollback_root/system-files/media-egress-config.json" "$MEDIA_EGRESS_CONFIG"
        elif [ -e "$rollback_root/system-files/media-egress-config.json.absent" ]; then
            rm -f -- "$MEDIA_EGRESS_CONFIG"
            rmdir "$MEDIA_EGRESS_CONFIG_DIR" >/dev/null 2>&1 || true
        fi
        if [ -e "$rollback_root/system-files/vehicle-config-coordinator.env" ]; then
            install -d -m 0755 -o root -g root "$(dirname "$VEHICLE_CONFIG_COORDINATOR_ENV")"
            cp -a -- "$rollback_root/system-files/vehicle-config-coordinator.env" \
                "$VEHICLE_CONFIG_COORDINATOR_ENV"
        elif [ -e "$rollback_root/system-files/vehicle-config-coordinator.env.absent" ]; then
            rm -f -- "$VEHICLE_CONFIG_COORDINATOR_ENV"
        fi
        if [ -e "$rollback_root/system-files/vehicle-config-coordinator-sandbox.conf" ]; then
            install -d -m 0755 -o root -g root "$VEHICLE_CONFIG_COORDINATOR_OVERRIDE_DIR"
            cp -a -- "$rollback_root/system-files/vehicle-config-coordinator-sandbox.conf" \
                "$VEHICLE_CONFIG_COORDINATOR_SANDBOX"
        elif [ -e "$rollback_root/system-files/vehicle-config-coordinator-sandbox.conf.absent" ]; then
            rm -f -- "$VEHICLE_CONFIG_COORDINATOR_SANDBOX"
        fi
        if [ -e "$rollback_root/system-files/power-policy.json" ]; then
            install -d -m 0755 -o root -g root "$(dirname "$POWER_POLICY_FILE")"
            cp -a -- "$rollback_root/system-files/power-policy.json" \
                "$POWER_POLICY_FILE"
        elif [ -e "$rollback_root/system-files/power-policy.json.absent" ]; then
            rm -f -- "$POWER_POLICY_FILE"
        fi
        if [ -e "$rollback_root/system-files/$POWERD_WAKE_UDEV_RULE" ]; then
            install -d -m 0755 -o root -g root "$(dirname "$POWERD_WAKE_UDEV_RULE_PATH")"
            cp -a -- "$rollback_root/system-files/$POWERD_WAKE_UDEV_RULE" \
                "$POWERD_WAKE_UDEV_RULE_PATH"
        elif [ -e "$rollback_root/system-files/$POWERD_WAKE_UDEV_RULE.absent" ]; then
            rm -f -- "$POWERD_WAKE_UDEV_RULE_PATH"
        fi
        if [ -e "$rollback_root/system-files/$OPEN_MMI_TMPFILES_CONFIG" ]; then
            install -d -m 0755 -o root -g root "$(dirname "$OPEN_MMI_TMPFILES_CONFIG_PATH")"
            cp -a -- "$rollback_root/system-files/$OPEN_MMI_TMPFILES_CONFIG" \
                "$OPEN_MMI_TMPFILES_CONFIG_PATH"
            systemd-tmpfiles --create "$OPEN_MMI_TMPFILES_CONFIG_PATH" >/dev/null 2>&1 || true
        elif [ -e "$rollback_root/system-files/$OPEN_MMI_TMPFILES_CONFIG.absent" ]; then
            rm -f -- "$OPEN_MMI_TMPFILES_CONFIG_PATH"
        fi
        udevadm control --reload-rules >/dev/null 2>&1 || true
        udevadm trigger \
            --subsystem-match=net \
            --sysname-match='can*' \
            --action=change >/dev/null 2>&1 || true
        for unit in canbusd.service open-mmi-dashboard.service; do
            if [ -e "$rollback_root/user-units/$unit" ]; then
                cp -a -- "$rollback_root/user-units/$unit" "$REAL_HOME/.config/systemd/user/$unit"
                chown "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/systemd/user/$unit"
            elif [ -e "$rollback_root/user-units/$unit.absent" ]; then
                rm -f -- "$REAL_HOME/.config/systemd/user/$unit"
            fi
        done
        systemctl daemon-reload >/dev/null 2>&1 || true
        if [ -e "/etc/systemd/system/$MEDIA_EGRESS_UNIT" ]; then
            systemctl restart "$MEDIA_EGRESS_UNIT" >/dev/null 2>&1 || true
        else
            systemctl stop "$MEDIA_EGRESS_UNIT" >/dev/null 2>&1 || true
        fi
        if [ -e "/etc/systemd/system/$VEHICLE_STORE_UNIT" ]; then
            systemctl restart "$VEHICLE_STORE_UNIT" >/dev/null 2>&1 || true
        else
            systemctl stop "$VEHICLE_STORE_UNIT" >/dev/null 2>&1 || true
        fi
        if [ -e "/etc/systemd/system/$VEHICLE_CONFIG_COORDINATOR_UNIT" ]; then
            systemctl restart "$VEHICLE_CONFIG_COORDINATOR_UNIT" >/dev/null 2>&1 || true
        else
            systemctl stop "$VEHICLE_CONFIG_COORDINATOR_UNIT" >/dev/null 2>&1 || true
        fi
        reconcile_power_manager >/dev/null 2>&1 || true
        export XDG_RUNTIME_DIR="/run/user/$USER_ID"
        sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
            systemctl --user daemon-reload >/dev/null 2>&1 || true
        sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
            systemctl --user restart canbusd.service "$OWNER_CONFIG_UNIT" open-mmi-dashboard.service >/dev/null 2>&1 || true
    }
    trap rollback_prepared_deployment ERR

    deployment_stage="packaging-tools"
    record_python_packaging_tool_version "$INSTALL_DIR/venv/bin/python" "$rollback_root"

    deployment_stage="package-artifact"
    # The already-installed trusted installer built and byte-verified this exact
    # wheel against the candidate Git-object inventory before invoking us.
    [[ -s "$candidate_wheel" ]]

    deployment_stage="repository-head"
    [[ $(sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" rev-parse HEAD) == "$previous_commit" ]]
    deployment_stage="repository-clean"
    sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" diff --quiet
    sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" diff --cached --quiet
    deployment_stage="repository-object"
    sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" cat-file -e "$commit^{commit}"
    deployment_stage="repository-merge"
    if [[ "${OPEN_MMI_PRESERVE_MANAGED_REPOSITORY:-0}" != "1" ]]; then
        sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" merge --ff-only "$commit"
    else
        [[ $(sudo -u "$REAL_USER" git -C "$OPEN_MMI_MANAGED_REPOSITORY" rev-parse HEAD) == "$commit" ]]
    fi

    deployment_stage="files"
    log_info "Deploying prepared candidate $version..."
    find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 \
        \( -name venv -o -name .version -o -name .update-source.json \) -prune \
        -o -exec rm -rf -- {} +
    for item in canbusd vehicles bindings actions powerd ui scripts packaging systemd; do
        [ ! -e "$resolved_stage/$item" ] || cp -a -- "$resolved_stage/$item" "$INSTALL_DIR/"
    done
    for item in pyproject.toml README.md LICENSE; do
        cp -a -- "$resolved_stage/$item" "$INSTALL_DIR/"
    done

    configure_maintained_catalogue_permissions

    REPO_ROOT="$resolved_stage"
    DESKTOP_ENTRY_SOURCE="$REPO_ROOT/packaging/linux-desktop/open-mmi-status.desktop"
    CHOOSER_ENTRY_SOURCE="$REPO_ROOT/packaging/linux-desktop/open-mmi-chooser.desktop"
    DESKTOP_ICON_SOURCE="$REPO_ROOT/packaging/linux-desktop/icons"
    deployment_stage="package"
    install_open_mmi_package "$candidate_wheel"
    install_command_links
    deployment_stage="system-services"
    install_update_coordinator
    install_media_egress_service
    install_vehicle_store_service
    deployment_stage="vehicle-config-coordinator"
    install_vehicle_config_coordinator
    deployment_stage="power-manager"
    install_power_manager

    deployment_stage="user-services"
    install_trusted_user_services
    install_desktop_entry

    printf '%s\n' "$version" > "$VERSION_FILE"
    write_update_source_metadata
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user daemon-reload
    configure_update_service_defaults
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        systemctl --user restart canbusd.service "$OWNER_CONFIG_UNIT" open-mmi-dashboard.service

    deployment_stage="service-health"
    sudo -u "$REAL_USER" env HOME="$REAL_HOME" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        systemctl --user is-active --quiet canbusd.service "$OWNER_CONFIG_UNIT" open-mmi-dashboard.service
    deployment_stage="api-health"
    local api_ready=false
    for _attempt in {1..15}; do
        if curl --fail --silent --max-time 2 http://127.0.0.1:8765/api/health >/dev/null; then
            api_ready=true
            break
        fi
        sleep 1
    done
    [[ "$api_ready" == true ]]
    deployment_stage="version-health"
    local version_ready=false
    for _attempt in {1..15}; do
        if curl --fail --silent --max-time 2 http://127.0.0.1:8765/api/version | \
            python3 -c 'import json,sys; expected=sys.argv[1]; payload=json.load(sys.stdin); raise SystemExit(0 if payload.get("build_id") == expected else 1)' "$version"; then
            version_ready=true
            break
        fi
        sleep 1
    done
    [[ "$version_ready" == true ]]
    trap - ERR
    log_success "Prepared update complete → $version"
}

# =============================================================================
# UNINSTALL
# =============================================================================

cmd_uninstall() {
    log_warn "This will uninstall $APP_NAME"
    
    if ! is_installed; then
        log_error "$APP_NAME not installed"
        return 1
    fi
    
    if ! confirm "Are you sure you want to uninstall?"; then
        log_info "Uninstall cancelled"
        return 0
    fi
    
    # Create backup before uninstall
    if confirm "Create backup before uninstalling?"; then
        log_info "Creating backup..."
        sudo mkdir -p "$BACKUP_DIR"
        local backup_name="backup-uninstall-$(date +%Y%m%d-%H%M%S)"
        local backup_path="$BACKUP_DIR/$backup_name"
        
        if sudo cp -r "$INSTALL_DIR" "$backup_path"; then
            log_success "Backup created: $backup_path"
        else
            log_warn "Failed to create backup (continuing anyway)"
        fi
    fi
    
    # Stop services
    log_info "Stopping systemd services..."
    export XDG_RUNTIME_DIR="/run/user/$USER_ID"
    for service in canbusd.service open-mmi-dashboard.service "$OWNER_CONFIG_UNIT"; do
        sudo -u "$REAL_USER" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" systemctl --user disable --now "$service" >/dev/null 2>&1 || true
    done
    systemctl disable --now "$UPDATE_COORDINATOR_UNIT" >/dev/null 2>&1 || true
    systemctl disable --now "$MEDIA_EGRESS_UNIT" >/dev/null 2>&1 || true
    systemctl disable --now "$VEHICLE_STORE_UNIT" >/dev/null 2>&1 || true
    systemctl disable --now "$VEHICLE_CONFIG_COORDINATOR_UNIT" >/dev/null 2>&1 || true
    systemctl disable --now "$POWERD_UNIT" >/dev/null 2>&1 || true
    systemctl stop "$VEHICLE_CAN_PROVISION_UNIT" >/dev/null 2>&1 || true
    systemctl stop "$UPDATE_INSTALLER_UNIT" >/dev/null 2>&1 || true
    rm -f \
        "/etc/systemd/system/$UPDATE_COORDINATOR_UNIT" \
        "/etc/systemd/system/$UPDATE_INSTALLER_UNIT" \
        "/etc/systemd/system/$MEDIA_EGRESS_UNIT" \
        "/etc/systemd/system/$VEHICLE_STORE_UNIT" \
        "$SYSTEMD_USER_UNIT_ROOT/canbusd.service" \
        "$SYSTEMD_USER_UNIT_ROOT/open-mmi-dashboard.service" \
        "$SYSTEMD_USER_UNIT_ROOT/$OWNER_CONFIG_UNIT" \
        "/etc/systemd/system/$VEHICLE_CONFIG_COORDINATOR_UNIT" \
        "/etc/systemd/system/$VEHICLE_CAN_PROVISION_UNIT" \
        "/etc/systemd/system/$POWERD_UNIT" \
        "$VEHICLE_CONFIG_COORDINATOR_ENV" \
        "$VEHICLE_CONFIG_UI_QUALIFICATION_GATE" \
        "$VEHICLE_CONFIG_COORDINATOR_SANDBOX" \
        "$POWER_POLICY_FILE" \
        "$OPEN_MMI_TMPFILES_CONFIG_PATH"
    rmdir "$VEHICLE_CONFIG_COORDINATOR_OVERRIDE_DIR" >/dev/null 2>&1 || true
    systemctl daemon-reload
    rm -rf "$UPDATE_COORDINATOR_RUNTIME_DIR" "$UPDATE_COORDINATOR_STATE_DIR"

    # Remove service file
    log_info "Removing systemd service..."
    rm -f \
        "$REAL_HOME/.config/systemd/user/canbusd.service" \
        "$REAL_HOME/.config/systemd/user/open-mmi-dashboard.service" \
        "$REAL_HOME/.config/systemd/user/$OWNER_CONFIG_UNIT"
    sudo -u "$REAL_USER" \
        XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
        systemctl --user daemon-reload

    remove_desktop_entry
    remove_login_autostart
    remove_command_links
    
    # Remove application directory and root-owned update policy.
    log_info "Removing application files..."
    sudo rm -rf "$INSTALL_DIR"
    sudo rm -f "$UPDATE_POLICY_FILE"
    sudo rmdir "$(dirname "$UPDATE_POLICY_FILE")" >/dev/null 2>&1 || true
    
    # Remove udev rules
    if [ -f /etc/udev/rules.d/80-canbus.rules ] || [ -f "$POWERD_WAKE_UDEV_RULE_PATH" ]; then
        log_info "Removing udev rules..."
        sudo rm -f \
            /etc/udev/rules.d/80-canbus.rules \
            "$POWERD_WAKE_UDEV_RULE_PATH"
        sudo udevadm control --reload-rules
        sudo udevadm trigger
    fi
    
    log_success "Uninstall complete"
    log_info "Note: User was not removed from 'video' and 'input' groups"
    log_info "Optional group cleanup:"
    log_info "  sudo gpasswd -d $REAL_USER video"
    log_info "  sudo gpasswd -d $REAL_USER input"
}

# =============================================================================
# STATUS
# =============================================================================

cmd_status() {
    log_info "Installation Status"
    echo ""
    
    if is_installed; then
        echo -e "  Status:         ${GREEN}✓ Installed${NC}"
        echo -e "  Install Dir:    $INSTALL_DIR"
        echo -e "  Version:        $(get_installed_version)"
        echo -e "  Service:        $(daemon_running && echo -e "${GREEN}✓ Running${NC}" || echo -e "${RED}✗ Stopped${NC}")"
        echo ""
        
        if daemon_running; then
            log_success "All systems operational"
        else
            log_warn "Daemon is not running"
            log_info "Start with: systemctl --user start canbusd"
        fi
    else
        echo -e "  Status:         ${RED}✗ Not Installed${NC}"
        log_info "Install with: sudo ./scripts/manage.sh install"
    fi
    
    echo ""
    echo "  User:           $REAL_USER"
    echo "  Groups:         $(groups $REAL_USER | cut -d: -f2)"
    echo "  Service Dir:    $REAL_HOME/.config/systemd/user"
    
    if [ -d "$BACKUP_DIR" ]; then
        echo ""
        echo "  Backups:"
        ls -1 "$BACKUP_DIR" 2>/dev/null | while read backup; do
            echo "    - $backup"
        done
    fi
}

# =============================================================================
# LOGS
# =============================================================================

cmd_logs() {
    log_info "Viewing daemon logs (Ctrl+C to exit)"
    sudo -u "$REAL_USER" \
        XDG_RUNTIME_DIR="/run/user/$USER_ID" \
        journalctl --user-unit=canbusd -f
}

# =============================================================================
# CONFIG
# =============================================================================

cmd_config() {
    local action="${1:-help}"

    case "$action" in
        apply-profile|set-profile)
            local vehicle="${2:-seat-leon-1p-pq35}"
            local bindings="${3:-default}"

            apply_profile_provisioning "$vehicle" "$bindings"
            reload_profile_provisioning

            log_success "Profile applied: $vehicle"
            echo ""
            log_info "Normal setup now comes from the selected vehicle profile."
            log_info "Use 'sudo $0 config edit-can' only for advanced hardware overrides."
            ;;
        init)
            local vehicle="${2:-seat-leon-1p-pq35}"
            local bindings="${3:-default}"

            log_info "Creating user config directory at $USER_CONFIG_DIR"
            harden_custom_catalogue_permissions

            local source_vehicle
            source_vehicle="$(resolve_maintained_profile_source "$vehicle")" || return 1
            local source_bindings="$INSTALL_DIR/bindings/$bindings.json"

            if [ ! -f "$source_bindings" ]; then
                source_bindings="$REPO_ROOT/bindings/$bindings.json"
            fi

            if [ ! -f "$source_vehicle" ]; then
                log_error "Vehicle profile not found: $source_vehicle"
                return 1
            fi

            if [ ! -f "$source_bindings" ]; then
                log_error "Bindings file not found: $source_bindings"
                return 1
            fi

            copy_if_missing \
                "$source_vehicle" \
                "$USER_CONFIG_DIR/vehicles/$vehicle/config.json"

            copy_if_missing \
                "$source_bindings" \
                "$USER_CONFIG_DIR/bindings/$bindings.json"

            log_success "User config ready"
            echo ""
            echo "  Vehicle profile: $USER_CONFIG_DIR/vehicles/$vehicle/config.json"
            echo "  Bindings file:   $USER_CONFIG_DIR/bindings/$bindings.json"
            echo ""
            log_info "These files are local user overrides/custom config files."
            log_info "Normal profile setup uses: sudo $0 config apply-profile $vehicle $bindings"
            ;;
        edit-profile)
            local vehicle="${2:-${OPEN_MMI_VEHICLE:-seat-leon-1p-pq35}}"
            local profile="$USER_CONFIG_DIR/vehicles/$vehicle/config.json"

            if [ ! -f "$profile" ]; then
                log_warn "User profile does not exist yet: $profile"
                log_info "Creating it from installed/repo profile..."
                cmd_config init "$vehicle" "${OPEN_MMI_BINDINGS:-default}"
            fi

            open_editor_as_user "$profile"
            ;;
        edit-bindings)
            local bindings="${2:-${OPEN_MMI_BINDINGS:-default}}"
            local file="$USER_CONFIG_DIR/bindings/$bindings.json"

            if [ ! -f "$file" ]; then
                log_warn "User bindings do not exist yet: $file"
                log_info "Creating it from installed/repo bindings..."
                cmd_config init "${OPEN_MMI_VEHICLE:-seat-leon-1p-pq35}" "$bindings"
            fi

            open_editor_as_user "$file"
            ;;
        edit-service|edit)
            log_info "Editing systemd service override"
            if [ ! -f "$SYSTEMD_USER_UNIT_ROOT/canbusd.service" ]; then
                log_error "Service not installed yet"
                return 1
            fi

            mkdir -p "$REAL_HOME/.config/systemd/user/canbusd.service.d"
            chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/systemd/user"

            export XDG_RUNTIME_DIR="/run/user/$USER_ID"
            sudo -u "$REAL_USER" \
                HOME="$REAL_HOME" \
                XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
                systemctl --user edit canbusd.service

            log_info "Reloading systemd..."
            sudo -u "$REAL_USER" \
                HOME="$REAL_HOME" \
                XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
                systemctl --user daemon-reload

            if daemon_running; then
                log_info "Restarting daemon..."
                sudo -u "$REAL_USER" \
                    HOME="$REAL_HOME" \
                    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
                    systemctl --user restart canbusd.service
            fi
            ;;
        edit-can)
            log_info "Editing CAN runtime override"
            if [ ! -f "$SYSTEMD_USER_UNIT_ROOT/canbusd.service" ]; then
                log_error "Service not installed yet"
                return 1
            fi

            local override_dir="$REAL_HOME/.config/systemd/user/canbusd.service.d"
            local override_file="$override_dir/10-can-runtime.conf"

            mkdir -p "$override_dir"
            chown -R "$REAL_USER:$REAL_USER" "$REAL_HOME/.config/systemd/user"

            if [ ! -f "$override_file" ]; then
                cat > "$override_file" <<'EOF'
# open-mmi CAN runtime selection
#
# This selects which already-provisioned SocketCAN interface the daemon consumes.
# It does not configure bitrate and does not bring the interface up.
#
# Current known-working default:
#   infotainment -> can0
#
# The normal profile-driven setup provisions can0 at 100000 for the Seat 1P
# reference profile.
# Keep udev/system setup responsible for hotplug/reboot survival.

[Service]
Environment="OPEN_MMI_CAN_BUS=infotainment"
Environment="OPEN_MMI_CAN_INTERFACE=can0"
EOF
                chown "$REAL_USER:$REAL_USER" "$override_file"
            fi

            open_editor_as_user "$override_file"

            log_info "Reloading systemd..."
            export XDG_RUNTIME_DIR="/run/user/$USER_ID"
            sudo -u "$REAL_USER" \
                HOME="$REAL_HOME" \
                XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
                systemctl --user daemon-reload

            if daemon_running; then
                log_info "Restarting daemon..."
                sudo -u "$REAL_USER" \
                    HOME="$REAL_HOME" \
                    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
                    systemctl --user restart canbusd.service
            fi
            ;;
        show)
            log_info "Current service configuration:"
            systemctl --user cat canbusd.service 2>/dev/null || cat "$SYSTEMD_USER_UNIT_ROOT/canbusd.service"
            ;;
        paths)
            log_info "Configuration paths"
            echo ""
            echo "  User config dir: $USER_CONFIG_DIR"
            echo "  User vehicles:   $USER_CONFIG_DIR/vehicles"
            echo "  User bindings:   $USER_CONFIG_DIR/bindings"
            echo "  Installed app:   $INSTALL_DIR"
            echo ""
            echo "  Lookup order:"
            echo "    1. Explicit env path overrides"
            echo "    2. Installed app defaults"
            echo ""
            echo "  User config files are used only when explicitly selected."
            ;;
        help|--help|-h|*)
            cat <<EOF
Usage: $0 config <command> [args]

Commands:
  apply-profile [vehicle] [bindings]
      Select a vehicle profile and apply its runtime/provisioning defaults.
      This is the normal setup path.
      Default vehicle: seat-leon-1p-pq35
      Default bindings: default

  init [vehicle] [bindings]
      Create safe user-owned config files only.
      This does not apply CAN runtime/provisioning defaults.
      $USER_CONFIG_DIR

  edit-profile [vehicle]
      Edit a user-owned vehicle profile.
      Default vehicle: seat-leon-1p-pq35

  edit-bindings [bindings]
      Edit a user-owned bindings file.
      Default bindings: default

  edit-service
      Edit the systemd service override.
      Use this for OPEN_MMI_VEHICLE, OPEN_MMI_BINDINGS, log level, etc.

  edit-can
      Edit the CAN runtime override.
      Defaults to the known-working single bus setup:
      OPEN_MMI_CAN_BUS=infotainment
      OPEN_MMI_CAN_INTERFACE=can0

      This selects which already-provisioned SocketCAN interface the daemon consumes.
      It does not configure bitrate or bring the interface up.

  show
      Show the effective systemd service config.

  paths
      Show where Open-MMI looks for config files.

Examples:
  sudo $0 config apply-profile seat-leon-1p-pq35 default
  sudo $0 config init seat-leon-1p-pq35 default
  sudo $0 config edit-profile seat-leon-1p-pq35
  sudo $0 config edit-bindings default
  sudo $0 config edit-can
  sudo $0 config edit-service
EOF
            ;;
    esac
}

# =============================================================================
# HELP
# =============================================================================

show_help() {
    cat <<EOF
${BLUE}Open-MMI Installation Manager${NC}

${BLUE}Usage:${NC}
  sudo ./scripts/manage.sh <command> [options]

${BLUE}Commands:${NC}
  install      Install open-mmi from scratch
  update       Update through the trusted coordinator
  deploy-local Explicitly deploy this clean checkout into /opt/open-mmi
  uninstall    Remove open-mmi (with optional backup)
  
  status       Show installation and daemon status
  logs         View daemon logs in real-time
  config       Manage user config and service overrides
  power        Manage CAN-silence automatic suspend
  
  help         Show this help message

${BLUE}Examples:${NC}
  sudo ./scripts/manage.sh install
  sudo ./scripts/manage.sh update
  sudo ./scripts/manage.sh deploy-local
  sudo ./scripts/manage.sh status
  sudo ./scripts/manage.sh logs
  sudo ./scripts/manage.sh config apply-profile seat-leon-1p-pq35 default
  sudo ./scripts/manage.sh config init
  sudo ./scripts/manage.sh config edit-profile seat-leon-1p-pq35
  sudo ./scripts/manage.sh config edit-service
  sudo ./scripts/manage.sh power enable 60

${BLUE}Installation Details:${NC}
  Install directory: $INSTALL_DIR
  Service location:  /etc/systemd/user/canbusd.service
  Backups:          $BACKUP_DIR

${BLUE}Troubleshooting:${NC}
  View logs:        sudo ./scripts/manage.sh logs
  Check status:     sudo ./scripts/manage.sh status
  Apply profile:    sudo ./scripts/manage.sh config apply-profile seat-leon-1p-pq35 default
  Edit profile:     sudo ./scripts/manage.sh config edit-profile seat-leon-1p-pq35
  sudo ./scripts/manage.sh config init
  sudo ./scripts/manage.sh config edit-profile seat-leon-1p-pq35
  sudo ./scripts/manage.sh config edit-service

EOF
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    local command="${1:-help}"
    
    case "$command" in
        install)
            check_root
            cmd_install
            ;;
        update)
            check_root
            cmd_update
            ;;
        deploy-local)
            check_root
            cmd_deploy_local
            ;;
        _deploy-prepared)
            check_root
            cmd_deploy_prepared
            ;;
        uninstall)
            check_root
            cmd_uninstall
            ;;
        status)
            cmd_status
            ;;
        logs)
            cmd_logs
            ;;
        config)
            check_root
            cmd_config "${@:2}"
            ;;
        power)
            check_root
            cmd_power "${@:2}"
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "Unknown command: $command"
            show_help
            exit 1
            ;;
    esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
