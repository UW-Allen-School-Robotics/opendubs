"""One controller for both arms, chosen robot-wide, and the deliberate changes between controllers.

The controller is whichever arm stack runs (openarm.active_stack, the dashboard's `stack` field):

- "ros2": the ROS 2 stack, `ros2_control_node` in the `openarm` container, driven by `openarm-up`;
- "gafro": the gafro arm service, `opendubs-arm@left` and `@right`;
- "none": neither. This is how the robot boots: nothing starts an arm stack on its own;
- "conflict": both run, or it cannot be told. Every arm button is off until a person stops one.

There is no per-arm mixing: choosing a controller, handing over and releasing always take both arms.
Each change is a list of steps. Every step says what the ARMS do while it runs, and the operator sees
the list as it goes (notes/design/arm-controller-handover.md):

- use(target): none -> target. The arms stiffen where they are and do not move.
- handover(target): the other controller -> target. The arms move to stow under the old controller
  (the deadman held), then rest there while the old one stops and the new one takes them.
- release(): the controller -> none. The arms go limp at stow. Refused anywhere but stow.

A step that fails ends the change. What follows is a ROLLBACK to the controller that was in charge,
and its steps are shown too, so the operator sees what the arms did. A failure before anything was
stopped needs no rollback: the old controller still has the arms, holding.

The robot is reached only through `io` (openarm._HandoverIo on the robot, a fake in the tests), so
this file runs no command of its own.
"""
from __future__ import annotations

LABELS = {"ros2": "ROS 2", "gafro": "gafro", "none": "none", "conflict": "CONFLICT"}
FROM_STACK = {"ros": "ros2", "gafro": "gafro", "none": "none", "both": "conflict", "unknown": "conflict"}
SIDES = ("left", "right")

#: CH5 positions in which the old controller may park the arms: gafro's own gate (guards.py) allows
#: MANUAL or AUTO, and the ROS 2 stack's park is held to the same.
MOTION_CH5 = ("MANUAL", "AUTO")


class Failed(Exception):
    """A step did not go through. The text is for the operator: what happened, and what to do."""


def controller(stack_name):
    return FROM_STACK.get(stack_name, "conflict")


# ---- what each button may do under each controller -----------------------------------------------

NO_OWNER = "no controller has the arms: choose one first (Use ROS 2, or Use gafro)"
GAFRO_ONLY = "gafro's alone, and the ROS 2 stack has the arms"

#: The buttons, by what the arm does. The page shows them disabled with these words, and the backend
#: refuses with the same words: a press is never sent to the controller that does not have the arms.
ACTIONS = ("park", "hold", "release", "ready", "follow", "supervise", "prepare", "teleop",
           "use-ros2", "use-gafro", "to-ros2", "to-gafro")


def unavailable(ctrl, why_conflict=""):
    """{action: None if the controller can do it now, else the reason in words}."""
    if ctrl == "conflict":
        why = (f"CONFLICT: {why_conflict}. Every arm button is off until only one arm stack runs: "
               "stop one at the robot's computer (`openarm-up down`, or `systemctl stop opendubs-arm@left opendubs-arm@right`)")
        return {a: why for a in ACTIONS}
    out = dict.fromkeys(ACTIONS)
    if ctrl == "none":
        for a in ("park", "hold", "release", "ready", "follow", "supervise", "prepare", "teleop", "to-ros2", "to-gafro"):
            out[a] = NO_OWNER
        out["release"] = "the arms are already limp: no controller has them"
        return out
    other = "gafro" if ctrl == "ros2" else "ros2"
    out["use-ros2"] = out["use-gafro"] = f"{LABELS[ctrl]} has the arms: hand them to {LABELS[other]} instead, or Release them first"
    out[f"to-{ctrl}"] = f"{LABELS[ctrl]} already has the arms"
    if ctrl == "ros2":
        out["ready"] = "the ROS 2 stack has no ready pose (Hold stiffens the arms where they are)"
        for a in ("follow", "supervise", "prepare"):
            out[a] = GAFRO_ONLY
    else:
        out["teleop"] = "the Quest teleop drives the ROS 2 stack's controllers, and gafro has the arms: Hand arms to ROS 2 first"
    return out


