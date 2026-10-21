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
  /tablet/imu           sensor_msgs/Imu           frame tablet_imu_link, ~60 Hz. ACCELERATION ONLY, gravity
                                                  included (REP-145), in the tablet's own axes (x right,
                                                  y up the screen, z out of it). The tablet has NO
                                                  gyroscope: orientation and angular velocity are marked
                                                  absent (covariance[0] = -1). A tilt and vibration check,
                                                  never a fusion input (workplan rule 7).
  /tablet/camera/{front,rear}/image_raw/compressed   sensor_msgs/CompressedImage (JPEG)
  /tablet/camera/{front,rear}/camera_info            sensor_msgs/CameraInfo
        frame tablet_{front,rear}_camera_optical_frame. ON DEMAND: a camera streams only while
        something on the Thor subscribes to its image (the Thor lane's tablet_camera_demand node counts
        the subscribers and POSTs /api/tablet/camera/demand; no word for 6 s is no demand) or it is
        switched on by hand (PUT /api/tablet/camera), and stops when neither holds. Front is the screen-side camera, the
        way the face looks; rear is the back-side one. The face shows a sign while either is open.
        camera_info carries this robot's calibration if one is kept on the robot
        (/var/lib/opendubs/tablet/camera_{front,rear}.yaml, camera_calibration's own format), else
        zeros: uncalibrated, as the message type defines it.

  /diagnostics          the tablet's thermal state, as status "tablet: thermal" (the Thor lane's spec):
                        level OK for none/light, WARN for moderate/severe, ERROR for critical and above,
                        STALE with no reading for 10 s; on change and every 5 s. `level` goes as a plain
                        integer: check one with `ros2 topic echo /diagnostics --once` on the robot.

The tilt and impact log is kept here (GET /api/tablet/sensors): the tilt from gravity, taken only while the tablet is
still (|a| within 5 % of 1 g), and every impact above IMPACT_G with its peak, on the Thor's clock.

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

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

import origin_guard

log = logging.getLogger("tablet_relay")
router = APIRouter()

ROSBRIDGE_URL = os.environ.get("OPENDUBS_ROSBRIDGE_URL", "ws://127.0.0.1:9090")
FIX_STALE_S = 5.0          # a position older than this is no fix
TOPICS = {
    "/tablet/illuminance": "sensor_msgs/msg/Illuminance",
    "/tablet/battery": "sensor_msgs/msg/BatteryState",
    "/tablet/fix": "sensor_msgs/msg/NavSatFix",
    "/tablet/imu": "sensor_msgs/msg/Imu",
    "/diagnostics": "diagnostic_msgs/msg/DiagnosticArray",
}
# sensor_msgs/BatteryState power_supply_status
CHARGING, DISCHARGING, NOT_CHARGING, FULL = 1, 2, 3, 4
NAN = float("nan")
G = 9.80665
IMPACT_G = 2.0             # an acceleration above this many g is an impact (a knock, a drop, a kerb)
IMPACT_GAP_S = 0.5         # one impact, however many samples its peak spans
STILL_G = 0.05             # |a| within this of 1 g: still enough to read tilt from gravity
ABSENT = [-1.0] + [0.0] * 8
THERMAL = ("none", "light", "moderate", "severe", "critical", "emergency", "shutdown")   # Android's 0..6
THERMAL_EVERY_S = 5.0
THERMAL_STALE_S = 10.0

CAMERAS = ("front", "rear")
CAMERA_DIR = os.environ.get("OPENDUBS_TABLET_CAMERA_DIR") or "/var/lib/opendubs/tablet"
CAMERA_DEFAULT = {"w": 640, "h": 480, "fps": 10, "q": 0.7}
CAMERA_TICK_S = 1.0
DEMAND_STALE_S = 6.0       # no word from the Thor's demand watcher for this long: nobody wants a camera
for _c in CAMERAS:
    TOPICS[f"/tablet/camera/{_c}/image_raw/compressed"] = "sensor_msgs/msg/CompressedImage"
    TOPICS[f"/tablet/camera/{_c}/camera_info"] = "sensor_msgs/msg/CameraInfo"

