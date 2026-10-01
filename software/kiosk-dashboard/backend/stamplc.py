"""Live StamPLC telemetry over serial (ESP32-S3 USB CDC).

Reads the StamPLC's text telemetry (the [SENS]/[PDH]/[CAN]/[I2C]/[SBUS] lines,
same format as rc_serial_probe/tools/telemetry_gui.py) in a background thread and
keeps the latest parsed values. The device is found by its stable by-id path
(Espressif USB JTAG/serial). USB CDC ignores baud, but we set it anyway.
"""
from __future__ import annotations

import glob
import math
import os
import re
import threading
import time

BAUD = 115200
FRESH_S = 3.0

_P_SENS = re.compile(r"\[SENS\] power=([\d.]+)V")
_P_SCAN = re.compile(r"\[I2C\] scan:(.*)")
_P_SCAN_FULL = re.compile(r"\[I2C\] full:(.*)")
_P_I2C_BUS = re.compile(r"\[I2C\].*SDA=G(\d+) SCL=G(\d+)")
_P_PDH_STAT = re.compile(
    r"\[PDH\] stat id=(\d+) v=([\d.]+)V i=([\d.]+)A p=([\d.]+)W e=([\d.]+)Wh "
    r"sw=(\d) brn=(\d) can=(\d) hw=(\d) seen=(\d{5})")
_P_PDH_CH = re.compile(r"\[PDH\] ch (.*)")
_P_CH_ITEM = re.compile(r"c(\d+)=([\d.]+)")
_P_CAN = re.compile(r"\[CAN\] rx=(\d+) uniq_std=(\d+) uniq_ext=(\d+) last_rx_ms=(\d+)")
_P_PDH_FLT = re.compile(r"\[PDH\] flt live=0x([0-9A-Fa-f]+) sticky=0x([0-9A-Fa-f]+)")
_P_IO = re.compile(r"\[IO\] di=0x([0-9A-Fa-f]+) relay=0x([0-9A-Fa-f]+) temp=([-\d.]+) isocket=([-\d.]+)")
_P_ENC = re.compile(r"\[ENC\] mast=(-?\d+) present=(\d)")
_P_SBUS_STAT = re.compile(r"\[SBUS\] prof=.* ok=(\d+) bad=(\d+) last_ok_ms=(\d+)")

# --- New telemetry contract (firmware TODO — parsed here now so the dashboard
# renders empty panels until the StamPLC emits them). Token form `[TAG] k=v ...`:
#   [BMS]   soc= v= i=(+chg/-dsg) cmin= cmax= tmin= tmax= chg= dsg= chgr= flags=0x..
#   [BMSC]  c0= c1= ... c15=            per-cell voltages (optional)
#   [ENV]   z= t= h=                    one line per thermal zone
#   [FAN]   z= on= pct=                 one line per fan zone
#   [RAIL]  logic_v= logic_i= drive_v= clamp=
#   [RC]    live= mode= dead= spd= pmode= pmarm= fr= rot= str= mast= raw=c1,..,c16
#           pmode = payload mode (OFF|ZAXIS|PANTILT): what the RIGHT STICK means.
#           pmarm = 0 while that stick is waiting to be re-centred after a mode change.
#   [DRIVE] mode= armed= w=fl,fr,rl,rr  (normalized -1..1) mast=<drv>:<0stop/1up/2down>
#           h=<mm, <0 unknown> pinch=nosensor|ok|HOLD|ack  (Z-axis + its pinch guard)
#           safe=0|1  DRIVE-SAFE GATE: 0 = nothing may be commanded to move, the control
#                     loop is not demonstrably keeping time. loopmax = worst loop ms.
#   [ODO]   ok= st= rdy= cal= xpod= ypod= x= y= h= vx= vy= vh= ex= ey= loop= fail= crcbad=
#           goBILDA Pinpoint: x/y mm, h rad (unnormalized, counts full turns),
#           ex/ey raw pod counts. Firmware already decodes DEVICE_STATUS, whose pod
#           bits are FAULT flags (set = pod MISSING) — xpod/ypod here are presence.
_KV = re.compile(r"(\w+)=(\S+)")


def _kv(line):
    """Parse `key=value` tokens from a telemetry line into a dict of strings."""
    return dict(_KV.findall(line))


