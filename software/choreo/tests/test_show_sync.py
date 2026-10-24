#!/usr/bin/env python3
"""show_sync.py and the robot's own shows folder, against a fake robot on this computer
(`local:/folder`): what a push checks, what it sends, and the palette listing both folders."""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

import yaml  # noqa: E402

import library  # noqa: E402
import library_sync as LS  # noqa: E402
import runner as R  # noqa: E402
import show_sync as SS  # noqa: E402

LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
SHRUG = open(os.path.join(HERE, "fixtures", "shows", "shrug.yaml"), encoding="utf-8").read()   # a stamped palette show
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


tmp = tempfile.mkdtemp(prefix="opendubs-show-sync-test-")
try:
    robot = os.path.join(tmp, "robot")
    own, lib, repo = (os.path.join(robot, x) for x in ("shows", "library", "repo-shows"))
    here_lib, src = os.path.join(tmp, "laptop-library"), os.path.join(tmp, "src")
    for d in (repo, src):
        os.makedirs(d)
    shutil.copy(os.path.join(HERE, "fixtures", "shows", "shrug.yaml"), repo)
    with open(os.path.join(src, "eyes.png"), "wb") as fh:
        fh.write(b"not really a png")
    library.add(os.path.join(src, "eyes.png"), "made-up-eyes", "skin", "made up for a test", "CC0", here_lib,
                type="image", width=480, height=480)
    os.environ["OPENDUBS_LIBRARY"] = here_lib          # the validator checks skins against this computer's library
    HOST = "local:" + own

    def show(name, text):
        p = os.path.join(src, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def push(*paths, replace=False, dry=False, create=True):
        lines = []
        rc = SS.push(HOST, list(paths), SS.REMOTE_LOCAL_SHOWS, LIMITS, lib, repo, dry, replace, create, out=lines.append)
        return rc, "\n".join(lines)

    mine = SHRUG.replace("id: shrug", "id: my-shrug", 1).replace("label: Shrug", "label: My shrug", 1)
    print("show sync: pushing")
    rc, out = push(show("my-shrug.yaml", mine), dry=True)
    check("a dry run says what would be sent and sends nothing",
          rc == 0 and "WOULD" in out and not os.path.exists(os.path.join(own, "my-shrug.yaml")), out)
    rc, out = push(show("my-shrug.yaml", mine))
    check("a valid palette show is sent to the robot's own folder, as <id>.yaml (the folder made with --create)",
          rc == 0 and "SENT" in out and open(os.path.join(own, "my-shrug.yaml")).read() == mine, out)
    rc, out = push(show("my-shrug.yaml", mine))
    check("pushing it again is skipped (the robot has it, identical)", rc == 0 and "SKIP" in out, out)
    changed = mine.replace("name: Shrug", "name: Shrug, slower", 1)
    rc, out = push(show("my-shrug.yaml", changed))
    check("a different show under the same id is a conflict, not overwritten",
          rc == 1 and "CONFLICT" in out and open(os.path.join(own, "my-shrug.yaml")).read() == mine, out)
    rc, out = push(show("my-shrug.yaml", changed), replace=True)
    check("... and --replace overwrites it", rc == 0 and open(os.path.join(own, "my-shrug.yaml")).read() == changed, out)

    print("\nshow sync: what is refused, before anything moves")
    rc, out = push(show("clash.yaml", SHRUG))
    check("a show whose id is a repository show's on the robot is refused, naming it",
          rc == 1 and "repository show" in out and not os.path.exists(os.path.join(own, "shrug.yaml")), out)
    rc, out = push(show("nopal.yaml", mine.replace("id: my-shrug", "id: nopal").replace(
        "palette: { label: My shrug, order: 3 }\n", "")))
    check("a show with no palette: block is refused, saying how to add one", rc == 1 and "palette:" in out, out)
    stale = mine.replace("id: my-shrug", "id: stale", 1).replace("clearance:", "x_clearance:", 1)
    stale = "\n".join(l for l in stale.splitlines() if not l.startswith("x_clearance")) + "\n"
    rc, out = push(show("stale.yaml", stale))
    check("an arm show without its self-collision stamp is refused here, as the robot would",
          rc == 1 and "swept" in out, out)
    skinned = mine.replace("id: my-shrug", "id: skinned", 1).rstrip("\n") + \
        "\n  - { t: 0.5, slot: FACE, do: skin, name: made-up-eyes }\n"
    rc, out = push(show("skinned.yaml", skinned))
    check("a show naming a skin the ROBOT's library lacks is refused, with the push that sends it",
          rc == 1 and "library_sync.py push" in out and "made-up-eyes" in out, out)
    os.makedirs(lib, exist_ok=True)
    with open(os.path.join(lib, "index.yaml"), "w") as fh:
        yaml.safe_dump([library.load_index(here_lib)["made-up-eyes"]], fh)
    rc, out = push(show("skinned.yaml", skinned))
    check("... and is sent once the robot's library has it", rc == 0 and "SENT" in out, out)

    shutil.copy(LIMITS, os.path.join(robot, "joint_limits.yaml"))
    lines = []
    rc = SS.push(HOST, [os.path.join(src, "my-shrug.yaml")], SS.REMOTE_LOCAL_SHOWS, "/nonexistent", lib, repo,
                 True, True, False, out=lines.append, limits_from_robot=os.path.join(robot, "joint_limits.yaml"))
    check("--limits-from-robot judges the show by the ROBOT's joint-limits file (read, never written)",
          rc == 0 and "robot's joint limits" in "\n".join(lines), lines)
    try:
        SS.push(HOST, [os.path.join(src, "my-shrug.yaml")], SS.REMOTE_LOCAL_SHOWS, LIMITS, lib, repo,
                True, True, False, out=lambda *_: None, limits_from_robot=os.path.join(robot, "missing.yaml"))
        stopped = False
    except SS.ShowSyncError as e:
        stopped = "joint limits" in str(e)
    check("... and an unreadable robot limits file stops the check, never falling back to the fixture", stopped)
    # the runner's path is a bind mount only its service sees: found through its drop-in instead
    real = os.path.join(robot, "someones-checkout", "hw_validation")
    os.makedirs(real)
    shutil.copy(LIMITS, os.path.join(real, "joint_limits.yaml"))
    dropin = os.path.join(robot, "arm-limits.conf")
    with open(dropin, "w") as fh:
        fh.write(f"[Service]\nBindReadOnlyPaths=-{real}:/etc/opendubs/arm-limits\n")
    lines = []
    rc = SS.push(HOST, [os.path.join(src, "my-shrug.yaml")], SS.REMOTE_LOCAL_SHOWS, "/nonexistent", lib, repo,
                 True, True, False, out=lines.append, limits_from_robot=SS.REMOTE_LIMITS, limits_dropin=dropin)
    check("the runner's limits path, a bind mount only its service sees, is read through its drop-in",
          rc == 0 and "REFUSED" not in "\n".join(lines) and any(x in "\n".join(lines) for x in ("WOULD", "SKIP")), lines)

    print("\nshow sync: the palette lists both folders")
    pal = R.Palette(repo, LIMITS, R.FakeRobotState(), R.TwinFeed(), log=io.StringIO(), local_shows=own)
    rows = {r["id"]: r for r in pal.routines()}
    check("the palette lists the robot's own shows beside the repository's, saying where each is from",
          rows.get("my-shrug", {}).get("from") == "robot" and rows.get("shrug", {}).get("from") == "repository", rows.keys())
    with open(os.path.join(own, "shrug.yaml"), "w") as fh:          # copied in by hand, past the push's check
        fh.write(SHRUG)
    dup = [r for r in R.Palette(repo, LIMITS, R.FakeRobotState(), R.TwinFeed(), log=io.StringIO(),
                                local_shows=own).routines() if r["id"] == "shrug"]
    check("an id in both folders: the robot's copy is refused (ambiguous), the repository's still runs",
          len(dup) == 2 and sum(r["valid"] for r in dup) == 1 and any("also the" in " ".join(r["problems"]) for r in dup), dup)
    os.remove(os.path.join(own, "shrug.yaml"))
    rows = {r["id"]: r for r in R.Palette(repo, LIMITS, R.FakeRobotState(), R.TwinFeed(), log=io.StringIO(),
                                          local_shows=os.path.join(tmp, "nowhere")).routines()}
    check("no robot folder: the palette is the repository's alone, as before", "my-shrug" not in rows and "shrug" in rows)

    print("\nshow sync: the library will not delete what a robot-only show plays")
    res = LS._remote_delete(lib, repo + os.pathsep + own, ["made-up-eyes"], dry=True)
    check("deleting a library item a show in the robot's own folder names is refused, naming it",
          res["report"][0]["action"] == "refused" and "skinned.yaml" in res["report"][0]["why"], res)

    print("\nshow sync: status and delete")
    lines = []
    SS.status(HOST, SS.REMOTE_LOCAL_SHOWS, out=lines.append)
    check("status lists the robot's own shows", "my-shrug.yaml" in "\n".join(lines) and "skinned.yaml" in "\n".join(lines),
          lines)
    lines = []
    check("delete takes one off the robot", SS.delete(HOST, SS.REMOTE_LOCAL_SHOWS, "skinned", out=lines.append) == 0
          and not os.path.exists(os.path.join(own, "skinned.yaml")), lines)
finally:
    shutil.rmtree(tmp)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
