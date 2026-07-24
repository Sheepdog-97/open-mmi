from __future__ import annotations

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGE_SCRIPT = ROOT / "scripts" / "manage.sh"
COMMANDS = (
    "open-mmi-canbusd",
    "open-mmi-config",
    "open-mmi-dashboard",
    "open-mmi-launcher",
    "open-mmi-powerd",
    "open-mmi-status",
    "open-mmi-update-coordinator",
    "open-mmi-update-installer",
    "open-mmi-vehicle-config-coordinator",
)


class CommandInstallationTests(unittest.TestCase):
    def _run_shell(self, body: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", body],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_package_install_verifies_wrappers_and_manages_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            install_dir = temporary / "install"
            venv_bin = install_dir / "venv" / "bin"
            link_dir = temporary / "bin"
            pip_arguments = temporary / "pip-arguments.txt"
            fake_python = venv_bin / "python"
            venv_bin.mkdir(parents=True)

            commands = " ".join(COMMANDS)
            fake_python.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env bash
                    set -euo pipefail
                    if [[ \"${{1:-}}\" == -m && \"${{2:-}}\" == pip ]]; then
                        printf '%s\\n' \"$@\" > {str(pip_arguments)!r}
                        for command in {commands}; do
                            printf '#!/usr/bin/env bash\\nexit 0\\n' > \"$(dirname \"$0\")/$command\"
                            chmod 0755 \"$(dirname \"$0\")/$command\"
                        done
                    fi
                    """
                ),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            shell = textwrap.dedent(
                f"""\
                set -euo pipefail
                source {str(MANAGE_SCRIPT)!r}
                INSTALL_DIR={str(install_dir)!r}
                COMMAND_LINK_DIR={str(link_dir)!r}

                install_open_mmi_package
                install_command_links

                for command in \"${{OPEN_MMI_COMMANDS[@]}}\"; do
                    wrapper=\"$INSTALL_DIR/venv/bin/$command\"
                    link=\"$COMMAND_LINK_DIR/$command\"
                    test -x \"$wrapper\"
                    test -L \"$link\"
                    test \"$(readlink \"$link\")\" = \"$wrapper\"
                done

                remove_command_links
                for command in \"${{OPEN_MMI_COMMANDS[@]}}\"; do
                    test ! -e \"$COMMAND_LINK_DIR/$command\"
                    test ! -L \"$COMMAND_LINK_DIR/$command\"
                done
                """
            )

            result = self._run_shell(shell)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                pip_arguments.read_text(encoding="utf-8").splitlines(),
                [
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "--force-reinstall",
                    str(install_dir),
                ],
            )

    def test_command_link_install_refuses_unrelated_target_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            install_dir = temporary / "install"
            venv_bin = install_dir / "venv" / "bin"
            link_dir = temporary / "bin"
            venv_bin.mkdir(parents=True)
            link_dir.mkdir(parents=True)

            for command in COMMANDS:
                wrapper = venv_bin / command
                wrapper.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
                wrapper.chmod(0o755)

            conflict = link_dir / "open-mmi-launcher"
            conflict.write_text("unrelated command\n", encoding="utf-8")

            shell = textwrap.dedent(
                f"""\
                set -euo pipefail
                source {str(MANAGE_SCRIPT)!r}
                INSTALL_DIR={str(install_dir)!r}
                COMMAND_LINK_DIR={str(link_dir)!r}

                if install_command_links; then
                    exit 20
                fi

                test -f \"$COMMAND_LINK_DIR/open-mmi-launcher\"
                test ! -L \"$COMMAND_LINK_DIR/open-mmi-launcher\"
                for command in open-mmi-canbusd open-mmi-config open-mmi-dashboard open-mmi-powerd open-mmi-status open-mmi-update-coordinator open-mmi-update-installer open-mmi-vehicle-config-coordinator; do
                    test ! -e \"$COMMAND_LINK_DIR/$command\"
                    test ! -L \"$COMMAND_LINK_DIR/$command\"
                done
                """
            )

            result = self._run_shell(shell)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(conflict.read_text(encoding="utf-8"), "unrelated command\n")

    def test_remove_leaves_unrelated_links_and_files_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary = Path(temporary_directory)
            install_dir = temporary / "install"
            link_dir = temporary / "bin"
            link_dir.mkdir(parents=True)
            unrelated_target = temporary / "other-launcher"
            unrelated_target.write_text("other\n", encoding="utf-8")

            (link_dir / "open-mmi-launcher").symlink_to(unrelated_target)
            (link_dir / "open-mmi-status").write_text("file\n", encoding="utf-8")

            shell = textwrap.dedent(
                f"""\
                set -euo pipefail
                source {str(MANAGE_SCRIPT)!r}
                INSTALL_DIR={str(install_dir)!r}
                COMMAND_LINK_DIR={str(link_dir)!r}
                remove_command_links
                test -L \"$COMMAND_LINK_DIR/open-mmi-launcher\"
                test -f \"$COMMAND_LINK_DIR/open-mmi-status\"
                """
            )

            result = self._run_shell(shell)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
