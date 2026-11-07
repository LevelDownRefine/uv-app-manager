"""Install source applications in isolated Python environments."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import portalocker
from dulwich.client import get_transport_and_path
from dulwich.object_store import peel_sha
from dulwich.objects import Commit, Tag
from dulwich.repo import Repo
from uv import find_uv_bin


@dataclass(frozen=True)
class Application:
    repository: str
    revision: str
    python_version: str
    requirements: str = "requirements.txt"


@dataclass(frozen=True)
class Command:
    program: str
    arguments: tuple[str, ...]
    working_directory: str


def _relative_file(root: Path, name: str) -> Path:
    if not name or Path(name).is_absolute():
        raise ValueError("Expected a relative file path")
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("File path escapes the source directory")
    return path


def install(
    app: Application,
    directory: Path,
    uv: Path | None = None,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Clone a revision and prepare its Python environment in a new directory."""
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", app.python_version):
        raise ValueError("Invalid Python version")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if uv is not None:
        bundled_uv = uv
    elif getattr(sys, "frozen", False):
        bundled_uv = Path(sys._MEIPASS) / ("uv.exe" if os.name == "nt" else "uv")
    else:
        bundled_uv = Path(find_uv_bin())
    bundled_uv = bundled_uv.resolve(strict=True)
    environment = os.environ.copy()
    environment["UV_PYTHON_INSTALL_DIR"] = str(directory / "python")
    environment["UV_CACHE_DIR"] = str(directory / "cache")
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX"):
        environment.pop(name, None)

    def run(stage: str, command: list[str], cwd: Path) -> None:
        if progress is not None:
            progress(stage)
        subprocess.run(command, cwd=cwd, env=environment, check=True)

    source = directory / "app"
    state_path = directory / "install.json"
    identity = {
        "repository": app.repository,
        "revision": app.revision,
        "python_version": app.python_version,
        "requirements": app.requirements,
    }
    with portalocker.Lock(directory / ".install.lock", timeout=0):
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if state.get("application", {}) != identity:
                raise ValueError(
                    "Installation belongs to another application or revision"
                )
            if state.get("complete", False):
                raise FileExistsError("Application is already installed")
        else:
            if source.exists():
                raise FileExistsError(f"Source directory already exists: {source}")
            temporary_state = state_path.with_suffix(".tmp")
            temporary_state.write_text(
                json.dumps({"application": identity, "complete": False}),
                encoding="utf-8",
            )
            temporary_state.replace(state_path)
        uv = directory / ("uv.exe" if os.name == "nt" else "uv")
        if not uv.exists():
            temporary_uv = directory / (uv.name + ".tmp")
            shutil.copy2(bundled_uv, temporary_uv)
            temporary_uv.replace(uv)
        if not source.exists():
            staging = Path(tempfile.mkdtemp(prefix="clone-", dir=directory))
            staged_source = staging / "app"
            if progress is not None:
                progress("clone")
            client, remote_path = get_transport_and_path(app.repository)
            revision = app.revision.encode("utf-8")
            prefixes = [b"refs/heads/" + revision, b"refs/tags/" + revision]
            with client.clone(
                remote_path,
                str(staged_source),
                depth=1,
                branch=app.revision,
                ref_prefix=prefixes,
                protocol_version=2,
                origin_url=app.repository,
                progress=(
                    lambda data: (
                        progress(data.decode("utf-8", errors="replace"))
                        if progress is not None
                        else None
                    )
                ),
            ) as repository:
                head = repository.head()
                if isinstance(repository[head], Tag):
                    _, peeled = peel_sha(repository.object_store, head)
                    if not isinstance(peeled, Commit):
                        raise ValueError("Selected tag does not refer to a commit")
                    repository.refs[b"HEAD"] = peeled.id
            staged_source.replace(source)
            staging.rmdir()
        else:
            with Repo(str(source)) as repository:
                repository.head()
        requirements = _relative_file(source, app.requirements)
        if not requirements.is_file():
            raise FileNotFoundError(requirements)
        run(
            "python",
            [
                str(uv),
                "python",
                "install",
                "--no-bin",
                "--no-registry",
                app.python_version,
            ],
            source,
        )
        run(
            "environment",
            [
                str(uv),
                "venv",
                "--allow-existing",
                "--managed-python",
                "--python",
                app.python_version,
                ".venv",
            ],
            source,
        )
        python = (
            source
            / ".venv"
            / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        run(
            "dependencies",
            [
                str(uv),
                "pip",
                "install",
                "--python",
                str(python),
                "-r",
                str(requirements),
            ],
            source,
        )
        temporary_state = state_path.with_suffix(".tmp")
        temporary_state.write_text(
            json.dumps({"application": identity, "complete": True}), encoding="utf-8"
        )
        temporary_state.replace(state_path)
        return source


def launch_command(source: Path, script: str, arguments: list[str]) -> Command:
    """Build a command using the application's interpreter and source directory."""
    source = source.resolve(strict=True)
    state_path = source.parent / "install.json"
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("complete", False) is not True:
            raise RuntimeError("Application installation is incomplete")
    script_path = _relative_file(source, script)
    if script_path.suffix.lower() != ".py" or not script_path.is_file():
        raise ValueError("Python entry point does not exist")
    python = (
        source / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not python.is_file():
        raise FileNotFoundError(python)
    if any(not isinstance(argument, str) for argument in arguments):
        raise ValueError("Arguments must be strings")
    return Command(str(python), (str(script_path), *arguments), str(source))
