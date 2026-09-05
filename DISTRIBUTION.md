# Distribution gates

These archives are local evaluation builds. Do not publish them as user releases yet.

## Licenses and source

Our packaging code is Apache-2.0. Codex ACP and Codex retain their upstream licenses
and notices. The build copies available npm license/notice files and the upstream
Bun, Codex, and ACP notices into `licenses/`. This collection is not a compliance
certification or a complete dependency audit.

Before distribution, review the exact pinned Bun binary and its embedded components.
Bun's MIT license does not replace JavaScriptCore/WebKit LGPL obligations. Prepare
applicable corresponding source and rebuilding/relinking material, and audit native
Codex helpers and bundled JavaScript dependencies. Record the completed audit and
include its required material in each archive before enabling release publication.
See [Bun licensing](https://bun.com/docs/project/license) and the versions and commits
in `provenance.json`. Source links alone do not establish compliance.

Our modification is the adapter entrypoint, which selects the adjacent bundled native
Codex executable. It preserves `CODEX_HOME` and never copies credentials. This is an
independent Brevilabs package, not an official OpenAI release.

## macOS support and signing

This slice builds only on native Apple Silicon macOS. [Bun documents macOS 13 or newer](https://bun.com/docs/installation#cpu-requirements)
as its minimum, but the complete package's oldest supported version remains unverified.
Do not advertise a supported floor until Codex and every helper pass there.

Local builds have Bun's development/ad-hoc signing and are not Developer ID signed or
notarized. A release owner must choose Developer ID signing plus notarization, verify
all nested executables and entitlements, and test a browser-downloaded quarantined
archive on a clean Mac. We do not instruct users to disable Gatekeeper.

## Human verification

With an explicitly authorized test account, test `codex-acp cli login` in the normal
profile, then authenticate an ACP client, complete a conversation, execute a tool,
and cancel a running turn. Check existing-profile login reuse and a new profile.
Do not commit credentials or authentication-bearing logs. Automated smoke tests use
an isolated profile and prove startup, CLI/login help, helper loading, EOF shutdown,
and process-group termination only. They do not prove OAuth or authenticated tools.
