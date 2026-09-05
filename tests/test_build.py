"""Reject incompatible inputs before fetching or compiling an artifact."""

import importlib.util
import pathlib
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
