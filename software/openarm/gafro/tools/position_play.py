"""Playing a joint-space path in the driver's POSITION mode, under a gafro controller that takes the whole path as its input.

The path is a joint reference (a chunk of knots, evenly spaced in time) and, when it is known, the joint rates along it (a second chunk on the same grid). The controller
is a `type: position` spec (gafro.control.spec): a posture stage whose velocity law is `K e + rate` runs on an ANCHOR (a virtual state its own velocity moves, so no actuator, friction or
bus delay is in that loop and K can be high), and the integrated anchor is the position command. The measurement only bounds the anchor (a leash) and drives a small offset that
closes what the actuator did not do. The motor's own PD, with the driver's gravity feed-forward, closes the rest.

Fed forward, the rate carries the reference's own speed, so the anchor follows a moving reference without the lag an error-driven law settles at (rate / gain). Without it
(`feedforward=False`) it still follows, `rate / gain` behind, which at a gain of 20 is 1 / 20 of the speed.

Nothing here talks to the robot by itself: `start` builds the controller and hands it to the interface's loop, `publish` gives it the path. The gates (bus, CH5, e-stop,
`--supervised`, CH6, twin clearance, the typed confirmation) are the callers'.
"""
from __future__ import annotations

import copy

import numpy as np


def position_controller(gain: float | None = None, maximum_lead: float | None = None, speed: float | None = None, feedforward: bool = True, limits: bool = True,
                        maximum_error: float | None = None, offset_gain: float | None = None) -> dict:
    """The spec of a position controller that follows a posture reference: `follow_path` of controllers.yaml, with what the caller names replaced (None keeps the yaml's).

    The posture law runs on the controller's own anchor, not on the measurement, so `gain` (1/s, the posture error to a velocity, at most `speed` rad/s on every joint) closes a loop with no
    actuator in it and can be high. The measurement enters twice, bounded: `maximum_error` (rad) is a leash on how far the anchor may get from the measured position (a held joint is
    never asked for more than kp * maximum_error beyond the plan), and an offset, integrated on the anchor's distance from the measurement at `offset_gain` (1/s) and bounded to
    `maximum_lead` (rad) around the anchor, closes what the actuator did not do. `feedforward` keeps the rates of the reference; `limits` keeps the joint velocity bound by the model's.
    """
    import engage

    description = copy.deepcopy(engage.load_controllers()["follow_path"])
    law = description["stages"][0]["law"]["pd"]
    for target, key, value in ((law, "gain", gain), (law, "max_speed", speed), (description, "maximum_lead", maximum_lead),
                               (description, "maximum_error", maximum_error), (description, "offset_gain", offset_gain)):
        if value is not None:
            target[key] = value
    if not feedforward:
        del law["feedforward"], description["inputs"]["posture_rate"]
    if not limits:
        del description["limits"]
    return description


def settings(gain=None, maximum_lead=None, speed=None, maximum_error=None, offset_gain=None) -> tuple:
    """(gain, maximum_lead, speed, maximum_error, offset_gain) as the controller will have them: what is named, else `follow_path` of controllers.yaml. For a tool to print and check."""
    description = position_controller(gain, maximum_lead, speed, maximum_error=maximum_error, offset_gain=offset_gain)
    law = description["stages"][0]["law"]["pd"]
    return law["gain"], description["maximum_lead"], law["max_speed"], description["maximum_error"], description["offset_gain"]


def rates_from_knots(knots, period: float) -> np.ndarray:
    """The joint rates at evenly spaced `knots` (K, J), by central differences, one-sided at the two ends: (K, J)."""
    knots = np.asarray(knots, dtype=float)
    if knots.shape[0] < 2:
        return np.zeros_like(knots)
    if period <= 0.0:
        raise ValueError("the knot period must be positive")
    return np.gradient(knots, period, axis=0)


def start(robot, system, side: str, configuration, pose, gain: float | None = None, maximum_lead: float | None = None, speed: float | None = None, feedforward: bool = True,
          maximum_error: float | None = None, offset_gain: float | None = None):
    """Builds the position controller (`follow_path`, controllers.yaml; a number given here replaces the yaml's), hands it to the interface's loop, and returns (posture source, rate source or None) for `publish`.

    `configuration` is the whole system's joint vector now; the arm holds where it is until something is published. `pose` is the hand's pose now (the pose reference source
    `engage.build` always takes; the posture stage does not read it).
    """
    from gafro import control

    import engage

    posture = control.JointReferenceSource(np.asarray(configuration, dtype=float).copy(), horizon=0.5)
    posture.set_staleness(control.Staleness.Hold)
    posture.prime(2)
    reference = control.MotorReferenceSource(pose, horizon=0.5)
    reference.set_staleness(control.Staleness.Hold)
    reference.prime(2)
    rates = None
    if feedforward:
        joints = len(system.get_task_space(side).get_joint_indices())
        rates = control.JointRateReferenceSource(np.zeros(joints), horizon=0.5)
        rates.prime(2)

    controllers = {"position": position_controller(gain, maximum_lead, speed, feedforward, maximum_error=maximum_error, offset_gain=offset_gain)}
    robot.control(engage.build("position", controllers, system, side, np.asarray(configuration, dtype=float), pose, reference, posture, rates), False)
    return posture, rates


def publish(posture, rates, begin: float, period: float, joints, joint_rates=None) -> None:
    """Gives the controller the whole path: `joints` (K whole-system joint vectors), and `joint_rates` (K vectors of the arm's own joints) when it feeds them forward.

    `begin` is when the first knot applies, in seconds on the monotonic clock (`gafro.control.monotonic_now()`); `period` is the spacing between knots.
    """
    from gafro import control

    posture.publish(control.JointReferenceChunk(begin, period, [np.asarray(q, dtype=float) for q in joints]))
    if rates is not None:
        if joint_rates is None:
            raise ValueError("a controller that feeds the rate forward needs the rates")
        rates.publish(control.JointRateReferenceChunk(begin, period, [np.asarray(r, dtype=float) for r in joint_rates]))
