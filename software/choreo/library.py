#!/usr/bin/env python3
"""The show library: everything a show PLAYS that is not code — songs, clips, eye skins.

Design: notes/design/choreo-screen-track.md ("The library index"), plan 4.6. The library lives
OUTSIDE the repo: on the Thor at /var/lib/opendubs/library (synced to composing laptops and, by
hash, to the tablet). Its files AND its index entries are library content — names, hashes, source
and licence lines name third-party sources and show-specific content — so neither is ever
committed. The repo holds only this schema, this tool, the validator that reads the index, and
test fixtures with made-up entries.

    python3 library.py check                      # the index is well-formed and every hash matches
    python3 library.py remove ID --shows SHOWS_DIR     # refused while a show there still uses it
    python3 library.py migrate                         # fill `bytes` into entries made before it existed
    python3 library.py add FILE_OR_DIR --id ID --kind song|clip|skin --source "…" --licence "…" \\
        [--type image|video] [--param size=0.8 --param spin_dps=90 ...] [--inputs lookX,lookY,lid] \\
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
import re
import shutil
import subprocess
import sys
import tempfile
import wave

DEFAULT_ROOT = "/var/lib/opendubs/library"
INDEX = "index.yaml"
KINDS = ("song", "clip", "skin")                 # later: overlay, theme, sound
# Library skins (the tablet lane's fields, built and measured in its #115; notes/design/face-engine.md,
# "Skin types"): `image` and `video` now, `svg` and `riv` later. One file each (a folder for svg/riv
# later, hashed by the folder rule). A video skin must be WebM — VP9 carries the alpha channel that
# MP4/H.264 cannot — and should be square, 400-512 px. Skins the engine draws in CODE (`default`,
# `ring`: cue.CODE_SKINS) are never in the index, and no library item may take one of their names.
SKIN_TYPES = ("image", "video")
CODE_SKIN_NAMES = ("default", "ring")
# A skin's `params`, all optional; any other key is a problem — a typo would otherwise do nothing.
SKIN_PARAMS = {
    "size": "fraction of the 480 px eye (default 0.8)",
    "spin_dps": "spin, deg/s (default 0)",
    "follow_look": "the skin follows where the eyes look (default true)",
    "look": "how far it follows (default 0.3)",
    "blink": "squash | none (default squash)",
    "mirror": "the right eye mirrors the left (default false)",
    "counter": "occasionally break a together-spin into opposite directions: {every_s: [lo, hi], hold_s: "
               "[lo, hi], ease_s} (default off; face-web asset.js, #138)",
}
# A skin's `counter` block's keys — the face engine's ASSET_COUNTER_DEFAULTS (asset.js); a test pins
# the two together. EASE_MIN is a clamp on ease_s, not a key, and is deliberately not here.
COUNTER_KEYS = ("every_s", "hold_s", "ease_s")
VIDEO_SKIN_SIZE_PX = (400, 512)
SKIN_INPUTS = ("lookX", "lookY", "lid", "emote", "shape", "colour", "side")   # face-engine.md §A
FACE_PANEL = (1340, 800)                         # the tablet's 8.7-inch panel
KIND_DIR = {"song": "songs", "clip": "clips", "skin": "skins"}
# `bytes` is the item's total size (a folder: its files summed). The tablet checks its free space
# before it fetches anything (tablet plan 5.3), so a missing size would mean fetching blind.
REQUIRED = {"id", "kind", "file", "sha256", "bytes", "source", "licence"}
# A song's file types, and the first bytes each really starts with: an upload is checked by its
# CONTENT, not only its name, so a renamed file of another kind is refused.
SONG_TYPES = {".wav": (b"RIFF",), ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa"),
              ".ogg": (b"OggS",), ".flac": (b"fLaC",), ".m4a": (b"ftyp",)}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")   # an id the composer may create (a safe file name)
REQUIRED_BY_KIND = {"song": {"duration_s"}, "clip": {"duration_s", "width", "height", "fps"},
                    "skin": {"type", "width", "height"}}


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


def item_bytes(path: str) -> int:
    """One file: its size. A folder: the sum of its files' sizes."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    return sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(path) for f in files)


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
        params = e.get("params")
        if params is not None and not isinstance(params, dict):
            p.append(f"{e['id']!r}: params must be a mapping")
        elif params:
            bad = sorted(k for k in params if k not in SKIN_PARAMS)
            if bad:
                p.append(f"{e['id']!r}: unknown params {', '.join(bad)} (known: {', '.join(SKIN_PARAMS)})")
            if "blink" in params and params["blink"] not in ("squash", "none"):
                p.append(f"{e['id']!r}: blink must be squash or none")
            p += _counter_problems(e["id"], params.get("counter"))
        if e.get("type") not in SKIN_TYPES:
            p.append(f"{e['id']!r}: skin type {e.get('type')!r} is not one of {', '.join(SKIN_TYPES)}")
        elif e["type"] == "video":
            missing_v = sorted({"fps", "duration_s"} - set(k for k, v in e.items() if v not in (None, "")))
            if missing_v:
                p.append(f"{e['id']!r}: a video skin needs {', '.join(missing_v)}")
            if not str(e.get("file", "")).lower().endswith(".webm"):
                p.append(f"{e['id']!r}: a video skin must be WebM (VP9 with alpha) — MP4/H.264 cannot carry "
                         "the transparency a skin needs")
        bad = [x for x in (e.get("inputs") or []) if x not in SKIN_INPUTS]
        if bad:
            p.append(f"{e['id']!r}: unknown face inputs {', '.join(bad)} (face-engine.md §A: {', '.join(SKIN_INPUTS)})")
    return p


