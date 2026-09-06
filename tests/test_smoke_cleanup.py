"""The smoke runner must not signal a process group after reaping its leader."""

import io
import pathlib
import signal
import unittest
from unittest.mock import Mock, patch

import smoke


class SmokeCleanup(unittest.TestCase):
    def test_completed_cancellation_does_not_signal_the_reaped_group_again(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        case = smoke.PackageSmoke()
        case.binary = pathlib.Path("fixture")
        case.root = pathlib.Path(".")
        case.env = {}
        process = Mock(
            stdin=io.BytesIO(),
            stdout=io.BytesIO(b'{"id":1,"result":{}}\n'),
            stderr=io.BytesIO(),
            returncode=0,
        )
        process.poll.return_value = 0
        with (
            patch.object(smoke.subprocess, "Popen", return_value=process),
            patch.object(
                case, "kill_tree", side_effect=[None, PermissionError()]
            ) as kill,
        ):
            case.acp(signal.SIGTERM)
        kill.assert_called_once_with(process, signal.SIGTERM)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_live_process_cleanup_still_propagates_permission_errors(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/378"""
        case = smoke.PackageSmoke()
        case.binary = pathlib.Path("fixture")
        case.root = pathlib.Path(".")
        case.env = {}
        process = Mock()
        process.stdin.write.side_effect = RuntimeError("write failed")
        process.poll.return_value = None
        with (
            patch.object(smoke.subprocess, "Popen", return_value=process),
            patch.object(case, "kill_tree", side_effect=PermissionError()),
            self.assertRaises(PermissionError),
        ):
            case.acp(None)
