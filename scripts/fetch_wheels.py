"""Download pinned wheels one by one (resumable) for slow connections, then install offline.

pip normally downloads each package's full index page first; for numpy that page is several MB
and times out on a ~30 KB/s line ("from versions: none"). This script asks PyPI's small
per-version JSON for the single wheel we need (Windows, CPython 3.12) and resumes partial
downloads. Uses only the standard library, so it runs before the venv has anything installed.

    python scripts/fetch_wheels.py            # download into .wheelhouse/
    .venv\\Scripts\\python -m pip install --no-index --find-links .wheelhouse -r requirements.lock
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOUSE = ROOT / ".wheelhouse"
LOCK = ROOT / "requirements.lock"
UA = {"User-Agent": "sentinel-shield-student-project"}
TAGS = ("cp312-cp312-win_amd64", "py3-none-any", "py2.py3-none-any", "cp312-abi3-win_amd64",
        "cp38-abi3-win_amd64", "cp39-abi3-win_amd64", "cp310-abi3-win_amd64", "cp311-abi3-win_amd64")


# curl is used for the network: on this connection Python's own TLS handshakes often stall
# (>120 s) while curl's complete in seconds. curl also resumes partial files (-C -).
CURL = shutil.which("curl") or "curl"


def get(url: str) -> bytes:
    for attempt in range(8):
        p = subprocess.run([CURL, "-sS", "-L", "-f", "--connect-timeout", "60", "--max-time", "300",
                            "-A", UA["User-Agent"], url], capture_output=True)
        if p.returncode == 0:
            return p.stdout
        if p.returncode == 22 and b"404" in p.stderr:
            raise urllib.error.HTTPError(url, 404, "not found", None, None)
        print(f"    retry {attempt + 1}: curl exit {p.returncode}", flush=True)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}")


def download(url: str, dest: Path, size: int) -> None:
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(40):
        if part.exists() and part.stat().st_size >= size:
            break
        p = subprocess.run([CURL, "-sS", "-L", "-f", "-C", "-", "--connect-timeout", "60",
                            "--speed-limit", "500", "--speed-time", "90",
                            "-A", UA["User-Agent"], "-o", str(part), url])
        if p.returncode not in (0, 33):
            have = part.stat().st_size / 1e6 if part.exists() else 0
            print(f"    resume after curl exit {p.returncode} at {have:.1f} MB", flush=True)
            time.sleep(5)
    if not part.exists() or part.stat().st_size != size:
        raise RuntimeError(f"incomplete download {dest.name}")
    part.rename(dest)


def main() -> None:
    HOUSE.mkdir(exist_ok=True)
    pins = [l.split("#")[0].strip() for l in LOCK.read_text().splitlines()]
    pins = [p for p in pins if p]
    total, missing = 0, []
    for pin in pins:
        name, ver = pin.split("==")
        name = name.split("[")[0]
        try:
            meta = json.loads(get(f"https://pypi.org/pypi/{name}/{ver}/json"))
        except urllib.error.HTTPError as ex:
            print(f"[MISS] {pin}: HTTP {ex.code}", flush=True)
            missing.append(pin)
            continue
        files = [f for f in meta["urls"] if f["packagetype"] == "bdist_wheel"]
        pick = None
        for tag in TAGS:
            pick = next((f for f in files if f["filename"].endswith(tag + ".whl")), None)
            if pick:
                break
        if not pick:
            print(f"[MISS] {pin}: no win_amd64/py3 wheel", flush=True)
            missing.append(pin)
            continue
        dest = HOUSE / pick["filename"]
        total += pick["size"]
        if dest.exists() and dest.stat().st_size == pick["size"]:
            print(f"[have] {pick['filename']}", flush=True)
            continue
        print(f"[get ] {pick['filename']} ({pick['size'] / 1e6:.1f} MB)", flush=True)
        download(pick["url"], dest, pick["size"])
    print(f"Wheels present: {len(pins) - len(missing)} packages, {total / 1e6:.1f} MB", flush=True)
    if missing:
        sys.exit(f"Missing: {missing}")


if __name__ == "__main__":
    main()