def _counter_problems(item_id, c) -> list:
    """A skin's `counter` (face-web #138): off (absent/null/false), or {every_s: [lo, hi] seconds between
    counter-spins, hold_s: [lo, hi] seconds each lasts, ease_s: the reversal's ease}; each key
    optional, with the engine's defaults [8, 20], [2, 4] and 0.9."""
    if c in (None, False):
        return []
    if not isinstance(c, dict):
        return [f"{item_id!r}: counter must be a mapping {{every_s, hold_s, ease_s}} or off"]
    p = [f"{item_id!r}: counter has unknown keys {', '.join(sorted(set(c) - set(COUNTER_KEYS)))} "
         f"(known: {', '.join(COUNTER_KEYS)})"] if set(c) - set(COUNTER_KEYS) else []
    for k in ("every_s", "hold_s"):
        v = c.get(k)
        if v is not None and not (isinstance(v, (list, tuple)) and len(v) == 2
                                  and all(isinstance(x, (int, float)) and x >= 0 for x in v) and v[0] <= v[1]):
            p.append(f"{item_id!r}: counter {k} must be [lo, hi] seconds, lo <= hi")
    e = c.get("ease_s")
    if e is not None and not (isinstance(e, (int, float)) and e >= 0):
        p.append(f"{item_id!r}: counter ease_s must be seconds >= 0")
    return p


def entry_warnings(e: dict) -> list:
    """What is legal but probably not meant."""
    w = []
    if e.get("kind") == "clip" and (e.get("width"), e.get("height")) != FACE_PANEL:
        w.append(f"{e['id']!r}: {e.get('width')}x{e.get('height')} is not the face panel's {FACE_PANEL[0]}x{FACE_PANEL[1]}")
    if e.get("kind") == "skin" and e.get("type") == "video":
        lo, hi = VIDEO_SKIN_SIZE_PX
        if e.get("width") != e.get("height") or not (lo <= (e.get("width") or 0) <= hi):
            w.append(f"{e['id']!r}: a video skin should be square, {lo}-{hi} px (it is {e.get('width')}x{e.get('height')})")
    return w


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


