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


def test_a_found_address_is_cached(monkeypatch):
    import socket
    calls = []

    def found(h):
        calls.append(h)
        return "10.42.2.14"
    monkeypatch.setattr(socket, "gethostbyname", found)
    p = state_out._Peer("opendubs-tab5.local")
    assert p.resolve() == "10.42.2.14"
    assert p.resolve() == "10.42.2.14"
    assert len(calls) == 1                         # the name is looked up once per 30 s


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


def test_none_or_off_means_no_neck():
    for v in ("none", "NONE", " off "):
        assert head.neck_host({"OPENDUBS_HEAD_FACE_HOST": "127.0.0.1", "OPENDUBS_HEAD_NECK_HOST": v}) is None
    # Empty still means "the same box as the face", so a Tab5 robot is unchanged.
    assert head.neck_host({"OPENDUBS_HEAD_FACE_HOST": "tab5.local", "OPENDUBS_HEAD_NECK_HOST": ""}) == "tab5.local"


def test_no_neck_means_the_stamplc_is_never_told_one(monkeypatch):
    """An address for an absent neck can stall the StamPLC's loop (its pan/tilt send blocks), so with
    no neck fitted it is never handed one."""
    import sys
    import types
    sent = []
    monkeypatch.setitem(sys.modules, "stamplc", types.SimpleNamespace(reader=types.SimpleNamespace(send=sent.append)))
    face, neck = state_out._make_peers("127.0.0.1", None)
    assert neck is None and face.host == "127.0.0.1"
    monkeypatch.setattr(state_out, "_neck", None)
    monkeypatch.setattr(state_out, "_peers_last_intro", -1e9)
    state_out._introduce_peers()
    state_out._neck_sender()                               # returns at once: nothing to introduce
    assert sent == []
