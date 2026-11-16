"""The capture mission's panel (HallPass): what the mission runner says, and start / pause / skip.

THE RUNNER OWNS THE MISSION; this only relays. The runner (a Thor-side ROS node) drives the base
through its own lease (/base/claim, docs/interfaces/navigation.md) and aims the head through look_at.py.
Nothing here touches the base or the neck: a press here is a request to the runner, which keeps every
gate it already has, and the deadman stays on the transmitter.

  /hallpass/mission/state  std_msgs/String JSON, from the runner (transient local, ~2 Hz):
      {"state": "idle|running|paused|done|failed", "session", "mission", "missions": [...],
       "stop_n", "stop_count", "target", "redrive": [...], "last_check": {"target", "verdict"},
       "deadman", "why"}
  /hallpass/mission/cmd    std_msgs/String JSON, to the runner:
      {"op": "start", "mission": "<id>"} | {"op": "pause"} | {"op": "resume"} | {"op": "skip"}

  GET  /api/mission              the runner's last state, its age, and whether it is answering
  POST /api/mission/<op>         start (body {"mission"}), pause, resume, skip

START asks for the operator's intent and nothing more: one press, with CH5 in AUTO and the deadman held
(the user's rule, 2026-11-05: the press plus the deadman is the attestation). Either missing refuses at
once with one line. Pause, resume and skip need neither: they only ever slow a mission down.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

log = logging.getLogger("mission")
router = APIRouter()

STATE, CMD = "/hallpass/mission/state", "/hallpass/mission/cmd"
STALE_S = 5.0                                    # the runner publishes ~2 Hz: three seconds of nothing is gone
OPS = ("start", "pause", "resume", "skip")
_clock = time.monotonic

last: dict = {"state": None, "at": None}
_task = None
_ros = None


def note(msg) -> None:
    """One /hallpass/mission/state message (std_msgs/String) from the runner."""
    try:
        s = json.loads(msg.get("data", "")) if isinstance(msg, dict) else None
    except (TypeError, ValueError):
        return
    if isinstance(s, dict):
        last["state"], last["at"] = s, _clock()


def status() -> dict:
    age = None if last["at"] is None else round(_clock() - last["at"], 1)
    return {"answering": age is not None and age < STALE_S, "age_s": age, "mission": last["state"]}


def start_refusal(rs: dict | None) -> str | None:
    """Why START is refused now (one line), or None. `rs` is the robot state (status._robot_state)."""
    if not rs:
        return "the robot's state is unknown"
    if rs.get("ch5") != "AUTO":
        return "switch CH5 to AUTO first"
    if not (rs.get("inputs") or {}).get("deadman"):
        return "hold the deadman (CH6) to start"
    return None


@router.get("/api/mission")
def get_mission() -> dict:
    return status()


@router.post("/api/mission/{op}")
async def post_mission(op: str, request: Request) -> JSONResponse:
    if op not in OPS:
        return JSONResponse({"error": f"one of {', '.join(OPS)}"}, status_code=404)
    st = status()
    if not st["answering"]:
        return JSONResponse({"error": "no mission runner is answering"}, status_code=503)
    cmd = {"op": op}
    if op == "start":
        try:
            body = await request.json()
        except ValueError:
            body = None
        mission = body.get("mission") if isinstance(body, dict) else None
        known = (st["mission"] or {}).get("missions") or []
        if not isinstance(mission, str) or (known and mission not in known):
            return JSONResponse({"error": "choose a mission the runner lists"}, status_code=400)
        import status as robot_status
        rs = await asyncio.get_running_loop().run_in_executor(None, robot_status._robot_state)
        why = start_refusal(rs)
        if why:
            return JSONResponse({"error": why}, status_code=409)
        cmd["mission"] = mission
    if _ros is None or not await _ros.publish(CMD, {"data": json.dumps(cmd)}):
        return JSONResponse({"error": "the robot's ROS bridge is not connected"}, status_code=503)
    return JSONResponse({"ok": True, "sent": cmd})


async def _listen() -> None:
    import websockets
    import tablet_relay
    delay = 1.0
    while True:
        try:
            async with websockets.connect(tablet_relay.ROSBRIDGE_URL, max_size=None, open_timeout=3) as ws:
                await ws.send(json.dumps({"op": "subscribe", "topic": STATE, "type": "std_msgs/msg/String",
                                          "queue_length": 1}))
                delay = 1.0
                async for raw in ws:
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    if m.get("op") == "publish" and m.get("topic") == STATE:
                        note(m.get("msg"))
        except Exception as e:                       # noqa: BLE001 — down: retry quietly; the panel says so
            log.debug("mission: rosbridge %s", e)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 10.0)


async def start() -> None:
    global _task, _ros
    if _task is None or _task.done():
        import tablet_relay
        _ros = tablet_relay.Rosbridge(topics={CMD: "std_msgs/msg/String"})
        _ros.start()
        _task = asyncio.get_running_loop().create_task(_listen())