state = {"page": None, "rtt_ms": None, "published": 0, "dropped": 0, "last": {}, "thermal": None,
         # per camera: mode "auto" (on while subscribed) | "on" | "off"; subscribed; on (what the page
         # was last told); frames published; busy (a frame is being published: the next one is dropped)
         "app": None,                                       # the kiosk app's version and its updater's last word
         "demand": {"at": None, "counts": None},              # the Thor's tablet_camera_demand node
         "tilt": None, "impacts": [], "thermal_at": None, "thermal_sent": None, "thermal_sent_at": 0.0,
         "camera": {c: {"mode": "auto", "subscribed": False, "on": False, "frames": 0, "dropped": 0,
                        "busy": False, "error": None, "w": None, "h": None} for c in CAMERAS}}


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
    if kind == "imu":
        out = []
        for smp in (r.get("samples") if isinstance(r.get("samples"), list) else [])[:64]:
            if not isinstance(smp, dict):
                continue
            a = [_num(smp.get(k), -50 * G, 50 * G) for k in ("ax", "ay", "az")]
            if None in a:
                continue
            out.append(("/tablet/imu", {
                "header": {"stamp": stamp(_num(smp.get("age_ms"), 0, 10_000) or 0.0, rtt_ms, now), "frame_id": "tablet_imu_link"},
                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}, "orientation_covariance": ABSENT,
                "angular_velocity": {"x": 0.0, "y": 0.0, "z": 0.0}, "angular_velocity_covariance": ABSENT,
                "linear_acceleration": {"x": a[0], "y": a[1], "z": a[2]},
                "linear_acceleration_covariance": [0.0] * 9}))                  # unknown
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

    def __init__(self, url: str = ROSBRIDGE_URL, topics: dict | None = None, latched=()):
        self.url, self.ws, self._task = url, None, None
        self.topics, self.latched = topics if topics is not None else TOPICS, set(latched)

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is None or self._task.done() or self._task.get_loop() is not loop:
            self._task = loop.create_task(self._run())

    async def _run(self) -> None:
        import websockets
        delay = 1.0
        while True:
            try:
                async with websockets.connect(self.url, max_size=None, open_timeout=3) as ws:
                    for topic, typ in self.topics.items():
                        adv = {"op": "advertise", "topic": topic, "type": typ}
                        if topic in self.latched:
                            adv["latch"] = True          # rosbridge: transient-local durability
                        await ws.send(json.dumps(adv))
                    self.ws, delay = ws, 1.0
                    log.info("tablet relay: rosbridge connected at %s", self.url)
                    async for _ in ws:                       # nothing is asked for; keeps the link honest
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


def camera_info(cam: str, w: int, h: int, hdr: dict) -> dict:
    """CameraInfo for `cam` at w x h: this robot's calibration if it keeps one (and it is for this size),
    else zeros, which the message type defines as uncalibrated."""
    info = {"header": hdr, "height": h, "width": w, "distortion_model": "", "d": [],
            "k": [0.0] * 9, "r": [0.0] * 9, "p": [0.0] * 12, "binning_x": 0, "binning_y": 0,
            "roi": {"x_offset": 0, "y_offset": 0, "height": 0, "width": 0, "do_rectify": False}}
    path = os.path.join(CAMERA_DIR, f"camera_{cam}.yaml")
    try:
        import yaml
        with open(path, encoding="utf-8") as fh:
            c = yaml.safe_load(fh)
        if int(c["image_width"]) == w and int(c["image_height"]) == h:
            info.update(distortion_model=c.get("distortion_model", "plumb_bob"),
                        d=[float(x) for x in c["distortion_coefficients"]["data"]],
                        k=[float(x) for x in c["camera_matrix"]["data"]],
                        r=[float(x) for x in c["rectification_matrix"]["data"]],
                        p=[float(x) for x in c["projection_matrix"]["data"]])
    except FileNotFoundError:
        pass
    except Exception as e:                                  # noqa: BLE001 — a bad file: uncalibrated, and say so
        log.warning("tablet relay: %s unreadable (%s): publishing it uncalibrated", path, e)
    return info


def camera_wanted(c: dict) -> bool:
    return c["mode"] == "on" or (c["mode"] == "auto" and c["subscribed"])


async def tell_page_cameras(force: bool = False) -> None:
    """Tell the kiosk page which cameras to stream, when that changes (or `force`, on connect)."""
    page = state["page"]
    want = {c: camera_wanted(state["camera"][c]) for c in CAMERAS}
    changed = any(want[c] != state["camera"][c]["on"] for c in CAMERAS)
    for c in CAMERAS:
        state["camera"][c]["on"] = want[c]
    if page is not None and (changed or force):
        try:
            await page.send_json({"camera": {c: {"on": want[c], **CAMERA_DEFAULT} for c in CAMERAS}})
        except Exception:                                   # noqa: BLE001
            pass


def _expire_demand() -> None:
    """FAIL OFF: a demand not renewed within DEMAND_STALE_S (the watcher stopped, ROS is down) is none."""
    at = state["demand"]["at"]
    if at is None or time.monotonic() - at > DEMAND_STALE_S:
        for c in CAMERAS:
            state["camera"][c]["subscribed"] = False


