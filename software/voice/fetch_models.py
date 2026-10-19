#!/usr/bin/env python3
"""Download the speech models' weights, pinned and verified, into the robot's model directory.

    fetch_models.py [--dest /var/lib/opendubs/models] [--only parakeet,kokoro,g2p-fallback] [--check]
    fetch_models.py --only qwen3-30b-a3b-w4a16          # a local language model: only when named

Run at PROVISION time, never at build time: the weights go to the host's model directory, which
run.sh mounts read-only into the container. They are never in the repository or an image, so the
project does not redistribute them (their licences and credits: NOTICE.md, copied beside them).

Entries marked `optional` (the local language models, 8 to 17 GB each) are fetched only when named
with --only: a robot gets one only once its gates have passed (notes/workplan-voice.md, 1.5).

Everything comes from models.lock.json: each repository at a full commit hash, each file with its
size and either its SHA-256 (large files) or its git blob id (small files). A file that does not
match is deleted and the run fails. Nothing is fetched from a branch, so a model cannot change
underneath a robot. --check verifies what is already there and downloads nothing.

The G2P fallback model is laid out as a Hugging Face cache (hub/models--<org>--<name>/snapshots/
<commit>/, refs/main -> <commit>), because misaki loads it by name; the service then runs with
HF_HUB_OFFLINE=1 and finds it there, and nothing is fetched at run time.

Standard library only, so it runs in the voice image or on a bare host.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = os.path.join(HERE, "models.lock.json")
NOTICE = os.path.join(HERE, "NOTICE.md")
BASE_URL = "https://huggingface.co"
CHUNK = 1 << 20


class Mismatch(RuntimeError):
    pass


def load_lock(path: str = LOCK) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)["models"]


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def verify(path: str, spec: dict) -> None:
    """Raise Mismatch unless the file at `path` is exactly the pinned one."""
    size = os.path.getsize(path)
    if size != spec["size"]:
        raise Mismatch(f"{path}: {size} bytes, expected {spec['size']}")
    if "sha256" in spec:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        got, want = h.hexdigest(), spec["sha256"]
    else:
        with open(path, "rb") as f:
            got, want = git_blob_id(f.read()), spec["git_blob"]
    if got != want:
        raise Mismatch(f"{path}: hash {got}, expected {want}")


def model_dir(dest: str, m: dict) -> str:
    if m.get("hf_cache"):
        org, name = m["repo"].split("/")
        return os.path.join(dest, "hf", "hub", f"models--{org}--{name}", "snapshots", m["revision"])
    return os.path.join(dest, m["dest"])


RETRIES = 6
RETRY_WAIT_S = (2, 5, 10, 20, 30, 60)


def http_get(url: str, out_path: str, offset: int = 0) -> None:
    """Append the bytes of `url` from `offset` to `out_path`. A connection that ends early is not an
    error here: fetch() sees the short file and resumes it with a Range request."""
    headers = {"User-Agent": "opendubs-fetch-models"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        if offset and r.status != 206:
            raise OSError(f"server ignored the resume request (HTTP {r.status})")
        with open(out_path, "ab" if offset else "wb") as f:
            shutil.copyfileobj(r, f, CHUNK)


def download(url: str, part: str, spec: dict, get=http_get, sleep=time.sleep, log=print) -> None:
    """Download into `part`, resuming after a dropped connection, until it has the pinned size.
    Large files come from a CDN whose connections can end early (seen on the robot: a 5 GB shard cut
    at 1.66 GB); each attempt asks for the full URL again, so an expired redirect is renewed."""
    for attempt in range(RETRIES + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have > spec["size"]:
            os.unlink(part)                               # longer than pinned: start over
            have = 0
        if have == spec["size"]:
            return
        try:
            get(url, part, have)
        except (urllib.error.URLError, OSError) as e:
            if attempt == RETRIES:
                raise Mismatch(f"{part}: download failed after {RETRIES + 1} attempts: {e}") from None
            log(f"  retrying after: {e}")
        else:
            if os.path.getsize(part) == spec["size"]:
                return
            if attempt == RETRIES:
                break
            log(f"  connection ended at {os.path.getsize(part) / 1e6:.0f} of {spec['size'] / 1e6:.0f} MB; resuming")
        sleep(RETRY_WAIT_S[min(attempt, len(RETRY_WAIT_S) - 1)])
    raise Mismatch(f"{part}: {os.path.getsize(part)} bytes after {RETRIES + 1} attempts, expected {spec['size']}")


def fetch(dest: str, models: list[dict], get=http_get, check_only: bool = False, log=print,
          sleep=time.sleep) -> int:
    """Download and verify; returns the number of files fetched. Raises Mismatch on any bad file."""
    fetched = 0
    for m in models:
        root = model_dir(dest, m)
        for spec in m["files"]:
            path = os.path.join(root, spec["path"])
            if os.path.exists(path):
                verify(path, spec)
                continue
            if check_only:
                raise Mismatch(f"{path}: missing (run fetch_models.py without --check)")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            url = f"{BASE_URL}/{m['repo']}/resolve/{m['revision']}/{spec['path']}"
            log(f"fetch {m['name']}: {spec['path']} ({spec['size'] / 1e6:.1f} MB)")
            # A fixed partial-file name, so a run that was interrupted resumes where it stopped.
            part = os.path.join(os.path.dirname(path), f".part-{os.path.basename(path)}")
            download(url, part, spec, get=get, sleep=sleep, log=log)
            try:
                verify(part, spec)
            except Mismatch:
                os.unlink(part)                           # never keep, or resume, bytes that do not match
                raise
            os.replace(part, path)
            os.chmod(path, 0o644)       # readable by the voice container's non-root user
            fetched += 1
        if m.get("hf_cache"):
            ref = os.path.join(os.path.dirname(os.path.dirname(root)), "refs", "main")
            try:
                with open(ref) as f:
                    current = f.read().strip()
            except FileNotFoundError:
                current = None
            if current != m["revision"]:
                # --check never writes: run.sh calls it with the weights mounted read-only.
                if check_only:
                    raise Mismatch(f"{ref}: {current or 'missing'}, expected {m['revision']} "
                                   "(run fetch_models.py without --check)")
                os.makedirs(os.path.dirname(ref), exist_ok=True)
                with open(ref, "w") as f:
                    f.write(m["revision"])
    if not check_only and os.path.exists(NOTICE):
        shutil.copyfile(NOTICE, os.path.join(dest, "NOTICE.md"))     # the credits travel with the weights
    return fetched


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", default=os.environ.get("OPENDUBS_VOICE_MODELS", "/var/lib/opendubs/models"))
    ap.add_argument("--only", help="comma-separated model names from models.lock.json")
    ap.add_argument("--check", action="store_true", help="verify what is there; download nothing")
    a = ap.parse_args(argv)
    models = load_lock()
    if not a.only:
        models = [m for m in models if not m.get("optional")]
    else:
        want = set(a.only.split(","))
        unknown = want - {m["name"] for m in models}
        if unknown:
            print(f"fetch_models: unknown model(s) {sorted(unknown)}", file=sys.stderr)
            return 2
        models = [m for m in models if m["name"] in want]
    try:
        n = fetch(a.dest, models, check_only=a.check)
    except Mismatch as e:
        print(f"fetch_models: {e}", file=sys.stderr)
        return 1
    print(f"fetch_models: {'verified' if a.check else f'{n} file(s) fetched, all verified'} in {a.dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
