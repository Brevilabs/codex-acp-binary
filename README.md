# Codex ACP binary

Brevilabs packaging for [Codex ACP](https://github.com/agentclientprotocol/codex-acp) and the native [Codex CLI](https://github.com/openai/codex).

The goal is a downloadable package that runs without installing Node.js, npm, Bun, or a separate Codex CLI. Packages will include the compiled adapter and the native Codex distribution, including its helper executables and resources.

Local native packaging and six-platform candidate CI are available below. No approved binary releases are available yet.

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

Read [distribution gates](DISTRIBUTION.md) before publishing any binaries. Signing,
minimum-version testing, authenticated verification and redistribution compliance
remain open. See the release workflow below.

## Candidate builds and releases

PRs build the reviewed `inputs.json` on all six native standard GitHub runners.
Nightly and manual runs resolve the latest stable upstream tag to an exact commit,
lockfile digest and locked Codex version/commit. Bun and packaging revision stay pinned.
Candidate pins are saved with each archive. Upstream tests use POSIX path snapshots
and a Windows `.cmd` fixture incompatible with our direct native launch; Windows
validation uses typechecking and the real packaged executable smoke tests instead.
All six targets must pass those package tests. Source layout changes stop the build for
review rather than silently dropping our native Windows launch patch.

Nightly runs skip published versions and successfully tested candidates with identical
inputs and packaging commit. Manual dispatch retries unpublished candidates and accepts
an older stable tag. All release runs share a concurrency group; a failed target prevents
completion. Intermediate artifacts expire after three days. An interrupted release
upload remains a draft and is never overwritten: increment `packagingRevision` to retry.

Publication is disabled until owners complete [DISTRIBUTION.md](DISTRIBUTION.md).
On the default branch, set repository variables `APPROVED_PACKAGING_SHA` to the reviewed
packaging commit, `APPROVED_INPUTS_SHA256` to the SHA-256 of the exact candidate input
JSON, and `DISTRIBUTION_EVIDENCE_URL` to the approved compliance, signing and authenticated
test evidence. Then manually rerun that candidate. These approvals must change whenever
inputs or packaging code change. Recording an approval does not add missing license
material or sign a binary; those gates require implementation/evidence before approval.

Each release contains six ZIPs, matching JSON manifests and `SHA256SUMS`. Each ZIP holds
one `codex-acp-v<VERSION>-r<REVISION>-<TARGET>/` directory; Windows uses `codex-acp.exe`.
The manifest records compressed/extracted sizes and all input pins. GitHub build
attestations cover archives and manifests; after downloading, run `sha256sum -c SHA256SUMS`
and `gh attestation verify <archive.zip> --repo Brevilabs/codex-acp-binary`.
