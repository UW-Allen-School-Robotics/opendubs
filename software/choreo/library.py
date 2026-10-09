#!/usr/bin/env python3
"""The show library: everything a show PLAYS that is not code — songs, clips, eye skins.

Design: notes/design/choreo-screen-track.md ("The library index"), plan 4.6. The library lives
OUTSIDE the repo: on the Thor at /var/lib/opendubs/library (synced to composing laptops and, by
hash, to the tablet). Its files AND its index entries are library content — names, hashes, source
and licence lines name third-party sources and show-specific content — so neither is ever
committed. The repo holds only this schema, this tool, the validator that reads the index, and
test fixtures with made-up entries.

    python3 library.py check                      # the index is well-formed and every hash matches
    python3 library.py add FILE_OR_DIR --id ID --kind song|clip|skin --source "…" --licence "…" \\
        [--type image|video] [--param key=value ...] [--inputs lookX,lookY,lid] \\
        [--duration S] [--width W --height H --fps F]

`add` COPIES the item into the library (songs/, clips/, skins/) and appends its entry, refusing one
without a source and a licence. Durations and sizes are read with ffprobe when it is installed;
otherwise pass them. The library root is OPENDUBS_LIBRARY, else /var/lib/opendubs/library.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

DEFAULT_ROOT = "/var/lib/opendubs/library"
INDEX = "index.yaml"
KINDS = ("song", "clip", "skin")                 # later: overlay, theme, sound
# Library skins (tablet lane, 2026-10-08): `image` and `video` now, `svg` and `riv` later, each with a
# `params` block whose fields the tablet lane fixes once those types are built and measured. Skins the
# engine draws in CODE (`default`, `ring`: cue.CODE_SKINS) are never in the index, and no library
# item may take one of their names.
SKIN_TYPES = ("image", "video")
CODE_SKIN_NAMES = ("default", "ring")
SKIN_INPUTS = ("lookX", "lookY", "lid", "emote", "shape", "colour", "side")   # face-engine.md §A
FACE_PANEL = (1340, 800)                         # the tablet's 8.7-inch panel
KIND_DIR = {"song": "songs", "clip": "clips", "skin": "skins"}
REQUIRED = {"id", "kind", "file", "sha256", "source", "licence"}
REQUIRED_BY_KIND = {"song": {"duration_s"}, "clip": {"duration_s", "width", "height", "fps"},
                    "skin": {"type"}}


def root() -> str:
    return os.environ.get("OPENDUBS_LIBRARY") or DEFAULT_ROOT


def item_hash(path: str) -> str:
    """One file: its sha256. A folder: the sha256 of the sorted `relative/path<TAB>sha256` lines,
    one per file — so a changed, added or removed file changes the item's hash."""
    if os.path.isfile(path):
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    lines = []
    for d, _, files in os.walk(path):
        for f in files:
            full = os.path.join(d, f)
            lines.append(f"{os.path.relpath(full, path).replace(os.sep, '/')}\t{item_hash(full)}")
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


def _yaml():
    import yaml
    return yaml


def load_index(lib_root: str | None = None):
    """{id: entry}, or None when there is no library here (a laptop that has not synced one)."""
    p = os.path.join(lib_root or root(), INDEX)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as fh:
        raw = _yaml().safe_load(fh) or []
    return {e["id"]: e for e in raw if isinstance(e, dict) and "id" in e}


def entry_problems(e: dict) -> list:
    """What is wrong with one index entry, as sentences."""
    p = []
    kind = e.get("kind")
    if kind not in KINDS:
        return [f"{e.get('id')!r}: kind {kind!r} is not one of {', '.join(KINDS)}"]
    missing = sorted((REQUIRED | REQUIRED_BY_KIND[kind]) - set(k for k, v in e.items() if v not in (None, "")))
    if missing:
        p.append(f"{e['id']!r}: missing {', '.join(missing)}")
    if str(e.get("id", "")).lower() in CODE_SKIN_NAMES:
        p.append(f"{e['id']!r}: that name belongs to a skin the face draws in code — choose another")
    if kind == "skin":
        if e.get("params") is not None and not isinstance(e.get("params"), dict):
            p.append(f"{e['id']!r}: params must be a mapping")
        if e.get("type") not in SKIN_TYPES:
            p.append(f"{e['id']!r}: skin type {e.get('type')!r} is not one of {', '.join(SKIN_TYPES)}")
        bad = [x for x in (e.get("inputs") or []) if x not in SKIN_INPUTS]
        if bad:
            p.append(f"{e['id']!r}: unknown face inputs {', '.join(bad)} (face-engine.md §A: {', '.join(SKIN_INPUTS)})")
    return p


