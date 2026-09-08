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
        self.pins = {"acpVersion": "1.10.0"}
        (self.root / "inputs.json").write_text(json.dumps(self.pins))
        self.dist = self.root / "dist"
        self.dist.mkdir()
        for target in TARGETS:
            stem = f"codex-acp-v1.10.0-{target}"
            name = stem + (".tar.gz" if target.startswith("linux-") else ".zip")
            archive = self.dist / name
            archive.write_bytes(b"archive")
            (self.dist / (stem + ".json")).write_text(
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

    def test_linux_zip_and_extra_archives_block_publication(self):
        for name in ["codex-acp-v1.10.0-linux-x64.zip", "unexpected.tar.gz"]:
            with self.subTest(name=name):
                extra = self.dist / name
                extra.write_bytes(b"archive")
                with self.assertRaisesRegex(ValueError, "Unexpected archives"):
                    release.verify(self.dist, self.pins, "abc")
                extra.unlink()
        manifest = self.dist / "codex-acp-v1.10.0-linux-x64.json"
        data = json.loads(manifest.read_text())
        data["archive"] = "codex-acp-v1.10.0-linux-x64.zip"
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "target/archive"):
            release.verify(self.dist, self.pins, "abc")

    def test_checksums_cover_both_archive_formats_and_manifests(self):
        checksums = release.write_checksums(self.dist)
        expected = {p.name for p in self.dist.iterdir() if p != checksums}
        lines = checksums.read_text().splitlines()
        self.assertEqual({line.split("  ")[1] for line in lines}, expected)
        for line in lines:
            digest, name = line.split("  ")
            self.assertEqual(digest, release.digest(self.dist / name))

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

    def publication_environment(self, event="schedule"):
        return {
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_SHA": "abc",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": event,
        }

    def test_untrusted_publication_never_writes_release(self):
        for change in [
            {"GITHUB_REF": "refs/heads/feature"},
            {"GITHUB_EVENT_NAME": "pull_request"},
            {"GITHUB_EVENT_NAME": "push"},
        ]:
            with (
                self.subTest(change=change),
                patch.object(release, "ROOT", self.root),
                patch.object(sys, "argv", ["release.py", "publish"]),
                patch.dict(os.environ, {**self.publication_environment(), **change}, clear=True),
                patch.object(release.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(ValueError, "scheduled or manual"):
                    release.main()
                run.assert_not_called()

    def test_invalid_artifacts_never_write_release(self):
        next(self.dist.glob("*.zip")).write_bytes(b"tampered")
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "publish"]),
            patch.dict(os.environ, self.publication_environment(), clear=True),
            patch.object(release.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                release.main()
            run.assert_not_called()

    @patch.object(release, "existing_release", return_value=None)
    def test_failed_upload_or_publication_leaves_retryable_draft(self, existing):
        for results, calls in [(RuntimeError("upload failed"), 1),
                               ([None, RuntimeError("publish failed")], 2)]:
            with (
                self.subTest(calls=calls),
                patch.object(release, "ROOT", self.root),
                patch.object(sys, "argv", ["release.py", "publish"]),
                patch.dict(os.environ, self.publication_environment(), clear=True),
                patch.object(release.subprocess, "run", side_effect=results) as run,
            ):
                with self.assertRaises(RuntimeError):
                    release.main()
                self.assertEqual(run.call_count, calls)
                self.assertIn("--draft", run.call_args_list[0].args[0])

    def test_published_release_skips_scheduled_build(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        stable = {"tag_name": "v1.10.0", "draft": False, "prerelease": False}
        with (
            patch.object(release, "api", return_value=stable),
            patch.object(
                release.subprocess,
                "check_output",
                return_value='[[{"tag_name":"v1.10.0","draft":false}]]',
            ),
            patch.object(release.urllib.request, "urlopen") as fetch,
        ):
            self.assertIsNone(release.discover(self.pins, "owner/repo"))
            fetch.assert_not_called()

    def test_manual_rebuild_and_draft_retry_resolve_existing_version(self):
        stable = {"tag_name": "v1.10.0", "draft": False, "prerelease": False}
        lock = b'{"packages":{"node_modules/@openai/codex":{"version":"0.153.3"}}}'
        for draft, replace in [(False, True), (True, False), (True, True)]:
            with (
                self.subTest(draft=draft, replace=replace),
                patch.object(release, "api", side_effect=[stable, {"sha": "adapter"}, {"sha": "engine"}]),
                patch.object(release, "existing_release", return_value={"draft": draft}),
                patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(lock)),
            ):
                candidate = release.discover(self.pins, "owner/repo", replace=replace)
                self.assertEqual(release.tag(candidate), "v1.10.0")

    def test_replacement_recreates_release_and_tag_after_verification(self):
        for event, draft in [("workflow_dispatch", False), ("schedule", True)]:
            with (
                self.subTest(event=event, draft=draft),
                patch.object(release, "ROOT", self.root),
                patch.object(sys, "argv", ["release.py", "publish"]),
                patch.dict(os.environ, self.publication_environment(event), clear=True),
                patch.object(release, "existing_release", return_value={"draft": draft}),
                patch.object(release.subprocess, "run") as run,
            ):
                release.main()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(commands[0], ["gh", "release", "delete", "v1.10.0", "--yes", "--cleanup-tag"])
                self.assertEqual(commands[1][:4], ["gh", "release", "create", "v1.10.0"])
                self.assertIn("--draft", commands[1])
                self.assertEqual(commands[1][commands[1].index("--target") + 1], "abc")
                self.assertEqual(commands[2], ["gh", "release", "edit", "v1.10.0", "--draft=false"])

    def test_scheduled_publication_preserves_existing_published_release(self):
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "publish"]),
            patch.dict(os.environ, self.publication_environment(), clear=True),
            patch.object(release, "existing_release", return_value={"draft": False}),
            patch.object(release.subprocess, "run") as run,
        ):
            release.main()
            run.assert_not_called()

    def test_failed_replacement_delete_stops_publication(self):
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "publish"]),
            patch.dict(os.environ, self.publication_environment("workflow_dispatch"), clear=True),
            patch.object(release, "existing_release", return_value={"draft": False}),
            patch.object(release.subprocess, "run", side_effect=RuntimeError("delete failed")) as run,
        ):
            with self.assertRaisesRegex(RuntimeError, "delete failed"):
                release.main()
            self.assertEqual(run.call_count, 1)

    def test_manual_discovery_enables_replacement(self):
        for event in ["schedule", "workflow_dispatch"]:
            with (
                self.subTest(event=event),
                patch.object(release, "ROOT", self.root),
                patch.object(sys, "argv", ["release.py", "discover"]),
                patch.dict(os.environ, {
                    **self.publication_environment(event),
                    "GITHUB_OUTPUT": str(self.root / "output"),
                    "UPSTREAM_TAG": "v1.10.0",
                }, clear=True),
                patch.object(release, "discover", return_value=self.pins) as discover,
            ):
                release.main()
                discover.assert_called_once_with(
                    self.pins, "owner/repo", "v1.10.0", replace=event == "workflow_dispatch"
                )

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

    def test_scheduled_and_manual_runs_retry_unpublished_candidates(self):
        stable = {"tag_name": "v1.10.0", "draft": False, "prerelease": False}
        lock = b'{"packages":{"node_modules/@openai/codex":{"version":"0.153.3"}}}'
        for event in ["schedule", "workflow_dispatch"]:
            with (
                self.subTest(event=event),
                patch.dict(os.environ, self.publication_environment(event), clear=True),
                patch.object(release, "api", side_effect=[stable, {"sha": "adapter"}, {"sha": "engine"}]),
                patch.object(release.subprocess, "check_output", side_effect=["[[]]"]) as request,
                patch.object(release.urllib.request, "urlopen", return_value=io.BytesIO(lock)),
            ):
                self.assertIsNotNone(release.discover(self.pins, "owner/repo"))
                # No commit-status lookup: a passed build is not a published release.
                self.assertEqual(request.call_count, 1)

    @patch.object(release, "existing_release", return_value=None)
    def test_complete_set_automatically_uploads_draft_before_publishing(self, existing):
        for event in ["schedule", "workflow_dispatch"]:
            with (
                self.subTest(event=event),
                patch.object(release, "ROOT", self.root),
                patch.object(sys, "argv", ["release.py", "publish"]),
                patch.dict(os.environ, self.publication_environment(event), clear=True),
                patch.object(release.subprocess, "run") as run,
            ):
                original = {p.name: p.read_bytes() for p in self.dist.iterdir()}
                release.main()
                for name, content in original.items():
                    self.assertEqual((self.dist / name).read_bytes(), content)
                self.assertFalse((self.dist / "distribution-approval.json").exists())
                self.assertEqual(run.call_count, 2)
                create = run.call_args_list[0].args[0]
                self.assertIn("--draft", create)
                self.assertIn("--draft=false", run.call_args_list[1].args[0])
                for path in self.dist.iterdir():
                    self.assertIn(str(path), create)
                self.assertEqual(len((self.dist / "SHA256SUMS").read_text().splitlines()), 12)

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

    def test_verify_does_not_publish(self):
        with (
            patch.object(release, "ROOT", self.root),
            patch.object(sys, "argv", ["release.py", "verify"]),
            patch.dict(os.environ, {"GITHUB_SHA": "abc"}, clear=True),
            patch.object(release.subprocess, "run") as run,
        ):
            release.main()
            run.assert_not_called()

    def test_unknown_mode_cannot_publish(self):
        with (
            patch.object(sys, "argv", ["release.py", "record"]),
            patch.object(release.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "Expected"):
                release.main()
            run.assert_not_called()


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
