"""Master status aggregator for the dashboard's MASTER STATUS page.

Gathers the health of major subsystems — network (wired/wifi), Pixhawk/MAVLink,
StamPLC USB, per-arm CAN, power (battery + charger), and VNC. Sources that don't
exist yet report present=False / connected=None so the page can still render
"no data" rows. Uses subprocesses (nmcli/iwgetid/ip/lsusb/pgrep) so it's called
at ~1 Hz, not in the fast telemetry loop.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time

import psutil

import battery_history
import robot_state
import stamplc
from supervisor import supervisor

_BOOT = time.monotonic()

# RC now comes from the StamPLC SBUS ([RC] line) — the Pixhawk/MAVLink path is
# retired. The StamPLC interprets the mode switch itself, so the FSM maps its
# reported mode string → authority instead of thresholding raw channels.
SBUS_RANGE = {"min": 200, "max": 1820, "center": 1000}
MODE_TO_AUTHORITY = {"OFF": "OFF", "MANUAL": "RC_MANUAL", "AUTO": "AUTONOMOUS"}

# Thermal zones, indexed by the StamPLC [ENV]/[FAN] zone id (see docs/punch-list.md).
# PaHub [ENV] zone → label. z0 = ENVIII on ch2 (regen clamp resistor). z1 = ENVIII on ch3
# (2nd probe, location TBD — relabel when wired). z2+ reserved for future ENV zones.
THERMAL_LABELS = ["REGEN RESISTOR", "ENV ZONE 1", "MOTOR CTRL", "ZONE 3", "ZONE 4"]
THERMAL_OVERTEMP_C = 70.0

VNC_DISABLED_FLAG = "/home/kiosk/.vnc/disabled"

# StamPLC I/O map (canonical — see docs/stamplc-connections.md). Only two inputs are used:
# DI0 = e-stop button, DI1 = contactor weld. The Z-axis is driven by the StamPLC itself (relays
# R1/R2, behind the pinch guard) and senses height with the ToF, so there are no limit-switch or
# battery-present inputs here, and drive mode is software-set (no jumper).
# The dashboard prepends the physical 1-indexed terminal number (IN1..IN8 / RELAY1..RELAY4) to
# match the StamPLC silkscreen; these are just the functional names. (Code/M5 API stays 0-indexed:
# IN1 = DI0, RELAY1 = R0 — see docs/stamplc-connections.md.)
DI_LABELS = ["E-STOP BTN", "CONTACTOR WELD", "SPARE", "SPARE",
             "SPARE", "SPARE", "SPARE", "SPARE"]
# R0 = e-stop contactor coil; R1/R2 = the scissor lift's DOWN and UP legs (pulse-toggle, driven
# only through /api/mast/*, never by a direct relay toggle); R3 spare.
RELAY_LABELS = ["E-STOP CONTACTOR", "Z-AXIS DOWN", "Z-AXIS UP", "SPARE"]
DI_ESTOP_BTN, DI_WELD = 0, 1
RELAY_ESTOP_CONTACTOR = 0
RELAY_ZAXIS_DOWN, RELAY_ZAXIS_UP = 1, 2

# DI6 = contactor weld sensing (an aux contact of the NO main contactor — see the
# SAFETY LOGIC inset). It detects a welded/stuck-closed contactor: if the safety
# chain has commanded the contactor OPEN (e-stopped) but the aux still reads
# closed, the contacts have welded. Polarity TBD until wired — assume HIGH = aux
# closed; flip WELD_AUX_ACTIVE_HIGH when the real aux is wired.
WELD_AUX_ACTIVE_HIGH = True

# The e-stop button is a NORMALLY-CLOSED chain: DI0 HIGH = released/safe, LOW = pressed
# or a broken wire. Fail-safe by design, and not robot-specific.
ESTOP_BTN_ACTIVE_HIGH = False   # NC e-stop chain: DI0 HIGH=released/safe, LOW=pressed or wire-break

_software_estop = False


def set_software_estop(v: bool):
    global _software_estop
    _software_estop = bool(v)


def software_estop() -> bool:
    return _software_estop


def _run(cmd, timeout=2):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


# Skip loopback, container/bridge, and Jetson USB-gadget interfaces.
_VIRTUAL = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap", "can", "cni",
            "flannel", "kube", "tailscale", "zt", "l4tbr", "usb", "rndis", "dummy")
# IPs that aren't a real LAN address (loopback, link-local, Jetson USB-gadget net).
_NON_LAN = ("127.", "169.254.", "192.168.55.")


def _net():
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()

    def real_ip(al):
        for a in al:
            if a.family.name == "AF_INET" and not a.address.startswith(_NON_LAN):
                return a.address
        return None

    wired_c, wifi_c = [], []
    for name, al in addrs.items():
        if name.startswith(_VIRTUAL):
            continue
        entry = {"iface": name, "ip": real_ip(al),
                 "up": stats[name].isup if name in stats else False}
        (wifi_c if name.startswith(("wl", "wlan", "wlp")) else wired_c).append(entry)

    def best(cands):
        if not cands:
            return {"iface": None, "ip": None, "up": False}
        # prefer interfaces that have a LAN IP, then that are up
        return sorted(cands, key=lambda e: (e["ip"] is not None, e["up"]), reverse=True)[0]

    wired = best(wired_c)
    wifi = best(wifi_c)
    wifi["ssid"] = None

    ssid = _run(["iwgetid", "-r"])
    if not ssid and shutil.which("nmcli"):
        for line in _run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"]).splitlines():
            if line.startswith("yes:"):
                ssid = line.split(":", 1)[1]
                break
    wifi["ssid"] = ssid or None
    if ssid:
        wifi["up"] = True
    return {"wired": wired, "wifi": wifi}


def _rc_link():
    """RC link health, now from the StamPLC SBUS ([RC] line) rather than the
    retired Pixhawk/MAVLink path."""
    s = stamplc.reader.snapshot()
    rc = s.get("rc") or {}
    return {"link": bool(rc.get("live")), "mode": rc.get("mode"),
            "fresh": bool(s.get("fresh")) and bool(rc.get("live")),
            "source": "stamplc-sbus"}


def _pack():
    """52V pack from the ECO-WORTHY BMS ([BMS]/[BMSC] over RS485). Falls back to the
    old [SENS]/[PDH] bus voltage for `voltage` until the BMS reader is live."""
    s = stamplc.reader.snapshot()
    bms = s.get("bms") or {}
    v = bms.get("v")
    if v is None:
        v = s.get("power_v") or (s.get("pdh") or {}).get("v")
    soc, i = bms.get("soc"), bms.get("i")
    battery_history.history.sample(
        soc, power_w=(v * i if (v is not None and i is not None) else None))
    return {"present": bool(bms) and s.get("fresh", False),
            "soc": soc, "voltage": v, "current_a": i,
            "power_w": (v * i) if (v is not None and i is not None) else None,
            "charging": bms.get("charging"), "discharging": bms.get("discharging"),
            "cell_min": bms.get("cmin"), "cell_max": bms.get("cmax"),
            "cell_delta": bms.get("cdelta"),
            "temp_min": bms.get("tmin"), "temp_max": bms.get("tmax"),
            "flags": bms.get("flags"), "cells": s.get("bms_cells"),
            "usage": battery_history.history.snapshot()}


def _rails():
    """The two 24V rails: logic (always-on) and drive (behind the contactor, with the
    regen clamp). Voltages from the StamPLC [RAIL] line; contactor from relay 0."""
    s = stamplc.reader.snapshot()
    rail = s.get("rail") or {}
    relay = s.get("relay") or []
    contactor = relay[RELAY_ESTOP_CONTACTOR] if len(relay) > RELAY_ESTOP_CONTACTOR else None
    return {
        "logic": {"present": rail.get("logic_v") is not None,
                  "voltage": rail.get("logic_v"), "current_a": rail.get("logic_i")},
        "drive": {"present": rail.get("drive_v") is not None,
                  "voltage": rail.get("drive_v"), "contactor": contactor,
                  "clamp_active": rail.get("clamp")},
    }


def _charger():
    """Charger state from the BMS charge current — the only charger signal wired today.
    There is no charger-present sense line (DI3 reads false when plugged in), and the JBD
    'chgr' bit is just `charging`, so a pack that's full at 0 A is indistinguishable from
    unplugged. We therefore report CHARGING (current flowing in) vs IDLE, not a
    connected/disconnected we can't verify. Drive is inhibited while charge current flows."""
    s = stamplc.reader.snapshot()
    bms = s.get("bms") or {}
    charging = bms.get("charging")
    return {"charging": charging, "drive_inhibit": bool(charging)}