async def _watch_subscribers() -> None:
    """Every CAMERA_TICK_S: drop a stale demand, tell the page, and keep the thermal status going."""
    while True:
        _expire_demand()
        await tell_page_cameras()
        await publish_thermal()                              # every 5 s, and STALE once readings stop
        await asyncio.sleep(CAMERA_TICK_S)


_watcher = None


def _start_watcher() -> None:
    global _watcher
    loop = asyncio.get_running_loop()
    if _watcher is None or _watcher.done() or _watcher.get_loop() is not loop:
        _watcher = loop.create_task(_watch_subscribers())


def parse_frame(data: bytes):
    """A camera frame from the page: 4 bytes (big-endian) of header length, the JSON header
    {"cam", "w", "h", "age_ms"}, then the JPEG. Returns (header, jpeg) or None."""
    if len(data) < 4:
        return None
    n = int.from_bytes(data[:4], "big")
    if n <= 0 or 4 + n > len(data):
        return None
    try:
        hdr = json.loads(data[4:4 + n])
    except ValueError:
        return None
    jpeg = data[4 + n:]
    if not isinstance(hdr, dict) or hdr.get("cam") not in CAMERAS or jpeg[:2] != b"\xff\xd8":
        return None
    return hdr, jpeg


async def publish_frame(data: bytes) -> None:
    import base64
    got = parse_frame(data)
    if got is None:
        return
    h, jpeg = got
    c = state["camera"][h["cam"]]
    if not c["on"] or c["busy"]:                            # not asked for, or still sending the last one
        c["dropped"] += 1
        return
    w, hh = int(_num(h.get("w"), 1, 8192) or 0), int(_num(h.get("h"), 1, 8192) or 0)
    c["busy"], c["w"], c["h"] = True, w, hh
    try:
        hdr = {"stamp": stamp(_num(h.get("age_ms"), 0, 10_000) or 0.0, state["rtt_ms"]),
               "frame_id": f"tablet_{h['cam']}_camera_optical_frame"}
        ok = await ros.publish(f"/tablet/camera/{h['cam']}/image_raw/compressed",
                               {"header": hdr, "format": "jpeg", "data": base64.b64encode(jpeg).decode()})
        await ros.publish(f"/tablet/camera/{h['cam']}/camera_info", camera_info(h["cam"], w, hh, hdr))
        c["frames" if ok else "dropped"] += 1
    finally:
        c["busy"] = False


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
    _start_watcher()
    await socket.send_json({"ok": True})
    for c in CAMERAS:
        state["camera"][c]["on"] = False                   # a new page has nothing open yet
    await tell_page_cameras(force=True)
    try:
        while True:
            raw = await socket.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            if raw.get("bytes") is not None:                # a camera frame, published beside this loop:
                if socket is state["page"]:                 # while one is in flight the next is dropped
                    asyncio.get_running_loop().create_task(publish_frame(raw["bytes"]))
                continue
            try:
                m = json.loads(raw.get("text") or "null")
            except ValueError:
                continue
            if not isinstance(m, dict):
                continue
            if "camera_error" in m and isinstance(m["camera_error"], dict):   # the page could not open one
                for c, why in m["camera_error"].items():
                    if c in CAMERAS:
                        state["camera"][c]["error"] = str(why)[:200] if why else None
                continue
            if "ping" in m:
                await socket.send_json({"pong": m["ping"]})
                continue
            if "rtt_ms" in m:
                state["rtt_ms"] = _num(m["rtt_ms"], 0, 5000)
                continue
            if socket is not state["page"]:
                break
            if m.get("kind") == "app":                     # the kiosk app: version and self-update state
                state["app"] = {"version": str(m.get("version"))[:32], "versionCode": _num(m.get("versionCode"), 0),
                                "update": m.get("update") if isinstance(m.get("update"), dict) else None}
                continue
            if m.get("kind") == "battery" and isinstance(m.get("thermal"), (str, type(None))):
                state["thermal"] = m.get("thermal")
                state["thermal_at"] = time.monotonic() if m.get("thermal") else state["thermal_at"]
                await publish_thermal()
            for topic, msg in to_messages(m, state["rtt_ms"]):
                if topic == "/tablet/imu":
                    note_motion(msg)
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
            for c in CAMERAS:
                state["camera"][c]["on"] = False


def thermal_status(word, age_s) -> dict:
    """The tablet's thermal state as one DiagnosticStatus (levels: 0 OK, 1 WARN, 2 ERROR, 3 STALE)."""
    if word not in THERMAL or age_s is None or age_s > THERMAL_STALE_S:
        level, message, value = 3, "no reading", ""
    else:
        n = THERMAL.index(word)
        level, message, value = (0 if n <= 1 else 1 if n <= 3 else 2), word, str(n)
    return {"level": level, "name": "tablet: thermal", "message": message, "hardware_id": "galaxy-tab-a11",
            "values": [{"key": "thermal_status", "value": value}]}


