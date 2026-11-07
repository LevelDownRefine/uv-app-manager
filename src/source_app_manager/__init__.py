"""Management of Python applications installed from source."""

from .manager import Application, Command, install, launch_command


def _pyinstaller_hook_dirs():
    from pathlib import Path

    return [str(Path(__file__).parent / "_pyinstaller")]


__all__ = ["Application", "Command", "install", "launch_command"]