def _thermal():
    """Internal thermal zones + cooling fans (StamPLC [ENV]/[FAN])."""
    s = stamplc.reader.snapshot()
    env = s.get("env") or {}
    fan = s.get("fan") or {}
    zones = []
    for i in sorted(set(env) | set(fan)):
        e, f = env.get(i, {}), fan.get(i, {})
        zones.append({"zone": i, "temp_c": e.get("t"), "humidity": e.get("h"),
                      "fan_on": f.get("on"), "fan_pct": f.get("pct"),
                      "label": THERMAL_LABELS[i] if i < len(THERMAL_LABELS) else f"ZONE {i}"})
    overtemp = any(z["temp_c"] is not None and z["temp_c"] >= THERMAL_OVERTEMP_C for z in zones)
    # Free component temps also surface here (board) / on the THERMAL page: the
    # StamPLC board temp (from [IO]), plus the Thor's multi-zone temps (system
    # metrics `temps_c`) and BMS pack temps (power.pack) composed client-side.
    return {"zones": zones, "board_c": s.get("temp_c"),
            "overtemp": overtemp, "fresh": s.get("fresh", False)}


def _drive():
    """Drive summary: mode (mecanum/skid/diff), armed, per-wheel command (StamPLC [DRIVE])."""
    s = stamplc.reader.snapshot()
    d = s.get("drive") or {}
    return {"mode": d.get("mode"), "armed": d.get("armed"),
            "wheels": d.get("wheels"),
            "fwd": d.get("fwd"), "rot": d.get("rot"), "str": d.get("str"), "turn": d.get("turn"),
            "spd": d.get("spd"),
            # Z-axis: what the lift is doing, its measured height, and whether the pinch
            # guard is holding a descent. "nosensor" means the guard cannot work at all.
            "mast": d.get("mast"), "mast_h_mm": d.get("mast_h_mm"), "pinch": d.get("pinch"),
            "guard": d.get("guard"),      # firmware guard internals, verbatim — debugging aid
            # False = the robot is still settling and will not accept motion commands.
            # The operator needs this: holding the deadman and getting nothing otherwise
            # looks like a transmitter fault, and the reasonable response is to keep trying.
            "drive_safe": d.get("safe"), "loop_max_ms": d.get("loop_max_ms"),
            "fresh": s.get("fresh", False)}


