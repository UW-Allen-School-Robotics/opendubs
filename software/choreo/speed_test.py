#!/usr/bin/env python3
"""The arm speed test: how fast each joint can really go on the show path (choreography plan 6.1, T4).

Designed with the arms plan on 2026-10-08. The show speed caps (cue.SPEED_CAP_DEG_S) came from an
old demo script with no source; this measures what the arms actually do, so the caps can be set
from evidence. It drives the arms the way a show does — one FollowJointTrajectory goal per run,
through arm_output.RosArms — so it tests the path shows use, not some other one.

One joint at a time, on one arm, from a raised pose well clear of the head (Celebrate's: shoulder
30, elbow 60, the rest at zero), it swings that joint +-AMPLITUDE at rising speeds, at constant
speed between the ends — the way a show moves — and logs what the joint really did:

  * tracking error against the commanded path (after the best-fitting lag, which is reported too)
  * overshoot past the ends
  * peak effort as a share of the joint's torque trip, and the goal's own outcome (a trip aborts it)

A joint's ramp STOPS at the first of: the goal aborted (a controller trip), effort over 70% of its
trip, tracking error over 5 deg. Its suggested cap is 75% of the speed that failed (or "at least"
75% of the highest that passed). No speed is ever commanded at or above 80% of a velocity trip.

It runs only while the robot allows a show: CH5 in AUTO, the deadman held, no e-stop, the arms
active, and no show running. Any of those lost mid-swing cancels the goal at once — the arm holds
where it is — and the test stops. A person must be at the robot: the deadman is the go.

    python3 speed_test.py --dry-run                      # the plan, checked against the limits; sends nothing
    python3 speed_test.py --arm right --joints 7,6,5     # the wrist first
    python3 speed_test.py --arm right                    # every joint, wrist to shoulder

On the Thor, as the runner's user with ROS sourced, the same environment as opendubs-runner:
    sudo -u kiosk env RMW_IMPLEMENTATION=rmw_fastrtps_cpp OPENDUBS_ARM_LIMITS=<limits> \\
        bash -c 'source /opt/ros/jazzy/setup.bash && python3 /opt/opendubs/choreo/speed_test.py ...'
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cue  # noqa: E402

BASE = {1: 0.0, 2: 30.0, 3: 0.0, 4: 60.0, 5: 0.0, 6: 0.0, 7: 0.0}   # outward sense, as a show writes it
AMPLITUDE_DEG = 20.0
SPEEDS = (30, 60, 120, 180, 240)
JOINT_ORDER = (7, 6, 5, 4, 3, 2, 1)          # the light wrist first, the weight-bearing shoulder last
TORQUE_TRIP_NM = {1: 45, 2: 45, 3: 25, 4: 20, 5: 15, 6: 15, 7: 15}   # the arm controller's config
TRIP_SHARE_MAX = 0.80                        # never command at or above this share of a velocity trip
STOP_EFFORT = 0.70                           # stop a joint's ramp: effort over this share of its trip
STOP_ERROR_DEG = 5.0                         # ... or tracking error over this
CAP_SHARE = 0.75                             # the suggested cap: this share of the speed that failed
CYCLES = 2                                   # out-and-back swings per speed
POINT_S = 0.02                               # trajectory point spacing (finer than a show's 0.1)
HOLD_S = 0.4                                 # at the base pose before and after each swing
APPROACH_DPS = 20.0                          # moving to and from the base pose: slow
LAG_SEARCH_S = (0.0, 0.25, 0.005)


def signed(arm: str, outward: dict) -> list:
    """Outward degrees (as a show writes them) -> the controller's sense for this arm."""
    m = -1 if arm == "left" else 1
    return [outward[j] * (m if j in cue.MIRRORED_JOINTS else 1) for j in range(1, 8)]