def looks_like_song(path: str) -> bool:
    """The file's first bytes match its extension (SONG_TYPES); m4a's `ftyp` sits at offset 4."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SONG_TYPES:
        return False
    with open(path, "rb") as fh:
        head = fh.read(12)
    if ext == ".m4a":
        return head[4:8] == b"ftyp"
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    return head.startswith(SONG_TYPES[ext])


def probe(path: str) -> dict:
    """duration_s / width / height / fps by ffprobe, when it is installed; a WAV's duration from its
    header otherwise (the standard library reads it); {} when neither can."""
    if not os.path.isfile(path):
        return {}
    if not shutil.which("ffprobe"):
        if path.lower().endswith(".wav"):
            try:
                with wave.open(path, "rb") as w:
                    return {"duration_s": round(w.getnframes() / float(w.getframerate()), 3)}
            except (wave.Error, EOFError, ZeroDivisionError):
                return {}
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
             "bytes": item_bytes(src), "source": source.strip(), "licence": licence.strip()}
    entry.update({k: v for k, v in probe(src).items() if v is not None})
    entry.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    p = entry_problems(entry)
    if p:
        raise ValueError("; ".join(p))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, dest)
    _write_index(r, list(idx.values()) + [entry])
    return entry


def _write_index(r: str, items: list) -> None:
    """The whole index, replaced in one step: a reader never sees half of it."""
    os.makedirs(r, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".index-", suffix=".yaml", dir=r)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        _yaml().safe_dump(items, fh, sort_keys=False, allow_unicode=True)
    os.replace(tmp, os.path.join(r, INDEX))


def migrate(lib_root: str | None = None) -> list:
    """Bring entries made before a field existed up to the schema: fill `bytes` from the file, after
    checking the file still has the hash the entry records (a changed or missing file is reported,
    never re-hashed into the index). Returns one sentence per entry changed or refused."""
    r = lib_root or root()
    idx = load_index(r)
    if idx is None:
        return []
    out, changed = [], False
    for e in idx.values():
        if e.get("bytes") not in (None, ""):
            continue
        f = os.path.join(r, e.get("file", ""))
        if not os.path.exists(f):
            out.append(f"{e['id']!r}: file {e.get('file')!r} is missing — left as it is")
        elif e.get("sha256") and item_hash(f) != e["sha256"]:
            out.append(f"{e['id']!r}: {e['file']} has changed since it was added — left as it is")
        else:
            e["bytes"] = item_bytes(f)
            out.append(f"{e['id']!r}: bytes {e['bytes']}")
            changed = True
    if changed:
        _write_index(r, list(idx.values()))
    return out


def items_of(kind: str, lib_root: str | None = None) -> list:
    """Every entry of one kind, by id."""
    return sorted((e for e in (load_index(lib_root) or {}).values() if e.get("kind") == kind),
                  key=lambda e: e["id"])


def shows_using(item_id: str, shows_dir: str) -> list:
    """The shows under `shows_dir` (relative paths) that name this item: as their music
    (`music: song:`), a face skin (`do: skin, name:`) or a clip (`clip:`). A show that does not
    parse is skipped — it names nothing yet."""
    out = []
    for d, _, files in os.walk(shows_dir):
        for f in sorted(files):
            if not f.endswith((".yaml", ".yml")):
                continue
            full = os.path.join(d, f)
            try:
                with open(full, encoding="utf-8") as fh:
                    doc = _yaml().safe_load(fh)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(doc, dict):
                continue
            m = doc.get("music")
            names = {m.get("song")} if isinstance(m, dict) else set()
            for c in doc.get("cues") or []:
                if isinstance(c, dict):
                    if c.get("do") == "skin":
                        names.add(c.get("name"))
                    names.add(c.get("clip"))
            for cl in (doc.get("clips") or {}).values() if isinstance(doc.get("clips"), dict) else []:
                for c in (cl.get("cues") or []) if isinstance(cl, dict) else []:
                    if isinstance(c, dict):
                        names.add(c.get("clip"))
                        if c.get("do") == "skin":
                            names.add(c.get("name"))
            if item_id in names:
                out.append(os.path.relpath(full, shows_dir).replace(os.sep, "/"))
    return sorted(out)


def remove(item_id: str, lib_root: str | None = None, used_by=()) -> dict:
    """Delete an item: its file and its entry. Refused while `used_by` (the shows that still name it)
    is not empty — the caller finds them, since only it knows which shows directory to look in."""
    r = lib_root or root()
    idx = load_index(r) or {}
    if item_id not in idx:
        raise ValueError(f"{item_id!r} is not in the library")
    if used_by:
        raise ValueError(f"{item_id!r} is still used by {', '.join(sorted(used_by))} — "
                         "take it out of those shows first")
    e = idx.pop(item_id)
    f = os.path.realpath(os.path.join(r, e.get("file", "")))
    if f.startswith(os.path.realpath(r) + os.sep) and os.path.exists(f):
        (shutil.rmtree if os.path.isdir(f) else os.remove)(f)
    _write_index(r, list(idx.values()))
    return e


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("migrate")
    rm = sub.add_parser("remove")
    rm.add_argument("id")
    rm.add_argument("--shows", required=True, help="the shows directory to check for users of it")
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
    if args.cmd == "migrate":
        for line in migrate():
            print(" ", line)
        return 0
    if args.cmd == "remove":
        try:
            e = remove(args.id, used_by=shows_using(args.id, args.shows))
        except ValueError as err:
            print(f"refused: {err}")
            return 1
        print(f"removed {e['id']} ({e['kind']}, {e.get('file')}) from {root()}")
        return 0
    try:
        params = dict(x.split("=", 1) for x in args.param if "=" in x) or None
        e = add(args.path, args.id, args.kind, args.source, args.licence, type=args.type, params=params,
                inputs=[x for x in (args.inputs or "").split(",") if x],
                duration_s=args.duration_s, width=args.width, height=args.height, fps=args.fps)
    except ValueError as err:
        print(f"refused: {err}")
        return 1
    print(f"added {e['id']} ({e['kind']}) to {root()}: {e['file']}")
    for w in entry_warnings(e):
        print("  note:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
