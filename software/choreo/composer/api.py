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

import os
import sys
import threading
import time

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHOREO = os.path.dirname(HERE)
sys.path.insert(0, CHOREO)
sys.path.insert(0, HERE)

import cue  # noqa: E402  the validator is the gate; we call it, never copy it
import yamledit  # noqa: E402

FIXTURE_LIMITS = os.path.join(CHOREO, "tests", "joint_limits_fixture.yaml")
STATIC_DIR = os.path.join(HERE, "static")
AUDIO_EXT = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".flac": "audio/flac",
             ".m4a": "audio/mp4"}


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


class Composer:
    def __init__(self, shows_dir: str, limits_path: str | None = None):
        self.shows_dir = os.path.realpath(shows_dir)
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
        return {"shows_dir": self.shows_dir, "limits": os.path.basename(self.limits_path),
                "limits_are_test_fixture": self.fixture, "preview_only": True,
                "slots": list(cue.SLOTS), "face_emotes": list(cue.FACE_EMOTES),
                "speed_caps": cue.SPEED_CAP_DEG_S}

    def list_shows(self) -> list:
        out = []
        for root, _, files in os.walk(self.shows_dir):
            for f in sorted(files):
                if f.endswith((".yaml", ".yml")):
                    rel = os.path.relpath(os.path.join(root, f), self.shows_dir)
                    out.append({"path": rel})
        return sorted(out, key=lambda x: x["path"])

    # ---- the validator in the loop ----------------------------------------------------------
    def analyse(self, rel: str, text: str) -> dict:
        """Parse + validate + resolve. Never raises on a bad show: a show being edited is
        allowed to be wrong, and the editor's job is to say where."""
        abspath = self._path(rel)
        out = {"path": rel, "text": text, "parse_error": None, "timeline": None,
               "authored": None, "cue_lines": None, "info": self.info()}
        try:
            show = cue.show_from_text(text, abspath)
        except (yaml.YAMLError, cue.ShowError) as e:
            out["parse_error"] = str(e)
            return out
        try:
            tl = show.export(self.limits, self.limits_path)
        except Exception as e:  # noqa: BLE001 — a validator crash is a finding, shown not hidden
            out["parse_error"] = f"the validator crashed on this text: {type(e).__name__}: {e}"
            return out
        tl["show"]["source"] = rel
        out["timeline"] = tl
        with self._twin_lock:
            self._twin.update(rev=self._twin["rev"] + 1, path=rel, timeline=tl)
        out["authored"] = cue._jsonable(show.cues if isinstance(show.cues, list) else [])
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
            return self.analyse(rel, fh.read())

    def edit(self, rel: str, text: str, edits: list) -> dict:
        try:
            new = yamledit.apply(text, edits or [])
        except (yamledit.EditError, yaml.YAMLError, KeyError, ValueError, TypeError) as e:
            raise ApiError(f"edit refused: {e}") from e
        return self.analyse(rel, new)

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
        tmp = p + ".composer-tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
        ik_note = None
        if any(isinstance(c, dict) and cue.ik_target_key(c) for c in (doc.get("cues") or [])):
            # IK targets (ik.py): solve them now, writing the angles into the file, so what is saved
            # is what the twin shows and the robot will play. Needs MuJoCo; without it, say so.
            try:
                import ik
            except ImportError as e:
                ik_note = (f"IK targets not compiled here ({e.name or e} missing) — run "
                           "`python3 ik.py compile <show>` with MuJoCo installed")
            else:
                bad = ik.compile_show(p, self.limits_path, write=True, verbose=False)
                with open(p, encoding="utf-8") as fh:
                    text = fh.read()
                ik_note = "; ".join(bad) if bad else "IK targets compiled"
        out = self.analyse(rel, text)
        out["saved"] = True
        if ik_note:
            out["ik"] = ik_note
        return out

    # ---- audio ------------------------------------------------------------------------------
    def audio(self, rel: str) -> tuple[str, str]:
        p = self._path(rel, tuple(AUDIO_EXT))
        if not os.path.isfile(p):
            raise ApiError("no such audio file — see shows/audio/make_test_audio.py", 404)
        return p, AUDIO_EXT[os.path.splitext(p)[1].lower()]

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
