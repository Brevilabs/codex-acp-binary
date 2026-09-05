"""Release safety checks use synthetic API results and tiny archive fixtures."""

import hashlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import release
from targets import TARGETS, bun_target, native_target

ISSUE = "https://github.com/Brevilabs/obsidian-copilot-private/issues/378"


class Release(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.pins = {"acpVersion": "1.10.0", "packagingRevision": 1}
        (self.root / "inputs.json").write_text(json.dumps(self.pins))
        self.dist = self.root / "dist"
        self.dist.mkdir()
        for target in TARGETS:
            name = f"codex-acp-v1.10.0-r1-{target}.zip"
            archive = self.dist / name
            archive.write_bytes(b"archive")
            (self.dist / (name[:-4] + ".json")).write_text(
                json.dumps(
                    {
                        **self.pins,
                        "target": target,
                        "archive": name,
                        "sha256": release.digest(archive),
                        "packagingCommit": "abc",
                    }
                )
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_matching_artifacts_pass(self):
        self.assertEqual(len(release.verify(self.dist, self.pins, "abc")), 6)

    def test_failed_target_mixed_inputs_and_tampering_block_publication(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        manifest = next(self.dist.glob("*.json"))
        original = manifest.read_text()
        for change in [
            {"target": "other"},
            {"packagingCommit": "old"},
            {"sha256": "wrong"},
            {"archive": "../escape.zip"},
            {"acpVersion": "wrong"},
        ]:
            with self.subTest(change=change):
                manifest.write_text(json.dumps({**json.loads(original), **change}))
                with self.assertRaises(ValueError):
                    release.verify(self.dist, self.pins, "abc")
        manifest.unlink()
        with self.assertRaisesRegex(ValueError, "six"):
            release.verify(self.dist, self.pins, "abc")

    def test_approval_is_required_before_any_release_write(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "publish"]),
            patch.dict(os.environ, {"GITHUB_SHA": "abc"}, clear=True),
            patch.object(release.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "not approved"):
                release.main()
            run.assert_not_called()

    def test_failed_draft_upload_never_marks_release_complete(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        environment = {
            "GITHUB_SHA": "abc",
            "APPROVED_PACKAGING_SHA": "abc",
            "APPROVED_INPUTS_SHA256": release.digest(self.root / "inputs.json"),
            "DISTRIBUTION_EVIDENCE_URL": "https://example.com/audit",
        }
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "publish"]),
            patch.dict(os.environ, environment, clear=True),
            patch.object(
                release.subprocess, "run", side_effect=RuntimeError("upload failed")
            ) as run,
        ):
            with self.assertRaises(RuntimeError):
                release.main()
            self.assertEqual(run.call_count, 1)
            self.assertIn("--draft", run.call_args.args[0])

    def test_existing_release_including_draft_skips_build(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        stable = {"tag_name": "v1.10.0", "draft": False, "prerelease": False}
        with (
            patch.object(release, "api", return_value=stable),
            patch.object(
                release.subprocess,
                "check_output",
                return_value='[[{"tag_name":"v1.10.0-r1","draft":true}]]',
            ),
            patch.object(release.urllib.request, "urlopen") as fetch,
        ):
            self.assertIsNone(release.discover(self.pins, "owner/repo"))
            fetch.assert_not_called()

    def test_unseen_stable_resolves_source_lock_and_native_codex(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        stable = {"tag_name": "v1.11.0", "draft": False, "prerelease": False}
        lock = b'{"packages":{"node_modules/@openai/codex":{"version":"0.154.0"}}}'
        with (
            patch.object(
                release,
                "api",
                side_effect=[stable, {"sha": "adapter"}, {"sha": "engine"}],
            ),
            patch.object(release.subprocess, "check_output", return_value="[[]]"),
            patch.object(
                release.urllib.request, "urlopen", return_value=io.BytesIO(lock)
            ),
        ):
            pins = release.discover(self.pins, "owner/repo")
        self.assertEqual(
            pins,
            {
                "acpVersion": "1.11.0",
                "packagingRevision": 1,
                "acpCommit": "adapter",
                "codexCommit": "engine",
                "codexVersion": "0.154.0",
                "lockSha256": hashlib.sha256(lock).hexdigest(),
            },
        )

    def test_unstable_or_failed_discovery_never_builds(self):
        for candidate in [
            {"tag_name": "v1.2.0", "draft": True, "prerelease": False},
            {"tag_name": "v1.2.0-rc1", "draft": False, "prerelease": True},
        ]:
            with (
                patch.object(release, "api", return_value=candidate),
                self.assertRaises(ValueError),
            ):
                release.discover(self.pins, "owner/repo")
        with (
            patch.object(release, "api", side_effect=RuntimeError("API unavailable")),
            self.assertRaises(RuntimeError),
        ):
            release.discover(self.pins, "owner/repo")

    def test_scheduled_success_skips_but_manual_retry_builds(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        stable = {"tag_name": "v1.10.0", "draft": False, "prerelease": False}
        lock = b'{"packages":{"node_modules/@openai/codex":{"version":"0.153.3"}}}'
        candidate = {
            **self.pins,
            "acpCommit": "adapter",
            "codexCommit": "engine",
            "codexVersion": "0.153.3",
            "lockSha256": hashlib.sha256(lock).hexdigest(),
        }
        for event, skipped in [("schedule", True), ("workflow_dispatch", False)]:
            with (
                patch.dict(
                    os.environ, {"GITHUB_EVENT_NAME": event, "GITHUB_SHA": "abc"}
                ),
                patch.object(
                    release,
                    "api",
                    side_effect=[stable, {"sha": "adapter"}, {"sha": "engine"}],
                ),
                patch.object(
                    release.subprocess,
                    "check_output",
                    side_effect=[
                        "[[]]",
                        json.dumps(
                            [
                                [
                                    {
                                        "context": release.context(candidate),
                                        "state": "success",
                                    }
                                ]
                            ]
                        ),
                    ],
                ),
                patch.object(
                    release.urllib.request, "urlopen", return_value=io.BytesIO(lock)
                ),
            ):
                self.assertEqual(
                    release.discover(self.pins, "owner/repo") is None, skipped
                )

    def test_approved_complete_set_uploads_draft_before_publishing(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        environment = {
            "GITHUB_SHA": "abc",
            "APPROVED_PACKAGING_SHA": "abc",
            "APPROVED_INPUTS_SHA256": release.digest(self.root / "inputs.json"),
            "DISTRIBUTION_EVIDENCE_URL": "https://example.com/audit",
        }
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "publish"]),
            patch.dict(os.environ, environment, clear=True),
            patch.object(release.subprocess, "run") as run,
        ):
            release.main()
            self.assertEqual(run.call_count, 2)
            self.assertIn("--draft", run.call_args_list[0].args[0])
            self.assertIn("--draft=false", run.call_args_list[1].args[0])
            self.assertEqual(
                len((self.dist / "SHA256SUMS").read_text().splitlines()), 12
            )

    def test_pull_request_uses_reviewed_pins_without_release_discovery(self):
        output = self.root / "output"
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "discover"]),
            patch.dict(
                os.environ,
                {"GITHUB_EVENT_NAME": "pull_request", "GITHUB_OUTPUT": str(output)},
                clear=True,
            ),
            patch.object(release, "discover") as discover,
        ):
            release.main()
            discover.assert_not_called()
            self.assertEqual(
                json.loads((self.root / "candidate-inputs.json").read_text()), self.pins
            )
            self.assertIn("build=true", output.read_text())

    def test_candidate_completion_marker_requires_verified_artifacts(self):
        for mode in ["verify", "record"]:
            with (
                patch.object(release, "ROOT", self.root),
                patch.object(sys, "argv", ["release.py", mode]),
                patch.dict(
                    os.environ,
                    {"GITHUB_SHA": "abc", "GITHUB_REPOSITORY": "owner/repo"},
                    clear=True,
                ),
                patch.object(release.subprocess, "run") as run,
            ):
                release.main()
                self.assertEqual(run.call_count, int(mode == "record"))


