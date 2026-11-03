"""OpenDubs kiosk dashboard — FastAPI backend.

Serves the static dashboard and streams a merged system+telemetry snapshot over
a websocket once a second. Exposes a small set of NON-SAFETY soft-control
endpoints (acknowledge, log action). Nothing here commands actuators or acts as
a safety control — safety authority stays in hardware + StamPLC per
docs/control-authority.md.

Run:
    uvicorn app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from refusals import refuse

import metrics
import status as status_mod
import telemetry

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
VNC_DISABLED_FLAG = status_mod.VNC_DISABLED_FLAG

app = FastAPI(title="OpenDubs Kiosk Dashboard")
# A state-changing request that another web page sent is refused; one that names no page (curl,
# scripts, the Thor's services) is not. Not a login — see origin_guard.py.
import origin_guard  # noqa: E402
origin_guard.install(app)

# Server-side state for non-safety soft controls.
STATE = {"last_action": None}



@app.on_event("startup")
def _start_state_out() -> None:
    """Drive the PRISM + Tab5 state indicators from the authoritative robot FSM."""
    import state_out
    state_out.start()


@app.on_event("startup")
async def _start_airplay_relay() -> None:
    """Drain the AirPlay receiver's FIFOs from the start, listener or not (airplay_relay.py)."""
    import airplay_relay
    await airplay_relay.start()


@app.on_event("startup")
async def _start_neck_follow() -> None:
    """The head follows the person the face sees (neck_follow.py): it moves the neck by itself."""
    import neck_follow
    await neck_follow.start()


@app.get("/api/head/follow")
async def _neck_follow_status(live: int = 0) -> dict:
    """Why the head is or is not following right now, and what it last sent. `neck` is the last NECK
    read before a send (`neck_age_s` says how old); `?live=1` adds `neck_live`, asked of the neck now."""
    import neck_follow
    s = neck_follow.status()
    if live:
        s["neck_live"] = await asyncio.get_running_loop().run_in_executor(None, neck_follow.live_neck)
    return s


@app.on_event("startup")
async def _start_face_door() -> None:
    """The tablet face's door: head-protocol UDP on 127.0.0.1:4242, to the face page (face_door.py)."""
    import face_door
    await face_door.start()


# No shutdown hook for the tracker. It is a systemd service with its own lifecycle, and
# restarting the dashboard must not stop the robot tracking — which is what killing a child
# process on shutdown did, every time the backend was redeployed.


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "ts": time.time()}


