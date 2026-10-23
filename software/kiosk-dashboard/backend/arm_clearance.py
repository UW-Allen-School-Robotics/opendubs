"""The head keeps out of a raised arm's way: tracking sends the neck no GAZE while either shoulder is up.

THE GAP THIS CLOSES (agreed with Choreography and the Orchestrator, 2026-10-23; the shoulder cap of #523,
J2 <= 155 deg, "the neck centred near the arms"). A show already aims the head itself, and its own head
moves are swept against its arms. But a run that STOPS mid-move leaves the arms where they are, the show
unloads, and tracking (neck_follow.py) takes the head back: with the Galaxy head and the neck free, arm to
head contact comes as low as J2 167.7 deg (the Arms lane's sim, worst pose pan -60 tilt -30). So while
either arm's J2 is above J2_MAX_DEG, the head does not follow; the neck holds where it is.

Read from ROS /joint_states (openarm_{left,right}_joint2, radians), through the local rosbridge, throttled
to a few a second. J2 IS MIRRORED there (the Arms lane, 2026-10-23): the right arm raises to POSITIVE J2,
the left to NEGATIVE, so the test is on |J2|; a plain "> 142" would never fire for the left arm.
/joint_states carries live positions whether the arms are armed or not, and stops only when the arm
stack is brought down, so the wait below does not stop tracking in normal use.

UNKNOWN MEANS WAIT: no reading, or none for STALE_S, and the head does not follow either, because an arm
whose position is unknown may be up. The reason says which, so the dashboard shows why the head is still.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

log = logging.getLogger("arm_clearance")

TOPIC = "/joint_states"
J2_MAX_DEG = 142.0             # either shoulder above this: no GAZE (the cap is 155, contact from 167.7)
STALE_S = 2.0
THROTTLE_MS = 200
JOINTS = {"left": "openarm_left_joint2", "right": "openarm_right_joint2"}

last = {"j2": {}, "at": None}  # j2: side -> degrees; at: monotonic time of the last message with either
_task = None


def note(msg: dict, now: float | None = None) -> None:
    """A JointState from rosbridge: keep the shoulders' angles, if it carries them."""
    names, pos = msg.get("name") or [], msg.get("position") or []
    got = {}
    for side, joint in JOINTS.items():
        if joint in names:
            i = names.index(joint)
            if i < len(pos) and isinstance(pos[i], (int, float)) and math.isfinite(pos[i]):
                got[side] = math.degrees(pos[i])
    if got:
        last["j2"].update(got)
        last["at"] = time.monotonic() if now is None else now


def gate(j2: dict, age_s: float | None) -> str | None:
    """Why the head must not follow, from the shoulders' angles (side -> degrees) and their age; or None."""
    if age_s is None:
        return "the arms' positions are unknown (no /joint_states yet): the head waits"
    if age_s > STALE_S:
        return f"the arms' positions are unknown (none for {age_s:.0f} s): the head waits"
    for side in ("left", "right"):
        if side not in j2:
            return f"the {side} arm's shoulder angle is unknown: the head waits"
        if abs(j2[side]) > J2_MAX_DEG:                       # MIRRORED: the left arm raises to NEGATIVE J2
            return (f"the {side} arm's shoulder is raised (J2 {j2[side]:.0f} deg, beyond {J2_MAX_DEG:.0f}): "
                    "the head waits, so it cannot turn into it")
    return None


def why_not(now: float | None = None) -> str | None:
    now = time.monotonic() if now is None else now
    return gate(last["j2"], None if last["at"] is None else now - last["at"])


async def _run() -> None:
    import websockets
    import tablet_relay
    delay = 1.0
    while True:
        try:
            async with websockets.connect(tablet_relay.ROSBRIDGE_URL, max_size=None, open_timeout=3) as ws:
                await ws.send(json.dumps({"op": "subscribe", "topic": TOPIC, "type": "sensor_msgs/msg/JointState",
                                          "throttle_rate": THROTTLE_MS, "queue_length": 1}))
                delay = 1.0
                async for raw in ws:
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if m.get("op") == "publish" and m.get("topic") == TOPIC and isinstance(m.get("msg"), dict):
                        note(m["msg"])
        except Exception as e:                       # noqa: BLE001 — down: retry quietly; unknown means wait
            log.debug("arm_clearance: rosbridge %s", e)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 10.0)


async def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_run())