def swing(arm: str, joint: int, speed: float, amp: float = AMPLITUDE_DEG) -> list:
    """[(seconds from goal start, [7 signed deg])]: hold at base, swing the joint +-amp CYCLES times at
    constant `speed`, return to base, hold. Points every POINT_S, so the path is the straight line."""
    base = signed(arm, BASE)
    j = joint - 1
    sgn = -1 if (arm == "left" and joint in cue.MIRRORED_JOINTS) else 1
    ends = [base[j] + sgn * amp]
    for _ in range(CYCLES):
        ends += [base[j] - sgn * amp, base[j] + sgn * amp]
    ends[-1] = base[j]                       # the last leg comes home
    knots = [(0.0, base[j]), (HOLD_S, base[j])]
    t, q = HOLD_S, base[j]
    for e in ends:
        t += abs(e - q) / speed
        knots.append((t, e))
        q = e
    knots.append((t + HOLD_S, base[j]))
    pts, k = [], POINT_S
    end_t = knots[-1][0]
    while k < end_t - 1e-9:
        pose = list(base)
        pose[j] = ref_at(knots, k)
        pts.append((round(k, 3), pose))
        k += POINT_S
    pose = list(base)
    pose[j] = base[j]
    pts.append((round(end_t, 3), pose))
    return pts, knots


def ref_at(knots, t):
    if t <= knots[0][0]:
        return knots[0][1]
    for (t0, a), (t1, b) in zip(knots, knots[1:]):
        if t <= t1:
            return a if t1 <= t0 else a + (b - a) * (t - t0) / (t1 - t0)
    return knots[-1][1]


def plan(arm: str, joints, speeds, limits: dict) -> tuple:
    """Every run, checked: (runs, problems). A problem means the test must not run as planned."""
    runs, problems = [], []
    side = arm
    for j in joints:
        name = f"openarm_{side}_joint{j}"
        lo, hi = limits.get(name, (None, None))
        base = signed(arm, BASE)[j - 1]
        if lo is None:
            problems.append(f"J{j}: no joint limits known for {name}")
            continue
        lo_d, hi_d = math.degrees(lo) + cue.MARGIN_DEG, math.degrees(hi) - cue.MARGIN_DEG
        if not (lo_d <= base - AMPLITUDE_DEG and base + AMPLITUDE_DEG <= hi_d):
            problems.append(f"J{j}: the swing {base - AMPLITUDE_DEG:+.0f}..{base + AMPLITUDE_DEG:+.0f} deg "
                            f"leaves the usable range [{lo_d:+.0f}, {hi_d:+.0f}]")
            continue
        for v in speeds:
            if v >= TRIP_SHARE_MAX * cue.TRIP_DEG_S[j]:
                continue                     # never near a velocity trip
            pts, knots = swing(arm, j, v)
            runs.append({"joint": j, "speed": v, "points": pts, "knots": knots,
                         "duration_s": pts[-1][0]})
    return runs, problems


def analyse(samples, knots, joint: int) -> dict:
    """samples: [(t from goal start, pos[7], vel[7]|None, eff[7]|None)]. Tracking error after the
    best-fitting lag (the controller starts a little after the goal is sent); overshoot past the
    swing's ends; peak effort as a share of the torque trip."""
    j = joint - 1
    if len(samples) < 5:
        return {"error": "too few joint samples"}
    lo_lag, hi_lag, step = LAG_SEARCH_S
    best = None
    lag = lo_lag
    while lag <= hi_lag + 1e-9:
        err = max(abs(p[j] - ref_at(knots, t - lag)) for t, p, _, _ in samples
                  if knots[1][0] <= t - lag <= knots[-2][0])
        if best is None or err < best[1]:
            best = (lag, err)
        lag += step
    ends = [k[1] for k in knots]
    lo, hi = min(ends), max(ends)
    over = max(max(p[j] - hi, lo - p[j], 0.0) for _, p, _, _ in samples)
    effs = [abs(e[j]) for _, _, _, e in samples if e is not None]
    vels = [abs(v[j]) for _, _, v, _ in samples if v is not None]
    return {"lag_ms": round(best[0] * 1000), "max_error_deg": round(best[1], 2),
            "overshoot_deg": round(over, 2),
            "peak_effort_nm": round(max(effs), 2) if effs else None,
            "effort_share": round(max(effs) / TORQUE_TRIP_NM[joint], 3) if effs else None,
            "peak_velocity_dps": round(max(vels)) if vels else None}