async def publish_thermal(force: bool = False) -> None:
    """On a change of the status (or its staleness), and every THERMAL_EVERY_S."""
    now = time.monotonic()
    age = None if state["thermal_at"] is None else now - state["thermal_at"]
    st = thermal_status(state["thermal"], age)
    key = (st["level"], st["message"])
    if not force and key == state["thermal_sent"] and now - state["thermal_sent_at"] < THERMAL_EVERY_S:
        return
    if await ros.publish("/diagnostics", {"header": {"stamp": stamp(0, None), "frame_id": ""}, "status": [st]}):
        state["thermal_sent"], state["thermal_sent_at"] = key, now


def note_motion(msg: dict) -> None:
    """The tilt and impact log, from one IMU sample: tilt only while still, impacts above IMPACT_G."""
    a = msg["linear_acceleration"]
    g = math.sqrt(a["x"] ** 2 + a["y"] ** 2 + a["z"] ** 2) / G
    t = msg["header"]["stamp"]["sec"] + msg["header"]["stamp"]["nanosec"] / 1e9
    if abs(g - 1.0) <= STILL_G:
        # Pitch: the screen's top edge tipped back (+) or forward; roll: its right edge down (+). From
        # gravity alone, so meaningful only while still, which is why it is taken only then.
        state["tilt"] = {"t": t, "pitch_deg": round(math.degrees(math.atan2(a["y"], a["z"])), 1),
                         "roll_deg": round(math.degrees(math.atan2(a["x"], math.hypot(a["y"], a["z"]))), 1)}
    if g >= IMPACT_G:
        imp = state["impacts"]
        if imp and t - imp[-1]["t"] < IMPACT_GAP_S:
            imp[-1]["peak_g"] = max(imp[-1]["peak_g"], round(g, 2))
        else:
            imp.append({"t": t, "peak_g": round(g, 2)})
            del imp[:-50]
            log.info("tablet: impact %.1f g at %.3f", g, t)


@router.get("/api/tablet/sensors")
def sensors_status() -> dict:
    """What the relay last published, for the dashboard and for checking it by hand."""
    return {"page": state["page"] is not None, "rosbridge": ros.ws is not None, "rtt_ms": state["rtt_ms"],
            "published": state["published"], "dropped": state["dropped"], "thermal": state["thermal"],
            "app": state["app"], "tilt": state["tilt"], "impacts": state["impacts"][-10:],
            "camera": {c: {k: v for k, v in d.items() if k != "busy"} for c, d in state["camera"].items()},
            "last": {t: json.loads(json.dumps(m, default=str).replace("NaN", "null")) for t, m in state["last"].items()}}


@router.put("/api/tablet/camera")
async def set_camera(request: Request) -> JSONResponse:
    """Switch a camera by hand: {"front": "on"|"off"|"auto", "rear": ...}. "auto" (the default) streams
    only while something subscribes. Not kept across a restart: on demand is the resting state."""
    try:
        change = await request.json()
    except ValueError:
        change = None
    if not isinstance(change, dict) or not change or any(
            c not in CAMERAS or v not in ("on", "off", "auto") for c, v in change.items()):
        return JSONResponse({"error": 'send {"front"|"rear": "on"|"off"|"auto"}'}, status_code=400)
    for c, v in change.items():
        state["camera"][c]["mode"] = v
    await tell_page_cameras()
    return JSONResponse({c: {"mode": d["mode"], "on": d["on"]} for c, d in state["camera"].items()})


@router.post("/api/tablet/camera/demand")
async def camera_demand(request: Request) -> JSONResponse:
    """Which cameras something on the Thor subscribes to: {"front": bool, "rear": bool, "counts": {...}},
    from the Thor lane's tablet_camera_demand node (it counts every subscriber, the CLI's too). Sent on
    a change and every 2 s; a camera in "auto" streams while its demand is true and fresh, and stops
    DEMAND_STALE_S after the last word, so a stopped watcher can never leave a camera on."""
    try:
        d = await request.json()
    except ValueError:
        d = None
    if not isinstance(d, dict) or any(not isinstance(d.get(c), bool) for c in CAMERAS):
        return JSONResponse({"error": 'send {"front": true|false, "rear": true|false}'}, status_code=400)
    state["demand"] = {"at": time.monotonic(), "counts": d.get("counts") if isinstance(d.get("counts"), dict) else None}
    for c in CAMERAS:
        state["camera"][c]["subscribed"] = d[c]
    _start_watcher()
    await tell_page_cameras()
    return JSONResponse({c: {"mode": state["camera"][c]["mode"], "on": state["camera"][c]["on"]} for c in CAMERAS})