@app.get("/api/frontend-version")
def frontend_version() -> dict:
    """Changes whenever a deploy changes any page file. The kiosk tablet has no reload control, so
    the page polls this and reloads itself (app.js followDeploys). Names, sizes and mtimes: the
    frontend is a handful of files, so this is cheap enough to compute on every call."""
    h = hashlib.sha1()
    for p in sorted(FRONTEND_DIR.rglob("*")):
        if p.is_file():
            st = p.stat()
            h.update(f"{p.relative_to(FRONTEND_DIR)}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return {"version": h.hexdigest()[:12]}


@app.get("/api/snapshot")
def snapshot() -> JSONResponse:
    return JSONResponse({
        "system": metrics.collect(), "telemetry": telemetry.collect(),
        "status": status_mod.collect(), "state": STATE,
    })




@app.get("/api/arms/status")
def arms_status() -> JSONResponse:
    """Arm + teleop state for the TELEOP page. Cheap enough to poll; motor reads are cached,
    because they are CAN traffic on the same bus the controllers are using."""
    import openarm
    return JSONResponse(openarm.snapshot())


@app.post("/api/arms/{op}")
def arms_op(op: str, arm: str = "both") -> JSONResponse:
    """Run one arm operation, in the background, one at a time.

    park, hold, activate and down go to the arm service (software/openarm/gafro/arm_service.py) over
    its socket; it owns the guards, and refuses what a person at the e-stop, CH5, CH6 or the start
    pose do not allow. START brings that arm's service up through its systemd unit. There is no
    fallback to `openarm-up`, and the dashboard has no other way to move an arm.

    Returns immediately: `activate` alone waits up to 10 s for the gripper motor, and an HTTP
    request held open that long is a browser timeout waiting to happen. Poll /api/arms/status.
    """
    import openarm
    ok, detail = openarm.start_op(op.lower(), arm.lower())
    if not ok:
        # 409 for "something else is running" -- a second arm command mid-motion is a collision,
        # not a queue -- and 400 for a malformed one.
        code = 409 if "still running" in detail else 400
        return JSONResponse({"ok": False, "error": detail,
                             "hint": ("arm operations go to the arm service (one per arm, "
                                      "software/openarm/opendubs-arm@.service): START it first; the START button needs "
                                      "the opendubs-arm sudoers rule")}, status_code=code)
    return JSONResponse({"ok": True, "started": detail, "poll": "/api/arms/status"})


@app.get("/api/arms/teleop")
def arms_teleop() -> JSONResponse:
    """The teleop bridge's per-hand state -- why a grip, a home or a target is being refused.
    Cheap (no CAN, no shell), so the TELEOP page polls it fast while the bridge runs."""
    import openarm
    return JSONResponse(openarm.teleop_status())


@app.post("/api/teleop/{verb}")
def teleop_control(verb: str) -> JSONResponse:
    """Start or stop the WebXR teleop bridge via its systemd unit.

    It does NOT start the arm stack or activate the arms; the bridge hard-fails within ~30 s
    without the gripper action servers, so `openarm-up activate` comes first and deliberately
    stays a separate, explicit step.
    """
    import openarm
    ok, detail = openarm.teleop(verb.lower())
    if not ok:
        return JSONResponse({"ok": False, "error": detail,
                             "hint": (f"needs a sudoers rule for: systemctl {verb.lower()} "
                                      f"{openarm.TELEOP_UNIT}")}, status_code=503)
    return JSONResponse({"ok": True, "detail": detail})


@app.get("/api/stamplc")
def stamplc_status() -> JSONResponse:
    """Lightweight StamPLC view (io / safety / encoder / power) for the ROS bridge
    (opendubs-stamplc-bridge) — avoids the heavy full snapshot on a fast poll."""
    f = status_mod.collect_fast()
    return JSONResponse({"io": f["io"], "safety": f["safety"],
                         "encoder": f["encoder"], "power": f["power"],
                         # Z-axis state, so the Tab5 bridge can report real lift height and
                         # guard state instead of running its own StamPLC reader.
                         "drive": f.get("drive") or {}})


@app.post("/api/mast/{cmd}")
def mast_control(cmd: str, by: str = "") -> JSONResponse:
    """Z-axis intent: up | down | stop.

    This is the ONLY way anything on the network moves the lift. It becomes `MAST CMD` on
    the StamPLC, which feeds the same driver the RC sticks do — so the pinch guard, the
    limit detection and the authority/deadman gate all still apply. Writing the lift's
    relays directly (as /api/relay used to allow) walks past every one of them.

    The command is an INTENT and the firmware expires it after 250 ms, so a caller has to
    keep asking. That is deliberate: a caller that dies stops the lift.

    `?by=show` marks the show runner's own commands. Every OTHER command is an operator's, and
    is counted (choreo_proxy.note_operator_mast): a running show's lift output stands down when
    an operator touches the lift — the operator wins, as the RC stick does. The head's RAISE/LOWER
    do not come through here (the Tab5 sends `MAST CMD` to the StamPLC itself), so they are not
    counted."""
    import stamplc
    v = {"up": 1.0, "down": -1.0, "stop": 0.0}.get(cmd.lower())
    if v is None:
        return JSONResponse({"ok": False, "error": "use up|down|stop"}, status_code=400)
    if by != "show":
        import choreo_proxy
        choreo_proxy.note_operator_mast()
    if not stamplc.reader.send(f"MAST CMD {v:.2f}"):
        return refuse("LINK_STAMPLC_LOST", "no link to the StamPLC — command not sent", 503)
    return JSONResponse({"ok": True, "mast": cmd.lower(), "v": v})


@app.post("/api/relay/{ch}/{state}")
def relay_control(ch: str, state: str) -> JSONResponse:
    """Toggle a dashboard-controllable StamPLC relay.

    Only relay 3 is free. The others all mean something now:
      R0  e-stop contactor
      R1  scissor lift DOWN leg   <- was documented "unassigned"; it is not
      R2  scissor lift UP leg
    Relays 1 and 2 are the lift's buttons, and pressing them from here would bypass the
    mast driver and its pinch guard entirely. Use /api/mast/{up|down|stop} instead."""
    import stamplc
    try:
        c = int(ch)
    except ValueError:
        return JSONResponse({"ok": False, "error": "bad channel"}, status_code=400)
    if c in (1, 2):
        return refuse("LIFT_RELAY_RESERVED", "relays 1 and 2 are the lift legs — use "
                      "/api/mast/{up|down|stop} so the pinch guard applies", 409)
    if c != 3:
        return JSONResponse({"ok": False, "error": "only relay 3 is free"}, status_code=400)
    on = state.lower() in ("1", "on", "true")
    if not stamplc.reader.send(f"RELAY {c} {1 if on else 0}"):
        return refuse("LINK_STAMPLC_LOST", "no link to the StamPLC — command not sent", 503)
    return JSONResponse({"ok": True, "relay": c, "state": on})


@app.post("/api/estop")
def estop() -> JSONResponse:
    """Software E-stop — safe direction: latch the flag and drop the contactor (relay 0)."""
    import stamplc
    # Safe direction, so latch our own flag whatever happens — but the operator must be told if
    # the board was not reached, because the contactor may still be closed.
    status_mod.set_software_estop(True)
    delivered = stamplc.reader.send("ESTOP")
    return JSONResponse({"ok": True, "estop": True, "delivered": delivered,
                         **({} if delivered else
                            {"warning": "no link to the StamPLC — the software latch is set but "
                                        "the contactor may still be closed. Use a physical "
                                        "E-STOP button."})})


@app.post("/api/estop/clear")
def estop_clear() -> JSONResponse:
    """Re-arm after E-stop. GUARDED by the supervisor (review R3/R4): refuses while
    the button is still pressed, telemetry is stale, the contactor is welded, or CH5
    isn't OFF. Only on a clean guard does it clear the latch + re-energize relay 0."""
    result = status_mod.try_estop_clear()
    if not result["ok"]:
        # The first blocker's code leads; every code is in `codes`, beside its sentence in
        # `blockers`. The codes are named where they are raised (supervisor.py, status.py).
        return JSONResponse({"ok": False, "estop": True, "code": result["codes"][0],
                             "error": "re-arm refused: " + " · ".join(result["blockers"]),
                             "blockers": result["blockers"], "codes": result["codes"]},
                            status_code=409)
    return JSONResponse({"ok": True, "estop": False})


@app.post("/api/fault/clear")
def fault_clear() -> JSONResponse:
    """Clear a latched DEGRADED fault (review R4; CLEAR FAULT on Operate). Guarded by the
    supervisor: only non-fatal faults whose condition is absent clear, and only once
    telemetry is ready. Fatal faults (welded contactor, PDH hardware) need service.
    Whatever it can't clear it names, with why, and the first one's troubleshooting code."""
    r = status_mod.clear_faults()
    if not r["kept"]:
        return JSONResponse({"ok": True, "cleared": r["cleared"]})
    k = r["kept"][0]
    if k["why"] == "not ready":
        return refuse("LINK_NOT_READY", "not cleared: the robot is not ready yet (no fresh telemetry "
                      "and RC link)", **r)
    why = {"needs service": "it needs service before it can be cleared",
           "still present": "its condition is still present"}
    text = "not cleared: " + "; ".join(f"{x['cause']}: {why[x['why']]}" for x in r["kept"])
    return refuse(k["code"] or "LINK_NOT_READY", text, **r)


@app.post("/api/i2c/scan")
def i2c_scan() -> JSONResponse:
    """Trigger an on-demand full I2C bus sweep on the StamPLC (not run periodically
    — probing all 112 addresses is high-latency). Result arrives as the [I2C] full:
    line → status.io.i2c_full."""
    import stamplc
    stamplc.reader.send("I2C_SCAN")
    return JSONResponse({"ok": True})


@app.post("/api/i2c/info")
def i2c_info() -> JSONResponse:
    """Ask the StamPLC which physical pins the external scan bus is bound to
    (result arrives as the [I2C] info/bus line → status.io.i2c_bus)."""
    import stamplc
    stamplc.reader.send("I2C_INFO")
    return JSONResponse({"ok": True})


@app.post("/api/led/color/{r}/{g}/{b}")
def led_color(r: int, g: int, b: int, bright: int = 100) -> JSONResponse:
    """Set the PRISM LED strip to a solid RGB color (0-255 each, brightness 0-100)."""
    import stamplc
    cl = lambda v, hi: max(0, min(hi, int(v)))
    stamplc.reader.send(f"LED_COLOR {cl(r,255)} {cl(g,255)} {cl(b,255)} {cl(bright,100)}")
    return JSONResponse({"ok": True, "rgb": [cl(r,255), cl(g,255), cl(b,255)], "bright": cl(bright,100)})


@app.post("/api/led/off")
def led_off() -> JSONResponse:
    """Clear the PRISM (all animations off)."""
    import stamplc
    stamplc.reader.send("LED_OFF")
    return JSONResponse({"ok": True})


@app.post("/api/led/len/{n}")
def led_len(n: int) -> JSONResponse:
    """Set the PRISM strip length (number of LEDs)."""
    import stamplc
    stamplc.reader.send(f"LED_LEN {max(0, min(255, int(n)))}")
    return JSONResponse({"ok": True, "len": max(0, min(255, int(n)))})


@app.post("/api/enc/reset")
def enc_reset() -> JSONResponse:
    """Zero the mast encoder (home)."""
    import stamplc
    stamplc.reader.send("ENC_RESET")
    return JSONResponse({"ok": True})


@app.post("/api/pdh/{action}")
def pdh_control(action: str) -> JSONResponse:
    """Control the REV PDH switchable channel via the StamPLC serial."""
    import stamplc
    if action == "on":
        stamplc.reader.send("PDH_SWITCH 1")
    elif action == "off":
        stamplc.reader.send("PDH_SWITCH 0")
    elif action == "clear_faults":
        stamplc.reader.send("PDH_CLEAR_FAULTS")
    else:
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    return JSONResponse({"ok": True, "action": action})


@app.post("/api/vnc/{action}")
def vnc_control(action: str) -> JSONResponse:
    """Start/stop the VNC mirror. A flag file gates the vnc.sh wrapper loop;
    stopping also kills the live x11vnc immediately."""
    import os
    import subprocess
    if action == "stop":
        os.makedirs(os.path.dirname(VNC_DISABLED_FLAG), exist_ok=True)
        open(VNC_DISABLED_FLAG, "w").close()
        subprocess.Popen(["pkill", "-x", "x11vnc"])
    elif action == "start":
        try:
            os.remove(VNC_DISABLED_FLAG)
        except FileNotFoundError:
            pass
    else:
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    return JSONResponse({"ok": True, "action": action})


@app.post("/api/action/{name}")
def action(name: str) -> dict:
    """Record a non-safety soft action (e.g. a view-level toggle). No actuator
    or safety effect — purely informational/UI."""
    STATE["last_action"] = {"name": name, "ts": time.time()}
    return {"ok": True, "action": STATE["last_action"]}


@app.post("/api/system/{action}")
def system_power(action: str) -> JSONResponse:
    """Power control for the Thor itself (shutdown/reboot). Requires a sudoers
    rule allowing the kiosk user to run systemctl poweroff/reboot. Delays 1s so
    the HTTP response returns before the machine goes down."""
    import subprocess
    cmd = {"shutdown": "poweroff", "reboot": "reboot"}.get(action)
    if not cmd:
        return JSONResponse({"ok": False, "error": "unknown action"}, status_code=400)
    subprocess.Popen(["sh", "-c", f"sleep 1; sudo systemctl {cmd}"])
    return JSONResponse({"ok": True, "action": action})


@app.websocket("/ws")
async def ws(socket: WebSocket) -> None:
    if await origin_guard.refuse_websocket(socket):
        return
    await socket.accept()
    # RC telemetry is cheap (reads the latest StamPLC frame from memory) → push
    # fast for responsive sticks. System metrics are expensive (nvidia-smi
    # subprocess, sysfs) → include them only ~1x/sec.
    HZ = 20
    FAST_EVERY = 2    # ~10 Hz: cheap in-memory status (safety/state/io/encoder)
    SLOW_EVERY = HZ   # ~1 Hz: subprocess status (net/vnc) + system metrics
    tick = 0
    slow = {}
    # Which robot-Wi-Fi client this page is, so it can find its own signal in net.robot_wifi.
    peer = socket.client.host if socket.client else None
    loop = asyncio.get_event_loop()
    try:
        while True:
            payload = {"telemetry": telemetry.collect(), "state": STATE}
            if tick % SLOW_EVERY == 0:
                # offload blocking collectors (nvidia-smi, nmcli, pgrep…) so the
                # fast path isn't stalled; cache the slow status between refreshes.
                payload["system"] = await loop.run_in_executor(None, metrics.collect)
                slow = await loop.run_in_executor(None, status_mod.collect_slow)
            if tick % FAST_EVERY == 0:
                # cheap (reader snapshots only) → safe to run inline at 10 Hz.
                payload["status"] = {**status_mod.collect_fast(), **slow, "client_ip": peer}
            await socket.send_text(json.dumps(payload))
            tick += 1
            await asyncio.sleep(1.0 / HZ)
    except WebSocketDisconnect:
        return
    except Exception:
        return


ROSBRIDGE_URL = os.environ.get("OPENDUBS_ROSBRIDGE_URL", "ws://127.0.0.1:9090")


@app.websocket("/rosbridge")
async def rosbridge_proxy(socket: WebSocket) -> None:
    """rosbridge through the dashboard's own origin. A page served over HTTPS (the tablet, :8443)
    may not open ws://…:9090 — mixed content — so it connects here as wss://…/rosbridge and every
    message is relayed unchanged to the local rosbridge. Over plain HTTP the page still talks to
    :9090 directly. Nothing here interprets ROS traffic."""
    import websockets
    if await origin_guard.refuse_websocket(socket):     # a foreign page may not publish to ROS
        return
    await socket.accept()
    try:
        async with websockets.connect(ROSBRIDGE_URL, max_size=None, open_timeout=3) as up:
            async def to_page():
                async for msg in up:
                    if isinstance(msg, bytes):
                        await socket.send_bytes(msg)
                    else:
                        await socket.send_text(msg)

            async def to_ros():
                while True:
                    m = await socket.receive()
                    if m["type"] == "websocket.disconnect":
                        return
                    if m.get("text") is not None:
                        await up.send(m["text"])
                    elif m.get("bytes") is not None:
                        await up.send(m["bytes"])

            tasks = {asyncio.create_task(to_page()), asyncio.create_task(to_ros())}
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
    except Exception:
        pass                      # rosbridge down: the page shows ROS DOWN and retries
    finally:
        try:
            await socket.close()
        except Exception:
            pass


# Static frontend mounted last so /api and /ws take precedence. Serve with no-cache so a
# long-running kiosk browser always revalidates and picks up a redeploy on reload — the
# frontend is small and same-LAN, so skipping the cache costs nothing and avoids stale UIs.
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


# The Shows page's routines, forwarded to the show runner (choreo_proxy.py). Included before the
# static mount so /api/choreo/* is not shadowed by it.
from choreo_proxy import router as _choreo_router  # noqa: E402
app.include_router(_choreo_router)

# The show library, read-only, for the tablet's cache (library_serve.py, tablet plan 5.3).
from library_serve import router as _library_router  # noqa: E402
app.include_router(_library_router)

from show_light import router as _show_light_router  # noqa: E402  a show's colour, layered (state_out.py)
app.include_router(_show_light_router)
# The ARCHITECT tab: the program, what the listener hears, the Orbbec's image. Read-only (architect_proxy.py).
from architect_proxy import router as _architect_router  # noqa: E402
app.include_router(_architect_router)

# The tablet face: its door (/ws/face) and its page (/face/), from software/face-web beside this app
# (/opt/opendubs/face-web on the robot, deploy.sh). Before the static mount, so neither is shadowed.
from face_door import router as _face_door_router  # noqa: E402
app.include_router(_face_door_router)
from head_settings import router as _head_settings_router  # noqa: E402
app.include_router(_head_settings_router)
# The face looks this robot has, and the content folder's looks for the face (head_looks.py: not
# face_looks, which is the show runner's module of that name, imported into this process by library.py). Before the
# /face static mount below, which would otherwise answer /face/content itself.
from head_looks import router as _head_looks_router  # noqa: E402
app.include_router(_head_looks_router)

# The face page's microphone stream, relayed to the voice service on loopback (voice_proxy.py, the
# Voice lane's). Before the static mounts, which would otherwise take the WebSocket's path.
from voice_proxy import router as _voice_router  # noqa: E402
app.include_router(_voice_router)

# Play to robot: sounds sent from a phone, stored on the robot (play_store.py; the page is /play/).
from play_store import router as _play_router  # noqa: E402
app.include_router(_play_router)
# AirPlay, live: the Thor's receiver → the face page (airplay_relay.py; the receiver is the Thor lane's).
from airplay_relay import router as _airplay_router  # noqa: E402
app.include_router(_airplay_router)
# The robot tablet's sensors into ROS as /tablet/*: light, battery, GNSS (tablet_relay.py).
from tablet_relay import router as _tablet_relay_router  # noqa: E402
app.include_router(_tablet_relay_router)
# The neck's HOLD, shown and released from the dashboard (neck_state.py; head protocol v9).
from neck_state import router as _neck_hold_router  # noqa: E402
app.include_router(_neck_hold_router)
# The kiosk app's next build, signed on the Thor, which the tablet installs itself (kiosk_update.py).
from kiosk_update import router as _kiosk_update_router  # noqa: E402
app.include_router(_kiosk_update_router)
# What the voice may say about the robot: what the dashboard shows, in its words (spoken_status.py).
from spoken_status import router as _spoken_router  # noqa: E402
app.include_router(_spoken_router)
# The stage page's live skeletons (stage.py, Choreography's): the relay's frames in (localhost only), the
# projector's page out. The page itself is static: frontend/stage/.
from stage import router as _stage_router  # noqa: E402
app.include_router(_stage_router)
# The wall tag test: hold the head, one back-camera frame, the AprilTags in it (tag_test.py).
from tag_test import router as _tag_test_router  # noqa: E402
app.include_router(_tag_test_router)
# The stage box's sound: its output, Bluetooth speaker, volumes and test tone (stage_box.py).
from stage_box import router as _stage_box_router  # noqa: E402
app.include_router(_stage_box_router)
FACE_DIR = Path(os.environ.get("OPENDUBS_FACE_WEB_DIR") or FRONTEND_DIR.parent.parent / "face-web")
if FACE_DIR.is_dir():
    app.mount("/face", NoCacheStaticFiles(directory=str(FACE_DIR), html=True), name="face")

# This repository's docs, built on the dev machine at deploy time (deploy.sh), so the "?" beside a
# refusal opens its troubleshooting entry with no internet and at the deployed version. Kept out of
# frontend/ so that 35 MB of pages neither feeds /api/frontend-version nor reloads the tablet on
# every deploy. Mounted only if present: a dashboard run without them falls back to the public site.
DOCS_DIR = FRONTEND_DIR.parent / "docs-site"
if DOCS_DIR.is_dir():
    app.mount("/docs", NoCacheStaticFiles(directory=str(DOCS_DIR), html=True), name="docs")

# follow_symlink: in a checkout frontend/assets/arm is a symlink into description/openarm (the arm view's chain.json and meshes), which StaticFiles refuses by default, so the pose view
# could not load anywhere but on the robot, where deploy.sh copies the files and there is no symlink.
app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True, follow_symlink=True), name="static")