def verdict(res: dict, status) -> str | None:
    """Why this speed FAILED, or None if it passed."""
    if status == 6:
        return "the controller aborted the goal (a trip)"
    if status not in (4, None):
        return f"the goal ended with status {status}"
    if res.get("error"):
        return res["error"]
    if res["effort_share"] is not None and res["effort_share"] > STOP_EFFORT:
        return f"effort {res['effort_share']:.0%} of the torque trip"
    if res["max_error_deg"] > STOP_ERROR_DEG:
        return f"tracking error {res['max_error_deg']:.1f} deg"
    return None


# ---- on the robot -------------------------------------------------------------------------------
class Gate:
    """What a show needs, read from the dashboard (read-only) — the same as the runner's preflight."""

    def __init__(self, robot_url, palette_url):
        self.robot_url, self.palette_url = robot_url, palette_url

    def _get(self, url, timeout=2.0):
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read())

    def why_not(self, probe=False) -> str | None:
        try:
            st = self._get(self.robot_url + ("?probe=1" if probe else ""), timeout=25.0 if probe else 1.0)
        except Exception as e:  # noqa: BLE001
            return f"robot state unreadable ({type(e).__name__})"
        if st.get("estop") is not False:
            return "e-stop asserted (or unknown)"
        if st.get("ch5") != "AUTO":
            return f"CH5 is {st.get('ch5')!r}, not AUTO"
        if st.get("deadman") is not True:
            return "the deadman is not held"
        if probe and st.get("arms") != "active":
            return f"the arms are {st.get('arms')!r}, not active"
        try:
            if self._get(self.palette_url).get("running"):
                return "a show is running"
        except Exception:  # noqa: BLE001  no runner: no show can be running
            pass
        return None


def move_slowly(arms, side, slot, target):
    s = arms.sample(side)
    if s is None:
        raise RuntimeError("the arm's position is unknown (/joint_states)")
    now = s[1]
    secs = max(0.5, max(abs(a - b) for a, b in zip(now, target)) / APPROACH_DPS)
    n = max(1, int(secs / 0.1))
    pts = [(round(secs * (i + 1) / n, 3), [a + (b - a) * (i + 1) / n for a, b in zip(now, target)])
           for i in range(n)]
    arms.send(slot, pts)
    return secs