# ---- the steps -----------------------------------------------------------------------------------

class Change:
    """One change of controller, run once. `steps` is what the page shows: [{title, arms, state, note}]
    with state pending | running | done | skipped | failed. `outcome` is set when it ends."""

    def __init__(self, io, name, on_update=lambda: None):
        self.io, self.name, self.on_update = io, name, on_update
        self.steps, self.outcome, self.error = [], None, None

    def plan(self, *titles_and_arms):
        for title, arms in titles_and_arms:
            self.steps.append({"title": title, "arms": arms, "state": "pending", "note": ""})
        self.on_update()

    def step(self, i, work):
        """Run steps[i]; `work` returns a note, or raises Failed. A return of ("skip", note) skips it."""
        s = self.steps[i]
        s["state"] = "running"
        self.on_update()
        try:
            note = work()
        except Failed as e:
            s.update(state="failed", note=str(e))
            self.on_update()
            raise
        if isinstance(note, tuple) and note[0] == "skip":
            s.update(state="skipped", note=note[1])
        else:
            s.update(state="done", note=note or "")
        self.on_update()

    def skip_rest(self, why):
        for s in self.steps:
            if s["state"] == "pending":
                s.update(state="skipped", note=why)
        self.on_update()


def _both_at_stow(io, sides=SIDES):
    away = [s for s in sides if io.at_stow(s) is not True]
    return away


def _require_motion_gates(io):
    """The old controller moves the arms to stow: a person at the e-stop, holding the deadman."""
    g = io.gates() or {}
    if g.get("estop") is not False:
        raise Failed("the e-stop is " + ("pressed" if g.get("estop") else "not reported") + ". Nothing was moved or stopped")
    if g.get("deadman") is not True:
        raise Failed("hold the deadman (CH6) for the whole handover: the arms move to stow first. Nothing was moved or stopped")
    if g.get("ch5") not in MOTION_CH5:
        raise Failed(f"CH5 is {g.get('ch5') or 'not reported'}: the park needs MANUAL or AUTO. Nothing was moved or stopped")


def _require_quiet_robot(io):
    show = io.show_running()
    if show:
        raise Failed(f"a show is running ({show}): a change of controller waits until it ends")
    if io.teleop_running():
        raise Failed("the Quest teleop bridge is running and commands the ROS 2 controllers: STOP it first")


def _quiet_buses(io):
    loud = {}
    for s in SIDES:
        quiet, text = io.senders(s)
        if not quiet:
            loud[s] = text
    if loud:
        raise Failed("something still commands " + "; ".join(f"the {s} arm ({t})" for s, t in loud.items()))
    return "both buses quiet: nothing commands the arms"


def _disable(io):
    for s in SIDES:
        ok, text = io.disable(s)
        if not ok:
            raise Failed(f"the {s} arm's motors did not all disable ({text}): check `openarm-motors status`")
    return "both arms' motors disabled"


def _stop_gafro(io):
    for s in SIDES:
        err = io.unit("stop", s)
        if err:
            raise Failed(f"could not stop opendubs-arm@{s}: {err}")
    if io.gafro_sides():
        raise Failed("an arm service still answers after its unit was stopped: " + ", ".join(io.gafro_sides()))
    return "both arm services stopped"


def _start_gafro(io, sides=SIDES):
    for s in sides:
        out, err = io.start_gafro(s)
        if err:
            raise Failed(err)
    return "both arm services answer"


def _ros(io, *args, timeout=180):
    rc, text = io.ros(*args, timeout=timeout)
    if rc != 0:
        raise Failed(f"`openarm-up {' '.join(args)}` failed: {text}")
    return text


def _ros_activate(io):
    _ros(io, "activate", "both")
    return "the ROS 2 stack runs; both arms active, holding where they rest"


