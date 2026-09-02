#!/usr/bin/env python3
"""Universal launcher for Open MMI user interfaces.

The launcher keeps dashboard process management in systemd, waits for the
local health endpoint, and only then starts or reuses the configured browser.
"""

from __future__ import annotations

import argparse
import fcntl
import ipaddress
import json
import math
import os
import shlex
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, List, Mapping, MutableMapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

SERVICE_NAME = "open-mmi-dashboard.service"
BROWSER_WINDOW_CLASS = "open-mmi"
BROWSER_STATE_FILE = "browser.json"
BROWSER_LOCK_FILE = "browser.lock"
AUTOSTART_ENTRY_NAME = "open-mmi.desktop"
AUTOSTART_LAUNCHER_COMMAND = "/usr/local/bin/open-mmi-launcher"
SYSTEMD_RUN = "/usr/bin/systemd-run"
NETWORK_SANDBOX_MARKER = "OPEN_MMI_LAUNCHER_NETWORK_SANDBOXED"
LEGACY_CONFIG_KEYS = {"start_at_login"}
DEFAULT_CONFIG: dict[str, Any] = {
    "default_ui": "web",
    "web_url": "http://127.0.0.1:8765",
    "browser_mode": "kiosk",
    "browser_command": "auto",
    "startup_timeout_seconds": 12.0,
    "health_poll_interval_seconds": 0.25,
}
BROWSER_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "firefox",
)
TERMINAL_CANDIDATES = (
    "gnome-terminal.real",
    "gnome-terminal",
    "mate-terminal",
    "konsole",
    "xfce4-terminal",
    "xterm",
    "x-terminal-emulator",
)
GRAPHICAL_CHOOSER_CANDIDATES = ("zenity", "yad")


class LauncherError(RuntimeError):
    """A user-facing launcher failure."""


