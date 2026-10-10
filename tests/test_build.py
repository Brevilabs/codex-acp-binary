"""Reject incompatible inputs before fetching or compiling an artifact."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import shutil
import zipfile
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "build", pathlib.Path(__file__).resolve().parents[1] / "scripts/build.py"
)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class BuildInputs(unittest.TestCase):
    def test_wrong_platform_does_not_start_build(self):
        with (
            patch.object(
                build,
                "native_target",
                side_effect=SystemExit("Unsupported native platform"),
            ),
            patch.object(build, "run") as run,
        ):
            with self.assertRaisesRegex(SystemExit, "Unsupported native platform"):
                build.build()
            run.assert_not_called()

    def test_wrong_bun_revision_does_not_start_build(self):
        with (
            patch.object(build.platform, "system", return_value="Darwin"),
            patch.object(build.platform, "machine", return_value="arm64"),
            patch.object(build.subprocess, "check_output", return_value="0.0.0"),
            patch.object(build, "run") as run,
        ):
            with self.assertRaisesRegex(SystemExit, "Install Bun"):
                build.build()
            run.assert_not_called()

    def test_sha256_records_file_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "artifact"
            path.write_bytes(b"abc")
            self.assertEqual(
                build.sha256(path),
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )

    def test_checkout_preserves_lock_bytes_with_inherited_autocrlf_true(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            upstream = root / "fixture"
            upstream.mkdir()
            config = root / "gitconfig"
            config.write_text("[core]\n    autocrlf = true\n")
            with patch.dict(
                os.environ,
                {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"},
            ):

                def git(*args):
                    return subprocess.check_output(["git", *args], cwd=upstream)

                git("init")
                (upstream / "package-lock.json").write_bytes(
                    b'{\n  "packages": {}\n}\n'
                )
                (upstream / "package.json").write_text('{"version":"1.10.0"}')
                git("add", ".")
                git(
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "-m",
                    "fixture",
                )
                commit = git("rev-parse", "HEAD").decode().strip()
                pins = {
                    "acpCommit": commit,
                    "acpVersion": "1.10.0",
                    "bunRevision": "test",
                    "lockSha256": build.sha256(upstream / "package-lock.json"),
                }
                (root / "inputs.json").write_text(json.dumps(pins))
                real_run = build.run

                class ReachedInstall(Exception):
                    pass

                def run(*args, **kwargs):
                    if args[:2] == ("git", "clone"):
                        args = (*args[:4], str(upstream), args[-1])
                    if args[0] == "npm":
                        raise ReachedInstall()
                    real_run(*args, **kwargs)

                with (
                    patch.object(build, "ROOT", root),
                    patch.object(
                        build, "native_target", return_value=("darwin-arm64", "unused")
                    ),
                    patch.object(
                        build.subprocess,
                        "check_output",
                        side_effect=["test", "darwin-arm64"],
                    ),
                    patch.object(build, "run", side_effect=run),
                    self.assertRaises(ReachedInstall),
                ):
                    build.build()
                self.assertEqual(
                    (root / "build/upstream/package-lock.json").read_bytes(),
                    (upstream / "package-lock.json").read_bytes(),
                )

    def test_upstream_checks_typecheck_every_target_and_run_posix_suite_on_posix(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        for target in (
            "darwin-arm64",
            "darwin-x64",
            "linux-arm64",
            "linux-x64",
            "win32-arm64",
            "win32-x64",
        ):
            with self.subTest(target=target), patch.object(build, "run") as run:
                build.check_upstream(pathlib.Path("source"), target)
                commands = [call.args for call in run.call_args_list]
                self.assertEqual(commands[-1], ("npm", "run", "typecheck"))
                self.assertEqual(
                    ("npm", "test") in commands, not target.startswith("win32-")
                )


class Patches(unittest.TestCase):
    def fixture(self, root):
        source = root / "upstream"
        source.mkdir()

        def git(*args):
            subprocess.run(
                ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
                cwd=source,
                check=True,
                capture_output=True,
            )

        git("init")
        (source / "launch.ts").write_text("shell: true\n")
        git("add", ".")
        git("commit", "-m", "upstream")
        patches = root / "patches"
        patches.mkdir()
        return source, patches

    def write_patch(self, patches, name, before, after):
        (patches / name).write_text(
            "--- a/launch.ts\n+++ b/launch.ts\n@@ -1 +1 @@\n"
            f"-{before}\n+{after}\n"
        )

    def test_applies_every_patch_in_file_name_order(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/685"""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, patches = self.fixture(root)
            self.write_patch(patches, "0002-second.patch", "shell: false", "shell: never")
            self.write_patch(patches, "0001-first.patch", "shell: true", "shell: false")
            with patch.object(build, "ROOT", root):
                build.apply_patches(source)
            self.assertEqual((source / "launch.ts").read_text(), "shell: never\n")

    def test_stops_the_build_naming_a_patch_that_no_longer_applies(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/685"""
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            source, patches = self.fixture(root)
            self.write_patch(patches, "0001-stale.patch", "shell: cmd", "shell: false")
            with (
                patch.object(build, "ROOT", root),
                self.assertRaisesRegex(SystemExit, "0001-stale.patch no longer applies"),
            ):
                build.apply_patches(source)


class Archives(unittest.TestCase):
    @unittest.skipIf(os.name == "nt" or not shutil.which("tar"), "requires POSIX tar")
    def test_linux_archives_extract_with_system_tar_and_preserve_executables(self):
        for target in ("linux-arm64", "linux-x64"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                package = root / f"codex-acp-v1.10.0-{target}"
                package.mkdir()
                files = {
                    "codex-acp": b"#!/bin/sh\nprintf 'adapter'\n",
                    "codex-runtime/codex/codex": b"#!/bin/sh\nprintf 'engine'\n",
                    "codex-runtime/path/rg": b"#!/bin/sh\nprintf 'helper'\n",
                    "licenses/LICENSE": b"license notice",
                    "provenance.json": b'{"target":"linux"}',
                }
                for name, content in files.items():
                    path = package / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
                    path.chmod(0o755 if content.startswith(b"#!") else 0o644)
                (package / "codex-link").symlink_to("codex-runtime/codex/codex")
                archive = build.create_archive(package, root, target)
                self.assertEqual(archive.name, package.name + ".tar.gz")
                extracted = root / "extract with spaces"
                extracted.mkdir()
                subprocess.run(["tar", "-xzf", str(archive), "-C", str(extracted)], check=True)
                self.assertEqual([p.name for p in extracted.iterdir()], [package.name])
                for name, content in files.items():
                    path = extracted / package.name / name
                    self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(path.stat().st_mode & 0o777, (package / name).stat().st_mode & 0o777)
                    if content.startswith(b"#!"):
                        self.assertTrue(subprocess.check_output([str(path)]))
                self.assertEqual(os.readlink(extracted / package.name / "codex-link"), "codex-runtime/codex/codex")

    def test_macos_and_windows_keep_zip_layout(self):
        for target in ("darwin-arm64", "darwin-x64", "win32-arm64", "win32-x64"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                package = root / f"codex-acp-v1.10.0-{target}"
                package.mkdir()
                binary = "codex-acp.exe" if target.startswith("win32-") else "codex-acp"
                (package / binary).write_bytes(b"adapter")
                archive = build.create_archive(package, root, target)
                self.assertEqual(archive.name, package.name + ".zip")
                with zipfile.ZipFile(archive) as zipped:
                    self.assertEqual(zipped.namelist(), [f"{package.name}/{binary}"])
                    self.assertEqual(zipped.read(f"{package.name}/{binary}"), b"adapter")
