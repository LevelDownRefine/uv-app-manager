import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dulwich import porcelain
from dulwich.client import get_transport_and_path
from dulwich.objects import Commit
from dulwich.repo import Repo
from source_app_manager import Application, install, launch_command
from source_app_manager.manager import _run_process


def make_repository(root):
    repo = root / "repo"
    repo.mkdir()
    porcelain.init(repo).close()
    (repo / "requirements.txt").touch()
    (repo / "main.py").touch()
    porcelain.add(repo, ["requirements.txt", "main.py"])
    porcelain.commit(
        repo,
        message=b"initial",
        author=b"Test <test@example.org>",
        committer=b"Test <test@example.org>",
    )
    porcelain.tag_create(repo, "v1")
    return Application(str(repo), "v1", "3.12")


class LaunchCommandTests(unittest.TestCase):
    def test_incomplete_installation_cannot_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "app"
            source.mkdir()
            (root / "install.json").write_text(
                json.dumps({"complete": False}), encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                launch_command(source, "main.py", [])

    def test_command_uses_source_directory_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
            interpreter = root / ".venv" / executable
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()
            (root / "main.py").touch()
            command = launch_command(root, "main.py", ["-t", "1", "hello world"])
            self.assertEqual(command.program, str(interpreter))
            self.assertEqual(
                command.arguments, (str(root / "main.py"), "-t", "1", "hello world")
            )
            self.assertEqual(command.working_directory, str(root))

    def test_entry_cannot_escape_source_directory(self):
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(ValueError):
            launch_command(Path(folder), "../outside.py", [])


class InstallTests(unittest.TestCase):
    def test_subprocess_output_is_forwarded_to_progress(self):
        messages = []
        _run_process(
            "dependencies",
            [
                sys.executable,
                "-c",
                "import sys; print('ready'); print('error', file=sys.stderr)",
            ],
            Path.cwd(),
            os.environ.copy(),
            messages.append,
        )
        self.assertEqual(messages[0], "dependencies")
        self.assertCountEqual(messages[1:], ["ready", "error"])

    def test_existing_unmanaged_directory_is_not_modified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_repository(root)
            uv = root / "uv.exe"
            uv.touch()
            target = root / "managed"
            (target / "app").mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                install(app, target, uv)
            self.assertCountEqual(
                [item.name for item in target.iterdir()], ["app", ".install.lock"]
            )

    def test_missing_revision_fails_without_marking_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_repository(root)
            app = Application(app.repository, "missing", "3.12")
            uv = root / "uv.exe"
            uv.touch()
            with (
                patch("source_app_manager.manager._run_process"),
                self.assertRaises(ValueError),
            ):
                install(app, root / "managed", uv)
            self.assertFalse((root / "managed" / "app").exists())

    def test_annotated_tag_points_head_to_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_repository(root)
            porcelain.tag_create(
                app.repository,
                "v2",
                author=b"Test <test@example.org>",
                message=b"release",
                annotated=True,
            )
            uv = root / "uv.exe"
            uv.touch()
            app = Application(app.repository, "v2", "3.12")
            with patch("source_app_manager.manager._run_process"):
                source = install(app, root / "managed", uv)
            with Repo(str(source)) as repository:
                self.assertIsInstance(repository[repository.head()], Commit)

    def test_default_uv_is_copied_from_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_repository(root)
            bundled = root / "bundled-uv"
            bundled.write_bytes(b"bundled uv")
            target = root / "managed"
            with (
                patch(
                    "source_app_manager.manager.find_uv_bin", return_value=str(bundled)
                ),
                patch("source_app_manager.manager._run_process"),
            ):
                install(app, target)
            executable = "uv.exe" if os.name == "nt" else "uv"
            self.assertEqual((target / executable).read_bytes(), b"bundled uv")

    def test_retry_after_clone_interruption(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_repository(root)
            uv = root / "uv.exe"
            uv.touch()
            target = root / "managed"
            client, remote_path = get_transport_and_path(app.repository)
            real_clone = client.clone
            attempts = []

            def clone(*args, **kwargs):
                attempts.append(Path(args[1]))
                if len(attempts) == 1:
                    Path(args[1]).mkdir()
                    raise RuntimeError("interrupted")
                return real_clone(*args, **kwargs)

            with (
                patch(
                    "source_app_manager.manager.get_transport_and_path",
                    return_value=(client, remote_path),
                ),
                patch.object(client, "clone", side_effect=clone),
                patch("source_app_manager.manager._run_process"),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    install(app, target, uv)
                self.assertEqual(install(app, target, uv), target / "app")
            self.assertTrue(attempts[0].exists())
            self.assertEqual(len(attempts), 2)

    def test_retry_after_dependency_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            app = make_repository(root)
            uv = root / "uv.exe"
            uv.touch()
            target = root / "managed"
            attempts = []

            def run(stage, command, cwd, environment, progress):
                attempts.append(command)
                if (
                    command[1:3] == ["pip", "install"]
                    and sum(item[1:3] == ["pip", "install"] for item in attempts) == 1
                ):
                    raise RuntimeError("interrupted")

            with patch("source_app_manager.manager._run_process", side_effect=run):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    install(app, target, uv)
                self.assertEqual(install(app, target, uv), target / "app")
                with self.assertRaises(FileExistsError):
                    install(app, target, uv)


if __name__ == "__main__":
    unittest.main()
