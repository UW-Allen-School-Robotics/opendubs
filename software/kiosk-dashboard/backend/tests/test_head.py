"""The head addressed by role — face and neck — with no change for a robot with the Tab5 fitted
(workplan-tablet.md item 0.1)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import head
import state_out


def test_with_no_settings_both_halves_are_the_tab5():
    assert head.face_host({}) == "opendubs-tab5.local"
    assert head.neck_host({}) == "opendubs-tab5.local"


def test_the_old_name_still_works_for_both_halves():
    env = {"OPENDUBS_TAB5_HOST": "127.0.0.1"}     # how a local dashboard run is kept off the robot
    assert head.face_host(env) == "127.0.0.1"
    assert head.neck_host(env) == "127.0.0.1"


def test_the_new_name_wins_over_the_old():
    env = {"OPENDUBS_TAB5_HOST": "old.local", "OPENDUBS_HEAD_FACE_HOST": " face.local "}
    assert head.face_host(env) == "face.local"


def test_a_separate_neck():
    env = {"OPENDUBS_HEAD_FACE_HOST": "tablet.local", "OPENDUBS_HEAD_NECK_HOST": "neck.local"}
    assert head.face_host(env) == "tablet.local"
    assert head.neck_host(env) == "neck.local"


def test_an_empty_setting_is_no_setting():
    assert head.neck_host({"OPENDUBS_HEAD_NECK_HOST": "  "}) == "opendubs-tab5.local"


def test_one_box_means_one_peer():
    """The Tab5: the same lookups and the same learned address for face and neck, as before."""
    face, neck = state_out._make_peers("opendubs-tab5.local", "opendubs-tab5.local")
    assert face is neck


def test_two_boxes_mean_two_peers():
    face, neck = state_out._make_peers("tablet.local", "neck.local")
    assert face is not neck
    assert (face.host, neck.host) == ("tablet.local", "neck.local")


def test_a_learned_address_beats_the_name(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "10.0.0.9")
    p = state_out._Peer("opendubs-tab5.local")
    p.learned = ("10.42.2.14", state_out.time.monotonic())
    assert p.resolve() == "10.42.2.14"


def test_a_failed_lookup_is_not_repeated_at_once(monkeypatch):
    import socket
    calls = []

    def fail(h):
        calls.append(h)
        raise OSError("no such host")
    monkeypatch.setattr(socket, "gethostbyname", fail)
    p = state_out._Peer("absent.local")
    assert p.resolve() is None
    assert p.resolve() is None
    assert len(calls) == 1                         # a 5 s lookup is paid once per retry window
