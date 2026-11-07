"""Run a real isolated install using a local Git repository."""

import os
import subprocess
import tempfile
from pathlib import Path

from source_app_manager import Application, install, launch_command

git = r"C:\Program Files\Git\cmd\git.exe"
with tempfile.TemporaryDirectory(prefix="source-app-smoke-") as folder:
    root = Path(folder)
    repository = root / "repository"
    repository.mkdir()
    (repository / "requirements.txt").write_text("", encoding="utf-8")
    (repository / "main.py").write_text(
        "import os, sys\nprint(os.getcwd())\nprint(repr(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    subprocess.run([git, "init", str(repository)], check=True, capture_output=True)
    subprocess.run([git, "-C", str(repository), "add", "."], check=True)
    subprocess.run(
        [
            git,
            "-C",
            str(repository),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "commit",
            "-m",
            "test",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run([git, "-C", str(repository), "tag", "v1"], check=True)
    os.environ["PATH"] = r"C:\Windows\System32;C:\Windows"
    source = install(Application(str(repository), "v1", "3.12"), root / "managed")
    command = launch_command(source, "main.py", ["hello world", "-t", "1"])
    result = subprocess.run(
        [command.program, *command.arguments],
        cwd=command.working_directory,
        check=True,
        text=True,
        capture_output=True,
    )
    lines = result.stdout.splitlines()
    assert Path(lines[0]) == source
    assert lines[1] == "['hello world', '-t', '1']"
