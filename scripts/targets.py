"""One matrix for native packages and their standard GitHub runners."""

import platform

TARGETS = {
    "darwin-arm64": ("macos-15", "aarch64-apple-darwin"),
    "darwin-x64": ("macos-15-intel", "x86_64-apple-darwin"),
    "linux-arm64": ("ubuntu-24.04-arm", "aarch64-unknown-linux-musl"),
    "linux-x64": ("ubuntu-24.04", "x86_64-unknown-linux-musl"),
    "win32-arm64": ("windows-11-arm", "aarch64-pc-windows-msvc"),
    "win32-x64": ("windows-2025", "x86_64-pc-windows-msvc"),
}


def native_target():
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "win32"}.get(
        platform.system()
    )
    machine = platform.machine().lower()
    architecture = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "amd64": "x64",
        "x86_64": "x64",
    }.get(machine)
    target = f"{system}-{architecture}"
    if target not in TARGETS:
        raise SystemExit(f"Unsupported native platform: {platform.system()} {machine}")
    return target, TARGETS[target][1]


def bun_target(target):
    # Install the same CPU variant we compile; cross-variant downloads can fail
    # during Windows compilation, before the native package can be tested.
    # https://github.com/Brevilabs/obsidian-copilot-private/issues/378
    return (
        "bun-"
        + target.replace("win32", "windows")
        + ("-baseline" if target.endswith("x64") else "")
    )


def archive_suffix(target):
    return ".tar.gz" if target.startswith("linux-") else ".zip"