def run_one(arms, gate, side, slot, r, log):
    arms.send(slot, r["points"])
    t0 = time.monotonic()
    samples, last_stamp, next_gate = [], None, 0.0
    while True:
        now = time.monotonic() - t0
        s = arms.sample(side)
        if s and s[0] != last_stamp:
            last_stamp = s[0]
            samples.append((s[0] - t0, s[1], s[2], s[3]))
        if now >= next_gate:
            why = gate.why_not()
            if why:
                arms.cancel_all()
                return {"stopped": why}, samples
            next_gate = now + 0.05
        status = arms.results.get(slot)
        if status is not None or now > r["duration_s"] + 3.0:
            break
        time.sleep(0.005)
    res = analyse(samples, r["knots"], r["joint"])
    res["status"] = arms.results.get(slot)
    return res, samples


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arm", choices=("right", "left"), default="right")
    ap.add_argument("--joints", default=",".join(map(str, JOINT_ORDER)))
    ap.add_argument("--speeds", default=",".join(map(str, SPEEDS)))
    ap.add_argument("--limits", default=os.environ.get("OPENDUBS_ARM_LIMITS", cue.DEFAULT_LIMITS))
    ap.add_argument("--dry-run", action="store_true", help="print the plan and check it; send nothing")
    ap.add_argument("--robot", default="http://127.0.0.1:8080/api/choreo/robot")
    ap.add_argument("--palette", default="http://127.0.0.1:8092/api/palette")
    ap.add_argument("--log", default=f"/var/log/opendubs-runner/speed-test-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    a = ap.parse_args()

    joints = [int(x) for x in a.joints.split(",") if x]
    speeds = [float(x) for x in a.speeds.split(",") if x]
    runs, problems = plan(a.arm, joints, speeds, cue.load_limits(a.limits))
    print(f"speed test: {a.arm} arm, joints {joints}, speeds {speeds} deg/s, +-{AMPLITUDE_DEG:.0f} deg "
          f"about shoulder {BASE[2]:.0f} / elbow {BASE[4]:.0f}")
    for p in problems:
        print("  PROBLEM:", p)
    total = sum(r["duration_s"] + 2.0 for r in runs)
    for j in joints:
        vs = [r["speed"] for r in runs if r["joint"] == j]
        skipped = [v for v in speeds if v not in vs]
        print(f"  J{j}: {', '.join(f'{v:g}' for v in vs) or '(none)'} deg/s"
              + (f"  (skipped, at or over {TRIP_SHARE_MAX:.0%} of its {cue.TRIP_DEG_S[j]} deg/s trip: "
                 f"{', '.join(f'{v:g}' for v in skipped)})" if skipped else ""))
    print(f"  at most {len(runs)} swings, about {total / 60:.1f} min of motion")
    if problems:
        return 2
    if a.dry_run:
        return 0

    import arm_output
    gate = Gate(a.robot, a.palette)
    why = gate.why_not(probe=True)
    if why:
        print(f"not starting: {why}")
        return 1
    arms = arm_output.RosArms(node_name="opendubs_speed_test")
    time.sleep(1.0)
    slot = "ARM_R" if a.arm == "right" else "ARM_L"
    log = open(a.log, "a", encoding="utf-8")

    def rec(**kw):
        log.write(json.dumps({"wall": round(time.time(), 3), **kw}) + "\n")
        log.flush()

    rec(kind="start", arm=a.arm, joints=joints, speeds=speeds, base=BASE, amplitude=AMPLITUDE_DEG)
    print(f"moving to the base pose (slowly)… log: {a.log}")
    time.sleep(move_slowly(arms, a.arm, slot, signed(a.arm, BASE)) + 0.5)
    summary, stopped = {}, None
    for j in joints:
        for r in [x for x in runs if x["joint"] == j]:
            why = gate.why_not()
            if why:
                stopped = why
                break
            res, samples = run_one(arms, gate, a.arm, slot, r, log)
            rec(kind="run", joint=j, speed=r["speed"], result=res,
                samples=[[round(t, 4), round(p[j - 1], 3)] for t, p, _, _ in samples])
            if res.get("stopped"):
                stopped = res["stopped"]
                break
            fail = verdict(res, res.get("status"))
            print(f"  J{j} {r['speed']:5.0f} deg/s: lag {res.get('lag_ms')} ms, error {res.get('max_error_deg')} deg, "
                  f"overshoot {res.get('overshoot_deg')} deg, effort {res.get('effort_share')}"
                  + (f"  -> FAIL: {fail}" if fail else "  -> ok"))
            s = summary.setdefault(j, {"passed": None, "failed": None, "why": None})
            if fail:
                s["failed"], s["why"] = r["speed"], fail
                time.sleep(move_slowly(arms, a.arm, slot, signed(a.arm, BASE)) + 0.3)
                break
            s["passed"] = r["speed"]
            time.sleep(0.5)
        if stopped:
            break
    if stopped:
        print(f"STOPPED: {stopped} — the arm holds where it is; nothing more is sent.")
        rec(kind="stopped", why=stopped)
    else:
        print("returning to rest (slowly)…")
        time.sleep(move_slowly(arms, a.arm, slot, [0.0] * 7) + 0.5)
    print("\njoint  passed  failed  suggested cap   (today's cap)")
    for j in joints:
        s = summary.get(j)
        if not s:
            continue
        cap = (f"{CAP_SHARE * s['failed']:.0f}" if s["failed"]
               else f">= {CAP_SHARE * s['passed']:.0f}" if s["passed"] else "—")
        print(f"  J{j}   {s['passed'] or '—':>6}  {s['failed'] or '—':>6}  {cap:>13}   ({cue.SPEED_CAP_DEG_S[j]})"
              + (f"   {s['why']}" if s["why"] else ""))
    rec(kind="summary", summary=summary, stopped=stopped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
