#!/usr/bin/env python3
"""The self-collision sweep (sweep.py) and its stamp — the robot gate (cue.py _check_sweep).
The gate's rules run anywhere; the sweep needs MuJoCo and skips, saying so, without it."""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "composer"))

import cue  # noqa: E402

LIMITS_PATH = os.path.join(HERE, "joint_limits_fixture.yaml")
LIMITS = cue.load_limits(LIMITS_PATH)
FAILS = []


def check(what, ok, detail=""):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(f"{what}: {detail}")


HEAD = "id: {id}\nslots: [ARM_L, ARM_R, FACE]\nrequires: { arms: active, tab5: true, rc_authority: AUTO }\ncues:\n"
CLEAR = HEAD + ("  - { t: 2.0, slot: ARM_L, joints: { 1: 25, 2: 15, 4: 60 } }\n"
                "  - { t: 2.0, slot: ARM_R, joints: { 1: 25, 2: 15, 4: 60 } }\n"
                "  - { t: 3.0, slot: FACE, do: emote, name: SOFT }\n")
# the arms' state at a real collision an agent-written show reached (-48 mm): upper arms rolled
# inward (J3), elbows folded, the left gripper open — the right fingers inside the left forearm
COLLIDE = HEAD + ("  - { t: 3.0, slot: ARM_L, joints: { 1: 35.4, 2: 8.3, 3: -26.7, 4: 94.5, 5: 70.7, 6: -30.1 } }\n"
                  "  - { t: 3.0, slot: ARM_R, joints: { 1: 35.4, 2: 8.3, 3: -65.6, 4: 94.5, 5: 14.9, 6: -8.2 } }\n"
                  "  - { t: 3.0, slot: GRIP_L, do: open }\n").replace("[ARM_L, ARM_R, FACE]", "[ARM_L, ARM_R, GRIP_L, FACE]")


def analyse(text, required=True):
    show = cue.show_from_text(text, "x")
    show.require_sweep = required
    return show.analyse(LIMITS)


print("sweep: the robot gate (no MuJoCo needed)")
a = analyse(CLEAR.replace("{id}", "c"))
check("an arm show with no collision stamp is refused where it meets the robot, saying how to sweep it",
      any("not been swept" in q.msg for q in a["problems"]), [q.msg for q in a["problems"]])
check("... and is fine for desk work that does not require it (--no-sweep, other tests)",
      not analyse(CLEAR.replace("{id}", "c"), required=False)["problems"])
no_arms = "id: f\nslots: [FACE]\nrequires: { tab5: true }\ncues:\n  - { t: 1.0, slot: FACE, do: emote, name: SOFT }\n"
check("a show that moves no arm needs no stamp", not analyse(no_arms)["problems"], analyse(no_arms)["problems"])
h = cue.motion_hash(analyse(CLEAR.replace("{id}", "c"), required=False)["events"])
stamped = CLEAR.replace("{id}", "c").replace("cues:", f'clearance: {{ min_mm: 41.0, at_s: 0.0, checked_for: "{h}@test" }}\ncues:')
check("a show stamped for its motion as it is, at >= 30 mm, passes", not analyse(stamped)["problems"],
      [q.msg for q in analyse(stamped)["problems"]])
face_edit = stamped.replace("name: SOFT", "name: ALERT")
check("changing a face (not motion) keeps the stamp valid", not analyse(face_edit)["problems"])
arm_edit = stamped.replace("4: 60 } }\n  - { t: 2.0, slot: ARM_R", "4: 70 } }\n  - { t: 2.0, slot: ARM_R")
check("changing any arm motion makes the stamp stale: refused until swept again",
      any("motion changed" in q.msg for q in analyse(arm_edit)["problems"]), [q.msg for q in analyse(arm_edit)["problems"]])
too_close = stamped.replace("min_mm: 41.0", "min_mm: 12.0")
check("a stamp under 30 mm is refused, saying where and how close",
      any("within 12 mm" in q.msg for q in analyse(too_close)["problems"]), [q.msg for q in analyse(too_close)["problems"]])

w = analyse(CLEAR.replace("{id}", "c"), required="warn")
check("in the composer while editing, a missing stamp is a WARNING (saving sweeps it), the show still valid",
      not w["problems"] and any("sweeps it when you save" in q.msg for q in w["warnings"]), [q.msg for q in w["warnings"]])
w = analyse(too_close, required="warn")
check("... but a measured collision is a problem even there", any("within 12 mm" in q.msg for q in w["problems"]))


# can_skip variants: a variant that changes the motion needs its OWN stamp; one that does not (a
# light) is covered by the show's
VAR = (CLEAR.replace("{id}", "v").replace("[ARM_L, ARM_R, FACE]", "[ARM_L, ARM_R, GRIP_L, FACE, LIGHT]")
       .replace("cues:", "can_skip: [grip_left, light]\ncues:")
       + "  - { t: 3.0, slot: GRIP_L, do: open }\n  - { t: 3.5, slot: LIGHT, do: color, rgb: [0, 0, 255] }\n")


