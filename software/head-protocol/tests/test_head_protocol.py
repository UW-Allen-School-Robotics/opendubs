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
    for c in ("GAZE: reaches pan", "GAZE: slews", "GAZE: lease expired, stays put",
              "TRACK thor: slews", "PARK: reaches pan 90", "PARK: lease expired, stays put"):
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


class _SpringNeck(Neck):
    """Returns to centre when nothing owns it — which the protocol forbids."""

    def target(self, now):
        return (90.0, 90.0) if self.owner(now) == "none" else super().target(now)


def test_a_head_that_springs_back_when_the_lease_ends_fails():
    h = FakeHead("legacy-tab5", port=0)
    h.neck = _SpringNeck()
    r = _run(None, sampled=True, face_head=h)
    assert any(c == "GAZE: lease expired, stays put" for _, c, _ in _by(r, "FAIL")), r.results


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
    # A new message the Tab5 has started parsing is no longer "new": update the document.
    new_now_built = {v for v, (_, st) in hp.MESSAGES.items() if st == "new"} & parsed
    assert not new_now_built, f"built on the Tab5, still marked new: {new_now_built}"


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


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    for name, fn in tests:
        fn()
        print("ok  ", name)
    print(f"{len(tests)} passed")