# Modules whose SILENCE is itself a fault, rather than merely an absent reading. A module
# listed here must report; not hearing from it degrades the robot once the boot window has
# passed. See docs/interfaces/payload-comms.md — presence grants, absence revokes.
REQUIRED_MODULES = {"lift": True}


def _lift():
    """The Z-axis as a module: height, motion, inhibit and its own fault.

    `fresh` needs BOTH the StamPLC link and the module's own liveness — the same combination
    `_rc_link()` uses. A stale module is not trusted even if it last reported a fault code,
    which is why `fault_active` is gated on freshness: a cached fault from a module that has
    gone quiet is not evidence of a present fault.
    """
    s = stamplc.reader.snapshot()
    lift = s.get("lift") or {}
    fresh = bool(s.get("fresh")) and bool(lift.get("live"))
    fault = lift.get("fault")
    return {"fresh": fresh,
            "height_mm": lift.get("height_mm"),
            "inhibit": lift.get("inhibit"),
            "motion": lift.get("motion"),
            "fault": fault,
            "fault_active": bool(fresh and fault not in (None, "NONE"))}


def _io():
    s = stamplc.reader.snapshot()
    return {"di": s.get("di"), "relay": s.get("relay"),
            "di_labels": DI_LABELS, "relay_labels": RELAY_LABELS,
            "temp_c": s.get("temp_c"), "isocket_a": s.get("isocket_a"),
            "bus_v": s.get("power_v"), "i2c": s.get("i2c"), "i2c_full": s.get("i2c_full"),
            "i2c_bus": s.get("i2c_bus"), "sbus": s.get("sbus_stat"),
            "env": s.get("env"),   # ENVIII SHT30 zones: {zone: {t, h}} — zone 0 = regen clamp resistor
            "fresh": s.get("fresh", False)}