def _find_device():
    """The StamPLC's serial port, or None — and NEVER anything else's port.

    The old version took the first /dev/serial/by-id/*Espressif* match. The Tab5 head is an
    Espressif USB-JTAG device too, so plugging the head into the Thor to flash it handed its
    port straight to the dashboard: it opened the port with DTR/RTS asserted, which on that
    chip drives reset and boot mode, and HELD THE HEAD IN RESET. Dark screen, no Wi-Fi, and a
    flash that fights whoever is doing it. Sorting made it worse by making the victim depend
    on which MAC sorted first.

    So: an explicit device wins, the head's port is excluded by name, and an ambiguous choice
    is refused rather than guessed. Opening the wrong port is not a harmless mistake here —
    the act of opening it resets that device — so "I cannot tell" must mean "touch nothing".
    """
    env = os.environ.get("OPENDUBS_STAMPLC_DEV")
    if env:
        return env
    try:                                   # lazy: state_out imports this module
        from state_out import _TAB5_GLOB as head_glob
    except Exception:
        head_glob = os.environ.get("OPENDUBS_TAB5_DEV_GLOB", "")
    heads = set(glob.glob(head_glob)) if head_glob else set()
    for pat in ("/dev/serial/by-id/*Espressif*if00", "/dev/serial/by-id/*Espressif*"):
        m = [d for d in sorted(glob.glob(pat)) if d not in heads]
        if len(m) == 1:
            return m[0]
        if len(m) > 1:
            import sys
            sys.stderr.write("STAMPLC: %d candidate serial ports (%s) — refusing to guess; "
                             "set OPENDUBS_STAMPLC_DEV\n" % (len(m), ", ".join(m)))
            return None
    return None


