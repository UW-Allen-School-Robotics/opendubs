"""Refusals carry a registry code beside their sentence, so the tablet can open the matching
troubleshooting entry. No robot, no network."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import status
import supervisor as supervisor_mod
from refusals import refuse


def test_refuse_body_keeps_the_sentence_and_adds_the_code():
    r = refuse("LIFT_RELAY_RESERVED", "relays 1 and 2 are the lift legs", 409, hint="use the lift")
    assert r.status_code == 409
    assert json.loads(r.body) == {"ok": False, "code": "LIFT_RELAY_RESERVED",
                                  "error": "relays 1 and 2 are the lift legs", "hint": "use the lift"}


def test_rearm_guard_names_every_blocker_with_its_code():
    s = supervisor_mod.Supervisor()
    ok, blockers = s.estop_rearm_guard(button_pressed=True, stale=True, welded=True, authority="RC")
    assert not ok
    assert [c for c, _ in blockers] == ["ESTOP_BUTTON", "LINK_STAMPLC_LOST",
                                        "CONTACTOR_WELDED", "ESTOP_REARM_CH5"]


def test_rearm_guard_passes_clean():
    s = supervisor_mod.Supervisor()
    assert s.estop_rearm_guard(button_pressed=False, stale=False, welded=False,
                               authority="OFF") == (True, [])


def test_try_estop_clear_keeps_sentences_and_adds_codes_in_step(monkeypatch):
    # The tablet reads `blockers` as sentences; `codes` must run beside it, in the same order.
    monkeypatch.setattr(status, "_safety", lambda: {
        "estop_button": False, "stale": False, "welded": False})
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {"rc": {"mode": "MANUAL"}})
    monkeypatch.setattr(status, "supervisor", supervisor_mod.Supervisor())
    out = status.try_estop_clear()
    assert out == {"ok": False, "blockers": ["CH5 not OFF"], "codes": ["ESTOP_REARM_CH5"]}


def test_try_estop_clear_unreachable_board_is_a_coded_refusal(monkeypatch):
    monkeypatch.setattr(status, "_safety", lambda: {
        "estop_button": False, "stale": False, "welded": False})
    monkeypatch.setattr(status.stamplc.reader, "snapshot", lambda: {"rc": {"mode": "OFF"}})
    monkeypatch.setattr(status.stamplc.reader, "send", lambda cmd: False)
    monkeypatch.setattr(status, "supervisor", supervisor_mod.Supervisor())
    out = status.try_estop_clear()
    assert out["ok"] is False and out["codes"] == ["LINK_STAMPLC_LOST"]
    assert len(out["blockers"]) == len(out["codes"])
