"""What a bringup motion step will do, said before it does it, and the checks that refuse it first.

Pure and robot-free, so the tests need neither gafro nor an arm. `bringup.py` wires these in: what needs no robot (the size and sign of the step, whether
a lapse test can pass at all) is checked before anything is enabled; what needs the arm's real pose (the clearance along the path, the plan and its
confirmation) is checked once the arm is held and that pose is known, and before anything moves.

Why it exists. A first version of `move` stepped joint 3 by +0.05 rad whatever the arm stood next to, with no word about direction, and the arm met the
body column instead. Nothing in the script could have known: it had no direction to ask about and no clearance to check. So now the step has no default
and a sign, the plan is printed with the pose the arm is really at, and the move starts only when the operator types the joint and the direction back.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np

import guards

MAX_STEP_DEG = 5.0          # the largest step these first-run stages take unless --allow-large says otherwise
LEASE = 0.5                 # PermitKeeper's: a permit stays valid this long after its last renewal. Keep in step with engage.PermitKeeper.
LAPSE_MARGIN = 0.2          # the move must still be under way this long after the permit has run out, or the test cannot tell a cut from a finish
DEFAULT_MIN_CLEARANCE_MM = 20.0
PATH_STEPS = 20             # poses checked along the straight line from the start to the target


def validate_step(joint: int, delta: float, max_step_deg: float = MAX_STEP_DEG, allow_large: bool = False) -> None:
    """Refuse a step that is not a joint, has no direction, or is larger than a first-run step may be."""
    if not 1 <= joint <= 7:
        raise guards.Refused("--joint is 1 to 7")
    if not math.isfinite(delta) or delta == 0.0:
        raise guards.Refused("--delta is the step in radians and needs a sign (+0.01 or -0.01): there is no default direction, because the right "
                             "one depends on what the arm stands next to")
    if abs(math.degrees(delta)) > max_step_deg and not allow_large:
        raise guards.Refused(f"a step of {math.degrees(delta):+.2f} deg is larger than the {max_step_deg:g} deg these first-run stages take; "
                             "start with 0.01 rad (0.57 deg), or pass --allow-large if you mean it")


def duration(delta: float, speed: float) -> float:
    """How long the ramp takes: gafro's JointPositionController, a linear ramp at `speed` rad/s of the fastest joint."""
    return abs(delta) / speed


def lapse_outlasts_permit(delta: float, speed: float, after: float, lease: float = LEASE, margin: float = LAPSE_MARGIN) -> bool:
    """Whether the move is still under way when the permit runs out, so that the lapse stage can tell a move that was cut from one that finished.

    The permit stays valid `lease` after its last renewal, and the last renewal falls just before `after`, so the arm keeps following the ramp for about
    after + lease. A move shorter than that has finished by then and the stage reports FAIL however well the arm holds.
    """
    return duration(delta, speed) > after + lease + margin


def require_lapse_can_pass(delta: float, speed: float, after: float) -> None:
    if not lapse_outlasts_permit(delta, speed, after):
        need = after + LEASE + LAPSE_MARGIN
        raise guards.Refused(f"this lapse test cannot pass: the move takes {duration(delta, speed):.2f} s but the permit only runs out about "
                             f"{after + LEASE:.2f} s in (it is valid {LEASE:g} s after the last renewal), so the move would be finished and the stage "
                             f"would report FAIL however well the arm holds. Make the move longer than {need:.2f} s: a slower --speed "
                             f"(for this step at most {abs(delta) / need:.3f} rad/s) or a larger step.")


def confirmation_token(joint: int, delta: float) -> str:
    """What the operator types to start a step: the joint and the direction, so that the direction has been read, not just agreed to."""
    return f"{joint}{'+' if delta > 0 else '-'}"


def describe(side: str, joint: int, start, delta: float, speed: float, closest=None, minimum_mm: float | None = None) -> str:
    """The plan, in plain words, with the pose the arm is really at."""
    start = np.asarray(start, dtype=float)
    target = start[joint - 1] + delta
    rows = [f"  joint {j + 1}: {math.degrees(v):+8.2f} deg" + (f"  ->  {math.degrees(target):+8.2f} deg   <- this one moves" if j == joint - 1 else "")
            for j, v in enumerate(start)]
    lines = [f"The {side} arm is at:", *rows, "",
             f"joint {joint} will turn {'POSITIVE (+)' if delta > 0 else 'NEGATIVE (-)'} by {abs(math.degrees(delta)):.2f} deg "
             f"({delta:+.4f} rad), at {math.degrees(speed):.1f} deg/s, for {duration(delta, speed):.2f} s, then (move) come back."]
    if closest is not None:
        mm, pair = closest
        lines.append(f"Closest approach along that path in the lane's twin: {mm:.1f} mm ({pair})"
                     + (f"; the stage refuses below {minimum_mm:g} mm. The twin agrees with the hand measurement to about half a degree; the floor is not a margin." if minimum_mm else ""))
    else:
        lines.append("Clearance along the path was NOT checked (--skip-clearance).")
    lines.append("The head is not in the model: it must be tracking OFF with the neck centred, or it can move into the path.")
    return "\n".join(lines)


