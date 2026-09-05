"""Reject incompatible inputs before fetching or compiling an artifact."""

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
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
