"""Build a local, unsigned macOS ARM64 evaluation archive from pinned inputs."""

import hashlib
import json
import pathlib
import platform
import shutil
import subprocess
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build():
    pins = json.loads((ROOT / "inputs.json").read_text())
    if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
        raise SystemExit("This build requires native macOS ARM64")
    if (
        subprocess.check_output(["bun", "--revision"], text=True).strip()
        != pins["bunRevision"]
    ):
        raise SystemExit(f"Install Bun {pins['bunRevision']} before building")
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
    run("git", "checkout", "--detach", pins["acpCommit"], cwd=source)
    if sha256(source / "package-lock.json") != pins["lockSha256"]:
        raise SystemExit("Upstream lockfile does not match pinned digest")
    if (
        json.loads((source / "package.json").read_text())["version"]
        != pins["acpVersion"]
    ):
        raise SystemExit("Pinned ACP version does not match source")
    run("npm", "ci", "--ignore-scripts", cwd=source)
    native = source / "node_modules/@openai/codex-darwin-arm64"
    if (
        json.loads((native / "package.json").read_text())["version"]
        != pins["codexVersion"] + "-darwin-arm64"
    ):
        raise SystemExit("Locked native Codex version does not match inputs")
    run("npm", "test", cwd=source)
    run("npm", "run", "typecheck", cwd=source)
    name = f"codex-acp-v{pins['acpVersion']}-r{pins['packagingRevision']}-darwin-arm64"
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
        "--target=bun-darwin-arm64",
        "--outfile",
        str(package / "codex-acp"),
        cwd=source,
    )
    shutil.copytree(native / "vendor/aarch64-apple-darwin", package / "codex-runtime")
    notices = package / "licenses"
    notices.mkdir()
    shutil.copyfile(ROOT / "LICENSE", notices / "packaging-LICENSE")
    shutil.copyfile(source / "LICENSE", notices / "codex-acp-LICENSE")
    # Retain available npm notices, including transitive bundled JavaScript dependencies.
    for path in (source / "node_modules").rglob("*"):
        if path.is_file() and path.name.lower().startswith(
            ("license", "notice", "copying")
        ):
            target = notices / "npm" / path.relative_to(source / "node_modules")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
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
        "target": "darwin-arm64",
        "macOSBuildVersion": platform.mac_ver()[0],
        "releaseApproved": False,
    }
    (package / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    # Test the exact package before making the local archive; no public publisher here.
    run("python3", str(ROOT / "tests/smoke.py"), str(package))
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / (name + ".zip")
    archive.unlink(missing_ok=True)
    run("zip", "-qr", str(archive), name, cwd=work)
    run("unzip", "-tq", str(archive))
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
