import shlex
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ENTRY = ROOT / "packaging" / "linux-desktop" / "open-mmi-status.desktop"
CHOOSER_ENTRY = ROOT / "packaging" / "linux-desktop" / "open-mmi-chooser.desktop"
DASHBOARD_SERVICE = ROOT / "systemd" / "user" / "open-mmi-dashboard.service"
ICON_ROOT = ROOT / "packaging" / "linux-desktop" / "icons" / "hicolor"


def _parse_unit(path):
    sections = {}
    current = None
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
            continue
        if current is None or "=" not in line:
            raise AssertionError(f"Malformed line {line_number} in {path}: {raw_line!r}")
        key, value = line.split("=", 1)
        sections[current].append((key.strip(), value.strip()))
    return sections


def _values(sections, section, key):
    return [value for item_key, value in sections.get(section, []) if item_key == key]


def _single(sections, section, key):
    values = _values(sections, section, key)
    if len(values) != 1:
        raise AssertionError(
            f"Expected one {section}.{key} value, found {len(values)}: {values!r}"
        )
    return values[0]


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


class DesktopShellAssetTests(unittest.TestCase):
    def test_desktop_entry_launches_universal_launcher(self):
        sections = _parse_unit(DESKTOP_ENTRY)

        self.assertEqual(_single(sections, "Desktop Entry", "Type"), "Application")
        self.assertEqual(_single(sections, "Desktop Entry", "Name"), "Open MMI")
        self.assertEqual(_single(sections, "Desktop Entry", "Path"), "/opt/open-mmi")
        self.assertEqual(
            _single(sections, "Desktop Entry", "Terminal").lower(), "false"
        )

        command = _single(sections, "Desktop Entry", "Exec")
        arguments = shlex.split(command)
        self.assertEqual(arguments, ["/usr/local/bin/open-mmi-launcher"])
        self.assertNotIn("status_cli", command)
        self.assertNotIn("gnome-terminal", command)
        self.assertEqual(_single(sections, "Desktop Entry", "Icon"), "open-mmi")

        actions = _single(sections, "Desktop Entry", "Actions").split(";")
        self.assertEqual([item for item in actions if item], ["Choose", "Web", "TUI"])
        self.assertEqual(
            _single(sections, "Desktop Action Choose", "Exec"),
            "/usr/local/bin/open-mmi-launcher --choose --ask-remember",
        )
        self.assertEqual(
            _single(sections, "Desktop Action Web", "Exec"),
            "/usr/local/bin/open-mmi-launcher web --remember",
        )
        self.assertEqual(
            _single(sections, "Desktop Action TUI", "Exec"),
            "/usr/local/bin/open-mmi-launcher tui --remember",
        )

    def test_standalone_interface_chooser_is_always_visible(self):
        sections = _parse_unit(CHOOSER_ENTRY)

        self.assertEqual(_single(sections, "Desktop Entry", "Type"), "Application")
        self.assertEqual(
            _single(sections, "Desktop Entry", "Name"),
            "Open MMI Interface Chooser",
        )
        self.assertEqual(
            _single(sections, "Desktop Entry", "Exec"),
            "/usr/local/bin/open-mmi-launcher --choose --ask-remember",
        )
        self.assertEqual(_single(sections, "Desktop Entry", "Terminal").lower(), "false")
        self.assertEqual(_single(sections, "Desktop Entry", "Icon"), "open-mmi")

    def test_repository_contains_named_icon_theme_assets(self):
        png_assets = sorted(ICON_ROOT.glob("*x*/apps/open-mmi.png"))
        scalable = ICON_ROOT / "scalable" / "apps" / "open-mmi.svg"

        self.assertTrue(png_assets, "no sized open-mmi PNG icons were found")
        self.assertTrue(scalable.is_file(), "missing scalable Open MMI icon")

    def test_dashboard_service_is_local_restartable_user_service(self):
        sections = _parse_unit(DASHBOARD_SERVICE)

        self.assertEqual(_single(sections, "Service", "Type"), "simple")
        self.assertEqual(
            _single(sections, "Service", "WorkingDirectory"), "/opt/open-mmi"
        )
        self.assertEqual(_single(sections, "Service", "Restart"), "on-failure")
        self.assertEqual(_single(sections, "Service", "TimeoutStopSec"), "10")
        self.assertEqual(_single(sections, "Install", "WantedBy"), "default.target")

        command = _single(sections, "Service", "ExecStart")
        self.assertIn(
            "/opt/open-mmi/venv/bin/python -m ui.web_dashboard.server", command
        )
        self.assertNotIn("/bin/sh", command)
        self.assertNotIn("bash -c", command)

        environment = {
            _unquote(value) for value in _values(sections, "Service", "Environment")
        }
        self.assertIn("PYTHONUNBUFFERED=1", environment)
        self.assertIn("OPEN_MMI_WEB_HOST=127.0.0.1", environment)
        self.assertIn("OPEN_MMI_WEB_PORT=8765", environment)
        self.assertEqual(_values(sections, "Service", "EnvironmentFile"), [])
        self.assertEqual(_single(sections, "Service", "IPAddressDeny"), "any")
        self.assertEqual(_single(sections, "Service", "IPAddressAllow"), "localhost")


if __name__ == "__main__":
    unittest.main()
