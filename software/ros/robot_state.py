#!/usr/bin/env python3
"""Robot state publisher and Z-axis lease arbiter.

Replaces the dashboard HTTP polling workaround (`stamplc_bridge.py` -> `/api/stamplc`)
by owning the StamPLC link directly (via `stamplc.Reader`) and publishing unified
safety/RC/Z-axis/power state to ROS 2, while preserving the existing granular topics
and services so existing consumers keep working unchanged.

Topics published:
  /robot/state              std_msgs/String (JSON, 10 Hz)
                            {"stamp", "estopped", "motion_enabled", "rc": {...},
                             "zaxis": {...}, "power": {...}, "safety": {...}}
  /robot/estopped           std_msgs/Bool
  /robot/motion_enabled     std_msgs/Bool
  /robot/rc                 std_msgs/String JSON {"ch5", "deadman", "live"}
  /zaxis/encoder            std_msgs/Int32
  /zaxis/travel_m           std_msgs/Float32
  /power/bus_voltage        std_msgs/Float32
  /power/temperature        std_msgs/Float32
  /power/pdh/{voltage,current,power}  std_msgs/Float32

Z-axis lease (same arbitration pattern as `base_selector.py`):
  /zaxis/claim              std_msgs/String  JSON {"owner": "manip", "ttl_s": 2.0}
  /zaxis/release            std_msgs/String  JSON {"owner": "manip"} | {"owner": "*"}
  /zaxis/owner              std_msgs/String  JSON, latched (TRANSIENT_LOCAL), 2 Hz + on change
  /zaxis/<owner>/cmd        std_msgs/String  "up" | "down" | "stop" -> `MAST CMD`

Services advertised (std_srvs/Trigger):
  /robot/estop              software e-stop (SAFE direction)
  /robot/estop_clear        clear software e-stop when safe
  /zaxis/{up,down,stop}     direct operator/service Z-axis intent (guarded by StamPLC)
  /zaxis/encoder_reset      zero mast encoder
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

from base_selector import Lease, parse

PUBLISH_HZ = float(os.environ.get("OPENDUBS_ROBOT_STATE_HZ", "10"))
URDF_ZAXIS_MAX_M = 0.2751
MAST_COMMANDS = {
    "up": "MAST CMD 1.00",
    "down": "MAST CMD -1.00",
    "stop": "MAST CMD 0.00",
}


def zaxis_cmd_to_mast(cmd: str) -> str:
    """Map a Z-axis intent ('up' | 'down' | 'stop') to the StamPLC `MAST CMD` string."""
    key = (cmd or "").strip().lower()
    if key not in MAST_COMMANDS:
        raise ValueError(f"invalid zaxis command {cmd!r}: expected up, down, or stop")
    return MAST_COMMANDS[key]


def _ch5_position(rc: dict) -> str | None:
    """Decode RC CH5 switch state from StamPLC [RC] mode token."""
    mode = rc.get("mode")
    if mode in ("AUTO", "MANUAL", "OFF"):
        return mode
    return None


def _derive_safety(snap: dict, software_estop: bool = False) -> dict:
    """Derive safety state from raw `stamplc.reader.snapshot()` fields (matching dashboard `status._safety`)."""
    if "safety" in snap:
        saf = snap["safety"] or {}
        fresh = bool(saf.get("fresh", snap.get("fresh", False)))
        estopped = bool(saf.get("estopped", True)) or (not fresh) or software_estop
        motion_enabled = bool(saf.get("motion_enabled", False)) and fresh and (not estopped)
        return {
            "fresh": fresh,
            "estopped": estopped,
            "motion_enabled": motion_enabled,
            "welded": bool(saf.get("welded", False)),
            "source": saf.get("source"),
        }

    fresh = bool(snap.get("fresh", False))
    di = snap.get("di") or []
    relay = snap.get("relay") or []
    rc = snap.get("rc") or {}
    contactor = bool(relay[0]) if len(relay) > 0 else False
    btn_pressed = (not bool(di[0])) if len(di) > 0 else False  # NC e-stop chain on DI0
    aux_closed = bool(di[1]) if len(di) > 1 else False         # weld aux contact on DI1

    sources = []
    if software_estop:
        sources.append("SOFTWARE")
    if fresh and rc.get("mode") == "ESTOP" and not software_estop:
        sources.append("STAMPLC")
    if btn_pressed:
        sources.append("BUTTON")
    if not fresh:
        sources.append("STALE TELEMETRY")

    estopped = bool(sources)
    motion_enabled = contactor and (not estopped)
    welded = bool(fresh and aux_closed and (not motion_enabled))
    return {
        "fresh": fresh,
        "estopped": estopped,
        "motion_enabled": motion_enabled,
        "welded": welded,
        "source": " + ".join(sources) if sources else None,
        "button_pressed": btn_pressed,
    }


def can_clear_estop(snap: dict) -> tuple[bool, str]:
    """Guarded e-stop re-arm check (matching `status.try_estop_clear` / `supervisor.estop_rearm_guard`)."""
    saf = _derive_safety(snap)
    if not saf["fresh"]:
        return False, "telemetry stale"
    if saf.get("button_pressed"):
        return False, "physical e-stop button is pressed"
    if saf["welded"]:
        return False, "contactor weld detected"
    ch5 = _ch5_position(snap.get("rc") or {})
    if ch5 in ("AUTO", "MANUAL"):
        return False, f"RC CH5 is {ch5} (must be OFF)"
    return True, "ok"


def build_robot_state(
    snap: dict,
    lift_zero_mm: float | None = None,
    now: float | None = None,
    software_estop: bool = False,
) -> dict:
    """Build the canonical `/robot/state` dict from a `stamplc.reader.snapshot()` dict."""
    ts = time.time() if now is None else float(now)
    saf = _derive_safety(snap, software_estop=software_estop)
    rc = snap.get("rc") or {}
    enc = snap.get("encoder") or {}
    io = snap.get("io") or {}
    power = snap.get("power") or {}
    drive = snap.get("drive") or {}
    lift = snap.get("lift") or {}

    height_mm = lift.get("height_mm")
    if height_mm is None and isinstance(drive.get("h"), (int, float)) and drive["h"] >= 0:
        height_mm = float(drive["h"])
    lift_fresh = bool(lift.get("fresh", lift.get("live", height_mm is not None)))

    travel_m = None
    if lift_zero_mm is not None and lift_fresh and height_mm is not None:
        travel_m = min(max((float(height_mm) - float(lift_zero_mm)) / 1000.0, 0.0), URDF_ZAXIS_MAX_M)

    enc_count = enc.get("count") if "count" in enc else snap.get("enc_count")
    bus_v = io.get("bus_v") if "bus_v" in io else snap.get("power_v")
    temp_c = io.get("temp_c") if "temp_c" in io else snap.get("temp_c")
    pdh = power.get("pdh") if "pdh" in power else (snap.get("pdh") or {})

    return {
        "stamp": ts,
        "estopped": saf["estopped"],
        "motion_enabled": saf["motion_enabled"],
        "rc": {
            "ch5": _ch5_position(rc),
            "deadman": bool(rc.get("dead")),
            "live": bool(rc.get("live")),
        },
        "zaxis": {
            "encoder": enc_count,
            "height_mm": height_mm,
            "travel_m": travel_m,
            "pinch": drive.get("pinch"),
            "fresh": lift_fresh,
        },
        "power": {
            "bus_v": bus_v,
            "temp_c": temp_c,
            "pdh": pdh,
        },
        "safety": {
            "fresh": saf["fresh"],
            "welded": saf["welded"],
            "source": saf["source"],
        },
    }


def _load_stamplc_reader():
    """Import `stamplc.reader` (`StampReader` singleton, daemon thread auto-started) from the dashboard backend."""
    backend = os.environ.get("OPENDUBS_DASH_BACKEND", "/opt/opendubs/kiosk-dashboard/backend")
    if backend not in sys.path and pathlib.Path(backend).is_dir():
        sys.path.insert(0, backend)
    import stamplc  # noqa: E402

    return stamplc.reader


def main():
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
    from std_msgs.msg import Bool, Float32, Int32, String
    from std_srvs.srv import Trigger

    rclpy.init()
    node = Node("robot_state")
    log = node.get_logger()
    reader = _load_stamplc_reader()

    z_env = os.environ.get("OPENDUBS_LIFT_TOF_ZERO_MM", "").strip()
    lift_zero_mm = float(z_env) if z_env else None

    latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL, reliability=ReliabilityPolicy.RELIABLE)
    p_state = node.create_publisher(String, "/robot/state", 10)
    p_estop = node.create_publisher(Bool, "/robot/estopped", 10)
    p_motion = node.create_publisher(Bool, "/robot/motion_enabled", 10)
    p_rc = node.create_publisher(String, "/robot/rc", 10)
    p_enc = node.create_publisher(Int32, "/zaxis/encoder", 10)
    p_travel = node.create_publisher(Float32, "/zaxis/travel_m", 10)
    p_busv = node.create_publisher(Float32, "/power/bus_voltage", 10)
    p_temp = node.create_publisher(Float32, "/power/temperature", 10)
    p_pv = node.create_publisher(Float32, "/power/pdh/voltage", 10)
    p_pi = node.create_publisher(Float32, "/power/pdh/current", 10)
    p_pp = node.create_publisher(Float32, "/power/pdh/power", 10)
    p_zowner = node.create_publisher(String, "/zaxis/owner", latched)

    lease = Lease()
    now = lambda: node.get_clock().now().nanoseconds / 1e9  # noqa: E731
    zstate = {"sub": None, "sub_owner": None, "refused": None}

    def forward_zaxis(owner):
        def cb(msg):
            if lease.current(now()) != owner:
                return
            try:
                mast_cmd = zaxis_cmd_to_mast(msg.data)
            except ValueError as e:
                log.warn(str(e))
                return
            reader.send(mast_cmd)

        return cb

    def sync_zaxis():
        holder = lease.current(now())
        if holder != zstate["sub_owner"]:
            if zstate["sub"] is not None:
                node.destroy_subscription(zstate["sub"])
                zstate["sub"] = None
            reader.send(MAST_COMMANDS["stop"])  # stop lift before changing hands
            if holder is not None:
                zstate["sub"] = node.create_subscription(String, f"/zaxis/{holder}/cmd", forward_zaxis(holder), 10)
            zstate["sub_owner"] = holder

    def publish_zowner():
        t = now()
        holder = lease.current(t)
        expires = max(0.0, lease.expires - t) if holder else 0.0
        payload = {"owner": holder, "expires_in_s": round(expires, 2), "refused": zstate["refused"]}
        p_zowner.publish(String(data=json.dumps(payload)))

    def on_zclaim(msg):
        d = parse(msg.data)
        ok, reason = lease.claim(d.get("owner"), d.get("ttl_s", 2.0), now())
        if not ok:
            zstate["refused"] = {"owner": str(d.get("owner")), "reason": reason}
            log.warn(f"zaxis claim refused for {d.get('owner')!r}: {reason}")
        else:
            zstate["refused"] = None
        sync_zaxis()
        publish_zowner()

    def on_zrelease(msg):
        d = parse(msg.data)
        if lease.release(str(d.get("owner", "")), now()):
            zstate["refused"] = None
        sync_zaxis()
        publish_zowner()

    node.create_subscription(String, "/zaxis/claim", on_zclaim, 10)
    node.create_subscription(String, "/zaxis/release", on_zrelease, 10)

    def mk_cmd_srv(wire_cmd):
        def cb(req, resp):
            ok = bool(reader.send(wire_cmd))
            resp.success = ok
            resp.message = wire_cmd if ok else "StamPLC link unavailable"
            return resp

        return cb

    sw_estop = {"latched": False}

    def on_estop(req, resp):
        sw_estop["latched"] = True
        delivered = bool(reader.send("ESTOP"))
        resp.success = True
        resp.message = "ESTOP sent" if delivered else "software latch set (StamPLC link unavailable)"
        return resp

    def on_estop_clear(req, resp):
        ok, reason = can_clear_estop(reader.snapshot())
        if not ok:
            resp.success = False
            resp.message = f"re-arm refused: {reason}"
            return resp
        if not reader.send("RELAY 0 1"):
            resp.success = False
            resp.message = "StamPLC link unavailable"
            return resp
        sw_estop["latched"] = False
        resp.success = True
        resp.message = "RELAY 0 1"
        return resp

    node.create_service(Trigger, "/robot/estop", on_estop)
    node.create_service(Trigger, "/robot/estop_clear", on_estop_clear)
    node.create_service(Trigger, "/zaxis/encoder_reset", mk_cmd_srv("ENC_RESET"))
    for cmd in ("up", "down", "stop"):
        node.create_service(Trigger, f"/zaxis/{cmd}", mk_cmd_srv(MAST_COMMANDS[cmd]))

    def tick_state():
        snap = reader.snapshot()
        state = build_robot_state(snap, lift_zero_mm=lift_zero_mm, now=time.time(), software_estop=sw_estop["latched"])
        p_state.publish(String(data=json.dumps(state)))
        p_estop.publish(Bool(data=state["estopped"]))
        p_motion.publish(Bool(data=state["motion_enabled"]))
        p_rc.publish(String(data=json.dumps(state["rc"])))
        z = state["zaxis"]
        if z["encoder"] is not None:
            p_enc.publish(Int32(data=int(z["encoder"])))
        if z["travel_m"] is not None:
            p_travel.publish(Float32(data=float(z["travel_m"])))
        pw = state["power"]
        if pw["bus_v"] is not None:
            p_busv.publish(Float32(data=float(pw["bus_v"])))
        if pw["temp_c"] is not None:
            p_temp.publish(Float32(data=float(pw["temp_c"])))
        pdh = pw["pdh"]
        if pdh.get("v") is not None:
            p_pv.publish(Float32(data=float(pdh["v"])))
        if pdh.get("i") is not None:
            p_pi.publish(Float32(data=float(pdh["i"])))
        if pdh.get("p") is not None:
            p_pp.publish(Float32(data=float(pdh["p"])))

    def tick_lease():
        sync_zaxis()
        publish_zowner()

    node.create_timer(1.0 / PUBLISH_HZ, tick_state)
    node.create_timer(0.5, tick_lease)
    log.info("robot_state node started (%.0f Hz, Z-axis lease enabled)" % PUBLISH_HZ)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        reader.send(MAST_COMMANDS["stop"])
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