class StampReader:
    def __init__(self):
        self._lock = threading.Lock()
        self._d = {}
        self._connected = False
        self._last = None
        self._device = None
        self._transport = None      # "udp" | "serial" | None
        self._peer = None           # (ip, port) to unicast commands to over UDP
        self._txq = []
        self._parse_errors = 0
        threading.Thread(target=self._run, name="stamplc", daemon=True).start()

    def send(self, cmd: str):
        """Queue a command line for the StamPLC (e.g. 'LED_COLOR 30 210 70 100'). Delivered over
        whichever transport is active — Ethernet UDP or USB serial."""
        with self._lock:
            self._txq.append(cmd)

    def _ingest(self, blob: bytes):
        """Split a serial/UDP blob into lines and parse each [TAG] line (transport-agnostic)."""
        try:
            text = blob.decode("utf-8", "replace")
        except Exception:
            return
        for line in text.replace("\r", "").split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                self._parse(line)
            except Exception as e:
                # One malformed or unexpected line must not stop the others in this datagram,
                # and must not stall `_last` into staleness — staleness is a SAFETY input.
                self._parse_errors += 1
                if self._parse_errors in (1, 10, 100) or self._parse_errors % 1000 == 0:
                    import sys
                    sys.stderr.write(f"STAMPLC: parse error #{self._parse_errors} "
                                     f"({type(e).__name__}: {e}) on: {line[:120]}\n")

    def _run(self):
        # Transport select: 'auto' (default) prefers Ethernet — the StamPLC tees the SAME [TAG]
        # text protocol over UDP:5005 — and falls back to USB serial; 'udp'/'serial' force one.
        mode = os.environ.get("OPENDUBS_STAMPLC_TRANSPORT", "auto").lower()
        while True:
            if mode in ("auto", "udp"):
                if self._run_udp(force=(mode == "udp")):
                    continue                       # UDP ran until it stalled — re-evaluate
                if mode == "udp":
                    time.sleep(2)                  # forced UDP, nothing heard yet — keep waiting
                    continue
            self._run_serial()                     # auto w/o Ethernet, or mode == 'serial'
            time.sleep(1)

    def _run_udp(self, force: bool) -> bool:
        """Consume the StamPLC's UDP [TAG] telemetry and unicast queued commands back to it.
        Returns True if Ethernet telemetry was seen (consumed until it stalled), False if none
        arrived in the probe window (caller then falls back to serial)."""
        import socket
        port = int(os.environ.get("OPENDUBS_STAMPLC_UDP_PORT", "5005"))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(("", port))
        except OSError:
            return False
        try:
            sock.settimeout(6.0 if force else 4.0)     # wait for first packet to confirm Ethernet
            try:
                data, addr = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                return False
            with self._lock:
                self._connected = True
                self._transport = "udp"
                self._peer = (addr[0], port)
                self._device = "udp://%s:%d" % (addr[0], port)
            self._ingest(data)
            sock.settimeout(1.0)
            quiet = 0.0
            while True:
                with self._lock:
                    pending, self._txq = self._txq, []
                    peer = self._peer
                for c in pending:
                    try:
                        sock.sendto((c + "\n").encode("utf-8"), peer)
                    except OSError:
                        pass
                try:
                    data, addr = sock.recvfrom(2048)
                except socket.timeout:
                    quiet += 1.0
                    if quiet >= FRESH_S + 3.0:          # Ethernet went silent — re-evaluate transport
                        break
                    continue
                except OSError:
                    break
                quiet = 0.0
                with self._lock:
                    self._peer = (addr[0], port)        # track the StamPLC IP across DHCP changes
                self._ingest(data)
            return True
        finally:
            with self._lock:
                self._connected = False
                self._transport = None
            try:
                sock.close()
            except OSError:
                pass

    def _run_serial(self):
        import serial
        dev = _find_device()
        if not dev:
            with self._lock:
                self._connected = False
            time.sleep(2)
            return
        try:
            with serial.Serial(dev, BAUD, timeout=1.0) as ser:
                with self._lock:
                    self._connected = True
                    self._transport = "serial"
                    self._device = os.path.realpath(dev)
                while True:
                    with self._lock:
                        pending, self._txq = self._txq, []
                    for c in pending:
                        ser.write((c + "\n").encode("utf-8"))
                    raw = ser.readline()
                    if not raw:
                        continue
                    self._ingest(raw)
        except Exception:
            with self._lock:
                self._connected = False
                self._transport = None
            time.sleep(2)

    def _parse(self, line):
        now = time.monotonic()
        with self._lock:
            if (m := _P_SENS.search(line)):
                self._d["power_v"] = float(m.group(1)); self._last = now; return
            if (m := _P_PDH_STAT.search(line)):
                self._d["pdh"] = {
                    "id": int(m.group(1)), "v": float(m.group(2)), "i": float(m.group(3)),
                    "p": float(m.group(4)), "e": float(m.group(5)),
                    "sw": m.group(6) == "1", "brn": m.group(7) == "1",
                    "can": m.group(8) == "1", "hw": m.group(9) == "1", "seen": m.group(10),
                }
                self._last = now; return
            if (m := _P_PDH_CH.search(line)):
                ch = self._d.get("pdh_ch", [0.0] * 24)
                for cm in _P_CH_ITEM.finditer(m.group(1)):
                    i = int(cm.group(1))
                    if 0 <= i < 24:
                        ch[i] = float(cm.group(2))
                self._d["pdh_ch"] = ch; self._last = now; return
            if (m := _P_CAN.search(line)):
                self._d["can"] = {"rx": int(m.group(1)), "uniq_std": int(m.group(2)),
                                  "uniq_ext": int(m.group(3)), "last_rx_ms": int(m.group(4))}
                self._last = now; return
            if (m := _P_PDH_FLT.search(line)):
                live = int(m.group(1), 16)
                self._d["pdh_fault"] = [(live >> i) & 1 == 1 for i in range(24)]
                self._d["pdh_sticky"] = int(m.group(2), 16)
                self._last = now; return
            if (m := _P_IO.search(line)):
                di = int(m.group(1), 16); relay = int(m.group(2), 16)
                self._d["di"] = [(di >> i) & 1 == 1 for i in range(8)]
                self._d["relay"] = [(relay >> i) & 1 == 1 for i in range(4)]
                self._d["temp_c"] = float(m.group(3))
                self._d["isocket_a"] = float(m.group(4))
                self._last = now; return
            if (m := _P_ENC.search(line)):
                self._d["enc_count"] = int(m.group(1))
                self._d["enc_present"] = m.group(2) == "1"
                self._last = now; return
            if (m := _P_I2C_BUS.search(line)):
                self._d["i2c_bus"] = {"sda": int(m.group(1)), "scl": int(m.group(2))}
                # fall through is fine, but this line carries no scan list
            if (m := _P_SCAN.search(line)):
                self._d["i2c"] = [t for t in m.group(1).split() if t.startswith("0x")]
                self._last = now; return
            if (m := _P_SCAN_FULL.search(line)):
                # On-demand full bus sweep result (I2C_SCAN command).
                self._d["i2c_full"] = [t for t in m.group(1).split() if t.startswith("0x")]
                self._d["i2c_full_age"] = 0.0
                self._d["i2c_full_at"] = now
                self._last = now; return
            if (m := _P_SBUS_STAT.search(line)):
                self._d["sbus_stat"] = {"ok": int(m.group(1)), "bad": int(m.group(2)),
                                        "last_ok_ms": int(m.group(3))}
                self._last = now; return
            if line.startswith("[BMS] "):
                kv = _kv(line)
                fnum = lambda k: float(kv[k]) if k in kv else None
                cmin, cmax = fnum("cmin"), fnum("cmax")
                self._d["bms"] = {
                    "soc": fnum("soc"), "v": fnum("v"), "i": fnum("i"),
                    "cmin": cmin, "cmax": cmax,
                    "cdelta": (cmax - cmin) if (cmin is not None and cmax is not None) else None,
                    "tmin": fnum("tmin"), "tmax": fnum("tmax"),
                    "charging": kv.get("chg") == "1", "discharging": kv.get("dsg") == "1",
                    "charger": (kv.get("chgr") == "1") if "chgr" in kv else None,
                    "flags": int(kv["flags"], 16) if "flags" in kv else None,
                }
                self._last = now; return
            if line.startswith("[BMSC] "):
                cells = self._d.get("bms_cells") or [None] * 16
                for k, v in _kv(line).items():
                    if k[:1] == "c" and k[1:].isdigit():
                        i = int(k[1:])
                        if 0 <= i < len(cells):
                            cells[i] = float(v)
                self._d["bms_cells"] = cells; self._last = now; return
            if line.startswith("[ENV] "):
                kv = _kv(line)
                if "z" in kv:
                    env = self._d.get("env") or {}
                    env[int(kv["z"])] = {"t": float(kv["t"]) if "t" in kv else None,
                                         "h": float(kv["h"]) if "h" in kv else None}
                    self._d["env"] = env; self._last = now
                return
            if line.startswith("[FAN] "):
                kv = _kv(line)
                if "z" in kv:
                    fan = self._d.get("fan") or {}
                    fan[int(kv["z"])] = {"on": kv.get("on") == "1",
                                         "pct": float(kv["pct"]) if "pct" in kv else None}
                    self._d["fan"] = fan; self._last = now
                return
            if line.startswith("[RAIL] "):
                kv = _kv(line)
                fnum = lambda k: float(kv[k]) if k in kv else None
                self._d["rail"] = {"logic_v": fnum("logic_v"), "logic_i": fnum("logic_i"),
                                   "drive_v": fnum("drive_v"), "clamp": kv.get("clamp") == "1"}
                self._last = now; return
            if line.startswith("[RC] "):
                kv = _kv(line)
                inum = lambda k: int(kv[k]) if (k in kv and kv[k].lstrip("-").isdigit()) else None
                raw = None
                if "raw" in kv:
                    try:
                        raw = [int(x) for x in kv["raw"].split(",")]
                    except ValueError:
                        raw = None
                self._d["rc"] = {
                    "live": kv.get("live") == "1", "mode": kv.get("mode"),
                    "dead": kv.get("dead") == "1",
                    "spd": float(kv["spd"]) if "spd" in kv else None,
                    "pmode": kv.get("pmode"), "pmarm": kv.get("pmarm") == "1",
                    "fr": inum("fr"), "rot": inum("rot"),
                    "str": inum("str"), "mast": inum("mast"), "raw": raw,
                }
                self._last = now; return
            if line.startswith("[ODO] "):
                kv = _kv(line)
                def _f(k):
                    # The Pinpoint reports nan for pose and velocity whenever it is faulted
                    # (ok=0), and the firmware prints it as "-nan". json.dumps refuses NaN, so
                    # passing it through took out the WHOLE /api/snapshot with a 500 — every
                    # dashboard poll, for a fault in one optional sensor. None means "no value".
                    try:
                        v = float(kv.get(k))
                        return v if math.isfinite(v) else None
                    except (TypeError, ValueError): return None
                def _i(k):
                    try: return int(kv.get(k))
                    except (TypeError, ValueError): return None
                self._d["odo"] = {
                    "ok": kv.get("ok") == "1",
                    "ready": kv.get("rdy") == "1", "calibrating": kv.get("cal") == "1",
                    "x_pod": kv.get("xpod") == "1", "y_pod": kv.get("ypod") == "1",
                    "imu_runaway": kv.get("imurun") == "1", "bad_read": kv.get("badread") == "1",
                    "status": kv.get("st"),
                    "x_mm": _f("x"), "y_mm": _f("y"), "heading_rad": _f("h"),
                    "vx": _f("vx"), "vy": _f("vy"), "vh": _f("vh"),
                    "enc_x": _i("ex"), "enc_y": _i("ey"),
                    "loop_us": _i("loop"), "fail": _i("fail"), "crc_bad": _i("crcbad"),
                }
                self._last = now
                return

            # A dedicated lift line, per docs/interfaces/payload-comms.md's LIFT_STATE. No
            # firmware emits it yet — the StamPLC owns the Z-axis today and reports it inside
            # [DRIVE], mapped below. This branch is here so that when the lift moves to its own
            # payload controller the dashboard already understands it, and takes precedence
            # because a dedicated module reporting itself beats a field borrowed from [DRIVE].
            if line.startswith("[LIFT] "):
                kv = _kv(line)
                def _b(k):      # firmware prints flags as 1/0
                    return kv.get(k) == "1"
                def _h(k):
                    try: return float(kv[k])
                    except (KeyError, TypeError, ValueError): return None
                self._d["lift"] = {"live": _b("live"), "height_mm": _h("height_mm"),
                                   "inhibit": _b("inhibit"), "at_top": _b("at_top"),
                                   "at_bot": _b("at_bot"), "tof_ok": _b("tof_ok"),
                                   "motion": kv.get("motion"), "fault": kv.get("fault")}
                # Latch the source. Without this the next [DRIVE] line would overwrite a real
                # module's own report with fields borrowed from the drive line.
                self._lift_dedicated = True
                self._last = now; return

            if line.startswith("[DRIVE] "):
                kv = _kv(line)
                w = None
                if "w" in kv:
                    try:
                        w = [float(x) for x in kv["w"].split(",")]
                    except ValueError:
                        w = None
                fnum = lambda k: (float(kv[k]) if k in kv else None)
                inum = lambda k: (int(float(kv[k])) if k in kv else None)
                self._d["drive"] = {"mode": kv.get("mode"), "armed": kv.get("armed") == "1",
                                    "wheels": w,
                                    "fwd": fnum("fwd"), "rot": fnum("rot"), "str": fnum("str"),
                                    "spd": fnum("spd"),       # CH8 speed knob 0..1
                                    "turn": kv.get("turn"),   # "L"/"R"/"0" — net rightward intent
                                    # Z-axis: 0=stop 1=up 2=down, height mm (<0 unknown), and the
                                    # pinch guard — "nosensor" means descent is UNGUARDED.
                                    "mast": kv.get("mast"),
                                    "mast_h_mm": fnum("h"),
                                    "pinch": kv.get("pinch"),
                                    "safe": kv.get("safe") == "1",
                                    # The guard's internals, `g[...]`, verbatim. Parsed HERE and
                                    # not at the top of _parse: this dict is rebuilt wholesale on
                                    # every [DRIVE] line, so anything set elsewhere in self._d is
                                    # not visible through it.
                                    "guard": (gm.group(1)
                                              if (gm := re.search(r"g\[([^\]]*)\]", line))
                                              else None),
                                    "loop_max_ms": inum("loopmax")}
                # The Z-axis as its own subsystem, independent of which telemetry line
                # happens to carry it. Today the StamPLC owns the lift and reports it inside
                # [DRIVE]; when the lift moves to its own payload controller it gets its own
                # line and only this mapping changes, not every consumer. Shape follows
                # docs/interfaces/payload-comms.md's LIFT_STATE.
                if not getattr(self, "_lift_dedicated", False):
                  _pinch = kv.get("pinch")
                  _h = fnum("h")
                  self._d["lift"] = {
                      # The lift reported at all. There is no separate module to go silent
                      # yet, so this is "the [DRIVE] line carried lift fields".
                      "live": ("h" in kv) or (_pinch is not None),
                      "height_mm": (_h if (_h is not None and _h >= 0) else None),
                      "inhibit": _pinch == "HOLD",
                      # The firmware prints `mast=<type>:<motion>` (`mast=%s:%d`), e.g.
                      # "scissor:0" — the Z-axis type and what it is doing. Take the motion.
                      "motion": {"0": "STOP", "1": "UP", "2": "DOWN"}.get(
                          (kv.get("mast") or "").rsplit(":", 1)[-1]),
                      # at_top/at_bot are deliberately absent: the limits are only in the
                      # guard's verbatim debug string, and parsing a debug format would couple
                      # this to something documented as free to change. _lift() does not use them.
                      "tof_ok": _pinch != "nosensor",
                      # No sensor means the guard cannot work and a descent is UNGUARDED. That
                      # is a fault, not a status line — TOF_FAULT in the payload protocol.
                      "fault": "TOF_FAULT" if _pinch == "nosensor" else "NONE",
                  }
                self._last = now; return

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            age = (now - self._last) if self._last else None
            return {"connected": self._connected,
                    "fresh": age is not None and age < FRESH_S,
                    "age": age, "device": self._device,
                    "transport": self._transport, **self._d}


reader = StampReader()
