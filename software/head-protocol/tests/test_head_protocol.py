"""The head protocol's suite, reference head, and agreement with the Tab5's source.

    python3 software/head-protocol/tests/test_head_protocol.py      (or pytest)
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

import conformance  # noqa: E402
import head_protocol as hp  # noqa: E402
from fake_head import FakeHead, Neck  # noqa: E402


def _run(kind, neck_kind=None, sampled=False, face_head=None, **kw):
    face = (face_head or FakeHead(kind, port=0)).start()
    neck = FakeHead(neck_kind, port=0).start() if neck_kind else None
    try:
        r = conformance.run(f"127.0.0.1:{face.port}",
                            f"127.0.0.1:{neck.port}" if neck else None,
                            active=True, motion=True, sampled=sampled, **kw)
    finally:
        face.stop()
        if neck:
            neck.stop()
    return r


def _by(r, status):
    return [(half, check, detail) for s, half, check, detail in r.results if s == status]


# ---- the suite against heads that follow the document ------------------------------------------

def test_a_version_1_tab5_passes_everything():
    r = _run("tab5")
    assert _by(r, "FAIL") == [] and _by(r, "PENDING") == [], r.results
    assert r.kind == {"face": "tab5", "neck": "tab5"}


def test_the_tablet_head_face_and_neck_apart_passes_everything():
    r = _run("tablet-face", "neck")
    assert _by(r, "FAIL") == [] and _by(r, "PENDING") == [], r.results
    assert r.kind == {"face": "tablet-face", "neck": "neck"}


def test_todays_tab5_passes_with_only_the_new_messages_pending():
    """The ESP-IDF head as it ships: no kind=, no TRACK or NECK. Nothing it does is wrong."""
    r = _run("legacy-tab5")
    assert _by(r, "FAIL") == [], _by(r, "FAIL")
    pending = {check.split()[0] for _, check, _ in _by(r, "PENDING")}
    assert pending == {"TRACK", "NECK"}, pending


def test_touch_and_park_are_not_the_tab5s():
    r = _run("tab5")
    touch = [d for s, h, c, d in r.results if c.startswith(("TOUCH", "PARK")) and s == "PASS"]
    assert touch and all(d.startswith("not a tab5 message") for d in touch)


def test_a_tracker_under_a_show_does_not_own_the_head():
    r = _run("tab5")
    assert any(c == "TRACK thor under a GAZE" and s == "PASS" for s, _, c, _ in r.results)


# ---- the suite catches heads that do not ----------------------------------------------------------

class _Liar(FakeHead):
    """Answers a retired message, and gives GAZE the wrong reply."""

    def handle(self, data):
        line = data.decode(errors="replace")
        if line.startswith("TURN"):
            return "OK turn"
        if line.startswith("GAZE 95"):
            return "OK"
        return super().handle(data)


def test_a_head_that_answers_a_retired_message_fails():
    h = _Liar("tab5", port=0).start()
    try:
        r = conformance.run(f"127.0.0.1:{h.port}", motion=True)
    finally:
        h.stop()
    failed = {c for _, c, _ in _by(r, "FAIL")}
    assert "retired: TURN" in failed and "GAZE 95 92 800" in failed, failed


def test_no_head_at_all_is_reported_not_passed():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                # a port nobody answers on
    r = conformance.run(f"127.0.0.1:{port}", timeout=0.05)
    assert r.kind == {} and _by(r, "FAIL")[0][1] == "PING"


def test_motion_is_refused_against_a_robot_without_saying_so():
    assert conformance.main(["10.42.2.14", "--motion"]) == 2
    assert conformance.main(["10.42.2.14", "--active"]) == 2


# ---- the sampled checks: what the head does, not what it says (real time, a few seconds each) ----

def test_sampled_motion_passes_on_a_head_that_moves():
    r = _run("tablet-face", "neck", sampled=True)
    assert _by(r, "FAIL") == [], _by(r, "FAIL")
    judged = {c for s, h, c, d in r.results if s == "PASS" and h == "neck"}
    for c in ("GAZE: holds ownership while renewed", "GAZE: reaches pan", "GAZE: slews",
              "GAZE: lease expired, no jump", "GAZE: lease expired, stays put (owner=none)",
              "TRACK thor: slews", "PARK: reaches pan 90", "PARK: lease expired, stays put (owner=none)"):
        assert any(j.startswith(c) for j in judged), (c, judged)


def test_a_head_that_crawls_fails_though_every_reply_says_ok():
    """The GAZE lease bug of 2026-10-08: ~3 deg/s, and every reply OK."""
    h = FakeHead("legacy-tab5", port=0)
    h.neck.slew = 3.0
    r = _run(None, sampled=True, face_head=h)
    failed = {c.split(":")[0] + ":" + c.split(":")[1].split()[0] for _, c, _ in _by(r, "FAIL")}
    assert {"GAZE:reaches", "GAZE:slews"} <= failed, _by(r, "FAIL")
    replies = [c for s, h_, c, d in r.results if c in ("GAZE 95 92 800", "GAZE OFF")]
    assert replies and all(s == "PASS" for s, h_, c, d in r.results if c in replies)


def test_the_tab5s_resting_tracker_is_a_handover_not_a_failure():
    """On the Tab5 the local tracker is always live and rests at centre: after a lease expires the
    head goes back to centre, at the slew rate, owned by `local`. That is the protocol working."""
    r = _run("tab5", sampled=True)
    assert _by(r, "FAIL") == [], _by(r, "FAIL")
    over = [d for s, h, c, d in r.results if c == "GAZE: lease expired, handed over" and s == "PASS"]
    assert over and over[0].startswith("owner=local"), r.results


class _SpringNeck(Neck):
    """Returns to centre while reporting that nothing owns it — a move nobody is responsible for."""

    def target(self, now):
        return (90.0, 90.0) if self.owner(now) == "none" else super().target(now)


def test_a_head_that_moves_with_nobody_owning_it_fails():
    h = FakeHead("neck", port=0)
    h.neck = _SpringNeck()
    r = _run(None, sampled=True, face_head=h)
    assert any(c == "GAZE: lease expired, stays put (owner=none)" for _, c, _ in _by(r, "FAIL")), r.results


class _JumpNeck(Neck):
    """Snaps to centre the moment the resting tracker takes over — the jump the rule exists for."""

    def advance(self, now):
        super().advance(now)
        if self.owner(now) == "local":
            self.pan, self.tilt = 90.0, 90.0


def test_a_head_that_jumps_when_the_lease_ends_fails():
    h = FakeHead("tab5", port=0)
    h.neck = _JumpNeck()
    h.rest_at_centre()
    r = _run(None, sampled=True, face_head=h)
    assert any(c == "GAZE: lease expired, no jump" for _, c, _ in _by(r, "FAIL")), r.results


class _FlapNeck(Neck):
    """A renewed lease that keeps dropping out — the GAZE lease underflow of 2026-10-08."""

    def owner(self, now):
        o = super().owner(now)
        return "none" if o == "gaze" and int(now // 150) % 2 else o


def test_a_lease_that_flaps_while_renewed_fails():
    h = FakeHead("neck", port=0)
    h.neck = _FlapNeck()
    r = _run(None, sampled=True, face_head=h)
    assert any(c == "GAZE: holds ownership while renewed" for _, c, _ in _by(r, "FAIL")), r.results


# ---- precedence and leases, on a clock the test drives -------------------------------------------

def test_precedence_and_leases():
    n = Neck()
    n.src["thor"] = (1000, "at", 100.0, 90.0)
    n.src["gaze"] = (500, "at", 80.0, 90.0)
    assert n.owner(0) == "gaze"
    assert n.owner(600) == "thor"                # the show's lease ran out: the next one down
    n.park_until = 900
    assert n.owner(700) == "park"                # the face is away: trackers set aside
    n.src["rc"] = (800, "rate", 10.0, 0.0)
    assert n.owner(700) == "rc"                  # a person on the stick still moves it
    n.src["touch"] = (800, "rate", 0.0, 0.0)
    assert n.owner(700) == "touch"
    assert n.owner(5000) == "none"


def test_an_expired_lease_leaves_the_head_where_it_is():
    n = Neck()
    n.advance(0)
    n.src["gaze"] = (300, "at", 120.0, 90.0)
    n.advance(250)                               # mid-lease, slewing toward 120 at 70 deg/s
    at = n.pan
    n.advance(2000)                              # lease gone: it stays, it does not spring back
    assert 100 < at < 120 and abs(n.pan - at) < 1e-9


def test_park_centres():
    n = Neck()
    n.pan = 120.0
    n.advance(0)
    n.park_until = 10_000
    n.advance(1000)
    assert abs(n.pan - 90.0) < 1e-9


def test_leases_are_capped():
    h = FakeHead("neck", port=0)
    try:
        h.handle(b"GAZE 90 90 999999")
        assert h.neck.src["gaze"][0] - h.clock() <= hp.LEASE["gaze"][1] + 50
    finally:
        h.stop()


# ---- the document and the Tab5's source agree ----------------------------------------------------

def _src(rel):
    with open(os.path.join(REPO, rel)) as f:
        return f.read()


def test_every_message_the_tab5_parses_is_in_the_spec_and_none_is_missing():
    src = _src("firmware/tab5_idf/app_face/net_cmd.cpp")
    parsed = set(re.findall(r'strn?cmp\(rx, "([A-Z0-9]+)', src))
    assert parsed <= set(hp.MESSAGES), parsed - set(hp.MESSAGES)
    shipping = {v for v, (_, st) in hp.MESSAGES.items() if st in ("shipping", "tab5-only", "twin-only")}
    assert shipping <= parsed, shipping - parsed
    # A message proposed but NOT agreed must not be parsed yet: that is the agreement gate.
    unagreed_built = {v for v, (_, st) in hp.MESSAGES.items() if st == "new"} & parsed
    assert not unagreed_built, f"parsed by the Tab5 before the lanes agreed it: {unagreed_built}"


def test_the_vocabulary_is_the_tab5s():
    face = _src("firmware/tab5_idf/app_face/app_face.cpp")
    block = lambda name: face[face.index(name):face.index("};", face.index(name))]
    assert tuple(re.findall(r'\{"([A-Z_]+)",\s*[\d.]+f', block("const Emote kEmotes[]"))) == hp.EMOTES
    assert tuple(re.findall(r'\{"([A-Z_]+)",\s*"', block("const St kStates[]"))) == hp.STATES
    cues = face[face.index("int faceCueByName"):]
    cues = cues[:cues.index("};")]
    assert tuple(re.findall(r'"([a-z]+)"', cues)) == hp.CUES
    for t in hp.THEMES_BUILT_IN:
        assert f'"{t}"' in face, t


# ---- the web face engine agrees too (software/face-web) ------------------------------------------

def test_the_web_face_speaks_the_same_vocabulary():
    """vocab.js promises this check: the tablet face's states, emotes, cues and themes are the
    protocol's, and so (above) the Tab5's."""
    js = _src("software/face-web/engine/vocab.js")
    states = tuple(re.findall(r'^\s*\["([A-Z_]+)",', js[js.index("STATES"):js.index("];", js.index("STATES"))], re.M))
    emotes = tuple(re.findall(r'^\s*([A-Z]+):\s*\[', js[js.index("EMOTES"):js.index("};", js.index("EMOTES"))], re.M))
    cues = tuple(re.findall(r'"([a-z]+)"', js[js.index("export const CUES"):js.index("];", js.index("export const CUES"))]))
    themes = tuple(re.findall(r'^\s*([a-z]+):', js[js.index("THEMES"):js.index("};\n", js.index("THEMES"))], re.M))
    assert states == hp.STATES and emotes == hp.EMOTES and cues == hp.CUES, (states, emotes, cues)
    assert themes == hp.THEMES_BUILT_IN, themes


def test_the_moves_are_the_engines():
    js = _src("software/face-web/engine/protocol.js")
    line = next(l for l in js.splitlines() if l.startswith("export const MOVES"))
    assert tuple(re.findall(r'"(\w+)"', line)) == hp.MOVES, line


def test_the_move_peaks_in_the_spec_are_the_engines():
    """head-protocol.md's MOVE peak table == face.js MOVES[name].peak, which the face's own
    test_moves.mjs holds to the measured curve. Plain asserts: CI runs this file with bare python3."""
    js = _src("software/face-web/engine/face.js")
    block = js.split("export const MOVES", 1)[1].split("\n};", 1)[0]
    engine = {m: int(v) for m, v in re.findall(r"^\s*(\w+):\s*\{[^\n]*?\bpeak:\s*(\d+)", block, re.M)}
    assert set(engine) == set(hp.MOVES), sorted(engine)
    doc = _src("notes/design/head-protocol.md").split("**`MOVE` peak offsets**", 1)[1].split("\n## ", 1)[0]
    spec = {}
    for names, peak in re.findall(r"^\| (`[^|]+`) \| ([0-9.]+) \|", doc, re.M):
        for m in re.findall(r"`(\w+)`", names):
            spec[m] = round(float(peak) * 1000)
    assert spec == engine, f"spec {spec} != engine {engine}"


def test_the_code_skins_are_the_engines():
    js = _src("software/face-web/engine/skins/index.js")
    line = next(l for l in js.splitlines() if l.startswith("export const CODE_SKINS"))
    assert tuple(re.findall(r"(\w+)(?::\s*\w+)?\s*[,}]", line.split("=", 1)[1])) == hp.CODE_SKINS, line



def test_looks_are_the_built_ins_plus_the_content_folders_and_a_bad_file_is_named():
    """The discovery both the dashboard and the show validator read (looks.py): a theme file is a
    state's colour by state name, a reserved state is refused, and content can't replace a core look."""
    import json
    import tempfile
    import looks
    with tempfile.TemporaryDirectory() as d:
        face = os.path.join(d, "face")
        os.makedirs(os.path.join(face, "themes")); os.makedirs(os.path.join(face, "skins"))
        def put(rel, text):
            with open(os.path.join(face, rel), "w") as fh:
                fh.write(text)
        put("themes/winter.json", json.dumps({"label": "Winter", "colors": {"SAFE_IDLE": "#88CCFF"}}))
        put("themes/alarm.json", json.dumps({"colors": {"E_STOP": "#00ff00"}}))           # reserved: refused
        put("themes/green.json", json.dumps({"colors": {"CHARGING": "#00ff00"}}))         # charging stays purple
        put("themes/plain.json", json.dumps({"colors": {"INIT": "#ffffff"}}))             # a built-in name
        put("skins/snowman.js", "export function draw() {}")
        put("skins/snowman.json", json.dumps({"label": "Snowman"}))
        found = looks.discover(d)
        themes = {t["id"]: t for t in found["themes"]}
        assert set(hp.THEMES_BUILT_IN) <= set(themes) and themes["winter"]["source"] == "content"
        assert themes["winter"]["label"] == "Winter" and themes["winter"]["colors"] == {"SAFE_IDLE": "#88ccff"}
        assert "alarm" not in themes and "green" not in themes and themes["plain"]["source"] == "builtin"
        assert any("alarm.json" in p and "reserved" in p for p in found["problems"])
        assert any("plain.json" in p for p in found["problems"])
        assert {"id": "snowman", "label": "Snowman", "kind": "code", "source": "content"} in found["skins"]
        assert looks.theme_ids(d) >= {"plain", "winter"} and "snowman" in looks.code_skin_ids(d)
    assert looks.content_dir(env={"OPENDUBS_SHOWS_DIR": "/x/shows", "OPENDUBS_LOCAL_SHOWS": "/y"}).as_posix() == "/x/shows"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} passed")