def variant(text, skip, required=True):
    show = cue.show_from_text(text, "x")
    show.require_sweep, show.skip = required, frozenset(skip)
    return show.analyse(LIMITS)


hv = cue.motion_hash(variant(VAR, (), required=False)["events"])
vstamped = VAR.replace("cues:", f'clearance: {{ min_mm: 41.0, at_s: 0.0, checked_for: "{hv}@test" }}\ncues:')
check("a variant without a part that MOVES (a gripper) is refused until its own stamp is there",
      any("without grip_left has not been swept" in q.msg for q in variant(vstamped, ["grip_left"])["problems"]),
      [q.msg for q in variant(vstamped, ["grip_left"])["problems"]])
check("... a variant without the light is covered by the show's own stamp (the motion is the same)",
      not variant(vstamped, ["light"])["problems"], [q.msg for q in variant(vstamped, ["light"])["problems"]])
check("... and a run may skip only what the show lists in can_skip:",
      any("does not list in can_skip" in q.msg for q in variant(vstamped, ["arm_right"])["problems"]))

print("\nsweep: on the twin (MuJoCo)")
try:
    import mujoco  # noqa: F401
except ImportError:
    print("  skip  MuJoCo is not installed here")
else:
    import yaml
    import sweep
    import clearance
    import scene
    _m = scene.build(None)
    _n = lambda g: mujoco.mj_id2name(_m, mujoco.mjtObj.mjOBJ_GEOM, g)  # noqa: E731
    _r, _l, _b = clearance._geom_sets(_m)
    _pairs = {(_n(a), _n(b)) for a, b in clearance.pairs_of_interest(_m)}
    _mount = {_n(g) for g in clearance._mounts(_m, _r + _l)}
    check("an arm's fixed mount (link0) is not measured against the structure, which no joint moves it "
          "relative to, but is against the other arm's moving links",
          len(_mount) == 2 and not any(a in _mount and b in {_n(g) for g in _b} for a, b in _pairs)
          and ("oa_openarm_right_link0_collision", "oa_openarm_left_link4_collision") in _pairs, _mount)
    tmp = tempfile.mkdtemp()
    try:
        bad, good = os.path.join(tmp, "bad.yaml"), os.path.join(tmp, "good.yaml")
        open(bad, "w").write(COLLIDE.replace("{id}", "bad"))
        open(good, "w").write(CLEAR.replace("{id}", "good"))
        r = sweep.run(bad, LIMITS_PATH, write=True)
        check("the sweep finds the arms inside each other (the real case: under 0 mm)",
              r["min_mm"] < 0 and "finger" in " ".join(r["pair"]), r)
        a = cue.load_show(bad)
        a.require_sweep = True
        probs = [q.msg for q in a.analyse(LIMITS)["problems"]]
        check("... stamps it, and the validator REFUSES it with the parts and the time",
              any("comes within" in p and "at least 30 mm" in p for p in probs), probs)
        r = sweep.run(good, LIMITS_PATH, write=True)
        g = cue.load_show(good)
        g.require_sweep = True
        check("a clear show is swept, stamped, and passes", r["min_mm"] >= sweep.THRESHOLD_MM
              and not g.analyse(LIMITS)["problems"] and "clearance" in yaml.safe_load(open(good)), r)
        r2 = sweep.run(good, LIMITS_PATH, write=True)
        check("sweeping again with the motion unchanged is skipped (its stamp stands)", r2.get("current") is True)
        vp = os.path.join(tmp, "v.yaml")
        open(vp, "w").write(VAR)
        rv = sweep.run(vp, LIMITS_PATH, write=True)
        sv = cue.load_show(vp)
        sv.require_sweep, sv.skip = True, frozenset(["grip_left"])
        check("stamping sweeps each can_skip variant that moves, and that variant then passes the robot gate",
              list(rv["variants"]) == ["grip_left"] and "grip_left" in yaml.safe_load(open(vp))["clearance"]["variants"]
              and not sv.analyse(LIMITS)["problems"], (rv.get("variants"), [q.msg for q in sv.analyse(LIMITS)["problems"]]))
        # the composer: a save sweeps and stamps; the runner: refuses an arm show without a stamp
        from api import Composer
        comp = Composer(tmp, LIMITS_PATH)
        text = CLEAR.replace("{id}", "good2")
        open(os.path.join(tmp, "good2.yaml"), "w").write(text)
        out = comp.save("good2.yaml", text)
        check("the composer sweeps on save and says so, and the saved show passes the robot gate",
              "swept" in out.get("sweep", "") and out["timeline"]["valid"], (out.get("sweep"), out["timeline"]["problems"]))
        import runner as R
        open(os.path.join(tmp, "nostamp.yaml"), "w").write(CLEAR.replace("{id}", "nostamp"))
        end = R.Runner(os.path.join(tmp, "nostamp.yaml"), LIMITS_PATH, R.FakeRobotState(), log=io.StringIO()).run()
        check("the runner refuses an arm show with no collision stamp", end[0] == "REFUSED" and "swept" in end[2], end)
    finally:
        shutil.rmtree(tmp)

print()
if FAILS:
    for f in FAILS:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
