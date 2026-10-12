"""The composer's backend, as plain functions — no web framework in here.

`server.py` serves these with the standard library (standalone, the way this PR ships it), and
`router.py` shows how the dashboard would mount the same functions with one `include_router`
later. Keeping the logic here means the two can never disagree.

WHAT THIS CAN DO: read show files, run the validator on them (cue.py — the gate, imported, not
re-implemented), apply text-surgical edits, write show files back, and convert a measured joint
state into a keyframe. WHAT IT CANNOT DO, by construction: command the robot. There is no
socket, no ROS node, no relay, no UDP and no lift command anywhere below this line. A live-output
binding is a separately armed future feature (workplan 5.4); its interface is sketched in
choreo-teach-and-sync.md, not implemented.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHOREO = os.path.dirname(HERE)
sys.path.insert(0, CHOREO)
sys.path.insert(0, HERE)

import cue  # noqa: E402  the validator is the gate; we call it, never copy it
import library  # noqa: E402  the show library (songs, clips, skins) — its rules, not copies of them
import timeline_notes  # noqa: E402  notes for the next editor, their history, the agent prompt
import yamledit  # noqa: E402

FIXTURE_LIMITS = os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml")
STATIC_DIR = os.path.join(HERE, "static")
AUDIO_EXT = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac",
             ".m4a": "audio/mp4"}
# An uploaded song: one of these types, checked by its first bytes as well as its name, and no
# bigger than this. The composer has no login when it listens beyond localhost (server.py), so an
# upload is limited to what a song needs: about 7 minutes of CD-quality WAV, or hours of MP3.
MAX_SONG_BYTES = 80 * 1024 * 1024
MAX_SONG_S = 20 * 60


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


class Composer:
    def __init__(self, shows_dir: str, limits_path: str | None = None, library_dir: str | None = None):
        self.shows_dir = os.path.realpath(shows_dir)
        # The show library the songs come from and go to. The validator reads OPENDUBS_LIBRARY, so
        # the composer sets it: what it checks against is what it writes.
        if library_dir:
            os.environ["OPENDUBS_LIBRARY"] = os.path.realpath(library_dir)
        self.library_dir = os.path.realpath(library.root())
        if limits_path is None:
            limits_path = cue.DEFAULT_LIMITS if os.path.isfile(cue.DEFAULT_LIMITS) else FIXTURE_LIMITS
        self.limits_path = limits_path
        self.limits = cue.load_limits(limits_path)
        self.fixture = os.path.realpath(limits_path) == os.path.realpath(FIXTURE_LIMITS)
        # The twin feed (workplan 2.2): the latest resolved timeline and the editor's playhead,
        # for local viewers that follow the editor. It carries TIME AND PLAY STATE ONLY; each
        # viewer evaluates the timeline itself, so seeking is free and latency cannot pile up.
        self._twin_lock = threading.Lock()
        self._twin = {"rev": 0, "path": None, "timeline": None,
                      "t": 0.0, "playing": False, "at": time.time()}

    # ---- paths: everything stays inside the shows directory ---------------------------------
    def _path(self, rel: str, exts=(".yaml", ".yml")) -> str:
        if not rel or not isinstance(rel, str):
            raise ApiError("no path")
        p = os.path.realpath(os.path.join(self.shows_dir, rel))
        if not (p == self.shows_dir or p.startswith(self.shows_dir + os.sep)):
            raise ApiError("path escapes the shows directory", 403)
        if os.path.splitext(p)[1].lower() not in exts:
            raise ApiError(f"not a {'/'.join(exts)} file", 403)
        return p

    def info(self) -> dict:
        return {"shows_dir": self.shows_dir, "library_dir": self.library_dir,
                "library_exists": library.load_index(self.library_dir) is not None,
                "max_song_mb": MAX_SONG_BYTES // (1024 * 1024), "song_types": sorted(library.SONG_TYPES),
                "limits": os.path.basename(self.limits_path),
                "limits_are_test_fixture": self.fixture, "preview_only": True,
                "slots": list(cue.SLOTS), "face_emotes": list(cue.FACE_EMOTES),
                "slot_requires": {s: cue.slot_requirements(s) for s in cue.SLOTS},
                "speed_caps": cue.SPEED_CAP_DEG_S}

    def list_shows(self) -> list:
        out = []
        for root, dirs, files in os.walk(self.shows_dir):
            dirs[:] = [d for d in dirs if not d.startswith(".")]      # .history is not a show
            for f in sorted(files):
                if f.endswith((".yaml", ".yml")):
                    rel = os.path.relpath(os.path.join(root, f), self.shows_dir)
                    out.append({"path": rel})
        return sorted(out, key=lambda x: x["path"])

    # ---- the validator in the loop ----------------------------------------------------------
    def analyse(self, rel: str, text: str, clip: str | None = None) -> dict:
        """Parse + validate + resolve. Never raises on a bad show: a show being edited is
        allowed to be wrong, and the editor's job is to say where. With `clip`, the timeline is that
        clip's own, from its start (editing inside a clip)."""
        if clip:
            return self._analyse_clip(rel, text, clip)
        abspath = self._path(rel)
        out = {"path": rel, "text": text, "parse_error": None, "timeline": None,
               "authored": None, "cue_lines": None, "info": self.info()}
        try:
            show = cue.show_from_text(text, abspath)
        except (yaml.YAMLError, cue.ShowError) as e:
            out["parse_error"] = str(e)
            return out
        try:
            tl = show.export(self.limits, self.limits_path, with_notes=True)
        except Exception as e:  # noqa: BLE001 — a validator crash is a finding, shown not hidden
            out["parse_error"] = f"the validator crashed on this text: {type(e).__name__}: {e}"
            return out
        tl["show"]["source"] = rel
        out["timeline"] = tl
        with self._twin_lock:
            self._twin.update(rev=self._twin["rev"] + 1, path=rel, timeline=tl)
        out["authored"] = cue._jsonable(show.cues if isinstance(show.cues, list) else [])
        out["clip_names"] = sorted(show.doc.get("clips") or {}) if isinstance(show.doc.get("clips"), dict) else []
        try:
            out["cue_lines"] = yamledit.cue_lines(text)
        except yamledit.EditError:
            out["cue_lines"] = None
        return out

    # ---- the twin feed --------------------------------------------------------------------
    def playhead(self, t, playing) -> dict:
        try:
            t = max(0.0, float(t))
        except (TypeError, ValueError) as e:
            raise ApiError("playhead t must be a number") from e
        with self._twin_lock:
            self._twin.update(t=t, playing=bool(playing), at=time.time())
        return {"ok": True}

    def twin(self) -> dict:
        """Where the playhead is. `t` was true at server time `at`; while playing, a viewer
        adds (now - at) and keeps extrapolating on its own clock until the next update."""
        with self._twin_lock:
            s = {k: v for k, v in self._twin.items() if k != "timeline"}
        s["now"] = time.time()
        return s

    def twin_timeline(self) -> dict:
        with self._twin_lock:
            return {"rev": self._twin["rev"], "path": self._twin["path"],
                    "timeline": self._twin["timeline"]}

    def load(self, rel: str) -> dict:
        p = self._path(rel)
        if not os.path.isfile(p):
            raise ApiError("no such show", 404)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        out = self.analyse(rel, text)
        out["disk_sha"] = _sha(text)
        return out

    # ---- show files: new, save as (duplicate), rename, delete --------------------------------
    @staticmethod
    def _slug(name: str) -> str:
        n = (name or "").strip()
        if "/" in n or "\\" in n or n.startswith("."):
            raise ApiError("a show's name is just a name — no folders, no leading dot")
        if n.lower().endswith((".yaml", ".yml")):
            n = n.rsplit(".", 1)[0]
        n = re.sub(r"[^a-z0-9_-]+", "-", n.lower()).strip("-_")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", n or ""):
            raise ApiError("a show's name is letters, digits, - and _ (up to 64)")
        return n

    def _new_rel(self, name: str, beside: str = "") -> str:
        """`name`, made a safe file name, in the folder of `beside` (or the top of the shows folder).
        Never an existing file, never outside the shows folder."""
        slug = self._slug(name)
        folder = os.path.dirname(beside or "")
        rel = (folder + "/" if folder else "") + slug + ".yaml"
        p = self._path(rel)
        if os.path.exists(p):
            raise ApiError(f"{rel} already exists — choose another name", 409)
        return rel

    @staticmethod
    def _title(slug: str) -> str:
        return slug.replace("-", " ").replace("_", " ").capitalize()

    def _retitle(self, text: str, old_slug: str, new_slug: str) -> str:
        """The show's id follows its file name (two shows never share an id); its name follows too
        when it was the old file's name, and is left alone when it was chosen."""
        try:
            doc = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise ApiError(f"the show does not parse: {e}") from e
        edits = [{"op": "top", "key": "id", "value": new_slug}]
        if str(doc.get("name", "")).strip() in ("", old_slug, self._title(old_slug)):
            edits.append({"op": "top", "key": "name", "value": self._title(new_slug)})
        return yamledit.apply(text, edits)

    def new_show(self, name: str, song=None, bpm=None) -> dict:
        """A new, empty show: an id and a name, no slots yet (the first cue on a lane lists it), and
        the music if a library song is chosen. Opens like any other show."""
        rel = self._new_rel(name)
        slug = os.path.splitext(os.path.basename(rel))[0]
        lines = [f"id: {slug}", f"name: {yamledit.fmt(self._title(slug))}", "slots: []",
                 "requires: { rc_authority: AUTO }"]
        if song:
            if song not in {e["id"] for e in library.items_of("song", self.library_dir)}:
                raise ApiError(f"{song!r} is not a song in the library")
            try:
                bpm = float(bpm) if bpm not in (None, "") else 120.0
            except (TypeError, ValueError) as e:
                raise ApiError("BPM must be a number") from e
            lines.append(f"music: {{ song: {yamledit.fmt(song)}, bpm: {yamledit.fmt(bpm)}, offset_s: 0.0 }}")
        elif bpm not in (None, ""):
            try:
                lines.append(f"music: {{ bpm: {yamledit.fmt(float(bpm))}, offset_s: 0.0 }}")
            except (TypeError, ValueError) as e:
                raise ApiError("BPM must be a number") from e
        lines.append("cues: []")
        with open(self._path(rel), "x", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        return self.load(rel)

    def save_as(self, rel: str, name: str, text: str) -> dict:
        """The text being edited, written under a new name beside the show (SAVE AS, and DUPLICATE).
        The original file is not touched."""
        self._path(rel)
        new = self._new_rel(name, rel)
        old_slug = os.path.splitext(os.path.basename(rel))[0]
        out = self._retitle(text, old_slug, os.path.splitext(os.path.basename(new))[0])
        with open(self._path(new), "x", encoding="utf-8") as fh:
            fh.write(out)
        return self.load(new)

    def rename(self, rel: str, name: str) -> dict:
        """The show FILE renamed (its id and, if derived from it, its name too), with its history
        following it. Refuses a name already in use."""
        p = self._path(rel)
        if not os.path.isfile(p):
            raise ApiError("no such show", 404)
        new = self._new_rel(name, rel)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        old_slug = os.path.splitext(os.path.basename(rel))[0]
        out = self._retitle(text, old_slug, os.path.splitext(os.path.basename(new))[0])
        q = self._path(new)
        with open(q, "x", encoding="utf-8") as fh:
            fh.write(out)
        h_old, h_new = timeline_notes.history_dir(p), timeline_notes.history_dir(q)
        if os.path.isdir(h_old) and not os.path.exists(h_new):
            os.makedirs(os.path.dirname(h_new), exist_ok=True)
            os.replace(h_old, h_new)
        os.remove(p)
        return self.load(new)

    def delete_show(self, rel: str, confirm) -> dict:
        """Delete a show file, behind confirm = its path. Its last version is kept in History first, so
        a deleted show can still be read back from <shows>/.history/."""
        p = self._path(rel)
        if confirm != rel:
            raise ApiError("deleting a show needs confirm: <its path>")
        if not os.path.isfile(p):
            raise ApiError("no such show", 404)
        kept = timeline_notes.snapshot(p, "deleted") or "the newest snapshot"
        os.remove(p)
        return {"deleted": rel, "kept": kept, "shows": self.list_shows()}

    # ---- the show on disk, and its history (timeline notes, plan #5) -------------------------
    def disk(self, rel: str) -> dict:
        """The show file's fingerprint as it is on disk now: the editor polls this to notice edits
        made outside it (an agent applying notes), and never overwrites them silently."""
        p = self._path(rel)
        if not os.path.isfile(p):
            raise ApiError("no such show", 404)
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        return {"path": rel, "disk_sha": _sha(text), "text": text}

    def history(self, rel: str) -> list:
        return timeline_notes.list_snapshots(self._path(rel))

    def history_read(self, rel: str, name: str, text: str) -> dict:
        """One snapshot, and what restoring it would change in the text being edited."""
        try:
            snap = timeline_notes.read_snapshot(self._path(rel), name)
        except (ValueError, OSError) as e:
            raise ApiError(f"no such snapshot: {e}", 404) from e
        return {"name": name, "text": snap, "diff": timeline_notes.diff(text or "", snap, "now", name)}

    def diff(self, a: str, b: str) -> dict:
        return {"diff": timeline_notes.diff(a or "", b or "", "yours", "on disk")}

    def notes_prompt(self, rel: str) -> dict:
        """The open notes as a request any agent can act on (timeline_notes.prompt) — from the file
        on DISK, which is what the agent will read."""
        p = self._path(rel)
        try:
            return {"prompt": timeline_notes.prompt(p)}
        except Exception as e:  # noqa: BLE001
            raise ApiError(f"could not build the prompt: {type(e).__name__}: {e}") from e

    def edit(self, rel: str, text: str, edits: list, clip: str | None = None) -> dict:
        try:
            new = yamledit.apply(text, edits or [])
        except (yamledit.EditError, yaml.YAMLError, KeyError, ValueError, TypeError) as e:
            raise ApiError(f"edit refused: {e}") from e
        return self.analyse(rel, new, clip)

    # ---- clips: make one from cues, edit inside one (the user's composer item #1) -----------
    def _analyse_clip(self, rel: str, text: str, clip: str) -> dict:
        """A clip on its own: its cues on the show's slots and beat grid, from the clip's start (beat
        1 = t 0, no song under it). Edits made here go into the clip's own cue list."""
        abspath = self._path(rel)
        out = {"path": rel, "text": text, "parse_error": None, "timeline": None, "authored": None,
               "cue_lines": None, "info": self.info(), "clip": clip}
        try:
            doc = yaml.safe_load(text)
            body = (doc.get("clips") or {})[clip]
        except (yaml.YAMLError, AttributeError, KeyError, TypeError):
            out["parse_error"] = f"no clip {clip!r} in this show"
            return out
        music = dict(doc["music"], offset_s=0.0) if isinstance(doc.get("music"), dict) else None
        if music:
            music.pop("song", None)
            music.pop("file", None)
        sub = {"id": f"{doc.get('id', 'show')}--clip-{clip}", "name": f"clip {clip}",
               "slots": doc.get("slots") or [], "requires": doc.get("requires") or {},
               "cues": list(body.get("cues") or [])}
        if music:
            sub["music"] = music
        show = cue.Show(sub, abspath)
        try:
            tl = show.export(self.limits, self.limits_path)
        except Exception as e:  # noqa: BLE001
            out["parse_error"] = f"the validator crashed on this clip: {type(e).__name__}: {e}"
            return out
        tl["show"]["source"] = rel
        tl["clip"] = {"name": clip, **{k: body[k] for k in ("length_beats", "length_s") if k in body}}
        out["timeline"] = tl
        with self._twin_lock:                    # the twins preview the clip on its own
            self._twin.update(rev=self._twin["rev"] + 1, path=rel, timeline=tl)
        out["authored"] = cue._jsonable(sub["cues"])
        try:
            out["cue_lines"] = yamledit.cue_lines(text, clip)
        except yamledit.EditError:
            out["cue_lines"] = None
        return out

    def make_clip(self, rel: str, text: str, indices: list, name: str) -> dict:
        """Turn some of the show's cues into a clip: their times become clip-local (beats when the show
        has a beat grid), the clip is as long as they last, and ONE placement takes their place at the
        first one's time, in the first one's own time form. One edit, so one undo."""
        if not isinstance(name, str) or not yamledit.CLIP_NAME.fullmatch(name):
            raise ApiError("a clip's name is letters, digits, - and _ (starting with a letter)")
        a = self.analyse(rel, text)
        tl = a["timeline"]
        if tl is None:
            raise ApiError("the show does not parse — fix it first")
        doc = yaml.safe_load(text)
        if name in (doc.get("clips") or {}):
            raise ApiError(f"there is already a clip {name!r}", 409)
        authored = doc.get("cues") or []
        idx = sorted({int(i) for i in indices or []})
        if not idx or any(not 0 <= i < len(authored) for i in idx):
            raise ApiError("select the cues to make the clip from")
        if any("clip" in authored[i] for i in idx):
            raise ApiError("a clip cannot contain a clip (no nesting in this format)")
        span = {}
        for evs in tl["tracks"].values():
            for e in evs:
                i = (e.get("src") or {}).get("index")
                if i in idx and not (e.get("src") or {}).get("clip"):
                    t = e["t"] if e.get("kind") == "keyframe" else e.get("start", e["t"])
                    lo, hi = span.get(i, (t, e.get("end", t)))
                    span[i] = (min(lo, t), max(hi, e.get("end", t)))
        if set(span) != set(idx):
            raise ApiError("some of those cues do not resolve to a time (the validator's problems say why)")
        t0 = min(v[0] for v in span.values())
        end = max(v[1] for v in span.values())
        m = tl.get("music")
        first = min(idx, key=lambda i: span[i][0])
        cues = []
        for i in sorted(idx, key=lambda i: span[i][0]):
            c = {k: v for k, v in authored[i].items() if k not in cue.TIME_KEYS}
            if m:
                b = round((span[i][0] - t0) / m["beat_s"] + 1, 4)
                cues.append({"beat": int(b) if b == int(b) else b, **c})
            else:
                cues.append({"t": round(span[i][0] - t0, 3), **c})
        if m:
            # through the beat its last cue falls on (beat-demo's `nod`: cues on beats 1 and 2, 2 beats long),
            # so a repeat starts after it, never on it
            body = {"length_beats": int((end - t0) / m["beat_s"] + 1e-6) + 1, "cues": cues}
        else:
            body = {"length_s": max(0.5, round(end - t0, 3)), "cues": cues}
        form = next(k for k in cue.TIME_KEYS if k in authored[first])
        place = {form: authored[first][form], "clip": name}
        edits = [{"op": "delete", "index": i} for i in sorted(idx, reverse=True)]
        edits += [{"op": "clip_set", "name": name, "clip": body}, {"op": "add", "cue": place}]
        out = self.edit(rel, text, edits)
        out["made_clip"] = name
        return out

    def save(self, rel: str, text: str) -> dict:
        """Write the text exactly as given. A show with VIOLATIONS may be saved — it is work in
        progress, and the gate is at run time — but text that is not a YAML mapping may not."""
        p = self._path(rel)
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ApiError(f"not saved: the text is not valid YAML: {e}") from e
        if not isinstance(doc, dict):
            raise ApiError("not saved: a show is a mapping")
        # the file as it was is kept first: every save can be compared and restored (History)
        timeline_notes.snapshot(p, "composer")
        tmp = p + ".composer-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
        ik_note = None
        if any(isinstance(c, dict) and cue.ik_target_key(c) for c in (doc.get("cues") or [])):
            # IK targets (ik.py): solve them now, writing the angles into the file, so what is saved
            # is what the twin shows and the robot will play. Needs MuJoCo; without it, say so.
            # MuJoCo is imported by the solver, not by `import ik`: catch its absence at the solve,
            # or a save of any show with targets fails outright on a composer without it
            try:
                import ik
                bad = ik.compile_show(p, self.limits_path, write=True, verbose=False)
            except ImportError as e:
                ik_note = (f"saved, but IK targets were not compiled here ({e.name or e} is not installed) — run "
                           "`python3 ik.py compile <show>` with MuJoCo installed, or start the composer with it")
            else:
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
                ik_note = "; ".join(bad) if bad else "IK targets compiled"
        out = self.analyse(rel, text)
        out["saved"] = True
        out["disk_sha"] = _sha(text)
        if ik_note:
            out["ik"] = ik_note
        return out

    # ---- audio ------------------------------------------------------------------------------
    def audio(self, rel: str) -> tuple[str, str]:
        p = self._path(rel, tuple(AUDIO_EXT))
        if not os.path.isfile(p):
            raise ApiError("no such audio file — see shows/audio/make_test_audio.py", 404)
        return p, AUDIO_EXT[os.path.splitext(p)[1].lower()]

    # ---- the library's songs (plan 4.6): add, list, delete, play ---------------------------
    def songs(self) -> list:
        """Every song in the library, with the shows here that use it."""
        out = []
        for e in library.items_of("song", self.library_dir):
            out.append({k: e.get(k) for k in ("id", "file", "duration_s", "source", "licence")}
                       | {"used_by": library.shows_using(e["id"], self.shows_dir)})
        return out

    def add_song(self, filename: str, data: bytes, item_id: str, source: str, licence: str,
                 duration_s=None) -> dict:
        """One uploaded song into the library, through library.add — its rules: a source and a
        licence, a hash, a duration. The type is checked by the file's first bytes, not only its
        name. `duration_s` is the browser's reading, used only when the server cannot read one."""
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in library.SONG_TYPES:
            raise ApiError(f"a song is one of {', '.join(sorted(library.SONG_TYPES))} — not {ext or 'that'}", 415)
        if not data:
            raise ApiError("the file is empty")
        if len(data) > MAX_SONG_BYTES:
            raise ApiError(f"over the {MAX_SONG_BYTES // (1024 * 1024)} MB limit for a song", 413)
        if not isinstance(item_id, str) or not library.ID_RE.match(item_id):
            raise ApiError("the id is lower-case letters, digits, - and _ (up to 64), starting with a letter or digit")
        if not (source or "").strip() or not (licence or "").strip():
            raise ApiError("a song needs its source and its licence — where it came from, and what allows "
                           "the robot to play it")
        tmp = tempfile.mkdtemp(prefix="composer-song-")
        try:
            path = os.path.join(tmp, item_id + ext)
            with open(path, "wb") as fh:
                fh.write(data)
            if not library.looks_like_song(path):
                raise ApiError(f"that file does not look like {ext} audio inside — refused", 415)
            dur = library.probe(path).get("duration_s")
            if dur is None and duration_s is not None:
                try:
                    dur = round(float(duration_s), 3)
                except (TypeError, ValueError):
                    dur = None
            if dur is None or not 0 < dur <= MAX_SONG_S:
                raise ApiError("could not read the song's length (the browser could not decode it either?)")
            try:
                e = library.add(path, item_id, "song", source, licence, self.library_dir, duration_s=dur)
            except ValueError as err:
                raise ApiError(f"not added: {err}") from err
            except OSError as err:
                raise ApiError(f"not added: cannot write the library at {self.library_dir} ({err.strerror}) — "
                               "start the composer with --library <a folder you can write>", 500) from err
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return {"added": e["id"], "songs": self.songs()}

    def delete_song(self, item_id: str, confirm) -> dict:
        """Delete a song from the library — its file and its entry. Refused while a show here still
        uses it (the list is returned), and without `confirm` equal to its id."""
        if confirm != item_id:
            raise ApiError("deleting a song needs confirm: <its id>")
        used = library.shows_using(item_id, self.shows_dir)
        if used:
            raise ApiError(f"{item_id!r} is still the music of {', '.join(used)} — take it out of "
                           "those shows first", 409)
        try:
            library.remove(item_id, self.library_dir)
        except ValueError as err:
            raise ApiError(str(err), 404) from err
        return {"deleted": item_id, "songs": self.songs()}

    def song_audio(self, item_id: str) -> tuple[str, str]:
        e = (library.load_index(self.library_dir) or {}).get(item_id)
        if not e or e.get("kind") != "song":
            raise ApiError("no such song in the library", 404)
        p = os.path.realpath(os.path.join(self.library_dir, e["file"]))
        if not p.startswith(self.library_dir + os.sep) or not os.path.isfile(p):
            raise ApiError("the song's file is missing from the library", 404)
        return p, AUDIO_EXT.get(os.path.splitext(p)[1].lower(), "application/octet-stream")

    # ---- teaching: measured joints in, outward-sense keyframe out ---------------------------
    def earliest(self, rel: str, text: str, slot: str, joints: dict, t=None, index=None) -> dict:
        """When can this arm pose legally arrive? (workplan 1.3)

        Teaching: `t` is the playhead; the answer is the soonest legal time at or after it.
        Re-timing an existing keyframe: `index` is its authored cue, which is left out, and the
        answer is the soonest legal time after the keyframe before it — earlier or later than
        where it sits now."""
        self._path(rel)
        if slot not in cue.ARM_SLOTS:
            raise ApiError("earliest is for ARM_L / ARM_R")
        if not isinstance(joints, dict) or not joints:
            raise ApiError("joints must be {joint: degrees}")
        try:
            show = cue.show_from_text(text, self._path(rel))
        except (yaml.YAMLError, cue.ShowError) as e:
            raise ApiError(f"the show does not parse: {e}") from e
        if index is not None:
            a = show.analyse(self.limits)
            mine = [e for e in a["events"] if e["src"]["index"] == index and e.get("slot") == slot]
            if not mine:
                raise ApiError("that cue is not an arm keyframe on this slot")
            t_now = mine[0]["t"]
            others = [e for e in a["events"] if e.get("slot") == slot and e.get("kind") == "keyframe"
                      and e["src"]["index"] != index and e["t"] < t_now - 1e-9]
            t = max((e["end"] for e in others), default=0.0)
        try:
            return show.earliest_time(self.limits, slot, joints, float(t or 0.0), index)
        except (ValueError, KeyError) as e:
            raise ApiError(str(e)) from e

    def capture(self, side: str, joint_state: dict) -> dict:
        """Convert ONE measured sensor_msgs/JointState (as JSON: {name: [...], position: [...]}
        in radians, controller-signed) into keyframe joints in the arm's outward sense.

        Read-only by construction: the browser supplies the sample (from rosbridge's
        /joint_states on the robot, or from the fake source in this PR), and this returns
        numbers. Nothing here can subscribe, publish or command."""
        if side not in ("left", "right"):
            raise ApiError("side must be left|right")
        if not isinstance(joint_state, dict):
            raise ApiError("joint_state must be {name: [...], position: [...]}")
        names, pos = joint_state.get("name") or [], joint_state.get("position") or []
        if len(names) != len(pos):
            raise ApiError("joint_state name and position differ in length")
        joints = cue.joint_state_to_outward(names, pos, side)
        if not joints:
            raise ApiError(f"no openarm_{side}_joint1..7 in that joint state")
        return {"side": side, "slot": "ARM_L" if side == "left" else "ARM_R",
                "joints": {str(k): v for k, v in sorted(joints.items())},
                "source": joint_state.get("source", "unknown")}
