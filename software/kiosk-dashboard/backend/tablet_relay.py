"""The robot tablet's sensors into ROS: the page sends readings, this stamps them on the Thor's clock
and publishes them (notes/workplan-tablet.md DT-2; the topics, types and frames are the Thor lane's).

  /ws/tablet   the kiosk page's end (frontend/tablet_sensors.js, loaded by the dashboard and the face).
               First message {"hello": {"kiosk": true}}; only a page inside the kiosk app is accepted,
               and the newest one wins (the kiosk shows one page at a time; a reload replaces it).
               Then readings: {"kind": "light"|"battery"|"fix", "age_ms": n, ...}. The relay answers
               {"ping": t} with {"pong": t} so the page can keep its round trip known.

Published through the local rosbridge, as one client (OPENDUBS_ROSBRIDGE_URL, default ws://127.0.0.1:9090):

  /tablet/illuminance   sensor_msgs/Illuminance   frame tablet_light_link
  /tablet/battery       sensor_msgs/BatteryState  (percentage, charging state, the battery's temperature)
  /tablet/fix           sensor_msgs/NavSatFix     frame tablet_gps_link; NO_FIX when there is none,
                                                  never a stale or guessed position (workplan rule 7)

Android's thermal throttling level is kept here (GET /api/tablet/sensors), not published: no topic for
it is agreed yet.

Stamps are the Thor's wall clock (ROS time): when the reading arrived, less its age on the tablet and
half the page's measured round trip. The tablet's own clock is never used. With rosbridge down,
readings are dropped and the client reconnects; nothing queues up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import origin_guard

log = logging.getLogger("tablet_relay")
router = APIRouter()

ROSBRIDGE_URL = os.environ.get("OPENDUBS_ROSBRIDGE_URL", "ws://127.0.0.1:9090")
FIX_STALE_S = 5.0          # a position older than this is no fix
TOPICS = {
    "/tablet/illuminance": "sensor_msgs/msg/Illuminance",
    "/tablet/battery": "sensor_msgs/msg/BatteryState",
    "/tablet/fix": "sensor_msgs/msg/NavSatFix",
}
# sensor_msgs/BatteryState power_supply_status
CHARGING, DISCHARGING, NOT_CHARGING, FULL = 1, 2, 3, 4
NAN = float("nan")

state = {"page": None, "rtt_ms": None, "published": 0, "dropped": 0, "last": {}, "thermal": None}


def stamp(age_ms: float, rtt_ms: float | None, now: float | None = None) -> dict:
    """The moment a reading was taken, on the Thor's clock, as a ROS stamp."""
    t = (time.time() if now is None else now) - max(0.0, age_ms or 0.0) / 1000.0 - (rtt_ms or 0.0) / 2000.0
    sec = math.floor(t)
    return {"sec": int(sec), "nanosec": int(round((t - sec) * 1e9)) % 1_000_000_000}


