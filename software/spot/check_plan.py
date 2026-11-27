"""Before a plan goes to Spot: build every window at every speed exactly as the player sends it, and say
what a real Spot would refuse. No robot, no player, a second or two.

    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' python software/spot/check_plan.py [--walking] [PLAN.json ...]
    # no arguments: every plan in ~/.opendubs-spot/plans. --walking: against the walking envelope, as
    # the player started with --walking holds it, from the fake Spot's starting pose

Each window is built at the moment the player would send it (lead_s before it starts), carrying the
previous window's points as the player does, then checked by the first-run envelope and by
command_problems(): point counts, strictly increasing times, the clock of every reference time, and
jaw angles. It exits 1 if any plan at any speed would be refused.
"""
import glob
import json
import os
import sys

from fake_spot import START
from player import SPEEDS, at_speed, windows
from spot_playback import build_window, command_problems, envelope_problems, link_windows, use_envelope

args = sys.argv[1:]
if "--walking" in args:
    args.remove("--walking")
    use_envelope("walking")
paths = args or sorted(glob.glob(os.path.expanduser("~/.opendubs-spot/plans/*.json")))
bad = 0
for path in paths:
    plan = json.load(open(path))
    name = os.path.basename(path)
    if plan.get("robot") != "spot":
        print(f"{name}: skipped, for robot {plan.get('robot')!r}")
        continue
    lead = float(plan.get("lead_s", 1.0))
    for speed in SPEEDS:
        p = link_windows(at_speed(plan, speed))
        problems = [f"envelope: {e}" for e in envelope_problems(p)]
        t0, carry, counts = 1.8e9, {}, []
        pose = START if any(c["part"] == "BASE" for c in p["commands"]) else None
        for start, cs in windows(p):
            now = t0 + start - lead
            try:
                cmd = build_window(cs, t0, now, carry, pose)
            except Exception as e:
                problems.append(f"window {cs[0]['window']}: building failed: {e}")
                break
            problems += [f"window {cs[0]['window']}: {q}" for q in command_problems(cmd, t0, now, pose)]
            counts.append("/".join(str(len(cs_.points)) for cs_ in (
                cmd.synchronized_command.arm_command.arm_joint_move_command.trajectory,
                cmd.synchronized_command.gripper_command.claw_gripper_command.trajectory)))
        verdict = "OK" if not problems else "REFUSED"
        bad += bool(problems)
        print(f"{name} at {speed:.0%}: {verdict}, {len(counts)} windows (arm/jaw points {', '.join(counts)})")
        for q in problems[:6]:
            print(f"    {q}")
sys.exit(1 if bad else 0)
