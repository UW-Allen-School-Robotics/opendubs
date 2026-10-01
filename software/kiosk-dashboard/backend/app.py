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
import json
import time
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import metrics
import status as status_mod
import telemetry

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
VNC_DISABLED_FLAG = status_mod.VNC_DISABLED_FLAG

app = FastAPI(title="OpenDubs Kiosk Dashboard")

# Server-side state for non-safety soft controls.
STATE = {"acknowledged_at": None, "last_action": None, "ai_tracking": False}

TRACKER_UNIT = "opendubs-yolo-tracker"


def _tracker_active() -> bool:
    """Is the face tracker running? Asked of systemd, not remembered.

    This used to be a flag the dashboard set when it spawned the tracker itself, which went
    wrong in both directions: the flag survived a crash, and an instance started any other way
    was invisible. systemd already knows, and it is the only thing that does.
    """
    try:
        r = subprocess.run(["systemctl", "is-active", TRACKER_UNIT],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "active"
    except Exception:
        return False


@app.on_event("startup")
def _start_state_out() -> None:
    """Drive the PRISM + Tab5 state indicators from the authoritative robot FSM."""
    import state_out
    state_out.start()


# No shutdown hook for the tracker. It is a systemd service with its own lifecycle, and
# restarting the dashboard must not stop the robot tracking — which is what killing a child
# process on shutdown did, every time the backend was redeployed.


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "ts": time.time()}


@app.get("/api/snapshot")
def snapshot() -> JSONResponse:
    # Report what systemd says, so the button cannot claim the tracker is running after it has
    # died — or claim it is stopped while it happily tracks.
    STATE["ai_tracking"] = _tracker_active()

    return JSONResponse({
        "system": metrics.collect(), "telemetry": telemetry.collect(),
        "status": status_mod.collect(), "state": STATE,
    })


@app.post("/api/tracker/{state}")
def tracker_control(state: str) -> JSONResponse:
    """Start or stop the AI face tracker.

    This drives the systemd unit. It used to `subprocess.Popen` the script itself with the Tab5's
    address hardcoded, which was wrong three ways: a second copy fought the service for the
    camera and lost (a RealSense admits one client), the hardcoded IP broke whenever DHCP moved
    the head, and a tracker started by systemd at boot was invisible to the button.
    """
    target = state.lower()
    if target not in ("on", "off"):
        return JSONResponse({"ok": False, "error": "use on|off"}, status_code=400)

    verb = "start" if target == "on" else "stop"
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", verb, TRACKER_UNIT],
                           capture_output=True, text=True, timeout=15)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"systemctl {verb} failed: {e}"},
                            status_code=503)
    if r.returncode != 0:
        # Almost always a missing sudoers rule — say so rather than failing silently, which is
        # how this button came to be described as "doesn't work".
        return JSONResponse({"ok": False,
                             "error": (r.stderr.strip() or f"systemctl {verb} returned "
                                       f"{r.returncode}"),
                             "hint": f"needs a sudoers rule for: systemctl {verb} {TRACKER_UNIT}"},
                            status_code=503)

    STATE["ai_tracking"] = _tracker_active()
    return JSONResponse({"ok": True, "ai_tracking": STATE["ai_tracking"]})


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
def mast_control(cmd: str) -> JSONResponse:
    """Z-axis intent: up | down | stop.

    This is the ONLY way anything on the network moves the lift. It becomes `MAST CMD` on
    the StamPLC, which feeds the same driver the RC sticks do — so the pinch guard, the
    limit detection and the authority/deadman gate all still apply. Writing the lift's
    relays directly (as /api/relay used to allow) walks past every one of them.

    The command is an INTENT and the firmware expires it after 250 ms, so a caller has to
    keep asking. That is deliberate: a caller that dies stops the lift."""
    import stamplc
    v = {"up": 1.0, "down": -1.0, "stop": 0.0}.get(cmd.lower())
    if v is None:
        return JSONResponse({"ok": False, "error": "use up|down|stop"}, status_code=400)
    if not stamplc.reader.send(f"MAST CMD {v:.2f}"):
        return JSONResponse({"ok": False, "error": "no link to the StamPLC — command not sent"},
                            status_code=503)
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
        return JSONResponse({"ok": False,
                             "error": "relays 1 and 2 are the lift legs — use /api/mast/{up|down|stop} "
                                      "so the pinch guard applies"}, status_code=409)
    if c != 3:
        return JSONResponse({"ok": False, "error": "only relay 3 is free"}, status_code=400)
    on = state.lower() in ("1", "on", "true")
    if not stamplc.reader.send(f"RELAY {c} {1 if on else 0}"):
        return JSONResponse({"ok": False, "error": "no link to the StamPLC — command not sent"},
                            status_code=503)
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
        return JSONResponse({"ok": False, "estop": True, "blockers": result["blockers"]},
                            status_code=409)
    return JSONResponse({"ok": True, "estop": False})


@app.post("/api/fault/clear")
def fault_clear() -> JSONResponse:
    """Clear a latched DEGRADED fault (review R4). Guarded by the supervisor on the
    next frame: only non-fatal faults whose condition is absent clear, and only once
    telemetry is ready. Fatal faults (welded contactor, PDH hardware) need service."""
    status_mod.request_fault_clear()
    return JSONResponse({"ok": True})


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


@app.post("/api/ack")
def acknowledge() -> dict:
    STATE["acknowledged_at"] = time.time()
    return {"ok": True, "acknowledged_at": STATE["acknowledged_at"]}


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
    await socket.accept()
    # RC telemetry is cheap (reads the latest StamPLC frame from memory) → push
    # fast for responsive sticks. System metrics are expensive (nvidia-smi
    # subprocess, sysfs) → include them only ~1x/sec.
    HZ = 20
    FAST_EVERY = 2    # ~10 Hz: cheap in-memory status (safety/state/io/encoder)
    SLOW_EVERY = HZ   # ~1 Hz: subprocess status (net/vnc) + system metrics
    tick = 0
    slow = {}
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
                payload["status"] = {**status_mod.collect_fast(), **slow}
            await socket.send_text(json.dumps(payload))
            tick += 1
            await asyncio.sleep(1.0 / HZ)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# Static frontend mounted last so /api and /ws take precedence. Serve with no-cache so a
# long-running kiosk browser always revalidates and picks up a redeploy on reload — the
# frontend is small and same-LAN, so skipping the cache costs nothing and avoids stale UIs.
class NoCacheStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp


app.mount("/", NoCacheStaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
