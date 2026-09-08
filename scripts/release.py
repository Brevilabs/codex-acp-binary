"""Discover immutable candidate inputs and automatically publish a complete verified set."""

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

from targets import TARGETS, archive_suffix, bun_target

ROOT = pathlib.Path(__file__).resolve().parents[1]


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tag(pins):
    return f"v{pins['acpVersion']}"


def existing_release(repository, version):
    pages = json.loads(
        subprocess.check_output(
            ["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases"],
            text=True,
        )
    )
    return next((r for page in pages for r in page if r["tag_name"] == version), None)


def discover(pins, repository, requested="", replace=False):
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
    existing = existing_release(repository, tag(candidate))
    if existing and not existing["draft"] and not replace:
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
        expected = f"codex-acp-{tag(pins)}-{target}{archive_suffix(target)}"
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
    archives = [p for p in dist.iterdir() if p.name.endswith((".zip", ".tar.gz"))]
    if {p.name for p in archives} != {
        f"codex-acp-{tag(pins)}-{t}{archive_suffix(t)}" for t in TARGETS
    }:
        raise ValueError("Unexpected archives")
    return sorted(archives)


def write_checksums(dist):
    path = dist / "SHA256SUMS"
    path.write_text(
        "".join(
            f"{digest(p)}  {p.name}\n"
            for p in sorted(dist.iterdir())
            if p.name.endswith((".zip", ".tar.gz", ".json"))
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
                replace=os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch",
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
        existing = existing_release(os.environ["GITHUB_REPOSITORY"], tag(pins))
        if existing:
            if not existing["draft"] and os.environ["GITHUB_EVENT_NAME"] != "workflow_dispatch":
                return
            # Replace only after the complete new set passes verification. Recreate
            # the tag too, so it points at the commit that built these packages.
            subprocess.run(
                ["gh", "release", "delete", tag(pins), "--yes", "--cleanup-tag"],
                check=True,
            )
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
