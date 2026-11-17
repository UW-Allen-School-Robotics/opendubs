"""The changes of controller (arm_handover.py): the order of the steps, what each refusal leaves in charge, and the rollbacks. A fake robot records every command; no robot,
no ROS, no root. Run by hand (the backend's tests are not in CI): python3 -m pytest tests/test_arm_handover.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import arm_handover as h  # noqa: E402

GATES_OK = {"estop": False, "deadman": True, "ch5": "MANUAL"}


class Robot:
    """Which stack runs, where the arms are, and every command sent, in order (`log`). `fail` names a command that fails: "gafro park", "gafro start right",
    "ros activate", "senders after stop", ..."""

    def __init__(self, ros=False, gafro=(), at_stow=True, gates=None, fail=(), supervised=True, show=None, teleop=False):
        self.ros_up, self.gafro, self.stow = ros, set(gafro), {"left": at_stow, "right": at_stow}
        self.gates_, self.fail, self.supervised, self.show, self.teleop = dict(GATES_OK if gates is None else gates), set(fail), supervised, show, teleop
        self.log, self.foreign = [], False

    # what the stack is
    def controller(self):
        return "conflict" if self.ros_up and self.gafro else "ros2" if self.ros_up else "gafro" if self.gafro else "none"

    def stack_why(self):
        return "two senders on one bus"

    def gates(self):
        return self.gates_

    def show_running(self):
        return self.show

    def teleop_running(self):
        return self.teleop

    # gafro
    def gafro_sides(self):
        return sorted(self.gafro)

    def gafro_supervised(self, side):
        return self.supervised

    def gafro_park(self, side):
        self.log.append(f"gafro park {side}")
        if "gafro park" in self.fail:
            return "refused: CH6 (the deadman) is released"
        self.stow[side] = True
        return None

    def gafro_hold(self, side):
        self.log.append(f"gafro hold {side}")

    def unit(self, verb, side):
        self.log.append(f"unit {verb} {side}")
        if verb == "stop":
            self.gafro.discard(side)
            if "senders after stop" in self.fail:
                self.foreign = True
        return None

    def start_gafro(self, side):
        self.log.append(f"gafro start {side}")
        if f"gafro start {side}" in self.fail:
            return "", f"{side}: the unit ended before it answered"
        self.gafro.add(side)
        return f"{side}: started", None

    # the ROS 2 stack
    def ros(self, *args, timeout=180):
        word = " ".join(args)
        self.log.append(f"ros {word}")
        if f"ros {args[0]}" in self.fail:
            return 1, f"{args[0]} failed"
        if args[0] == "activate":
            self.ros_up = True
        elif args[0] == "down":
            self.ros_up = False
        elif args[0] == "park":
            self.stow = {"left": True, "right": True}
        return 0, ""

    def ros_running(self):
        return self.ros_up

    # the buses
    def at_stow(self, side):
        return self.stow[side]

    def limp(self, side):
        return self.limp_

    limp_ = False

    def senders(self, side):
        self.log.append(f"listen {side}")
        return (False, "motor 1 (MIT): 90 frames") if self.foreign else (True, "quiet")

    def disable(self, side):
        self.log.append(f"disable {side}")
        return True, "enabled=0"


def change(robot, kind, target=None):
    c = h.Change(robot, f"{kind} {target}")
    out, err = h.run(robot, kind, target, c)
    return c, out, err


def test_gafro_to_ros2_parks_stops_listens_disables_then_activates():
    r = Robot(gafro=("left", "right"), at_stow=False)
    c, out, err = change(r, "handover", "ros2")
    assert err is None and r.controller() == "ros2", (err, r.log)
    assert r.log == ["gafro park left", "gafro park right", "unit stop left", "unit stop right", "listen left", "listen right",
                     "disable left", "disable right", "ros activate both"], r.log
    assert [s["state"] for s in c.steps] == ["done"] * 5
    assert "MOVE to stow" in c.steps[0]["arms"] and "limp at stow" in c.steps[3]["arms"], c.steps


def test_no_deadman_moves_and_stops_nothing():
    for gates in ({**GATES_OK, "deadman": False}, {**GATES_OK, "estop": True}, {**GATES_OK, "ch5": "STANDBY"}, {}):
        r = Robot(gafro=("left", "right"), gates=gates)
        c, out, err = change(r, "handover", "ros2")
        assert err and "Nothing was moved or stopped" in err and r.log == [] and r.controller() == "gafro", (gates, err, r.log)


def test_a_park_gafro_refuses_leaves_gafro_holding_and_nothing_stopped():
    r = Robot(gafro=("left", "right"), at_stow=False, fail={"gafro park"})
    c, out, err = change(r, "handover", "ros2")
    assert err and "still has them" in err and r.controller() == "gafro", err
    assert r.log == ["gafro park left", "gafro hold left", "gafro hold right"], r.log
    assert [s["state"] for s in c.steps] == ["failed"] + ["skipped"] * 4


def test_ros2_to_gafro_that_gafro_cannot_take_rolls_back_to_ros2():
    r = Robot(ros=True, at_stow=False, fail={"gafro start right"})
    c, out, err = change(r, "handover", "gafro")
    assert err and r.controller() == "ros2", (err, r.log)
    assert r.log[:5] == ["ros park both", "ros down", "listen left", "listen right", "gafro start left"], r.log
    assert r.log[5:] == ["gafro start right", "unit stop left", "unit stop right", "disable left", "disable right", "ros activate both"], r.log
    assert c.steps[-1]["title"].startswith("Roll back") and c.steps[-1]["state"] == "done", c.steps


def test_ros2_to_gafro_from_stow_skips_the_park():
    r = Robot(ros=True, at_stow=True)
    c, out, err = change(r, "handover", "gafro")
    assert err is None and r.controller() == "gafro" and "ros park both" not in r.log, (err, r.log)
    assert c.steps[0]["state"] == "skipped"


def test_something_still_on_the_bus_after_gafro_stops_is_not_handed_over_or_restarted():
    r = Robot(gafro=("left", "right"), fail={"senders after stop"})
    c, out, err = change(r, "handover", "ros2")
    assert err and "something still commands" in err and "rollback failed" in err, err
    assert not any(e.startswith(("disable", "ros activate", "gafro start")) for e in r.log), r.log


def test_release_is_refused_away_from_stow_and_sends_nothing():
    for ctrl in ({"ros": True}, {"gafro": ("left", "right")}):
        r = Robot(at_stow=False, **ctrl)
        c, out, err = change(r, "release")
        assert err and "Park first" in err and r.log == [], (err, r.log)
    r = Robot(ros=True, at_stow=False)
    r.limp_ = True                                  # power was lost and came back: already limp, so releasing drops nothing (as `openarm-up down` allows)
    c, out, err = change(r, "release")
    assert err is None and r.log == ["ros down"], (err, r.log)


def test_release_under_gafro_stops_listens_and_disables():
    r = Robot(gafro=("left", "right"))
    c, out, err = change(r, "release")
    assert err is None and r.controller() == "none", err
    assert r.log == ["unit stop left", "unit stop right", "listen left", "listen right", "disable left", "disable right"], r.log


def test_use_ros2_that_fails_returns_to_none_limp_where_the_arms_were():
    r = Robot(fail={"ros activate"})
    c, out, err = change(r, "use", "ros2")
    assert err and r.log == ["ros activate both", "ros down --drop"] and r.controller() == "none", (err, r.log)


def test_a_change_waits_for_a_show_and_for_the_teleop():
    for kw in ({"show": "shrug"}, {"teleop": True}):
        r = Robot(gafro=("left", "right"), **kw)
        c, out, err = change(r, "handover", "ros2")
        assert err and r.log == [], (kw, err)


def test_conflict_turns_every_arm_button_off():
    off = h.unavailable("conflict", "two senders on one bus")
    assert all(reason and "CONFLICT" in reason for reason in off.values()), off
    assert h.unavailable("gafro")["teleop"] and h.unavailable("ros2")["ready"] and h.unavailable("ros2")["follow"]
    assert h.unavailable("none")["use-ros2"] is None and h.unavailable("none")["park"]
