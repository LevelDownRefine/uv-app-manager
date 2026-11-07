"""Exercise the bundled uv executable in a frozen application."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import source_app_manager  # noqa: F401
from dulwich import porcelain

uv = Path(sys._MEIPASS) / ("uv.exe" if os.name == "nt" else "uv")
subprocess.run([str(uv), "--version"], check=True)
with tempfile.TemporaryDirectory() as folder:
    repo = Path(folder) / "repo"
    repo.mkdir()
    porcelain.init(repo).close()
    (repo / "main.py").write_text("pass\n", encoding="utf-8")
    porcelain.add(repo, ["main.py"])
    porcelain.commit(
        repo,
        message=b"initial",
        author=b"Test <test@example.org>",
        committer=b"Test <test@example.org>",
    )
    porcelain.tag_create(repo, "v1")
    target = Path(folder) / "clone"
    with porcelain.clone(str(repo), target, depth=1, branch="v1"):
        assert (target / "main.py").is_file()