def _ros_down(io, drop=False):
    _ros(io, *(("down", "--drop") if drop else ("down",)), timeout=120)
    if io.ros_running():
        raise Failed("`openarm-up down` returned, but ros2_control_node still runs")
    return "the ROS 2 stack is stopped"


def use(io, target, change):
    """none -> target: the arms stiffen where they are. A failure returns to none, limp where they were."""
    if io.controller() != "none":
        raise Failed(unavailable(io.controller(), io.stack_why())[f"use-{target}"] or "a controller already has the arms")
    _require_quiet_robot(io)
    if target == "ros2":
        change.plan(("Start the ROS 2 stack and activate both arms", "the arms stiffen where they are; they do not move"))
        try:
            change.step(0, lambda: _ros_activate(io))
        except Failed:
            _rollback(change, ("Stop the ROS 2 stack again (openarm-up down --drop)", "the arms go limp where they are, as they were before"),
                      lambda: _ros_down(io, drop=True))
            raise
    else:
        change.plan(("Start the arm service on both arms (opendubs-arm@left, @right)", "the arms stiffen where they are; they do not move"))
        try:
            change.step(0, lambda: _start_gafro(io))
        except Failed:
            _rollback(change, ("Stop the arm services and disable the motors", "the arms go limp where they are, as they were before"),
                      lambda: (_stop_gafro(io), _disable(io)) and "back to no controller")
            raise
    return f"{LABELS[target]} has the arms"


def _rollback(change, title_arms, work):
    """Add a rollback step after a failure and run it; a failed rollback is reported, not raised."""
    change.skip_rest("not run: an earlier step failed")
    change.plan(title_arms)
    try:
        change.step(len(change.steps) - 1, work)
    except Failed as e:
        change.error = f"and the rollback failed too: {e}"


def handover(io, target, change):
    """The other controller -> target, both arms. Park under the old one, then rest at stow while it stops and the new one takes over."""
    old = io.controller()
    if old not in ("ros2", "gafro") or old == target:
        raise Failed(unavailable(old, io.stack_why())[f"to-{target}"] or "nothing to hand over")
    _require_quiet_robot(io)
    _require_motion_gates(io)
    if old == "gafro":
        served = io.gafro_sides()
        unsup = [s for s in served if not io.gafro_supervised(s)]
        if unsup:
            raise Failed(f"gafro parks only for a person at the e-stop: SUPERVISE the {' and '.join(unsup)} arm first. Nothing was moved or stopped")
        change.plan(
            ("Park under gafro", "the arms MOVE to stow: keep clear, hold the deadman"),
            ("Stop the arm services (opendubs-arm@left, @right)", "the arms hold, stiff, at stow (the motors keep their last command)"),
            ("Listen on both arms' buses", "the arms hold at stow; nothing may still command them"),
            ("Disable both arms' motors", "the arms go limp at stow and rest there"),
            ("Start the ROS 2 stack and activate both arms", "the arms stiffen at stow; they do not move"))

        def park():
            for s in served:
                err = io.gafro_park(s)
                if err:
                    for h in served:
                        io.gafro_hold(h)
                    raise Failed(f"{s}: {err}. gafro was asked to hold both arms where they are, and still has them")
            away = _both_at_stow(io, served)
            if away:
                raise Failed(f"the park reported done, but the {' and '.join(away)} arm does not read at stow. gafro still has the arms, holding")
            return "both arms at stow"
        try:
            change.step(0, park)
        except Failed:
            change.skip_rest("not run: gafro still has the arms")
            raise
        try:
            change.step(1, lambda: _stop_gafro(io))
            change.step(2, lambda: _quiet_buses(io))
            change.step(3, lambda: _disable(io))
            change.step(4, lambda: _ros_activate(io))
        except Failed:
            def back():
                if io.ros_running():
                    _ros_down(io)
                if any(io.senders(s)[0] is False for s in SIDES):
                    raise Failed("something still commands an arm, so gafro cannot start again: the arms are left where they are. Stop it at the robot's computer")
                _disable(io)
                _start_gafro(io, served)
                return "gafro has the arms again, holding at stow"
            _rollback(change, ("Roll back: stop anything started, disable the motors, start gafro again", "the arms go limp at stow, then stiffen at stow"), back)
            raise
    else:
        change.plan(
            ("Park under the ROS 2 stack", "the arms MOVE to stow: keep clear, hold the deadman (the ROS 2 stack has no deadman gate of its own; the e-stop stops it)"),
            ("Stop the ROS 2 stack (openarm-up down)", "the arms go limp at stow and rest there"),
            ("Listen on both arms' buses", "the arms rest limp at stow; nothing may still command them"),
            ("Start the arm service on both arms (opendubs-arm@left, @right)", "the arms stiffen at stow; they do not move"))

        def park():
            if not _both_at_stow(io):
                return ("skip", "both arms already read at stow")
            _ros(io, "park", "both")
            away = _both_at_stow(io)
            if away:
                raise Failed(f"the park ended, but the {' and '.join(away)} arm does not read at stow. The ROS 2 stack still has the arms")
            return "both arms at stow"
        try:
            change.step(0, park)
        except Failed:
            io.ros("hold", "both", timeout=90)
            change.skip_rest("not run: the ROS 2 stack still has the arms")
            raise
        try:
            change.step(1, lambda: _ros_down(io))
            change.step(2, lambda: _quiet_buses(io))
            change.step(3, lambda: _start_gafro(io))
        except Failed:
            def back():
                for s in SIDES:
                    io.unit("stop", s)
                _disable(io)
                return _ros_activate(io)
            _rollback(change, ("Roll back: stop anything started, start the ROS 2 stack again", "the arms go limp at stow, then stiffen at stow"), back)
            raise
    return f"{LABELS[target]} has the arms"


