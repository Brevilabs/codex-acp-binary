import { dirname, join } from "node:path";
// Global overrides must not bypass the tested bundled engine; preserve CODEX_HOME.
// https://github.com/Brevilabs/obsidian-copilot-private/issues/377
process.env.CODEX_PATH = join(dirname(process.execPath), "codex-runtime", "bin", process.platform === "win32" ? "codex.exe" : "codex");
await import("./src/index.ts");