def _safety():
    """Separate e-stop CAUSES (sources) from the contactor EFFECT.

    Sources (latching causes of an e-stop): the software e-stop and the physical
    e-stop button on DI0. Relay 0 drives the e-stop CONTACTOR coil — that's the
    power-gate OUTPUT/effect (it opens *because* of a source), NOT a cause. So
    `estopped` depends only on the sources; the contactor is reported separately
    as the motion-power state.
    """
    s = stamplc.reader.snapshot()
    fresh = s.get("fresh", False)
    di = s.get("di") or []
    relay = s.get("relay") or []
    contactor = relay[RELAY_ESTOP_CONTACTOR] if len(relay) > RELAY_ESTOP_CONTACTOR else None
    di0 = di[DI_ESTOP_BTN] if len(di) > DI_ESTOP_BTN else None
    btn = None if di0 is None else (bool(di0) if ESTOP_BTN_ACTIVE_HIGH else not di0)
    sw = software_estop()
    di6 = di[DI_WELD] if len(di) > DI_WELD else None
    aux_closed = None if di6 is None else (bool(di6) if WELD_AUX_ACTIVE_HIGH else not di6)

    sources = []
    if sw:
        sources.append("SOFTWARE")
    if btn:
        sources.append("BUTTON")
    # Fail-safe (review R2): a STALE StamPLC link freezes the entire safety-sensing
    # channel at its last values — we can't trust DI/relay, so fail to the safe side
    # and treat it as e-stopped (this is the spec's "system watchdog timeout" event).
    if not fresh:
        sources.append("STALE TELEMETRY")
    estopped = bool(sources)

    contactor_energized = bool(contactor)
    motion_enabled = contactor_energized and not estopped

    # Contactor weld (review R3): the aux contact (DI6) reads CLOSED while the
    # contactor should be OPEN (NOT motion-enabled) ⇒ welded/stuck-closed contacts.
    # Computed independent of the e-stop priority so it surfaces even UNDER e-stop —
    # where it's most dangerous (you commanded power off but it's still flowing).
    # Gated on `fresh` (don't trust a stale aux). No fault on the bench (DI6 low).
    welded = bool(fresh and aux_closed and not motion_enabled)

    return {
        "estopped": estopped,
        "sources": sources,
        "source": " + ".join(sources) if sources else None,
        "estop_button": btn,            # interpreted: True = pressed
        "di0_raw": di0,                 # raw line level
        "software_estop": sw,
        "contactor": contactor,         # relay 0 — effect/output (power gate)
        "contactor_energized": contactor_energized,
        "motion_enabled": motion_enabled,
        "weld_aux_closed": aux_closed,  # DI6 interpreted
        "welded": welded,               # welded/stuck contactor detected
        "stale": not fresh,             # StamPLC link dead → fail-safe e-stop
        "fresh": fresh,
    }


def _stamplc():
    s = stamplc.reader.snapshot()
    dev = s.get("device")
    return {"detected": s["connected"], "fresh": s.get("fresh", False),
            "port": os.path.basename(dev) if dev else None}


def _can_arms():
    # The arms do NOT connect over the StamPLC CAN bus and aren't wired yet —
    # report not-connected (no telemetry source) rather than green.
    nc = {"present": False, "up": False, "connected": False}
    return {"left": dict(nc), "right": dict(nc)}


def _robot_state():
    """Derive the robot-level FSM (docs/state-machines.md) from live telemetry:
    safety latch, RC authority/deadman, link health, and (non-bench) faults."""
    s = stamplc.reader.snapshot()
    saf = _safety()

    # RC authority/deadman/link now come from the StamPLC SBUS ([RC] line); the
    # StamPLC already interprets the mode switch, so we map its mode → authority.
    rc = s.get("rc") or {}
    link = bool(rc.get("live"))
    authority = MODE_TO_AUTHORITY.get(rc.get("mode"))
    deadman = bool(rc.get("dead"))
    charging = bool(_charger().get("drive_inhibit"))

    raw_faults = []
    if saf.get("welded"):
        raw_faults.append("CONTACTOR WELDED")
    pdh = s.get("pdh") or {}
    if pdh.get("brn"):
        raw_faults.append("PDH brownout")
    if pdh.get("hw"):
        raw_faults.append("PDH hardware")
    if _thermal().get("overtemp"):
        raw_faults.append("OVERTEMP")
    # NOTE: per-channel PDH breaker faults are intentionally NOT rolled into
    # DEGRADED — on the bench many channels are unfused and fault by design.

    # The Z-axis is NOT independent of the StamPLC: it owns the ToF, the relays and the pinch
    # guard, and reports all three. So a lift fault is a real input to the FSM.
    lift = _lift()
    mast_fault = lift["fault_active"]
    # A required module that has gone quiet degrades the robot — but not during boot, when the
    # link legitimately has not come up yet. Latching that on every power-on would mean a
    # manual DEGRADED clear every single time.
    if REQUIRED_MODULES.get("lift") and not lift["fresh"] and supervisor.ready:
        raw_faults.append("lift module not responding")

    # Fold raw telemetry through the stateful supervisor so latching/readiness
    # actually persist between frames (review R1/R4/R5); derive() stays pure.
    sup = supervisor.step(
        raw_estop_sources=saf["sources"], raw_faults=raw_faults,
        fresh=saf["fresh"], control_ok=link, uptime=time.monotonic() - _BOOT)

    # The StamPLC's own drive-safe gate is a boot condition too, and a more authoritative
    # one than our uptime heuristic: it is the controller saying its control loop is not yet
    # keeping time, so nothing it is told will move. Fold it in so the face and the state
    # colour say "INIT" for the whole settling window instead of looking ready early.
    plc_safe = (stamplc.reader.snapshot().get("drive") or {}).get("safe")
    booting = sup["booting"] or (plc_safe is False)

    out = robot_state.derive(
        booting=booting, estop_sources=sup["estop_sources"], authority=authority,
        deadman=deadman, link=link, faults=sup["faults"], mast_fault=mast_fault,
        motion_enabled=saf["motion_enabled"], contactor=saf["contactor"],
        welded=saf["welded"], charging=charging)
    if sup["note"]:
        out["reason"] = out["reason"] + " · " + sup["note"]
    out["estop_events"] = supervisor.recent_events()
    out["latched"] = {"estop": supervisor.estop_latched,
                      "faults": list(supervisor.fault_latched),
                      "ready": supervisor.ready}
    return out


