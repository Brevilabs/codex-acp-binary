# Copilot patches

The build applies every `*.patch` in this folder to the upstream codex-acp release being built, in file-name order, with `git apply --3way`. Release runs build each new stable upstream release, and pull requests build the release in `inputs.json`. A patch that no longer applies to a new release stops that build. Upstream's tests and typecheck then run on the patched source, including any tests a patch adds.

Each patch carries a change Copilot needs before upstream has it. Every patch has an upstream issue, so it can be deleted as soon as upstream ships the change.

| Patch | Change | Why Copilot needs it | Upstream issue | Copilot issue | Remove when |
| --- | --- | --- | --- | --- | --- |
| `0001-turn-diff-prompt-meta.patch` | Return Codex's turn diff on the `session/prompt` response to clients that opt in with `_meta.codex.turnDiff`. | Copilot's file-change review needs each file's exact content from before the turn, and ACP tool calls cannot provide it reliably. | [codex-acp#601](https://github.com/agentclientprotocol/codex-acp/issues/601) | [obsidian-copilot-private#347](https://github.com/Brevilabs/obsidian-copilot-private/issues/347) | Upstream ships #601 or an equivalent. If the upstream shape differs, update Copilot to read it before deleting the patch. |

## Updating patches for a new upstream release

1. In a codex-acp clone, check out the new release tag and run `git am --3way` on the patches in order.
2. Resolve any conflicts, then run `npm run typecheck` and `npm test`.
3. Regenerate the files with `git format-patch --zero-commit -o <dir> <tag>`, and replace the files here under their existing names.

## Adding a patch

File the upstream issue first. Add the patch and its row in this table in the same change.
