"""Discover immutable candidate inputs and publish only a complete approved set."""

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


def context(pins):
    return (
        "native-packages/"
        + hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()
    )


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
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        statuses = json.loads(
            subprocess.check_output(
                [
                    "gh",
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repository}/commits/{os.environ['GITHUB_SHA']}/statuses",
                ],
                text=True,
            )
        )
        if any(
            s["context"] == context(candidate) and s["state"] == "success"
            for page in statuses
            for s in page
        ):
            return None
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


def validate_candidate_run(run, commit):
    if (
        run["head_sha"] != commit
        or run["head_branch"] != "main"
        or run["event"] not in ("schedule", "workflow_dispatch")
        or run["path"] != ".github/workflows/release.yml"
        or run["conclusion"] != "success"
    ):
        raise ValueError("Candidate must be a successful trusted build of this commit")


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
    if sys.argv[1] == "candidate":
        run_id = os.environ["CANDIDATE_RUN_ID"]
        if not re.fullmatch(r"[1-9][0-9]*", run_id):
            raise ValueError("Candidate run ID must be a positive integer")
        validate_candidate_run(
            api(f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{run_id}"),
            os.environ["GITHUB_SHA"],
        )
        return
    pins = json.loads((ROOT / "inputs.json").read_text())
    if sys.argv[1] == "discover":
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
        checksums = write_checksums(dist)
        if sys.argv[1] == "verify":
            return
        if sys.argv[1] == "record":
            api_path = f"repos/{os.environ['GITHUB_REPOSITORY']}/statuses/{os.environ['GITHUB_SHA']}"
            subprocess.run(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    api_path,
                    "-f",
                    "state=success",
                    "-f",
                    "context=" + context(pins),
                    "-f",
                    "description=All six native candidate packages passed",
                ],
                check=True,
            )
            return
        # Approval binds packaging code, input pins and the exact candidate bytes.
        # https://github.com/Brevilabs/obsidian-copilot-private/issues/378
        if (
            os.environ.get("APPROVED_INPUTS_SHA256") != digest(ROOT / "inputs.json")
            or os.environ.get("APPROVED_PACKAGING_SHA") != os.environ["GITHUB_SHA"]
            or os.environ.get("APPROVED_ARTIFACTS_SHA256") != digest(checksums)
            or not os.environ.get("DISTRIBUTION_EVIDENCE_URL", "").startswith(
                "https://"
            )
        ):
            raise ValueError(
                "Distribution gates are not approved for these exact artifacts, inputs and packaging commit"
            )
        (dist / "distribution-approval.json").write_text(
            json.dumps(
                {
                    "releaseApproved": True,
                    "packagingCommit": os.environ["GITHUB_SHA"],
                    "inputsSha256": digest(ROOT / "inputs.json"),
                    "candidateChecksumsSha256": digest(checksums),
                    "evidenceUrl": os.environ["DISTRIBUTION_EVIDENCE_URL"],
                    "artifacts": {
                        p.name: digest(p)
                        for p in sorted(dist.iterdir())
                        if p.suffix in (".zip", ".json")
                    },
                },
                indent=2,
            )
            + "\n"
        )
        write_checksums(dist)
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
                "Independent Codex ACP bundle. Verification: "
                + os.environ["DISTRIBUTION_EVIDENCE_URL"],
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
