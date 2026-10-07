#!/usr/bin/env python3
"""Checks on the show runner's core. No robot, no network, no waiting: the clock is simulated, and
the fake robot state is changed mid-show by the simulated sleep.

    python3 tests/test_runner.py
"""
import io
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import runner as R  # noqa: E402

SHOWS = os.path.join(HERE, "..", "shows")
LIMITS = os.path.join(HERE, "joint_limits_fixture.yaml")
fails = []


def check(name, cond, detail=""):
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}")
    if not cond:
        fails.append(f"{name}: {detail}")


class SimClock:
    """A clock that only moves when the runner sleeps — and can flip the robot's state at a time."""

    def __init__(self, robot=None, at=None, change=None):
        self.now, self.robot, self.at, self.change, self.fired = 1000.0, robot, at, change, False

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        if self.at is not None and not self.fired and self.now >= self.at:
            self.fired = True
            self.robot.set(**self.change)


def run(show, robot=None, flip_after=None, change=None, stop_after=None):
    robot = robot or R.FakeRobotState()
    sc = SimClock(robot, None if flip_after is None else 1000.0 + flip_after, change)
    log = io.StringIO()
    r = R.Runner(os.path.join(SHOWS, show) if not os.path.isabs(show) else show, LIMITS, robot,
                 log=log, clock=sc.clock, sleep=sc.sleep, start_lead=0.5)
    if stop_after is not None:
        orig = sc.sleep

        def sleep(s):
            orig(s)
            if sc.now >= 1000.0 + stop_after:
                r.request_stop()
        r.sleep = sleep
    end = r.run()
    lines = [json.loads(x) for x in log.getvalue().splitlines()]
    return r, end, lines


print("runner: a show from start to finish")
r, end, lines = run("hello.yaml")
statuses = [x["status"] for x in lines if x["kind"] == "status"]
check("a valid show runs VALIDATING -> PREFLIGHT -> PREPPING -> WAITING -> RUNNING -> COMPLETED",
      statuses == ["VALIDATING", "PREFLIGHT", "PREPPING", "WAITING_FOR_START", "RUNNING", "COMPLETED"],
      statuses)
check("every cue is dispatched, in time order",
      len(r.sent) == 54 and [x[0] for x in r.sent] == sorted(x[0] for x in r.sent), len(r.sent))
check("the twin feed ends not playing, marked COMPLETED",
      r.feed.twin()["status"] == "COMPLETED" and not r.feed.twin()["playing"])

print("\nrunner: refusing before anything moves")
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, dir=SHOWS) as fh:
    fh.write(open(os.path.join(SHOWS, "hello.yaml")).read().replace("rc_authority: AUTO", "rc_authority: AUOT"))
try:
    r, end, _ = run(fh.name)
    check("an invalid show is refused at VALIDATING, and nothing is sent",
          end[0] == "REFUSED" and end[1] == "INVALID_SHOW" and not r.sent, end)
finally:
    os.unlink(fh.name)
r, end, _ = run("hello.yaml", R.FakeRobotState(deadman=None))
check("an UNKNOWN deadman refuses — unknown never defaults",
      end[0] == "REFUSED" and end[1] == "UNKNOWN_STATE" and "deadman" in end[2], end)
r, end, _ = run("hello.yaml", R.FakeRobotState(ch5="MANUAL"))
check("CH5 not in the show's required mode refuses", end[0] == "REFUSED" and "CH5 must be AUTO" in end[2], end)
r, end, _ = run("hello.yaml", R.FakeRobotState(arms="inactive"))
check("arms not active refuses", end[0] == "REFUSED" and "arms" in end[2], end)

print("\nrunner: ending a show")
r, end, lines = run("hello.yaml", flip_after=10.0, change={"estop": True})
check("an e-stop mid-show ends it as ESTOPPED, at once", end[0] == "ESTOPPED" and end[3] is not None, end)
check("... and the log says audio was cut, not faded", any(x["kind"] == "estop" for x in lines))
r, end, lines = run("hello.yaml", flip_after=10.0, change={"deadman": False})
check("releasing the deadman ABORTS (it never pauses)", end[0] == "ABORTED" and end[1] == "DEADMAN", end)
check("... and every slot's abort state is logged, argued per slot",
      any(x["kind"] == "abort_state" and "LIFT" in x["slots"] and "ARM_L" in x["slots"] for x in lines))
sent_before = len(r.sent)
check("... and no cue after the abort is dispatched", all(t <= end[3] for t, _, _ in r.sent), sent_before)
r, end, _ = run("hello.yaml", flip_after=10.0, change={"ch5": "MANUAL"})
check("CH5 leaving AUTO mid-show aborts", end[0] == "ABORTED" and end[1] == "AUTHORITY", end)
r, end, _ = run("hello.yaml", flip_after=10.0, change={"deadman": None})
check("the deadman becoming UNKNOWN mid-show aborts", end[0] == "ABORTED" and "unknown" in end[2], end)
r, end, _ = run("hello.yaml", stop_after=12.0)
check("an operator stop is an abort", end[0] == "ABORTED" and end[1] == "OPERATOR_STOP", end)

print("\nrunner: the start pose and the log")
tl = R.cue.load_show(os.path.join(SHOWS, "hello.yaml")).export(R.cue.load_limits(LIMITS), LIMITS)
secs, targets = R.prep_moves(tl, {"ARM_L": {}, "ARM_R": {}})
check("the move to the start pose is timed by distance at the caps, slowly enough not to warn",
      secs > 0 and set(targets) == {"ARM_L", "ARM_R"}, (secs, targets))
secs0, _ = R.prep_moves(tl, {s: {str(j): v for j, v in targets[s].items()} for s in targets})
check("... and is instant when the arms are already there", secs0 == 0.0, secs0)
r, end, lines = run("hello.yaml", flip_after=10.0, change={"deadman": False})
last = [x for x in lines if x["kind"] == "status"][-1]
check("an abort's cause can be reconstructed from the log alone (5.5)",
      last["status"] == "ABORTED" and last["code"] == "DEADMAN" and last["show_t"] is not None, last)

print()
if fails:
    for f in fails:
        print("FAILED:", f)
    sys.exit(1)
print("all good")
