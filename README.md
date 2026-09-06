# Codex ACP binary

Brevilabs packaging for [Codex ACP](https://github.com/agentclientprotocol/codex-acp) and the native [Codex CLI](https://github.com/openai/codex).

The goal is a downloadable package that runs without installing Node.js, npm, Bun, or a separate Codex CLI. Packages will include the compiled adapter and the native Codex distribution, including its helper executables and resources.

Local macOS ARM64 packaging is available below. No binary releases are available yet.

Brevilabs maintains this independent packaging project. It is not an official OpenAI distribution. Upstream projects and bundled components retain their respective licenses and notices.

## Local macOS ARM64 build

Build prerequisites are Python 3.9+, Git, Node.js/npm, zip/unzip, and the exact Bun
revision in `inputs.json`. End users do not need those tools. On an Apple Silicon Mac:

```sh
python3 scripts/build.py
```

The command fetches the pinned ACP commit, verifies its lockfile digest, installs locked
dependencies, runs upstream tests/typecheck, compiles the adapter, and copies the full
native Codex distribution. Clean-environment tests relocate the package into a path
with spaces before the ZIP is produced. To rebuild, remove the generated `build/`
directory first. `dist/` contains the archive and JSON with exact inputs, SHA-256 and
compressed/extracted sizes. Binary outputs are not tracked. Input pinning provides a
repeatable recipe; byte-identical reproducibility is not asserted.

The archive has one top-level versioned directory containing `codex-acp`,
`codex-runtime/`, licenses and provenance. Extract the whole directory and run
`codex-acp`; moving just the adapter loses its engine. The launcher ignores inherited
`CODEX_PATH`, preserves `CODEX_HOME`, and leaves the normal user profile untouched.

Read [distribution gates](DISTRIBUTION.md) before publishing any binaries. Signing,
minimum-version testing, authenticated verification and redistribution compliance
remain open. Release automation and other targets are separate work.