def release(io, change):
    """The controller -> none: the arms go limp at stow. Refused unless both arms read at stow."""
    old = io.controller()
    if old not in ("ros2", "gafro"):
        raise Failed(unavailable(old, io.stack_why())["release"])
    _require_quiet_robot(io)
    # An arm with no motor energized (power was lost and came back) is already limp: releasing it drops nothing. `openarm-up down` allows the same.
    away = [s for s in _both_at_stow(io) if not io.limp(s)]
    if away:
        raise Failed(f"the {' and '.join(away)} arm is not at stow, and releasing drops it from where it is: Park first")
    if old == "ros2":
        change.plan(("Stop the ROS 2 stack (openarm-up down)", "the arms go limp at stow"))
        change.step(0, lambda: _ros_down(io))
    else:
        served = io.gafro_sides()
        change.plan(("Stop the arm services (opendubs-arm@left, @right)", "the arms hold, stiff, at stow"),
                    ("Listen on both arms' buses", "the arms hold at stow"),
                    ("Disable both arms' motors", "the arms go limp at stow"))
        try:
            change.step(0, lambda: _stop_gafro(io))
            change.step(1, lambda: _quiet_buses(io))
            change.step(2, lambda: _disable(io))
        except Failed:
            def back():
                if any(io.senders(s)[0] is False for s in SIDES):
                    raise Failed("something still commands an arm, so gafro cannot start again: the arms are left holding at stow")
                return _start_gafro(io, served)
            _rollback(change, ("Roll back: start gafro again", "the arms stiffen at stow"), back)
            raise
    return "no controller: the arms rest limp at stow"


def run(io, kind, target, change):
    """Run one change to the end. Returns (output, error) for the dashboard's op."""
    try:
        if kind == "use":
            change.outcome = use(io, target, change)
        elif kind == "handover":
            change.outcome = handover(io, target, change)
        else:
            change.outcome = release(io, change)
    except Failed as e:
        change.error = f"{e}" + (f" ({change.error})" if change.error else "")
        change.on_update()
        return "", change.error
    change.on_update()
    return change.outcome, None
