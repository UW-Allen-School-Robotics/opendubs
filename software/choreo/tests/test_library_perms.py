#!/usr/bin/env python3
"""A library index another user can read, and what happens when one cannot be read — found at the
robot (2026-10-12): the index was written 0600, the runner (as `kiosk`) could not read it, and every
show playing from the library crashed its run thread before it started, silently."""
from __future__ import annotations

import io
import os
import shutil
import stat
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import cue  # noqa: E402
import library  # noqa: E402
import runner as R  # noqa: E402

LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


tmp = tempfile.mkdtemp(prefix="opendubs-lib-perms-")
try:
    lib, src = os.path.join(tmp, "library"), os.path.join(tmp, "src")
    os.makedirs(src)
    with open(os.path.join(src, "eyes.png"), "wb") as fh:
        fh.write(b"not really a png")
    library.add(os.path.join(src, "eyes.png"), "made-up-eyes", "skin", "made up for a test", "CC0", lib,
                type="image", width=480, height=480)
    idx = os.path.join(lib, "index.yaml")
    print("library: the index is readable by others")
    check("the index is written 0644, like the items beside it (mkstemp alone makes it 0600)",
          stat.S_IMODE(os.stat(idx).st_mode) == 0o644, oct(os.stat(idx).st_mode))

    show_text = ("id: s\nslots: [FACE]\nrequires: { tab5: true }\ncues:\n"
                 "  - { t: 1.0, slot: FACE, do: skin, name: made-up-eyes }\n")
    os.environ["OPENDUBS_LIBRARY"] = lib
    a = cue.show_from_text(show_text, "s").analyse(cue.load_limits(LIMITS))
    check("a show playing a library skin validates against a readable library", not a["problems"],
          [q.msg for q in a["problems"]])

    can_lock_out = os.geteuid() != 0
    if can_lock_out:
        os.chmod(idx, 0)
        print("\nlibrary: an index this user cannot read")
        a = cue.show_from_text(show_text, "s").analyse(cue.load_limits(LIMITS))
        check("is a PROBLEM for a show that plays from it — not passed off as 'no library here'",
              any("cannot be read" in q.msg for q in a["problems"])
              and not any("no show library" in n for n in a["notices"]), ([q.msg for q in a["problems"]], a["notices"]))
        song = cue.show_from_text("id: m\nslots: [FACE]\nrequires: { tab5: true }\n"
                                  "music: { song: made-up-song, bpm: 120 }\ncues:\n"
                                  "  - { t: 1.0, slot: FACE, do: emote, name: SOFT }\n", "m")
        song.audio_override = None
        items, skipped = R.audio_items(song, {"music": {"song": "made-up-song"}, "tracks": {}})
        check("the runner's audio lookup does not crash on it: no music, said why (DEC-7)",
              not items and any("cannot be read" in x for x in skipped), skipped)
        os.chmod(idx, 0o644)
    else:
        print("  skip  running as root: a file cannot be made unreadable here")

    print("\nthe palette: a run that crashes is reported, never left looking alive")
    shows = os.path.join(tmp, "shows")
    os.makedirs(shows)
    shutil.copy(os.path.join(HERE, "fixtures", "shows", "shrug.yaml"), shows)
    pal = R.Palette(shows, LIMITS, R.FakeRobotState(), R.TwinFeed(), log=io.StringIO())
    real_run = R.Runner.run

    def boom(self):
        raise PermissionError("made up for a test")
    R.Runner.run = boom
    try:
        pal.run("shrug")
        for _ in range(100):
            if pal.last:
                break
            time.sleep(0.02)
    finally:
        R.Runner.run = real_run
    check("the run's end status says the runner failed, and why",
          bool(pal.last) and pal.last["code"] == "RUNNER_ERROR" and "PermissionError" in (pal.last["cause"] or ""),
          pal.last)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
