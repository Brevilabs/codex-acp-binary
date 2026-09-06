"""Exercise a relocated package without ambient credentials or external runtimes."""

import json
import os
import pathlib
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

PACKAGE = pathlib.Path(sys.argv.pop(1)).resolve()


class PackageSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="codex package test ")
        cls.root = pathlib.Path(cls.temp.name)
        cls.package = cls.root / "relocated package"
        shutil.copytree(PACKAGE, cls.package)
        for name in ("home", "profile", "empty-path", "tmp"):
            (cls.root / name).mkdir()
        cls.env = {
            "HOME": str(cls.root / "home"),
            "CODEX_HOME": str(cls.root / "profile"),
            "PATH": str(cls.root / "empty-path"),
            "TMPDIR": str(cls.root / "tmp"),
            "LANG": "en_US.UTF-8",
            "CODEX_PATH": "/nonexistent/inherited-codex",
        }
        cls.binary = cls.package / "codex-acp"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def command(self, binary, *args):
        return subprocess.run(
            [str(binary), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def test_version_cli_login_and_helpers_without_external_runtime(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/377"""
        pins = json.loads((self.package / "provenance.json").read_text())
        for binary, args, expected in [
            (self.binary, ["--version"], pins["acpVersion"]),
            (self.binary, ["cli", "--help"], "Codex CLI"),
            (self.binary, ["cli", "login", "--help"], "login"),
            (
                self.package / "codex-runtime/bin/codex",
                ["--version"],
                pins["codexVersion"],
            ),
            (self.package / "codex-runtime/codex-path/rg", ["--version"], "ripgrep"),
            (
                self.package / "codex-runtime/codex-resources/zsh/bin/zsh",
                ["--version"],
                "zsh",
            ),
        ]:
            with self.subTest(binary=binary.name, args=args):
                result = self.command(binary, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
        # The code-mode host is a protocol helper; clean EOF exercises its loader.
        result = subprocess.run(
            [str(self.package / "codex-runtime/bin/codex-code-mode-host")],
            input="",
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_acp_initializes_and_shuts_down_after_client_disconnect(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/377"""
        self.acp(signal_to_send=None)

    def test_acp_can_be_cancelled_by_process_group(self):
        """https://github.com/Brevilabs/obsidian-copilot-private/issues/377"""
        self.acp(signal_to_send=signal.SIGTERM)

    def acp(self, signal_to_send):
        process = subprocess.Popen(
            [str(self.binary)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
            env=self.env,
            start_new_session=True,
        )
        try:
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "package-smoke", "version": "1"},
                },
            }
            process.stdin.write((json.dumps(request) + "\n").encode())
            process.stdin.flush()
            output = b""
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 20
                response = None
                while time.monotonic() < deadline and response is None:
                    for key, _ in selector.select(0.1):
                        chunk = os.read(key.fd, 65536)
                        self.assertTrue(chunk, "ACP exited before initialization")
                        output += chunk
                        while b"\n" in output:
                            line, output = output.split(b"\n", 1)
                            message = json.loads(line)
                            if message.get("id") == 1:
                                response = message
                self.assertIsNotNone(response, "ACP initialize timed out")
                self.assertIn("result", response)
            if signal_to_send:
                os.killpg(process.pid, signal_to_send)
            else:
                process.stdin.close()
            process.wait(timeout=10)
            if signal_to_send is None:
                self.assertEqual(process.returncode, 0)
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


if __name__ == "__main__":
    unittest.main()