def confirm(plan: str, joint: int, delta: float, supplied: str | None = None, ask=input, out=print) -> None:
    """Print the plan and require the joint and direction back. `supplied` is --confirm, for a script that has already read the plan."""
    token = confirmation_token(joint, delta)
    out(plan)
    if supplied is None:
        try:
            supplied = ask(f"Is the arm clear for that? Type {token} to move, anything else to stop: ")
        except EOFError:
            raise guards.Refused(f"no one to ask: run it from a terminal, or pass --confirm {token} once you have read the plan above") from None
    if supplied.strip() != token:
        raise guards.Refused(f"not confirmed (expected {token}, got {supplied.strip()!r}); nothing moved")


def line_poses(start, target, steps: int = PATH_STEPS):
    """The poses on the straight line in joint space from start to target, both ends included: what a linear ramp passes through."""
    start, target = np.asarray(start, dtype=float), np.asarray(target, dtype=float)
    return [start + (target - start) * (i / steps) for i in range(steps + 1)]


def path_poses(states, steps: int = PATH_STEPS):
    """The poses along a planned joint path (7, N+1), the knots and the straight lines between them, at least `steps` in all: what a controller that follows the
    path passes through, where `line_poses` is what a ramp between its ends does."""
    states = np.asarray(states, dtype=float)
    segments = max(states.shape[1] - 1, 1)
    per_segment = max(1, -(-steps // segments))
    poses = []
    for index in range(segments):
        a, b = states[:, index], states[:, min(index + 1, states.shape[1] - 1)]
        poses += [a + (b - a) * (k / per_segment) for k in range(per_segment)]
    poses.append(states[:, -1].copy())
    return poses


def require_clearance(poses, clearance_of, minimum_mm: float = DEFAULT_MIN_CLEARANCE_MM):
    """The nearest the path comes to anything, as (mm, pair); refuses if it is under `minimum_mm`.

    `clearance_of(q)` is (mm, pair-name) for one pose of the arm's seven joints, in radians. The worst pose along the path counts, not the ends:
    a path can start and end clear and pass close in between. The start is checked too, since the first thing the move does is leave it.
    """
    worst = None
    for q in poses:
        mm, pair = clearance_of(q)
        if worst is None or mm < worst[0]:
            worst = (mm, pair)
    if worst is None:
        raise guards.Refused("no poses to check")
    if worst[0] < minimum_mm:
        raise guards.Refused(f"the path comes within {worst[0]:.1f} mm of {worst[1]} in the lane's twin, under the {minimum_mm:g} mm these stages require "
                             "(a floor and not a margin: the twin agrees with the hand measurement to about half a degree, and the arm may overshoot its plan). "
                             "Step the other way, or a smaller step, or check the geometry by eye and pass --min-clearance-mm lower.")
    return worst


def twin_clearance(side: str, assets: str | None = None, sim_dir: str | None = None):
    """`clearance_of` backed by the Choreography lane's MuJoCo twin (sim/clearance.py, read, never edited). Refuses if it cannot be built:
    a check that cannot run must not read as a check that passed."""
    sim_dir = sim_dir or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "sim"))
    if not os.path.isdir(sim_dir):
        raise guards.Refused(f"the twin is not at {sim_dir}, so clearance cannot be checked; pass --skip-clearance to go without it")
    for name in ("scene", "clearance"):       # another checkout's twin may have been imported already: use this one
        sys.modules.pop(name, None)
    sys.path.insert(0, sim_dir)
    try:
        import mujoco  # noqa: PLC0415
        import clearance  # noqa: PLC0415
        import scene  # noqa: PLC0415

        model = scene.build(assets or os.environ.get("OPENARM_ASSETS"))
    except (Exception, SystemExit) as error:  # noqa: BLE001 -- any way of not having the twin is the same refusal; scene.find_assets exits when it finds no model
        raise guards.Refused(f"the lane's twin could not be built ({type(error).__name__}: {error}), so clearance cannot be checked; give --assets "
                             "(the openarm_assets checkout) or pass --skip-clearance to go without it") from error
    finally:
        sys.path.remove(sim_dir)
    data = mujoco.MjData(model)

    mine = f"openarm_{side}_"

    def clearance_of(q):
        """The nearest approach of the arm being moved to anything: a pair that does not involve it (the other arm's hand against the body, which
        does not move with this step) would only hide its own number."""
        clearance.set_pose(model, data, ",".join(f"{side}:{j + 1}={math.degrees(v):.4f}" for j, v in enumerate(q)))
        for mm, a, b in clearance.clearances(model, data):    # closest first
            if mine in (a or "") or mine in (b or ""):
                return mm, f"{a} <-> {b}"
        return float("inf"), "nothing"

    return clearance_of
