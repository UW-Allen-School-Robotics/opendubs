#!/usr/bin/env python3
"""The tool registry (effectors.py), tools on the arms, and the crawling hand (CRAWL): what the validator
accepts and refuses, what the runner refuses, the twin's crawler, and the left hand's mirror.

The Surge model itself is the vendor's and is not in the repository: the checks that need it run only when
$OPENDUBS_SURGE_DIR points at it, and say they skipped otherwise. Everything else uses the registry and
the primitive hand."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
# the Surge pack is content (the content repository's tools/surge/): the tests use a fixture copy of it
os.environ["OPENDUBS_SHOWS_DIR"] = os.path.join(HERE, "fixtures", "content")

import cue  # noqa: E402
import effectors as E  # noqa: E402
import runner  # noqa: E402
import sim_preview as P  # noqa: E402

FAILS = []
LIMITS = cue.load_limits(os.path.join(HERE, "joint_limits_fixture.yaml"))


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


def show(extra: str, cues: str, slots="[GRIP_R]", music=True) -> cue.Show:
    text = ("id: t\nname: t\nrequires: { arms: active, rc_authority: AUTO }\n"
            + ("music: { bpm: 120, offset_s: 0, beats_per_bar: 4 }\n" if music else "")
            + f"slots: {slots}\n" + extra + "cues:\n" + cues)
    return cue.show_from_text(text, "")


def problems(s) -> list[str]:
    return [p.msg for p in s.analyse(LIMITS)["problems"]]


def has(msgs, frag) -> bool:
    return any(frag in m for m in msgs)


print("registry")
reg = E.registry()
check("every type loads, and the left Surge is the right one mirrored",
      {"openarm_gripper", "surge_right", "surge_left", "suction_cup", "pneumatic_gripper"} <= set(reg)
      and reg["surge_left"].mirrored and reg["surge_left"].hand == "left", sorted(reg))
check("a mirrored hand's pivots swap sides (their gaits; its own speed estimates may override the rates)",
      reg["surge_left"].crawl["pivots"]["left"]["sim_params"] == reg["surge_right"].crawl["pivots"]["right"]["sim_params"])
check("the mirror flips the heading loop's thumb gain",
      reg["surge_left"].crawl["gaits"]["outrigger"]["sim_params"]["heading_ctrl"]["plant_sign"] == 1.0)
check("every crawl number says it is from simulation",
      all("SIM" in t.crawl["provenance"].upper() for t in reg.values() if t.crawl))
check("open: maps through each tool's own open_map",
      reg["openarm_gripper"].open_channels(0.3) == {"open": 0.3}
      and reg["suction_cup"].open_channels(1.0) == {"vacuum": 0.0}
      and reg["pneumatic_gripper"].open_channels(0.0) == {"valve": "extend"}
      and reg["surge_right"].open_channels(1.0)["index"] == 0.0)
check("a valve cannot be half open", isinstance(reg["pneumatic_gripper"].open_channels(0.5), str))
check("no fitted-tools file means today's robot", E.load_fitted("/nonexistent/tools.yaml") == E.DEFAULT_FITTED)

print("shows that name no tools: exactly as before")
old = show("", "  - { t: 1, slot: GRIP_R, do: open }\n")
tl = old.export(LIMITS)
check("valid, and the export carries no tools or crawler", tl["valid"] and "tools" not in tl and "crawler" not in tl,
      tl["problems"])
check("pose: on today's gripper is refused by name",
      has(problems(show("", "  - { t: 1, slot: GRIP_R, pose: half }\n")), "position only"))

print("a tool on a flange")
surge = "tools: { right: surge_right }\n"
s = show(surge, "  - { t: 1, slot: GRIP_R, pose: horns }\n  - { t: 2, slot: GRIP_R, open: 0.5 }\n"
         "  - { t: 3, slot: GRIP_R, set: { thumb_rot: 0.2 } }\n")
tl = s.export(LIMITS)
check("a Surge hand takes pose:, open: and set:", tl["valid"], tl["problems"])
check("its GRIP events carry the resolved channels",
      tl["tracks"]["GRIP_R"][0]["channels"]["middle"] == 1 and tl["tracks"]["GRIP_R"][2]["channels"]["thumb_rot"] == 0.2)
check("and the show is noted as twin only", any("no driver" in n for n in tl["notices"]))
smp = P.sample(tl, 1.05)
check("the twin poses the flange tool, its channels travelling at their own speed",
      smp["tools"]["right"]["tool"] == "surge_right" and 0 < smp["tools"]["right"]["channels"]["middle"] < 1
      and P.sample(tl, 1.9)["tools"]["right"]["channels"]["middle"] == 1, smp.get("tools"))
check("an unknown pose is refused", has(problems(show(surge, "  - { t: 1, slot: GRIP_R, pose: jazz }\n")), "no pose 'jazz'"))
check("an unknown channel is refused", has(problems(show(surge, "  - { t: 1, slot: GRIP_R, set: { wrist: 1 } }\n")), "no channel 'wrist'"))
check("an out-of-range channel is refused", has(problems(show(surge, "  - { t: 1, slot: GRIP_R, set: { index: 2 } }\n")), "within 0..1"))
quick = s.analyse(LIMITS)
q2 = show(surge, "  - { t: 1, slot: GRIP_R, pose: fist }\n  - { t: 1.1, slot: GRIP_R, pose: open }\n").analyse(LIMITS)
check("a target the fingers cannot reach in time warns, never refuses",
      not q2["problems"] and any("still moving" in w.msg for w in q2["warnings"]), [w.msg for w in q2["warnings"]])
check("a valve at 0.5 is refused",
      has(problems(show("tools: { left: pneumatic_gripper }\n", "  - { t: 1, slot: GRIP_L, open: 0.5 }\n", "[GRIP_L]")), "nothing between"))
s.fitted_tools = E.DEFAULT_FITTED
check("validated against a robot that has grippers fitted, the Surge show is refused",
      has([p.msg for p in s.analyse(LIMITS)["problems"]], "needs surge_right at right"))
check("a crawl mode on a flange is refused",
      has(problems(show("tools: { right: { type: surge_right, mode: crawl } }\n", "  - { t: 1, slot: GRIP_R, do: open }\n")), "lives on the floor"))

print("the crawling hand")
CR = ("tools: { base: { type: surge_right, mode: crawl } }\n"
      "crawler: { tether: { anchor: base_front, length_m: 1.8 }, area: { x: [0.75, 2.6], y: [-0.7, 0.7] } }\n")
good = ("  - { t: 1, slot: CRAWL, do: walk, secs: 10 }\n"
        "  - { t: 20, slot: CRAWL, do: dance, pattern: wave, beats: 8 }\n"
        "  - { t: 30, slot: CRAWL, do: walk, dir: back, to: start }\n")
s = show(CR, good, "[CRAWL]")
tl = s.export(LIMITS)
check("a walk out, a finger dance and a walk home validate", tl["valid"], tl["problems"])
import hand_output  # noqa: E402
check("on the robot it needs a floor hand: noted, and this hand type (no driver) is unavailable — refused, or "
      "skipped on approval with can_skip (test_floor_hand.py)",
      any("CRAWL plays on the robot only" in n for n in tl["notices"]) and not runner.twin_only_tools(tl)
      and "no driver" in (hand_output.availability(tl, None)["why"] or ""))
check("the export carries the plan, with sim speeds and a sampled path",
      tl["crawler"]["legs"][0]["speed_key"] == "dragging_m_s" and len(tl["crawler"]["path"]) > 100
      and "SIM, NOT HARDWARE" in tl["crawler"]["provenance"])
check("the timing is said in words (distance and time at the sim speed)",
      any("walk forward for 10s" in n and "cm/s" in n for n in tl["notices"]))
back = tl["crawler"]["legs"][2]
check("the walk home is held for its slowest case", abs(back["secs"] - back["dist_m"] / back["speed"][0]) < 1e-6, back)
st0, st1 = P.crawler_state(tl, 0.0), P.crawler_state(tl, 15.0)
check("the hand starts on the floor, well clear of the chassis, and stays where its path puts it",
      st0["x"] > 1.0 and st1["x"] > st0["x"], (st0["x"], st1["x"]))
check("standing with no cue, it plays its idle pattern (never frozen): the index moves between frames",
      P.crawler_state(tl, 0.30)["channels"]["index"] != P.crawler_state(tl, 0.55)["channels"]["index"])
check("the twin's sample carries the crawler, and a show without one has no such key",
      "crawler" in P.sample(tl, 5.0) and "crawler" not in P.sample(old.export(LIMITS), 5.0))
m = problems(show(CR, "  - { t: 1, slot: CRAWL, do: walk, secs: 10 }\n  - { t: 5, slot: CRAWL, do: walk, dir: back, to: start }\n", "[CRAWL]"))
check("a leg that starts before the last can finish (at the slowest speed) is refused", has(m, "still busy"), m)
m = problems(show(CR, "  - { t: 1, slot: CRAWL, do: walk, dir: back, dist_m: 0.4 }\n", "[CRAWL]"))
check("backing in too near the chassis is refused (keep_off_m)", has(m, "keep_off_m"), m)
m = problems(show(CR.replace("crawler: {", "crawler: { start: [0.8, 0, 0],"), "  - { t: 1, slot: CRAWL, do: pause, secs: 1 }\n", "[CRAWL]"))
check("a start too near the chassis is refused", has(m, "starts"), m)
m = problems(show(CR.replace("length_m: 1.8", "length_m: 0.5"), good, "[CRAWL]"))
check("a leash too short for the walk is refused", has(m, "leash"), m)
m = problems(show(CR.replace("x: [0.75, 2.6]", "x: [0.75, 1.3]"), good, "[CRAWL]"))
check("leaving the declared floor area is refused", has(m, "floor area"), m)
m = problems(show(CR, "  - { t: 1, slot: CRAWL, do: walk, secs: 10 }\n  - { t: 20, slot: CRAWL, do: turn, by_deg: 90 }\n"
                      "  - { t: 80, slot: CRAWL, do: walk, dir: back, to: start }\n", "[CRAWL]"))
check("a walk to a point off the hand's line is refused (turn first; it walks straight)", has(m, "off the hand's line"), m)
pl = show(CR, "  - { t: 1, slot: CRAWL, do: turn, by_deg: 30 }\n", "[CRAWL]").export(LIMITS)
leg = pl["crawler"]["legs"][0]
check("a turn is a pivot at the slowest simulated rate", leg["do"] == "turn" and abs(leg["secs"] - 30 / leg["rate"][0]) < 1e-6, leg)
m = problems(show("", "  - { t: 1, slot: CRAWL, do: pause, secs: 2 }\n", "[CRAWL]"))
check("CRAWL with no crawler fitted is refused", has(m, "fits none"), m)
m = problems(show(CR, "  - { t: 1, slot: CRAWL, do: dance, pattern: moonwalk, secs: 2 }\n", "[CRAWL]"))
check("an unknown finger dance is refused", has(m, "no finger dance"), m)
m = problems(show(CR, "  - { t: 1, slot: CRAWL, do: walk, secs: 10, gait: flat }\n", "[CRAWL]"))
check("a gait with no simulated speed on a trailing leash is refused", has(m, "trailing leash"), m)
m = problems(show(CR, "  - { t: 1, slot: CRAWL, do: walk, dir: sideways, secs: 2 }\n", "[CRAWL]"))
check("a crawling hand cannot sidestep", has(m, "cannot sidestep"), m)

print("the real hand's sequence (export-effector -> the Surge pack's drivers/surge_at.py)")
import json, tempfile  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "fixtures", "content", "tools", "surge", "drivers"))
import surge_at  # noqa: E402
LH = CR.replace("surge_right", "surge_left").replace("start", "start").replace("crawler: {", "crawler: { start: [1.0, 0, 0],")
bench = show(LH, "  - { t: 0.5, slot: CRAWL, do: walk, gait: bare_flexstroke, secs: 3 }\n"
                 "  - { t: 8, slot: CRAWL, do: dance, pattern: ripple, secs: 3 }\n", "[CRAWL]").export(LIMITS)
seq = E.hardware_sequence(bench, "test", 0.0, 12.0)
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump(seq, fh)
_, segs, total = surge_at.load_sequence(fh.name)
os.unlink(fh.name)
check("an exported sequence loads in the driver's player: every joint in range, no closing preset, the tested gait in it",
      bench["valid"] and any(k == "gait" and abs(a["thumb_flex"] - 0.977) < 1e-6 for k, a, _ in segs)
      and not any(k == "pose" for k, _, _ in segs) and 10 < total < 14, (bench["problems"], total))
check("... and its finger dance keeps the thumb clear (the calibration's dance thumb)",
      all(a.get("T") == 10 and a.get("X") == 0 for k, a, _ in segs if k == "joints"))

print("the twin's model of the hand")
try:
    import mujoco  # noqa: F401
    sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "sim"))
    import hand
    surge = hand.surge_dir()
    os.environ.pop("OPENDUBS_SURGE_DIR", None)
    check("the left PRIMITIVE hand is the right one exactly mirrored (bodies and geoms)", hand.mirror_check() < 1e-9)
    if surge:
        check("the left SURGE hand is the vendor's right hand exactly mirrored", hand.mirror_check(surge) < 1e-9)
    else:
        print("  skip  the Surge model's mirror ($OPENDUBS_SURGE_DIR is not set)")
except ImportError:
    print("  skip  the hand models (no MuJoCo here)")

# ---- tool packs in the content folder (content.py: <dir>/tools/<pack>/) ------------------------------
import shutil  # noqa: E402
import tempfile  # noqa: E402

import content  # noqa: E402

_tmp = tempfile.mkdtemp()
_pack = os.path.join(_tmp, "tools", "demo_pack")
os.makedirs(os.path.join(_pack, "drivers"))
_doc = open(os.path.join(E.REG_DIR, "suction_cup.yaml")).read().replace("id: suction_cup", "id: pack_cup")
open(os.path.join(_pack, "pack_cup.yaml"), "w").write(_doc.replace("output: null", "output: pack_driver"))
open(os.path.join(_pack, "pack_cup.gaits.json"), "w").write('{"cycles": {}}')
open(os.path.join(_pack, "drivers", "pack_driver.py"), "w").write("# a pack's driver\n")
_was = os.environ.get("OPENDUBS_SHOWS_DIR")
os.environ["OPENDUBS_SHOWS_DIR"] = _tmp
E._REG = None
try:
    reg = E.registry()
    check("a tool pack in the content folder adds its tool types to the engine's",
          "pack_cup" in reg and "openarm_gripper" in reg and content.tool_packs() == [_pack], sorted(reg))
    check("... its gaits file and its driver are found in the pack",
          E.tool_file(reg["pack_cup"], "pack_cup.gaits.json") == os.path.join(_pack, "pack_cup.gaits.json")
          and reg["pack_cup"].output == "pack_driver"
          and E.driver_path(reg["pack_cup"]) == os.path.join(_pack, "drivers", "pack_driver.py"),
          (reg["pack_cup"].dirs, reg["pack_cup"].output))
    check("... and an engine tool's driver is still the engine's (its drivers/), never a pack's",
          all(E.driver_path(t) in (None, os.path.join(E.HERE, "drivers", f"{t.output}.py"))
              for t in reg.values() if t.dirs[0] == E.REG_DIR), None)
    open(os.path.join(_pack, "suction_cup.yaml"), "w").write(open(os.path.join(E.REG_DIR, "suction_cup.yaml")).read())
    E._REG = None
    try:
        E.registry()
        dup = None
    except E.RegistryError as e:
        dup = str(e)
    check("a pack that redefines an engine tool type is refused, naming both files",
          dup is not None and "defined in both" in dup, dup)
finally:
    if _was is None:
        os.environ.pop("OPENDUBS_SHOWS_DIR", None)
    else:
        os.environ["OPENDUBS_SHOWS_DIR"] = _was
    E._REG = None
    shutil.rmtree(_tmp)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
