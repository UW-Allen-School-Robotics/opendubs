#!/usr/bin/env python3
"""Download the speech models' weights, pinned and verified, into the robot's model directory.

    fetch_models.py [--dest /var/lib/opendubs/models] [--only parakeet,kokoro,g2p-fallback] [--check]

Run at PROVISION time, never at build time: the weights go to the host's model directory, which
run.sh mounts read-only into the container. They are never in the repository or an image, so the
project does not redistribute them (their licences and credits: NOTICE.md, copied beside them).

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
import tempfile
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


def http_get(url: str, out_path: str) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "opendubs-fetch-models"})
    with urllib.request.urlopen(req, timeout=60) as r, open(out_path, "wb") as f:
        shutil.copyfileobj(r, f, CHUNK)


def fetch(dest: str, models: list[dict], get=http_get, check_only: bool = False, log=print) -> int:
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
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".part-")
            os.close(fd)
            try:
                get(url, tmp)
                verify(tmp, spec)
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            fetched += 1
        if m.get("hf_cache"):
            refs = os.path.join(os.path.dirname(os.path.dirname(root)), "refs")
            os.makedirs(refs, exist_ok=True)
            with open(os.path.join(refs, "main"), "w") as f:
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
    if a.only:
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
