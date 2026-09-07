# Codex ACP binary

Brevilabs packaging for [Codex ACP](https://github.com/agentclientprotocol/codex-acp) and the native [Codex CLI](https://github.com/openai/codex).

The goal is a downloadable package that runs without installing Node.js, npm, Bun, or a separate Codex CLI. Packages will include the compiled adapter and the native Codex distribution, including its helper executables and resources.

The release workflow automatically builds and publishes new stable upstream releases after all six native targets pass.

Brevilabs maintains this independent packaging project. It is not an official OpenAI distribution. Upstream projects and bundled components retain their respective licenses and notices.

## Local native build

Build prerequisites are Python 3.9+, Git, Node.js/npm, and the exact Bun
revision in `inputs.json`. End users do not need those tools. On a supported native host:

```sh
python3 scripts/build.py
```

The command fetches the pinned ACP commit, verifies its lockfile digest, installs locked
dependencies, typechecks upstream on every OS, runs its full suite on POSIX hosts,
compiles the adapter, and copies the full
native Codex distribution. Clean-environment tests relocate the package into a path
with spaces before the ZIP is produced. To rebuild, remove the generated `build/`
directory first. `dist/` contains the archive and JSON with exact inputs, SHA-256 and
compressed/extracted sizes. Binary outputs are not tracked. Input pinning provides a
repeatable recipe; byte-identical reproducibility is not asserted.

The archive has one top-level versioned directory containing `codex-acp`,
`codex-runtime/`, licenses and provenance. Extract the whole directory and run
`codex-acp`; moving just the adapter loses its engine. The launcher ignores inherited
`CODEX_PATH`, preserves `CODEX_HOME`, and leaves the normal user profile untouched.

See [distribution limitations](DISTRIBUTION.md) for signing, minimum-version testing,
authenticated verification and the remaining redistribution audit work. Automated
publication does not certify those checks.

## Candidate builds and releases

PRs build the reviewed `inputs.json` on all six native standard GitHub runners.
Nightly and manual runs resolve the latest stable upstream tag to an exact commit,
lockfile digest and locked Codex version/commit. Bun stays pinned.
Candidate pins are saved with each archive. Upstream tests use POSIX path snapshots
and a Windows `.cmd` fixture incompatible with our direct native launch; Windows
validation uses typechecking and the real packaged executable smoke tests instead.
All six targets must pass those package tests. Source layout changes stop the build for
review rather than silently dropping our native Windows launch patch.

Nightly runs check at 07:23 UTC and skip versions that already have a published
release. Manual dispatch rebuilds and replaces the selected stable tag, or the latest
stable version when no tag is supplied. All release runs
share a concurrency group; a failed target prevents publication. Intermediate
artifacts expire after three days. Builds that fail before creating a release are
retried on the next nightly run; previous build-success statuses do not suppress them.
Interrupted drafts are retried on the next run. After all new packages pass
verification, replacement deletes the existing release and tag, then recreates them
with the new build commit and assets. Downloads are briefly unavailable during
replacement; if publication fails, rerun the workflow to retry.

On `main`, scheduled and manual **Native packages** runs automatically publish the
verified packages after creating GitHub build attestations. No approval variables or
separate publish dispatch are required. PR runs only build and verify; they cannot
publish. Merging does not trigger a build immediately: wait for the next nightly run
or dispatch **Native packages** on `main`.

Each release contains six ZIPs, matching JSON manifests and `SHA256SUMS`. Each ZIP
holds one `codex-acp-v<VERSION>-<TARGET>/` directory; Windows uses
`codex-acp.exe`. The manifest records compressed/extracted sizes and all input pins.
GitHub build attestations cover archives and manifests; after downloading, run
`sha256sum -c SHA256SUMS` and
`gh attestation verify <archive.zip> --repo Brevilabs/codex-acp-binary`.