class Targets(unittest.TestCase):
    def test_all_native_os_architectures_select_expected_package(self):
        for system, prefix in [
            ("Darwin", "darwin"),
            ("Linux", "linux"),
            ("Windows", "win32"),
        ]:
            for machine, arch in [
                ("ARM64", "arm64"),
                ("aarch64", "arm64"),
                ("AMD64", "x64"),
                ("x86_64", "x64"),
            ]:
                with (
                    patch("targets.platform.system", return_value=system),
                    patch("targets.platform.machine", return_value=machine),
                ):
                    self.assertEqual(native_target()[0], f"{prefix}-{arch}")
        with (
            patch("targets.platform.system", return_value="FreeBSD"),
            self.assertRaises(SystemExit),
        ):
            native_target()

    def test_bun_install_and_compile_share_native_cpu_variant(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        expected = {
            "darwin-arm64": "bun-darwin-arm64",
            "darwin-x64": "bun-darwin-x64-baseline",
            "linux-arm64": "bun-linux-arm64",
            "linux-x64": "bun-linux-x64-baseline",
            "win32-arm64": "bun-windows-arm64",
            "win32-x64": "bun-windows-x64-baseline",
        }
        for target, compiler in expected.items():
            with self.subTest(target=target):
                self.assertEqual(bun_target(target), compiler)