def _num(v, lo=-math.inf, hi=math.inf):
    """A finite number in range, else None."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
        return None
    return float(v)


def to_messages(r: dict, rtt_ms: float | None, now: float | None = None) -> list[tuple[str, dict]]:
    """One reading from the page -> the (topic, message) pairs to publish. Malformed: none."""
    kind = r.get("kind")
    age = _num(r.get("age_ms"), 0, 600_000) or 0.0
    hdr = lambda frame: {"stamp": stamp(age, rtt_ms, now), "frame_id": frame}   # noqa: E731
    if kind == "light":
        lux = _num(r.get("lux"), 0)
        return [] if lux is None else [("/tablet/illuminance", {"header": hdr("tablet_light_link"), "illuminance": lux, "variance": 0.0})]
    if kind == "battery":
        level = _num(r.get("level"), 0, 100)
        if level is None:
            return []
        charging, plugged = r.get("charging") is True, r.get("plugged") is not None
        status = FULL if level >= 100 and plugged else CHARGING if charging else NOT_CHARGING if plugged else DISCHARGING
        temp = _num(r.get("battery_c"), -40, 120)
        out = [("/tablet/battery", {
            "header": hdr(""), "voltage": NAN, "temperature": NAN if temp is None else temp, "current": NAN,
            "charge": NAN, "capacity": NAN, "design_capacity": NAN, "percentage": level / 100.0,
            "power_supply_status": status, "power_supply_health": 0, "power_supply_technology": 2,   # Li-ion
            "present": True, "cell_voltage": [], "cell_temperature": [], "location": "tablet", "serial_number": "",
        })]
        return out
    if kind == "fix":
        lat, lon = _num(r.get("lat"), -90, 90), _num(r.get("lon"), -180, 180)
        acc = _num(r.get("acc"), 0)
        fresh = age <= FIX_STALE_S * 1000
        if lat is None or lon is None or acc is None or not fresh:
            return [("/tablet/fix", {"header": hdr("tablet_gps_link"), "status": {"status": -1, "service": 1},
                                     "latitude": NAN, "longitude": NAN, "altitude": NAN,
                                     "position_covariance": [0.0] * 9, "position_covariance_type": 0})]
        alt, alt_acc = _num(r.get("alt")), _num(r.get("alt_acc"), 0)
        v_alt = alt_acc ** 2 if alt is not None and alt_acc is not None else 1e6   # no altitude: say so, hugely
        return [("/tablet/fix", {"header": hdr("tablet_gps_link"), "status": {"status": 0, "service": 1},
                                 "latitude": lat, "longitude": lon, "altitude": NAN if alt is None else alt,
                                 # Android's accuracy is a 68 % horizontal radius in metres: taken as sigma
                                 # per axis, which errs on the side of trusting it less.
                                 "position_covariance": [acc * acc, 0.0, 0.0, 0.0, acc * acc, 0.0, 0.0, 0.0, v_alt],
                                 "position_covariance_type": 2})]                  # DIAGONAL_KNOWN
    return []


class Rosbridge:
    """One rosbridge client: advertises the topics on (re)connect, publishes, never queues."""

    def __init__(self, url: str = ROSBRIDGE_URL):
        self.url, self.ws, self._task = url, None, None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def _run(self) -> None:
        import websockets
        delay = 1.0
        while True:
            try:
                async with websockets.connect(self.url, max_size=None, open_timeout=3) as ws:
                    for topic, typ in TOPICS.items():
                        await ws.send(json.dumps({"op": "advertise", "topic": topic, "type": typ}))
                    self.ws, delay = ws, 1.0
                    log.info("tablet relay: rosbridge connected at %s", self.url)
                    async for _ in ws:                       # nothing to read; keeps the link honest
                        pass
            except Exception as e:                          # noqa: BLE001 — down: retry, quietly
                log.debug("tablet relay: rosbridge %s", e)
            self.ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 10.0)

    async def publish(self, topic: str, msg: dict) -> bool:
        ws = self.ws
        if ws is None:
            return False
        try:
            await ws.send(json.dumps({"op": "publish", "topic": topic, "msg": msg}))
            return True
        except Exception:                                   # noqa: BLE001
            return False


ros = Rosbridge()


@router.websocket("/ws/tablet")
async def tablet_ws(socket: WebSocket) -> None:
    if await origin_guard.refuse_websocket(socket):
        return
    await socket.accept()
    try:
        hello = await asyncio.wait_for(socket.receive_json(), timeout=5)
    except Exception:                                       # noqa: BLE001
        await socket.close(code=4400)
        return
    if not (isinstance(hello, dict) and isinstance(hello.get("hello"), dict) and hello["hello"].get("kiosk") is True):
        await socket.send_json({"error": "tablet sensors are accepted only from the kiosk app"})
        await socket.close(code=4403)
        return
    old = state["page"]
    state["page"] = socket                                  # the newest kiosk page wins
    if old is not None:
        try:
            await old.close(code=4409)
        except Exception:                                   # noqa: BLE001
            pass
    ros.start()
    await socket.send_json({"ok": True})
    try:
        while True:
            m = await socket.receive_json()
            if not isinstance(m, dict):
                continue
            if "ping" in m:
                await socket.send_json({"pong": m["ping"]})
                continue
            if "rtt_ms" in m:
                state["rtt_ms"] = _num(m["rtt_ms"], 0, 5000)
                continue
            if socket is not state["page"]:
                break
            if m.get("kind") == "battery" and isinstance(m.get("thermal"), (str, type(None))):
                state["thermal"] = m.get("thermal")
            for topic, msg in to_messages(m, state["rtt_ms"]):
                state["last"][topic] = msg
                if await ros.publish(topic, msg):
                    state["published"] += 1
                else:
                    state["dropped"] += 1
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if state["page"] is socket:
            state["page"] = None


@router.get("/api/tablet/sensors")
def sensors_status() -> dict:
    """What the relay last published, for the dashboard and for checking it by hand."""
    return {"page": state["page"] is not None, "rosbridge": ros.ws is not None, "rtt_ms": state["rtt_ms"],
            "published": state["published"], "dropped": state["dropped"], "thermal": state["thermal"],
            "last": {t: json.loads(json.dumps(m, default=str).replace("NaN", "null")) for t, m in state["last"].items()}}
