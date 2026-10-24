"""The face looks this robot has: themes and code skins, the core's own plus the content folder's.

ONE IMPLEMENTATION, read by everything that needs the list: the dashboard (GET /api/head/looks, its
pickers), the show validator (Choreography's cue.py and library.py) and, through the dashboard, the face.
Written for the content split (notes/hygiene-content-split.md, engine change b): seasonal and show
looks live in a separate content repository, so the names can no longer be hard-coded here.

THE CONTENT FOLDER is the one folder for shows, clips, tool packs and face looks, found in the same
order as the show runner's (agreed with Choreography, 2026-10-24):
    $OPENDUBS_SHOWS_DIR, else the old $OPENDUBS_LOCAL_SHOWS, else /var/lib/opendubs/shows if it exists
    (the robot's show folder, which show_sync fills), else ~/opendubs-shows.
Face looks are under its face/ folder:
    face/themes/<id>.json  {"label": "Halloween", "colors": {"SAFE_IDLE": "#8ac926", ...}}
                           a state's colour, by state name; DEGRADED and E_STOP are refused (reserved:
                           a problem looks the same in every theme), CHARGING too (purple everywhere,
                           as on the PRISM and the chest light; Face & HRI's rule), and anything not a
                           state
    face/skins/<id>.js     a code skin: an ES module with the same exports as a built-in one; the face
                           imports it from the dashboard (/face/content/skins/<id>.js). It imports
                           core helpers ONLY from the face engine's one stable entry point (Face & HRI's
                           skin-api.js), never an engine file by path
    face/skins/<id>.json   optional: {"label": "..."}
An id is ^[a-z0-9_]{1,40}$. A built-in name always wins: content cannot replace a core look.

The head protocol is unchanged: THEME <id> and SKIN <id> take a built-in or a discovered id. Nothing
moves yet: the built-ins still hold every look until the content repository takes them.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import head_protocol as hp

ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
RESERVED = ("DEGRADED", "E_STOP")               # a problem looks the same in every theme
FIXED = ("CHARGING",)                           # purple everywhere, like the PRISM and the chest light
ROBOT_SHOWS = Path("/var/lib/opendubs/shows")


def content_dir(path: str | os.PathLike | None = None, env=os.environ) -> Path:
    """The content folder: `path` if given, else in the show runner's order (see above)."""
    if path:
        return Path(path).expanduser()
    for name in ("OPENDUBS_SHOWS_DIR", "OPENDUBS_LOCAL_SHOWS"):
        v = (env.get(name) or "").strip()
        if v:
            return Path(v).expanduser()
    return ROBOT_SHOWS if ROBOT_SHOWS.is_dir() else Path.home() / "opendubs-shows"


def _label(ident: str) -> str:
    return ident.replace("_", " ").capitalize()


def read_theme(path: Path) -> dict:
    """A theme file -> {"id", "label", "colors"}; raises ValueError saying what is wrong."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"not readable JSON ({e})") from None
    colors = d.get("colors") if isinstance(d, dict) else None
    if not isinstance(colors, dict) or not colors:
        raise ValueError('needs "colors": {"<STATE>": "#rrggbb", ...}')
    for state, value in colors.items():
        if state in RESERVED:
            raise ValueError(f"{state} is reserved: a problem looks the same in every theme")
        if state in FIXED:
            raise ValueError(f"{state} is purple in every theme, as on the PRISM and the chest light")
        if state not in hp.STATES:
            raise ValueError(f"{state!r} is not a state ({', '.join(hp.STATES)})")
        if not isinstance(value, str) or not HEX_RE.match(value):
            raise ValueError(f"{state}'s colour must be #rrggbb, not {value!r}")
    label = d.get("label")
    return {"id": path.stem, "label": label if isinstance(label, str) and label.strip() else _label(path.stem),
            "colors": {k: v.lower() for k, v in colors.items()}}


def discover(path: str | os.PathLike | None = None) -> dict:
    """{"themes": [...], "skins": [...], "problems": [...], "content_dir": str}.
    Each theme is {"id", "label", "source": "builtin"|"content"} (a content one also carries "colors");
    each skin is {"id", "label", "kind": "code", "source"}. A bad content file is left out and named in
    `problems`, so a typo shows on the dashboard instead of a look quietly missing."""
    root = content_dir(path)
    themes = [{"id": t, "label": _label(t), "source": "builtin"} for t in hp.THEMES_BUILT_IN]
    skins = [{"id": s, "label": _label(s), "kind": "code", "source": "builtin"} for s in hp.CODE_SKINS]
    problems = []
    seen_t, seen_s = {t["id"] for t in themes}, {s["id"] for s in skins}
    face = root / "face"
    for f in sorted((face / "themes").glob("*.json")) if (face / "themes").is_dir() else ():
        if not ID_RE.match(f.stem) or f.stem in seen_t:
            problems.append(f"face/themes/{f.name}: " + ("a built-in name" if f.stem in seen_t else "not an id"))
            continue
        try:
            themes.append({**read_theme(f), "source": "content"})
            seen_t.add(f.stem)
        except ValueError as e:
            problems.append(f"face/themes/{f.name}: {e}")
    for f in sorted((face / "skins").glob("*.js")) if (face / "skins").is_dir() else ():
        if not ID_RE.match(f.stem) or f.stem in seen_s:
            problems.append(f"face/skins/{f.name}: " + ("a built-in name" if f.stem in seen_s else "not an id"))
            continue
        label = _label(f.stem)
        meta = f.with_suffix(".json")
        if meta.is_file():
            try:
                m = json.loads(meta.read_text(encoding="utf-8"))
                if isinstance(m, dict) and isinstance(m.get("label"), str) and m["label"].strip():
                    label = m["label"]
            except (OSError, ValueError):
                problems.append(f"face/skins/{meta.name}: not readable JSON")
        skins.append({"id": f.stem, "label": label, "kind": "code", "source": "content"})
        seen_s.add(f.stem)
    return {"themes": themes, "skins": skins, "problems": problems, "content_dir": str(root)}


def theme_ids(path: str | os.PathLike | None = None) -> set:
    return {t["id"] for t in discover(path)["themes"]}


def code_skin_ids(path: str | os.PathLike | None = None) -> set:
    return {s["id"] for s in discover(path)["skins"]}