def check(lib_root: str | None = None) -> list:
    """Every problem with the library: entries, and files whose hash no longer matches."""
    r = lib_root or root()
    idx = load_index(r)
    if idx is None:
        return [f"no library index at {os.path.join(r, INDEX)}"]
    out = []
    for e in idx.values():
        out += entry_problems(e)
        f = os.path.join(r, e.get("file", ""))
        if not os.path.exists(f):
            out.append(f"{e['id']!r}: file {e.get('file')!r} is missing")
        elif e.get("sha256") and item_hash(f) != e["sha256"]:
            out.append(f"{e['id']!r}: {e['file']} has changed since it was added (hash differs)")
    return out


def probe(path: str) -> dict:
    """duration_s / width / height / fps by ffprobe, when it is installed; {} otherwise."""
    if not shutil.which("ffprobe") or not os.path.isfile(path):
        return {}
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration:stream=width,height,r_frame_rate,codec_type", "-of", "json", path],
                             capture_output=True, text=True, timeout=30).stdout
        d = json.loads(out)
    except Exception:  # noqa: BLE001
        return {}
    info = {}
    if d.get("format", {}).get("duration"):
        info["duration_s"] = round(float(d["format"]["duration"]), 3)
    for s in d.get("streams", []):
        if s.get("codec_type") == "video" and s.get("width"):
            num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
            info.update(width=int(s["width"]), height=int(s["height"]),
                        fps=round(float(num) / float(den or 1), 3) if float(den or 1) else None)
    return info


def add(src: str, item_id: str, kind: str, source: str, licence: str, lib_root: str | None = None, **extra) -> dict:
    """Copy `src` into the library and append its entry. Refuses an entry without a source and a
    licence, an id already in use, or one that does not satisfy the schema. Returns the entry."""
    r = lib_root or root()
    if not (source or "").strip() or not (licence or "").strip():
        raise ValueError("every library item needs its source and its licence")
    idx = load_index(r) or {}
    if item_id in idx:
        raise ValueError(f"{item_id!r} is already in the library")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    rel = os.path.join(KIND_DIR[kind], os.path.basename(os.path.normpath(src)))
    dest = os.path.join(r, rel)
    if os.path.exists(dest):
        raise ValueError(f"{rel} already exists in the library")
    entry = {"id": item_id, "kind": kind, "file": rel.replace(os.sep, "/"), "sha256": item_hash(src),
             "source": source.strip(), "licence": licence.strip()}
    entry.update({k: v for k, v in probe(src).items() if v is not None})
    entry.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    p = entry_problems(entry)
    if p:
        raise ValueError("; ".join(p))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dest)
    items = list(idx.values()) + [entry]
    with open(os.path.join(r, INDEX), "w", encoding="utf-8") as fh:
        _yaml().safe_dump(items, fh, sort_keys=False, allow_unicode=True)
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    a = sub.add_parser("add")
    a.add_argument("path")
    a.add_argument("--id", required=True)
    a.add_argument("--kind", required=True, choices=KINDS)
    a.add_argument("--source", required=True)
    a.add_argument("--licence", required=True)
    a.add_argument("--type", choices=SKIN_TYPES)
    a.add_argument("--param", action="append", default=[], metavar="KEY=VALUE",
                   help="a skin's params entry (e.g. motion=spin); repeat for more")
    a.add_argument("--inputs", help="a skin's face inputs, comma-separated (face-engine.md §A)")
    a.add_argument("--duration", type=float, dest="duration_s")
    a.add_argument("--width", type=int)
    a.add_argument("--height", type=int)
    a.add_argument("--fps", type=float)
    args = ap.parse_args()
    if args.cmd == "check":
        p = check()
        for x in p:
            print("  PROBLEM:", x)
        print("ok" if not p else f"{len(p)} problem(s) in {root()}")
        return 1 if p else 0
    try:
        params = dict(x.split("=", 1) for x in args.param if "=" in x) or None
        e = add(args.path, args.id, args.kind, args.source, args.licence, type=args.type, params=params,
                inputs=[x for x in (args.inputs or "").split(",") if x],
                duration_s=args.duration_s, width=args.width, height=args.height, fps=args.fps)
    except ValueError as err:
        print(f"refused: {err}")
        return 1
    print(f"added {e['id']} ({e['kind']}) to {root()}: {e['file']}")
    if e["kind"] == "clip" and (e.get("width"), e.get("height")) != FACE_PANEL:
        print(f"  note: {e['width']}x{e['height']} is not the face panel's {FACE_PANEL[0]}x{FACE_PANEL[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