def default_config_path() -> Path:
    config_home = os.getenv("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "open-mmi" / "launcher.json"
    return Path.home() / ".config" / "open-mmi" / "launcher.json"


def default_state_dir() -> Path:
    state_home = os.getenv("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "open-mmi"
    return Path.home() / ".local" / "state" / "open-mmi"


def default_browser_runtime_dir() -> Path:
    runtime_home = os.getenv("XDG_RUNTIME_DIR")
    if runtime_home:
        return Path(runtime_home) / "open-mmi"
    return default_state_dir() / "runtime"


def _validate_config(config: MutableMapping[str, Any]) -> None:
    if config["default_ui"] not in {"web", "tui"}:
        raise LauncherError("default_ui must be 'web' or 'tui'")

    if config["browser_mode"] not in {"kiosk", "fullscreen", "window"}:
        raise LauncherError("browser_mode must be 'kiosk', 'fullscreen', or 'window'")

    parsed = urlparse(str(config["web_url"]))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise LauncherError("web_url must be an absolute http:// or https:// URL")
    if parsed.username is not None or parsed.password is not None:
        raise LauncherError("web_url must not contain embedded credentials")
    hostname = parsed.hostname or ""
    if hostname.lower() != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise LauncherError("web_url must target the local loopback interface") from exc
        if not address.is_loopback:
            raise LauncherError("web_url must target the local loopback interface")

    for key in ("startup_timeout_seconds", "health_poll_interval_seconds"):
        try:
            value = float(config[key])
        except (TypeError, ValueError) as exc:
            raise LauncherError(f"{key} must be numeric") from exc
        if value <= 0:
            raise LauncherError(f"{key} must be greater than zero")
        config[key] = value


    command = config["browser_command"]
    if not isinstance(command, (str, list, tuple)):
        raise LauncherError("browser_command must be 'auto', a command string, or an argument list")
    if isinstance(command, (list, tuple)) and not all(isinstance(item, str) for item in command):
        raise LauncherError("browser_command argument lists may contain strings only")


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load user configuration, merging it over conservative defaults."""

    target = path or default_config_path()
    config = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _validate_config(config)
        return config
    except json.JSONDecodeError as exc:
        raise LauncherError(f"invalid launcher configuration in {target}: {exc}") from exc
    except OSError as exc:
        raise LauncherError(f"cannot read launcher configuration {target}: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise LauncherError(f"launcher configuration root must be an object: {target}")

    unknown = sorted(set(raw) - set(DEFAULT_CONFIG) - LEGACY_CONFIG_KEYS)
    if unknown:
        raise LauncherError(f"unknown launcher configuration keys: {', '.join(unknown)}")

    config.update({key: value for key, value in raw.items() if key not in LEGACY_CONFIG_KEYS})
    _validate_config(config)
    return config


def save_preferences(
    updates: Mapping[str, Any],
    path: Optional[Path] = None,
) -> None:
    """Persist selected launcher preferences without discarding other settings."""

    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, Any] = {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            current.update(loaded)
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"cannot update launcher configuration {target}: {exc}") from exc

    for legacy_key in LEGACY_CONFIG_KEYS:
        current.pop(legacy_key, None)
    current.update(updates)
    merged = dict(DEFAULT_CONFIG)
    merged.update(current)
    _validate_config(merged)

    temporary = target.with_suffix(target.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise LauncherError(f"cannot update launcher configuration {target}: {exc}") from exc


def save_default_ui(ui_name: str, path: Optional[Path] = None) -> None:
    save_preferences({"default_ui": ui_name}, path)


def health_url(web_url: str) -> str:
    return urljoin(web_url.rstrip("/") + "/", "api/health")


def check_health(endpoint: str, timeout: float = 1.0) -> bool:
    """Return true when the dashboard HTTP server returns its health payload."""

    request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "open-mmi-launcher"})
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False
    return isinstance(payload, dict) and "health" in payload


def run_command(args: Sequence[str], *, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )


def service_is_active(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> bool:
    result = command_runner(
        ["systemctl", "--user", "is-active", "--quiet", SERVICE_NAME],
        capture_output=True,
    )
    return result.returncode == 0


def service_is_enabled(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> bool:
    result = command_runner(
        ["systemctl", "--user", "is-enabled", "--quiet", SERVICE_NAME],
        capture_output=True,
    )
    return result.returncode == 0


def default_autostart_path() -> Path:
    config_home = os.getenv("XDG_CONFIG_HOME")
    base = Path(config_home) if config_home else Path.home() / ".config"
    return base / "autostart" / AUTOSTART_ENTRY_NAME


def open_at_login_enabled(path: Optional[Path] = None) -> bool:
    target = path or default_autostart_path()
    try:
        body = target.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return False
    return (
        "Type=Application" in body
        and f"Exec={AUTOSTART_LAUNCHER_COMMAND}" in body
        and "X-GNOME-Autostart-enabled=false" not in body
    )


def configure_open_at_login(enabled: bool, path: Optional[Path] = None) -> None:
    target = path or default_autostart_path()
    if target.is_symlink():
        raise LauncherError(f"refusing to modify symlinked autostart entry: {target}")
    if not enabled:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise LauncherError(f"cannot remove autostart entry {target}: {exc}") from exc
        return

    body = "\n".join((
        "[Desktop Entry]",
        "Type=Application",
        "Name=Open MMI",
        "Comment=Open Open MMI at graphical login",
        f"Exec={AUTOSTART_LAUNCHER_COMMAND}",
        "Icon=open-mmi",
        "Terminal=false",
        "X-GNOME-Autostart-enabled=true",
        "",
    ))
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        temporary.chmod(0o644)
        temporary.replace(target)
        target.chmod(0o644)
    except OSError as exc:
        try:
            temporary.unlink()
        except (NameError, OSError):
            pass
        raise LauncherError(f"cannot write autostart entry {target}: {exc}") from exc


def configure_dashboard_service(
    action: str,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> None:
    if action not in {"start", "stop", "restart", "enable", "disable"}:
        raise LauncherError(f"unsupported dashboard service action: {action}")
    result = command_runner(
        ["systemctl", "--user", action, SERVICE_NAME],
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl failed").strip()
        raise LauncherError(f"could not {action} {SERVICE_NAME}: {detail}")


def ensure_dashboard_ready(
    config: Mapping[str, Any],
    *,
    health_checker: Callable[[str, float], bool] = check_health,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    """Start or restart the dashboard service and wait for HTTP readiness."""

    endpoint = health_url(str(config["web_url"]))
    if health_checker(endpoint, 0.75):
        return

    action = "restart" if service_is_active(command_runner) else "start"
    result = command_runner(
        ["systemctl", "--user", action, SERVICE_NAME],
        capture_output=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "systemctl failed").strip()
        raise LauncherError(f"could not {action} {SERVICE_NAME}: {detail}")

    timeout = float(config["startup_timeout_seconds"])
    interval = float(config["health_poll_interval_seconds"])
    attempts = max(1, math.ceil(timeout / interval))
    request_timeout = min(0.75, max(0.1, interval))
    for _ in range(attempts):
        sleeper(interval)
        if health_checker(endpoint, request_timeout):
            return

    raise LauncherError(
        f"dashboard did not become healthy within {timeout:g} seconds; "
        f"check: journalctl --user -u {SERVICE_NAME}"
    )


def _configured_browser_parts(value: Any) -> Optional[List[str]]:
    if value == "auto":
        return None
    if isinstance(value, str):
        parts = shlex.split(value)
    else:
        parts = list(value)
    if not parts:
        raise LauncherError("browser_command may not be empty")
    return parts


def resolve_browser(configured: Any) -> list[str]:
    parts = _configured_browser_parts(configured)
    if parts is not None:
        executable = shutil.which(parts[0])
        if executable is None:
            raise LauncherError(f"configured browser was not found: {parts[0]}")
        parts[0] = executable
        return parts

    for candidate in BROWSER_CANDIDATES:
        executable = shutil.which(candidate)
        if executable:
            return [executable]
    raise LauncherError(
        "no supported browser found; install Chromium/Firefox or set browser_command in launcher.json"
    )


def browser_family(command: Sequence[str]) -> str:
    name = Path(command[0]).name.lower()
    if "chrom" in name or "chrome" in name:
        return "chromium"
    if "firefox" in name:
        return "firefox"
    return "custom"


def browser_profile_dir(family: str, state_dir: Optional[Path] = None) -> Optional[Path]:
    if family not in {"chromium", "firefox"}:
        return None
    root = state_dir or default_state_dir()
    return root / "browser-profile" / family


def _has_option(command: Sequence[str], option: str) -> bool:
    return any(part == option or part.startswith(option + "=") for part in command)


def _add_chromium_profile(command: list[str], profile_dir: Path) -> None:
    expected_profile = f"--user-data-dir={profile_dir}"
    profile_options = [part for part in command if part.startswith("--user-data-dir=")]
    if profile_options and profile_options != [expected_profile]:
        raise LauncherError("browser_command may not override the managed Chromium profile")
    if not profile_options:
        command.append(expected_profile)

    expected_class = f"--class={BROWSER_WINDOW_CLASS}"
    class_options = [part for part in command if part.startswith("--class=")]
    if class_options and class_options != [expected_class]:
        raise LauncherError("browser_command may not override the Open MMI window class")
    if not class_options:
        command.append(expected_class)


def _add_firefox_profile(command: list[str], profile_dir: Path) -> None:
    if _has_option(command, "--profile"):
        raise LauncherError("browser_command may not override the managed Firefox profile")
    command.extend(["--profile", str(profile_dir)])

    if "--no-remote" not in command:
        command.append("--no-remote")
    if _has_option(command, "--class"):
        raise LauncherError("browser_command may not override the Open MMI window class")
    command.extend(["--class", BROWSER_WINDOW_CLASS])


def build_browser_command(
    base_command: Sequence[str],
    web_url: str,
    mode: str,
    *,
    profile_dir: Optional[Path] = None,
) -> list[str]:
    """Build a browser invocation without invoking a shell."""

    has_url_placeholder = any("{url}" in part for part in base_command)
    replacements = {
        "{url}": web_url,
        "{profile_dir}": str(profile_dir or ""),
        "{window_class}": BROWSER_WINDOW_CLASS,
    }
    command = []
    for part in base_command:
        replaced = part
        for placeholder, value in replacements.items():
            replaced = replaced.replace(placeholder, value)
        command.append(replaced)

    family = browser_family(command)
    if family == "chromium":
        if profile_dir is not None:
            _add_chromium_profile(command, profile_dir)
        for option in ("--no-first-run", "--disable-session-crashed-bubble"):
            if option not in command:
                command.append(option)
        if has_url_placeholder:
            return command
        if mode == "kiosk":
            return command + ["--kiosk", f"--app={web_url}"]
        if mode == "fullscreen":
            return command + ["--start-fullscreen", f"--app={web_url}"]
        return command + [web_url]

    if family == "firefox":
        if profile_dir is not None:
            _add_firefox_profile(command, profile_dir)
        if has_url_placeholder:
            return command
        if mode in {"kiosk", "fullscreen"}:
            return command + ["--kiosk", web_url]
        return command + [web_url]

    if has_url_placeholder:
        return command
    return command + [web_url]


def _ensure_private_directory(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(0o700)
    except OSError as exc:
        raise LauncherError(f"cannot prepare browser state directory {path}: {exc}") from exc


@contextmanager
def _browser_instance_lock(path: Path) -> Iterator[None]:
    _ensure_private_directory(path.parent)
    try:
        with path.open("a+b") as handle:
            path.chmod(0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise LauncherError(f"cannot lock browser instance state {path}: {exc}") from exc


def _read_browser_state(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise LauncherError(f"cannot read browser instance state {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise LauncherError(f"browser instance state must be an object: {path}")
    return payload


def _write_browser_state(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_private_directory(path.parent)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
    except OSError as exc:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise LauncherError(f"cannot write browser instance state {path}: {exc}") from exc


def _remove_browser_state(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise LauncherError(f"cannot remove stale browser instance state {path}: {exc}") from exc


def read_process_command(pid: int, proc_root: Path = Path("/proc")) -> list[str]:
    data = (proc_root / str(pid) / "cmdline").read_bytes()
    return [part.decode("utf-8", errors="replace") for part in data.split(b"\0") if part]


def _command_contains(command: Sequence[str], value: str) -> bool:
    return bool(value) and any(value in part for part in command)


def browser_state_is_running(
    state: Mapping[str, Any],
    *,
    process_reader: Callable[[int], Sequence[str]] = read_process_command,
) -> bool:
    try:
        pid = int(state["pid"])
        marker = str(state["marker"])
        web_url = str(state["web_url"])
    except (KeyError, TypeError, ValueError):
        return False
    if pid <= 0 or not marker or not web_url:
        return False
    try:
        command = list(process_reader(pid))
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        return False
    return _command_contains(command, marker) and _command_contains(command, web_url)


def find_browser_process(
    marker: str,
    required_text: Optional[str] = None,
    *,
    proc_root: Path = Path("/proc"),
    process_reader: Optional[Callable[[int], Sequence[str]]] = None,
) -> Optional[int]:
    reader = process_reader or (lambda pid: read_process_command(pid, proc_root))
    try:
        entries = sorted(
            (entry for entry in proc_root.iterdir() if entry.name.isdigit()),
            key=lambda entry: int(entry.name),
        )
    except OSError:
        return None

    for entry in entries:
        pid = int(entry.name)
        try:
            command = list(reader(pid))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if not _command_contains(command, marker):
            continue
        if required_text is not None and not _command_contains(command, required_text):
            continue
        return pid
    return None


def focus_browser_window(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> bool:
    """Best-effort focus of an existing Open MMI window."""

    wmctrl = shutil.which("wmctrl")
    if wmctrl:
        result = command_runner(
            [wmctrl, "-x", "-a", BROWSER_WINDOW_CLASS],
            capture_output=True,
        )
        if result.returncode == 0:
            return True

    xdotool = shutil.which("xdotool")
    if xdotool:
        result = command_runner(
            [
                xdotool,
                "search",
                "--onlyvisible",
                "--classname",
                BROWSER_WINDOW_CLASS,
                "windowactivate",
                "--sync",
            ],
            capture_output=True,
        )
        if result.returncode == 0:
            return True
    return False


def _browser_state_payload(
    *,
    pid: int,
    command: Sequence[str],
    marker: str,
    family: str,
    profile_dir: Optional[Path],
    web_url: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "pid": pid,
        "command": list(command),
        "marker": marker,
        "browser_family": family,
        "profile_dir": str(profile_dir) if profile_dir is not None else None,
        "web_url": web_url,
        "started_at_epoch": time.time(),
    }


def launch_browser(
    config: Mapping[str, Any],
    *,
    runtime_dir: Optional[Path] = None,
    state_dir: Optional[Path] = None,
    popen_factory: Callable[..., Any] = subprocess.Popen,
    process_reader: Callable[[int], Sequence[str]] = read_process_command,
    process_finder: Optional[Callable[[str, Optional[str]], Optional[int]]] = None,
    focus_window: Callable[[], bool] = focus_browser_window,
) -> list[str]:
    """Start one owned browser instance or reuse the existing instance."""

    base = resolve_browser(config["browser_command"])
    family = browser_family(base)
    profile_dir = browser_profile_dir(family, state_dir)
    if profile_dir is not None:
        _ensure_private_directory(profile_dir)

    web_url = str(config["web_url"])
    command = build_browser_command(
        base,
        web_url,
        str(config["browser_mode"]),
        profile_dir=profile_dir,
    )
    marker = str(profile_dir) if profile_dir is not None else web_url

    runtime_root = runtime_dir or default_browser_runtime_dir()
    state_path = runtime_root / BROWSER_STATE_FILE
    lock_path = runtime_root / BROWSER_LOCK_FILE

    if process_finder is None:
        process_finder = lambda value, required: find_browser_process(
            value,
            required,
            process_reader=process_reader,
        )

    with _browser_instance_lock(lock_path):
        state = _read_browser_state(state_path)
        if state is not None and browser_state_is_running(state, process_reader=process_reader):
            if state.get("command") != command:
                raise LauncherError(
                    "an Open MMI browser is already running with different launcher settings; "
                    "close that window before changing browser, URL, or display mode"
                )
            focus_window()
            return command
        if state is not None:
            _remove_browser_state(state_path)

        if profile_dir is not None:
            recovered_pid = process_finder(marker, web_url)
            if recovered_pid is not None:
                _write_browser_state(
                    state_path,
                    _browser_state_payload(
                        pid=recovered_pid,
                        command=command,
                        marker=marker,
                        family=family,
                        profile_dir=profile_dir,
                        web_url=web_url,
                    ),
                )
                focus_window()
                return command

            conflicting_pid = process_finder(marker, None)
            if conflicting_pid is not None:
                raise LauncherError(
                    "the managed Open MMI browser profile is already in use with a different URL; "
                    "close that Open MMI window before relaunching"
                )

        try:
            process = popen_factory(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise LauncherError(f"could not start browser: {exc}") from exc

        try:
            pid = int(process.pid)
        except (AttributeError, TypeError, ValueError) as exc:
            raise LauncherError("browser process did not provide a valid process ID") from exc
        if pid <= 0:
            raise LauncherError("browser process did not provide a valid process ID")

        _write_browser_state(
            state_path,
            _browser_state_payload(
                pid=pid,
                command=command,
                marker=marker,
                family=family,
                profile_dir=profile_dir,
                web_url=web_url,
            ),
        )
        return command


def browser_status(
    *,
    runtime_dir: Optional[Path] = None,
    process_reader: Callable[[int], Sequence[str]] = read_process_command,
) -> dict[str, Any]:
    runtime_root = runtime_dir or default_browser_runtime_dir()
    state_path = runtime_root / BROWSER_STATE_FILE
    try:
        state = _read_browser_state(state_path)
    except LauncherError as exc:
        return {"running": False, "state_path": str(state_path), "error": str(exc)}
    if state is None:
        return {"running": False, "state_path": str(state_path)}
    return {
        "running": browser_state_is_running(state, process_reader=process_reader),
        "state_path": str(state_path),
        "pid": state.get("pid"),
        "browser_family": state.get("browser_family"),
        "profile_dir": state.get("profile_dir"),
        "web_url": state.get("web_url"),
    }


def _status_command() -> list[str]:
    installed = shutil.which("open-mmi-status")
    if installed:
        return [installed]
    return [sys.executable, "-m", "ui.dashboard.status_cli"]


def _unwrap_terminal_executable(terminal: str) -> str:
    """Return the native terminal executable behind distro compatibility wrappers.

    Linux Mint exposes ``x-terminal-emulator`` through wrappers such as
    ``gnome-terminal.wrapper``. Those wrappers implement the portable ``-e``
    interface but do not accept GNOME Terminal's native ``--wait --`` syntax.
    When the matching ``.real`` binary exists, use it directly so the launcher
    can both execute the TUI and wait for the terminal lifecycle.
    """

    resolved = Path(os.path.realpath(terminal))
    if resolved.name.endswith(".wrapper"):
        native = resolved.with_name(resolved.name[: -len(".wrapper")] + ".real")
        if os.access(native, os.X_OK):
            return str(native)
    return terminal


def _terminal_command(terminal: str, status_command: Sequence[str]) -> list[str]:
    name = Path(terminal).name
    if name.startswith("gnome-terminal") and not name.endswith(".wrapper"):
        return [terminal, "--wait", "--"] + list(status_command)
    if name.startswith("mate-terminal") and not name.endswith(".wrapper"):
        return [terminal, "--disable-factory", "--"] + list(status_command)
    if name.startswith("xfce4-terminal") and not name.endswith(".wrapper"):
        return [terminal, "--disable-server", "--command", shlex.join(status_command)]
    if name.startswith("konsole"):
        return [terminal, "--nofork", "-e"] + list(status_command)
    return [terminal, "-e"] + list(status_command)


def _run_tui_once() -> int:
    status_command = _status_command()

    if sys.stdin.isatty() and sys.stdout.isatty():
        return run_command(status_command).returncode

    for candidate in TERMINAL_CANDIDATES:
        terminal = shutil.which(candidate)
        if terminal:
            terminal = _unwrap_terminal_executable(terminal)
            try:
                process = subprocess.Popen(
                    _terminal_command(terminal, status_command),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError as exc:
                raise LauncherError(f"could not start terminal: {exc}") from exc
            return process.wait()
    raise LauncherError("no terminal emulator found for the TUI")


def _graphical_session_available() -> bool:
    return bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"))


def _graphical_chooser_command(executable: str) -> list[str]:
    return [
        executable,
        "--list",
        "--radiolist",
        "--title=Open MMI",
        "--text=Choose the interface to launch",
        "--column=Select",
        "--column=Interface",
        "TRUE",
        "Web Dashboard",
        "FALSE",
        "Terminal UI",
        "--print-column=2",
        "--width=420",
        "--height=260",
    ]


def _graphical_remember_command(executable: str, selected: str) -> list[str]:
    label = "Web Dashboard" if selected == "web" else "Terminal UI"
    return [
        executable,
        "--question",
        "--title=Open MMI",
        f"--text=Remember {label} as the default interface?",
        "--ok-label=Remember",
        "--cancel-label=Just this time",
        "--width=420",
    ]


def _graphical_chooser_executable() -> Optional[str]:
    if not _graphical_session_available():
        return None
    for candidate in GRAPHICAL_CHOOSER_CANDIDATES:
        executable = shutil.which(candidate)
        if executable is not None:
            return executable
    return None


def _selection_from_label(label: str) -> Optional[str]:
    normalized = label.strip().lower()
    if normalized in {"web dashboard", "web", "1", "w"}:
        return "web"
    if normalized in {"terminal ui", "terminal", "tui", "2", "t"}:
        return "tui"
    return None


def choose_ui(
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> str:
    if _graphical_session_available():
        executable = _graphical_chooser_executable()
        if executable is not None:
            result = command_runner(
                _graphical_chooser_command(executable),
                capture_output=True,
            )
            if result.returncode == 0:
                selected = _selection_from_label(result.stdout or "")
                if selected is None:
                    raise LauncherError("graphical chooser returned an unknown interface")
                return selected
            if result.returncode in {1, 5}:
                raise LauncherError("interface selection cancelled")
            detail = (result.stderr or result.stdout or "chooser failed").strip()
            raise LauncherError(f"graphical interface chooser failed: {detail}")

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise LauncherError(
            "no graphical chooser is available; install zenity or run --choose in a terminal"
        )

    print("Open MMI interface")
    print("  1. Web dashboard")
    print("  2. Terminal UI")
    while True:
        answer = input("Select [1-2]: ").strip().lower()
        selected = _selection_from_label(answer)
        if selected is not None:
            return selected
        print("Please choose 1 or 2.", file=sys.stderr)


def confirm_remember_choice(
    selected: str,
    command_runner: Callable[..., subprocess.CompletedProcess[str]] = run_command,
) -> bool:
    executable = _graphical_chooser_executable()
    if executable is not None:
        result = command_runner(
            _graphical_remember_command(executable, selected),
            capture_output=True,
        )
        if result.returncode == 0:
            return True
        if result.returncode in {1, 5}:
            return False
        detail = (result.stderr or result.stdout or "confirmation failed").strip()
        raise LauncherError(f"could not confirm the default interface: {detail}")

    if sys.stdin.isatty() and sys.stdout.isatty():
        answer = input("Remember this interface as the default? [y/N]: ").strip().lower()
        return answer in {"y", "yes"}
    return False


def _open_web_dashboard(config: Mapping[str, Any]) -> int:
    ensure_dashboard_ready(config)
    launch_browser(config)
    return 0


def _recover_after_tui(config: Mapping[str, Any], config_path: Path) -> str:
    """Choose the next interface after the TUI closes.

    A graphical installation must never strand a touchscreen-only user. If the
    chooser is unavailable or cancelled, open the web dashboard for this
    session. When no graphical session exists, return ``exit`` and preserve the
    normal terminal behaviour.
    """

    graphical = _graphical_session_available()
    interactive_terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if not graphical and not interactive_terminal:
        return "exit"

    try:
        selected = choose_ui()
    except LauncherError as exc:
        if not graphical:
            raise
        print(
            f"open-mmi-launcher: {exc}; opening the web dashboard instead",
            file=sys.stderr,
        )
        return "web"

    try:
        remember = confirm_remember_choice(selected)
    except LauncherError as exc:
        print(
            f"open-mmi-launcher: {exc}; using the selection once",
            file=sys.stderr,
        )
        remember = False
    if remember:
        save_default_ui(selected, config_path)
    return selected


def launch_tui(
    config: Optional[Mapping[str, Any]] = None,
    config_path: Optional[Path] = None,
) -> int:
    """Run the TUI and provide a graphical route back when it closes."""

    while True:
        try:
            exit_code = _run_tui_once()
        except LauncherError as exc:
            if config is None or config_path is None or not _graphical_session_available():
                raise
            print(
                f"open-mmi-launcher: {exc}; opening interface recovery",
                file=sys.stderr,
            )
            exit_code = 1
        if config is None or config_path is None:
            return exit_code

        selected = _recover_after_tui(config, config_path)
        if selected == "exit":
            return exit_code
        if selected == "web":
            return _open_web_dashboard(config)
        # Selecting the TUI again starts another guarded session. Closing it
        # returns to the chooser rather than leaving an unusable terminal.


def status_payload(config: Mapping[str, Any], config_path: Path) -> dict[str, Any]:
    endpoint = health_url(str(config["web_url"]))
    return {
        "config_path": str(config_path),
        "default_ui": config["default_ui"],
        "open_at_login": open_at_login_enabled(),
        "web_url": config["web_url"],
        "health_url": endpoint,
        "service": SERVICE_NAME,
        "service_active": service_is_active(),
        "service_enabled": service_is_enabled(),
        "dashboard_reachable": check_health(endpoint),
        "browser": browser_status(),
    }


def _network_sandbox_command(argv: Sequence[str]) -> list[str]:
    return [
        SYSTEMD_RUN,
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--property=IPAddressDeny=any",
        "--property=IPAddressAllow=localhost",
        sys.executable,
        "-I",
        "-m",
        "ui.launcher",
        *argv,
    ]


def _reexec_in_network_sandbox(argv: Sequence[str]) -> int:
    environment = os.environ.copy()
    environment[NETWORK_SANDBOX_MARKER] = "1"
    result = subprocess.run(
        _network_sandbox_command(argv),
        check=False,
        env=environment,
    )
    return int(result.returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch an Open MMI interface")
    parser.add_argument("ui", nargs="?", choices=("web", "tui"), help="override the configured interface")
    parser.add_argument("--choose", action="store_true", help="select the interface interactively")
    parser.add_argument("--remember", action="store_true", help="remember an explicit or chosen interface")
    parser.add_argument(
        "--ask-remember",
        action="store_true",
        help="after --choose, ask whether the selection should become the default",
    )
    parser.add_argument("--status", action="store_true", help="show launcher and dashboard status")
    parser.add_argument("--stop", action="store_true", help="stop the dashboard service")
    parser.add_argument(
        "--enable-autostart", "--enable-startup",
        dest="enable_autostart",
        action="store_true",
        help="open Open MMI automatically at graphical login",
    )
    parser.add_argument(
        "--disable-autostart", "--disable-startup",
        dest="disable_autostart",
        action="store_true",
        help="do not open Open MMI automatically at graphical login",
    )
    parser.add_argument("--config", type=Path, help="use an alternate launcher configuration file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None and os.environ.get(NETWORK_SANDBOX_MARKER) != "1":
        return _reexec_in_network_sandbox(sys.argv[1:])

    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = args.config or default_config_path()

    try:
        config = load_config(config_path)
        exclusive_operations = (
            args.choose,
            args.status,
            args.stop,
            args.enable_autostart,
            args.disable_autostart,
        )
        operation_count = sum(bool(value) for value in exclusive_operations)
        if operation_count > 1:
            raise LauncherError(
                "--choose, --status, --stop, --enable-autostart, and "
                "--disable-autostart are mutually exclusive"
            )
        if args.ui and operation_count:
            raise LauncherError("an explicit interface cannot be combined with an operation flag")
        if args.remember and not (args.choose or args.ui):
            raise LauncherError("--remember requires an explicit interface or --choose")
        if args.ask_remember and not args.choose:
            raise LauncherError("--ask-remember requires --choose")
        if args.ask_remember and args.remember:
            raise LauncherError("--ask-remember and --remember cannot be combined")

        if args.status:
            print(json.dumps(status_payload(config, config_path), indent=2, sort_keys=True))
            return 0

        if args.stop:
            result = run_command(
                ["systemctl", "--user", "stop", SERVICE_NAME],
                capture_output=True,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "systemctl failed").strip()
                raise LauncherError(f"could not stop {SERVICE_NAME}: {detail}")
            return 0

        if args.enable_autostart or args.disable_autostart:
            configure_open_at_login(bool(args.enable_autostart))
            return 0

        selected = choose_ui() if args.choose else (args.ui or str(config["default_ui"]))
        if args.remember:
            save_default_ui(selected, config_path)
        elif args.ask_remember and confirm_remember_choice(selected):
            save_default_ui(selected, config_path)

        if selected == "tui":
            return launch_tui(config, config_path)

        return _open_web_dashboard(config)
    except LauncherError as exc:
        print(f"open-mmi-launcher: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
