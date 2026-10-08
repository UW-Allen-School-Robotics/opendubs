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

Reviewed by the arms plan (2026-10-08) before its first run. A swing is CANCELLED MID-SWING, before a
trip, at effort over 70% of the joint's limit (its torque trip or its motor rating, whichever is
lower), a measured speed over 80% of its trip, or the arm off the path by more than 5 deg plus the
lag budget. After a swing, the STEADY middle of each leg decides: error over 5 deg, effort over 70%,
or lag over 100 ms fails the speed and ends that joint's ramp. The reversals are reported apart — if
they fail first, that argues for blending the runner's corners rather than a lower cap. No goal
outcome is INCONCLUSIVE, never a pass. The suggested cap is 75% of the speed that failed (or "at
least" 75% of the highest that passed). No speed is ever commanded at or above 80% of a trip.

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
TORQUE_RATED_NM = {1: 40, 2: 40, 3: 27, 4: 27, 5: 7, 6: 7, 7: 7}       # URDF effort ratings
# The effort yardstick: whichever comes first, the trip or what the motor can give. A wrist motor
# saturates at 7 N·m long before its 15 N·m trip (arms plan review, 2026-10-08), so 70% of the trip
# alone could never fire there. (The trip itself compares a MODEL-based torque estimate; effort here
# is the hardware's own report — related, not identical.)
EFFORT_LIMIT_NM = {j: min(TORQUE_TRIP_NM[j], TORQUE_RATED_NM[j]) for j in range(1, 8)}
TRIP_SHARE_MAX = 0.80                        # never command at or above this share of a velocity trip
STOP_EFFORT = 0.70                           # stop a joint's ramp: effort over this share of its trip
STOP_ERROR_DEG = 5.0                         # ... or tracking error over this (after the lag)
STOP_LAG_S = 0.100                           # ... or lag over this: a fifth of a beat at 123 BPM
STOP_VEL_SHARE = 0.80                        # ... or a MEASURED speed over this share of the trip
REVERSAL_WINDOW_S = 0.100                    # either side of a reversal: reported apart from the steady legs
CAP_SHARE = 0.75                             # the suggested cap: this share of the speed that failed
CYCLES = 2                                   # out-and-back swings per speed
POINT_S = 0.02                               # trajectory point spacing (finer than a show's 0.1)
HOLD_S = 0.4                                 # at the base pose before and after each swing
APPROACH_DPS = 20.0                          # moving to and from the base pose: slow
LAG_STEP_S = 0.005
LAG_SEARCH_MAX_S = 0.080                     # well under half a leg at the top speed, so the search
                                             # cannot line up with the reversed swing and hide an error


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
    """samples: [(t from goal start, pos[7], vel[7]|None, eff[7]|None)].

    Tracking error after the best-fitting lag (the controller starts a little after the goal is
    sent), searched only up to LAG_SEARCH_MAX_S. The STEADY middle of each leg and a window of
    REVERSAL_WINDOW_S either side of each reversal are reported apart: caps come from the steady
    part, and a reversal that fails first is a finding of its own (an argument for blending the
    runner's corners, not for a lower cap). Plus overshoot past the swing's ends, peak effort
    against the joint's effort limit, and the measured peak speed against its trip."""
    j = joint - 1
    if len(samples) < 5:
        return {"error": "too few joint samples"}
    span = (knots[1][0], knots[-2][0])                         # the swing, without the holds
    turns = [k[0] for k in knots[2:-2]]                        # the reversals (and the last leg's start)
    legs = [b[0] - a[0] for a, b in zip(knots[1:-1], knots[2:-1]) if b[0] > a[0]]
    lag_max = min(LAG_SEARCH_MAX_S, 0.4 * min(legs)) if legs else LAG_SEARCH_MAX_S

    def err_at(lag, sel=None):
        es = [abs(p[j] - ref_at(knots, t - lag)) for t, p, _, _ in samples
              if span[0] <= t - lag <= span[1] and (sel is None or sel(t - lag))]
        return max(es) if es else 0.0

    best, lag = None, 0.0
    while lag <= lag_max + 1e-9:
        e = err_at(lag)
        if best is None or e < best[1]:
            best = (lag, e)
        lag += LAG_STEP_S
    lag = best[0]
    near = lambda t: any(abs(t - tt) <= REVERSAL_WINDOW_S for tt in turns)
    ends = [k[1] for k in knots]
    lo, hi = min(ends), max(ends)
    over = max(max(p[j] - hi, lo - p[j], 0.0) for _, p, _, _ in samples)

    def peak_eff(sel):
        es = [abs(e[j]) for t, _, _, e in samples if e is not None and sel(t - lag)]
        return round(max(es) / EFFORT_LIMIT_NM[joint], 3) if es else None

    vels = [abs(v[j]) for _, _, v, _ in samples if v is not None]
    return {"lag_ms": round(lag * 1000), "lag_searched_ms": round(lag_max * 1000),
            "steady_error_deg": round(err_at(lag, lambda t: not near(t)), 2),
            "reversal_error_deg": round(err_at(lag, near), 2),
            "steady_effort_share": peak_eff(lambda t: span[0] <= t <= span[1] and not near(t)),
            "reversal_effort_share": peak_eff(near),
            "overshoot_deg": round(over, 2),
            "peak_velocity_dps": round(max(vels)) if vels else None,
            "velocity_share_of_trip": round(max(vels) / cue.TRIP_DEG_S[joint], 3) if vels else None}


def verdict(res: dict, status) -> tuple:
    """(why this speed FAILED or None, a REVERSAL finding or None). Caps come from the steady legs;
    a reversal that fails is reported, not used to set the cap. No goal outcome is INCONCLUSIVE,
    never a pass."""
    if res.get("stopped_live"):
        return f"stopped mid-swing: {res['stopped_live']}", None
    if status == 6:
        return "the controller aborted the goal (a trip)", None
    if status is None:
        return "INCONCLUSIVE: no goal outcome arrived", None
    if status != 4:
        return f"the goal ended with status {status}", None
    if res.get("error"):
        return res["error"], None
    fail = None
    if res["steady_effort_share"] is not None and res["steady_effort_share"] > STOP_EFFORT:
        fail = f"steady effort {res['steady_effort_share']:.0%} of the joint's limit"
    elif res["steady_error_deg"] > STOP_ERROR_DEG:
        fail = f"steady tracking error {res['steady_error_deg']:.1f} deg"
    elif res["lag_ms"] > STOP_LAG_S * 1000:
        fail = f"lag {res['lag_ms']} ms"
    elif res["velocity_share_of_trip"] is not None and res["velocity_share_of_trip"] > STOP_VEL_SHARE:
        fail = f"measured speed {res['peak_velocity_dps']} deg/s, over {STOP_VEL_SHARE:.0%} of the trip"
    rev = None
    if res["reversal_effort_share"] is not None and res["reversal_effort_share"] > STOP_EFFORT:
        rev = f"at the reversals: effort {res['reversal_effort_share']:.0%} of the limit"
    elif res["reversal_error_deg"] > STOP_ERROR_DEG:
        rev = f"at the reversals: tracking error {res['reversal_error_deg']:.1f} deg"
    return fail, rev


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
    j, v = r["joint"] - 1, r["speed"]
    # Live, so a swing heading for a trip is cancelled BEFORE it (review, 2026-10-08). The error here
    # is raw — no lag fitted yet — so it is allowed the lag budget on top: v x STOP_LAG_S.
    raw_allow = STOP_ERROR_DEG + v * STOP_LAG_S
    while True:
        now = time.monotonic() - t0
        s = arms.sample(side)
        if s and s[0] != last_stamp:
            last_stamp = s[0]
            ts = s[0] - t0
            samples.append((ts, s[1], s[2], s[3]))
            live = None
            if s[3] is not None and abs(s[3][j]) > STOP_EFFORT * EFFORT_LIMIT_NM[r["joint"]]:
                live = f"effort {abs(s[3][j]):.1f} N·m, over {STOP_EFFORT:.0%} of {EFFORT_LIMIT_NM[r['joint']]}"
            elif s[2] is not None and abs(s[2][j]) > STOP_VEL_SHARE * cue.TRIP_DEG_S[r["joint"]]:
                live = f"speed {abs(s[2][j]):.0f} deg/s, over {STOP_VEL_SHARE:.0%} of the trip"
            elif r["knots"][1][0] <= ts <= r["knots"][-2][0] + STOP_LAG_S and \
                    abs(s[1][j] - ref_at(r["knots"], ts)) > raw_allow:
                live = f"off the path by {abs(s[1][j] - ref_at(r['knots'], ts)):.1f} deg"
            if live:
                arms.cancel_all()
                res = analyse(samples, r["knots"], r["joint"])
                res.update(stopped_live=live, status=arms.results.get(slot), at_s=round(ts, 3))
                return res, samples
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
            fail, rev = verdict(res, res.get("status"))
            print(f"  J{j} {r['speed']:5.0f} deg/s: lag {res.get('lag_ms')} ms, steady error "
                  f"{res.get('steady_error_deg')} deg / effort {res.get('steady_effort_share')}, reversals "
                  f"{res.get('reversal_error_deg')} deg / {res.get('reversal_effort_share')}, overshoot "
                  f"{res.get('overshoot_deg')} deg, peak {res.get('peak_velocity_dps')} deg/s"
                  + (f"  -> FAIL: {fail}" if fail else "  -> ok") + (f"  [{rev}]" if rev else ""))
            s = summary.setdefault(j, {"passed": None, "failed": None, "why": None, "reversals": None})
            if rev and not s["reversals"]:
                s["reversals"] = f"{r['speed']:g} deg/s: {rev}"
            if fail:
                s["why"] = fail
                if not fail.startswith("INCONCLUSIVE"):
                    s["failed"] = r["speed"]
                # A live stop or a failure leaves the arm part-way: back to the base pose, slowly.
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
        if s["reversals"]:
            print(f"         reversals limit it first — {s['reversals']}: an argument for blending the "
                  "runner's corners, not for a lower cap")
    rec(kind="summary", summary=summary, stopped=stopped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
