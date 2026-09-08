"""Build a native evaluation archive from pinned inputs."""

import hashlib
import json
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from targets import archive_suffix, bun_target, native_target

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT):
    subprocess.run([shutil.which(args[0]) or args[0], *args[1:]], cwd=cwd, check=True)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_upstream(source, target):
    # Upstream tests require POSIX snapshots and a .cmd shim on Windows; our
    # native-only launcher is verified by real package smoke tests on every OS.
    # https://github.com/Brevilabs/obsidian-copilot-private/issues/378
    if not target.startswith("win32-"):
        run("npm", "test", cwd=source)
    run("npm", "run", "typecheck", cwd=source)


def create_archive(package, dist, target):
    archive = dist / (package.name + archive_suffix(target))
    archive.unlink(missing_ok=True)
    if target.startswith("linux-"):
        with tarfile.open(archive, "w:gz") as packed:
            packed.add(package, arcname=package.name)
    else:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
            for path in package.rglob("*"):
                zipped.write(path, path.relative_to(package.parent))
        with zipfile.ZipFile(archive) as zipped:
            if zipped.testzip():
                raise SystemExit("Archive integrity failed")
    return archive


def build():
    pins = json.loads((ROOT / "inputs.json").read_text())
    target, triple = native_target()
    extension = ".exe" if target.startswith("win32") else ""
    if (
        subprocess.check_output(["bun", "--revision"], text=True).strip()
        != pins["bunRevision"]
    ):
        raise SystemExit(f"Install Bun {pins['bunRevision']} before building")
    if (
        subprocess.check_output(
            ["bun", "-e", "console.log(process.platform + '-' + process.arch)"],
            text=True,
        ).strip()
        != target
    ):
        raise SystemExit("Bun must match the native runner architecture")
    work = ROOT / "build"
    if work.exists():
        raise SystemExit("Remove build/ before starting a clean build")
    source = work / "upstream"
    run(
        "git",
        "clone",
        "--no-checkout",
        "--filter=blob:none",
        "https://github.com/agentclientprotocol/codex-acp.git",
        str(source),
    )
    # Preserve upstream lockfile bytes even with Windows global autocrlf enabled.
    # https://github.com/Brevilabs/obsidian-copilot-private/issues/378
    run("git", "config", "core.autocrlf", "false", cwd=source)
    run("git", "checkout", "--detach", pins["acpCommit"], cwd=source)
    if sha256(source / "package-lock.json") != pins["lockSha256"]:
        raise SystemExit("Upstream lockfile does not match pinned digest")
    if (
        json.loads((source / "package.json").read_text())["version"]
        != pins["acpVersion"]
    ):
        raise SystemExit("Pinned ACP version does not match source")
    run("npm", "ci", "--ignore-scripts", cwd=source)
    native = source / f"node_modules/@openai/codex-{target}"
    if (
        json.loads((native / "package.json").read_text())["version"]
        != pins["codexVersion"] + "-" + target
    ):
        raise SystemExit("Locked native Codex version does not match inputs")
    # Native .exe launches must bypass cmd.exe so relocated paths with spaces work.
    # https://github.com/Brevilabs/obsidian-copilot-private/issues/378
    patches = {
        "CodexCli.ts": ('shell: process.platform === "win32"', "shell: false"),
        "CodexJsonRpcConnection.ts": (
            """process.platform === 'win32'
            ? spawn(`"${codexPath}" app-server`, { shell: true, env: spawnEnv })
            : spawn(codexPath, ['app-server'], { env: spawnEnv })""",
            "spawn(codexPath, ['app-server'], { env: spawnEnv })",
        ),
    }
    for filename, (needle, replacement) in patches.items():
        path = source / "src" / filename
        text = path.read_text()
        if text.count(needle) != 1:
            raise SystemExit("Upstream native spawn patch needs review")
        path.write_text(text.replace(needle, replacement))
    check_upstream(source, target)
    name = f"codex-acp-v{pins['acpVersion']}-{target}"
    package = work / name
    package.mkdir()
    shutil.copyfile(ROOT / "scripts/entry.ts", source / "portable-entry.ts")
    run(
        "bun",
        "build",
        "portable-entry.ts",
        "--minify",
        "--sourcemap",
        "--compile",
        "--target=" + bun_target(target),
        "--outfile",
        str(package / ("codex-acp" + extension)),
        cwd=source,
    )
    shutil.copytree(native / ("vendor/" + triple), package / "codex-runtime")
    notices = package / "licenses"
    notices.mkdir()
    shutil.copyfile(ROOT / "LICENSE", notices / "packaging-LICENSE")
    shutil.copyfile(source / "LICENSE", notices / "codex-acp-LICENSE")
    # Retain available npm notices, including transitive bundled JavaScript dependencies.
    for path in (source / "node_modules").rglob("*"):
        if path.is_file() and path.name.lower().startswith(
            ("license", "notice", "copying")
        ):
            notice = notices / "npm" / path.relative_to(source / "node_modules")
            notice.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, notice)
    for project, revision, filename in [
        ("oven-sh/bun", "bun-v" + pins["bunRevision"].split("+")[0], "LICENSE.md"),
        ("openai/codex", pins["codexCommit"], "LICENSE"),
        ("openai/codex", pins["codexCommit"], "NOTICE"),
    ]:
        urllib.request.urlretrieve(
            f"https://raw.githubusercontent.com/{project}/{revision}/{filename}",
            notices / (project.replace("/", "-") + "-" + filename),
        )
    shutil.copyfile(ROOT / "DISTRIBUTION.md", package / "DISTRIBUTION.md")
    provenance = {
        **pins,
        "packagingCommit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "packagingDirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
        ),
        "target": target,
        "buildPlatform": platform.platform(),
    }
    (package / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    # Test the exact package before making the local archive; no public publisher here.
    run(sys.executable, str(ROOT / "tests/smoke.py"), str(package))
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = create_archive(package, dist, target)
    manifest = {
        **provenance,
        "archive": archive.name,
        "sha256": sha256(archive),
        "archiveBytes": archive.stat().st_size,
        "extractedBytes": sum(
            p.stat().st_size for p in package.rglob("*") if p.is_file()
        ),
    }
    (dist / (name + ".json")).write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    build()
