"""Discover immutable candidate inputs and automatically publish a complete verified set."""

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

from targets import TARGETS, bun_target

ROOT = pathlib.Path(__file__).resolve().parents[1]


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tag(pins):
    return f"v{pins['acpVersion']}-r{pins['packagingRevision']}"


def discover(pins, repository, requested=""):
    upstream = "agentclientprotocol/codex-acp"
    release = api(
        f"repos/{upstream}/releases/" + (f"tags/{requested}" if requested else "latest")
    )
    version = release["tag_name"]
    if (
        release["draft"]
        or release["prerelease"]
        or not re.fullmatch(r"v\d+\.\d+\.\d+", version)
    ):
        raise ValueError("Only stable semver releases are supported")
    candidate = {**pins, "acpVersion": version[1:]}
    # Do not edit assets of an existing release, including an interrupted draft.
    # https://github.com/Brevilabs/obsidian-copilot-private/issues/378
    existing = json.loads(
        subprocess.check_output(
            ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases"],
            text=True,
        )
    )
    if any(r["tag_name"] == tag(candidate) for page in existing for r in page):
        return None
    candidate["acpCommit"] = api(f"repos/{upstream}/commits/{version}")["sha"]
    lock = urllib.request.urlopen(
        f"https://raw.githubusercontent.com/{upstream}/{candidate['acpCommit']}/package-lock.json"
    ).read()
    candidate["lockSha256"] = hashlib.sha256(lock).hexdigest()
    candidate["codexVersion"] = json.loads(lock)["packages"][
        "node_modules/@openai/codex"
    ]["version"]
    candidate["codexCommit"] = api(
        f"repos/openai/codex/commits/rust-v{candidate['codexVersion']}"
    )["sha"]
    return candidate


def verify(dist, pins, commit):
    manifests = list(dist.glob("*.json"))
    if len(manifests) != len(TARGETS):
        raise ValueError("All six target manifests are required")
    seen = set()
    for path in manifests:
        manifest = json.loads(path.read_text())
        target = manifest["target"]
        expected = f"codex-acp-{tag(pins)}-{target}.zip"
        if target not in TARGETS or target in seen or manifest["archive"] != expected:
            raise ValueError("Unexpected or duplicate target/archive")
        seen.add(target)
        if (
            any(manifest.get(k) != v for k, v in pins.items())
            or manifest["packagingCommit"] != commit
        ):
            raise ValueError("Mixed build inputs")
        if digest(dist / expected) != manifest["sha256"]:
            raise ValueError("Archive checksum mismatch")
    if {p.name for p in dist.glob("*.zip")} != {
        f"codex-acp-{tag(pins)}-{t}.zip" for t in TARGETS
    }:
        raise ValueError("Unexpected archives")
    return sorted(dist.glob("*.zip"))


def write_checksums(dist):
    path = dist / "SHA256SUMS"
    path.write_text(
        "".join(
            f"{digest(p)}  {p.name}\n"
            for p in sorted(dist.iterdir())
            if p.suffix in (".zip", ".json")
        )
    )
    return path


def main():
    mode = sys.argv[1]
    if mode not in ("discover", "verify", "publish"):
        raise ValueError("Expected discover, verify or publish")
    if mode == "publish" and (
        os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_EVENT_NAME") not in ("schedule", "workflow_dispatch")
    ):
        raise ValueError("Publication requires a scheduled or manual run on main")
    pins = json.loads((ROOT / "inputs.json").read_text())
    if mode == "discover":
        if os.environ["GITHUB_EVENT_NAME"] != "pull_request":
            pins = discover(
                pins,
                os.environ["GITHUB_REPOSITORY"],
                os.environ.get("UPSTREAM_TAG", ""),
            )
        if pins:
            (ROOT / "candidate-inputs.json").write_text(
                json.dumps(pins, indent=2) + "\n"
            )
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"build={'true' if pins else 'false'}\n")
            output.write(
                "matrix="
                + json.dumps(
                    {
                        "include": [
                            {
                                "target": t,
                                "runner": r[0],
                                "bun": bun_target(t).replace("arm64", "aarch64"),
                            }
                            for t, r in TARGETS.items()
                        ]
                    }
                )
                + "\n"
            )
    else:
        dist = ROOT / "dist"
        archives = verify(dist, pins, os.environ["GITHUB_SHA"])
        write_checksums(dist)
        if mode == "verify":
            return
        # A failed upload remains a draft: bump packagingRevision for retry, never overwrite it.
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag(pins),
                "--draft",
                "--target",
                os.environ["GITHUB_SHA"],
                "--title",
                tag(pins),
                "--notes",
                "Independent Codex ACP bundle. All six native package builds and "
                "automated verification passed. See the bundled DISTRIBUTION.md "
                "for signing, platform and authenticated-testing limitations.",
                *map(str, archives),
                *map(str, dist.glob("*.json")),
                str(dist / "SHA256SUMS"),
            ],
            check=True,
        )
        subprocess.run(
            ["gh", "release", "edit", tag(pins), "--draft=false"], check=True
        )


if __name__ == "__main__":
    main()
