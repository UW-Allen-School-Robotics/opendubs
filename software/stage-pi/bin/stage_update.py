"""The stage box updates itself (the user's go, 2026-10-26): no one types sudo on it again.

Each deploy publishes a SIGNED copy of software/stage-pi on the robot (release/publish-bundle.sh), signed
by the project's firmware-signing step, the one every board's update uses, for the target "stage". The
box's agent PULLS it, as it pulls the media, so the box still opens no port:

    GET <robot>/api/stage/box/bundle/manifest.json       {"version" (the folder's content), "sha256", ...}
    GET <robot>/api/stage/box/bundle/manifest.json.sig   ECDSA P-256 over "OPENDUBS-FW-SIG-1" \0 stage \0 manifest
    GET <robot>/api/stage/box/bundle/stage-bundle.tar.gz

and installs it only when ALL of these hold, else keeps running what it has and says why:
  - the manifest is signed by a key setup.sh pinned (keys/, the firmware-signing keys' public halves, as
    every board trusts them) and the archive's SHA-256 matches;
  - its version is not the one installed;
  - no show is armed or playing (the media sync's rule), checked again just before the reboot;
  - every path in it stays inside it (no absolute path, no .., no link);
  - the box has every package its packages.txt names: an update installs no packages (the robot's network
    may have no uplink), so one that needs a new package waits for the setup by hand.

INSTALLING UNDER THE OVERLAY: the root is read-only with a RAM overlay, so a file written to / is gone at
the next boot. The real root sits beneath it (the overlay's lowerdir, e.g. /media/root-ro): it is remounted
read-write, the bundle's own bin/stage-install puts the files in place there (the same list setup.sh
installs), the version is written beside them, it is synced and remounted read-only, and the box reboots.
A box with no overlay (set up with --no-overlay) installs into / itself.

KNOWN LIMIT, as for the boards' firmware: no version floor. Something on the robot's network could offer an
OLDER bundle that was genuinely signed, and the box would install it.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
import time
import urllib.request

LIB = "/usr/local/lib/stage"
KEYS = f"{LIB}/keys"                 # PEM public keys, pinned by setup.sh; any one of them may sign
PREFIX = b"OPENDUBS-FW-SIG-1\0stage\0"  # sign-firmware.sh's message for the target "stage"
MARK = "BUNDLE"                      # {LIB}/BUNDLE: the installed bundle's manifest
MAX_BYTES = 32 << 20                 # a bundle is a folder of scripts: far below this
PATH = "/api/stage/box/bundle/"

status = {"installed": None, "offered": None, "state": "not checked", "at": None}


def installed(root: str = "/") -> str | None:
    try:
        with open(os.path.join(root, LIB.lstrip("/"), MARK)) as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


def verify(manifest: bytes, sig: bytes, keys: str = KEYS) -> bool:
    """The manifest is signed, for "stage", by one of the pinned keys (openssl, which the box has)."""
    try:
        pems = sorted(os.path.join(keys, k) for k in os.listdir(keys) if k.endswith(".pem"))
    except OSError:
        return False
    with tempfile.TemporaryDirectory() as d:
        m, s = os.path.join(d, "m"), os.path.join(d, "s")
        open(m, "wb").write(PREFIX + manifest)
        open(s, "wb").write(sig)
        return any(subprocess.run(["openssl", "dgst", "-sha256", "-verify", k, "-signature", s, m],
                                  capture_output=True, timeout=20).returncode == 0 for k in pems)


def safe_extract(blob: bytes, into: str) -> None:
    """Unpack the archive, refusing anything that is not a plain file or folder inside it."""
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        for m in t.getmembers():
            name = os.path.normpath(m.name)
            if name.startswith(("/", "..")) or os.path.isabs(m.name) or ".." in m.name.split("/"):
                raise ValueError(f"a path outside the bundle: {m.name}")
            if not (m.isfile() or m.isdir()):
                raise ValueError(f"not a plain file: {m.name}")
        if hasattr(tarfile, "data_filter"):                    # Python 3.12+ (and patched older ones)
            t.extractall(into, filter="data")
        else:
            t.extractall(into)


def missing_packages(packages_txt: str, have=None) -> list[str]:
    """The lines of packages.txt the box has none of (dpkg)."""
    have = have or (lambda p: subprocess.run(["dpkg", "-s", p], capture_output=True).returncode == 0)
    out = []
    for line in packages_txt.splitlines():
        line = line.split("#", 1)[0].strip()
        if line and not any(have(p) for p in line.split("|")):
            out.append(line)
    return out


def lower_root(mounts: str | None = None) -> str | None:
    """Where to write so it lasts: the overlay's lower root when / is an overlay (None if it cannot be
    reached from here), else / itself."""
    if mounts is None:
        with open("/proc/mounts") as f:
            mounts = f.read()
    for line in mounts.splitlines():
        p = line.split()
        if len(p) >= 4 and p[1] == "/" and p[2] == "overlay":
            opts = dict(o.split("=", 1) for o in p[3].split(",") if "=" in o)
            lower = opts.get("lowerdir", "").split(":")[0]
            return lower if lower and os.path.isdir(os.path.join(lower, "usr")) else None
    return "/"


def _get(url: str, limit: int, timeout: float = 20) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url}: larger than {limit} bytes")
    return data


def check_once(origins, busy, get=_get, apply=None, keys: str = KEYS, root: str = "/") -> dict:
    """One look at the robot's bundle; installs it (and reboots) when everything holds. Returns the status."""
    status.update(installed=installed(root), at=time.time())
    origin = next(iter(origins), None)
    if origin is None:
        status.update(state="the robot does not answer")
        return status
    try:
        manifest = _json_bytes(get(origin + PATH + "manifest.json", 64 << 10))
        m = json.loads(manifest)
    except Exception as e:                                         # noqa: BLE001 — nothing published yet, or no robot
        status.update(state=f"no bundle offered ({str(e)[:80]})", offered=None)
        return status
    status["offered"] = m.get("version")
    if m.get("version") == status["installed"]:
        status.update(state="up to date")
        return status
    if busy():
        status.update(state="waiting: a show is loaded or playing")
        return status
    try:
        sig = get(origin + PATH + "manifest.json.sig", 4096)
        if not verify(manifest, sig, keys):
            status.update(state="REFUSED: the bundle is not signed by a pinned key")
            return status
        blob = get(origin + PATH + "stage-bundle.tar.gz", MAX_BYTES, timeout=120)
        if hashlib.sha256(blob).hexdigest() != m.get("sha256"):
            status.update(state="REFUSED: the archive does not match its signed SHA-256")
            return status
        with tempfile.TemporaryDirectory() as d:
            safe_extract(blob, d)
            try:
                need = missing_packages(open(os.path.join(d, "packages.txt")).read())
            except OSError:
                need = []
            if need:
                status.update(state="WAITING for the setup by hand: it needs packages the box lacks: " + ", ".join(need))
                return status
            if busy():                                             # a show started while it downloaded
                status.update(state="waiting: a show is loaded or playing")
                return status
            (apply or install)(d, manifest)
    except Exception as e:                                         # noqa: BLE001 — refused, never half-applied silently
        status.update(state=f"REFUSED: {str(e)[:160]}")
        return status
    status.update(state=f"installed {m.get('version')}; rebooting", installed=m.get("version"))
    return status


def _json_bytes(b: bytes) -> bytes:
    json.loads(b)                                                  # it must at least be JSON
    return b


def install(bundle_dir: str, manifest: bytes) -> None:
    """Put the bundle in place where it lasts, record its version, and reboot."""
    lower = lower_root()
    if lower is None:
        raise RuntimeError("the root is an overlay whose lower root cannot be reached from here: update by hand")
    remount = lower != "/"
    if remount:
        subprocess.run(["mount", "-o", "remount,rw", lower], check=True, timeout=30)
    try:
        subprocess.run(["bash", os.path.join(bundle_dir, "bin", "stage-install"), "--root", lower],
                       check=True, timeout=120)
        mark = os.path.join(lower, LIB.lstrip("/"), MARK)
        with open(mark + ".new", "wb") as f:
            f.write(manifest)
            f.flush()
            os.fsync(f.fileno())
        os.replace(mark + ".new", mark)
        subprocess.run(["sync"], timeout=60)
    finally:
        if remount:
            subprocess.run(["mount", "-o", "remount,ro", lower], timeout=30)
    subprocess.run(["systemctl", "reboot"], timeout=30)