def try_estop_clear() -> dict:
    """Guarded software e-stop re-arm (review R3/R4). Refuses while the button is
    still pressed, telemetry is stale, the contactor is welded, or CH5 isn't OFF —
    THEN clears the software latch + re-energizes relay 0. Returns the outcome."""
    saf = _safety()
    rc = stamplc.reader.snapshot().get("rc") or {}
    authority = MODE_TO_AUTHORITY.get(rc.get("mode"))
    ok, blockers = supervisor.estop_rearm_guard(
        button_pressed=bool(saf["estop_button"]), stale=saf["stale"],
        welded=saf["welded"], authority=authority)
    if not ok:
        return {"ok": False, "blockers": blockers}
    set_software_estop(False)
    stamplc.reader.send("RELAY 0 1")
    supervisor.clear_estop()
    return {"ok": True}


def request_fault_clear():
    """Arm a one-shot DEGRADED clear; the supervisor guards it on the next frame."""
    supervisor.request_clear_fault()


def _vnc():
    try:
        running = subprocess.run(["pgrep", "-x", "x11vnc"], capture_output=True).returncode == 0
    except Exception:
        running = False
    return {"running": running}


def collect_fast() -> dict:
    """Cheap, in-memory status (reads reader snapshots only — no subprocesses).
    Safe to push at ~10 Hz so the MASTER STATUS card (state machine + safety) is
    responsive. Excludes net/VNC, which shell out and belong in collect_slow()."""
    s = stamplc.reader.snapshot()
    pack = _pack()
    rails = _rails()
    return {
        "ts": time.time(),
        "robot_state": _robot_state(),
        "rc_link": _rc_link(),
        "odo": (stamplc.reader.snapshot().get("odo") or {}),
        # deprecated; RC moved to the StamPLC SBUS — kept so an old frontend degrades.
        "mavlink": {"connected": False, "note": "deprecated — RC now from StamPLC SBUS"},
        "stamplc": _stamplc(),
        "io": _io(),
        "encoder": {"count": s.get("enc_count"), "present": s.get("enc_present")},
        "safety": _safety(),
        "can": _can_arms(),
        "thermal": _thermal(),
        "drive": _drive(),
        "power": {
            "pack": pack,
            "logic_rail": rails["logic"],
            "drive_rail": rails["drive"],
            "charger": _charger(),
            "pdh": s.get("pdh"),
            "pdh_ch": s.get("pdh_ch"),
            "pdh_fault": s.get("pdh_fault"),
            "pdh_sticky": s.get("pdh_sticky"),
            "battery": pack,   # back-compat alias — prefer `pack`
        },
    }


def collect_slow() -> dict:
    """Subprocess-backed status (nmcli/iwgetid/ip via psutil, pgrep). Slow —
    push at ~1 Hz. Merged into the same `status` block client-side."""
    return {"net": _net(), "vnc": _vnc()}


def collect() -> dict:
    """Full snapshot (for /api/snapshot) — fast + slow merged."""
    return {**collect_fast(), **collect_slow()}
