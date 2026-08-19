#!/usr/bin/env python3
"""
OpenDubs Telemetry Viewer — Dear PyGui edition.
pip install dearpygui pyserial

Usage:
    python telemetry_gui.py
    python telemetry_gui.py --port /dev/cu.usbmodem101
    python telemetry_gui.py --list-ports
"""

import argparse, re, sys, threading, time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import serial, serial.tools.list_ports
except ImportError:
    print("ERROR: pip install pyserial"); sys.exit(1)

try:
    import dearpygui.dearpygui as dpg
except ImportError:
    print("ERROR: pip install dearpygui"); sys.exit(1)

# ── Constants ────────────────────────────────────────────────────────────

DEFAULT_PORT = "/dev/cu.usbmodem101"
DEFAULT_BAUD = 115200
STALE_S     = 3.0
RECONNECT_S = 2.0
RC_MIN, RC_MAX, RC_CENTER = 200, 1820, 1000
BAR_W = 180

KNOWN = {
    "0x25":"8Servo",    "0x31":"Pinpoint",  "0x32":"?",
    "0x40":"Encoder?",  "0x43":"?",         "0x48":"?",
    "0x59":"ExtEncoder","0x63":"custom",     "0x70":"PaHub",
}

CH_DEFS = [
    ("1","Rotate  L / R",                 "axis"),
    ("2","Mast  Up / Down",               "axis"),
    ("3","FWD / RVS",                     "axis"),
    ("4","Strafe  L / R",                 "axis"),
    ("5","Authority Rocker (OFF/RC/AUTO)","tri"),
    ("6","Deadman",                       "dead"),
    ("7","Mast Rocker (OFF/ON/RSVD)",     "tri"),
    ("8","Speed control",                 "uni"),
]

# ── State ────────────────────────────────────────────────────────────────

@dataclass
class SbusFrame:
    channels: list = field(default_factory=lambda: [RC_CENTER]*8)
    failsafe: bool = False; frame_lost: bool = False; frame_count: int = 0
    last_seen: Optional[float] = None

@dataclass
class SbusStats:
    ok: int = 0; bad: int = 0; last_ok_ms: int = 0
    last_seen: Optional[float] = None

@dataclass
class Sensors:
    power_v: float = 0.0; last_seen: Optional[float] = None

@dataclass
class I2cState:
    detected: List[str] = field(default_factory=list)
    last_seen: Optional[float] = None

@dataclass
class PdhState:
    device_id: int = -1
    voltage_v: float = 0.0
    total_current_a: float = 0.0
    total_power_w: float = 0.0
    energy_wh: float = 0.0
    switch_channel: bool = False
    brownout_fault: bool = False
    can_warning_fault: bool = False
    hardware_fault: bool = False
    seen_flags: str = "00000"
    channels_a: list = field(default_factory=lambda: [0.0] * 24)
    last_seen: Optional[float] = None

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.sbus = SbusFrame(); self.stats = SbusStats()
        self.sensors = Sensors(); self.i2c = I2cState(); self.pdh = PdhState()
        self.connected = False; self.conn_err = ""; self.log: List[str] = []
        self.rx_count = 0
        self.tx_queue: List[str] = []
    def push(self, line):
        self.log.append(line)
        self.rx_count += 1
        if len(self.log) > 500: self.log = self.log[-500:]

def _queue_cmd(state: State, cmd: str):
    with state.lock:
        state.tx_queue.append(cmd)

def _cmd_pdh_switch_on(sender, app_data, user_data):
    _queue_cmd(user_data, "PDH_SWITCH 1")

def _cmd_pdh_switch_off(sender, app_data, user_data):
    _queue_cmd(user_data, "PDH_SWITCH 0")

def _cmd_pdh_clear_faults(sender, app_data, user_data):
    _queue_cmd(user_data, "PDH_CLEAR_FAULTS")

# ── Parsers ──────────────────────────────────────────────────────────────

_P_OK   = re.compile(r"\[SBUS\] OK f=(\d+).*ch1=(\d+) ch2=(\d+) ch3=(\d+) ch4=(\d+) ch5=(\d+) ch6=(\d+) ch7=(\d+) ch8=(\d+) fs=(\d+) lost=(\d+)")
_P_STAT = re.compile(r"\[SBUS\] prof=.* ok=(\d+) bad=(\d+) last_ok_ms=(\d+)")
_P_SENS = re.compile(r"\[SENS\] power=([\d.]+)V")
_P_SCAN = re.compile(r"\[I2C\] scan:(.*)")
_P_PDH_STAT = re.compile(r"\[PDH\] stat id=(\d+) v=([\d.]+)V i=([\d.]+)A p=([\d.]+)W e=([\d.]+)Wh sw=(\d) brn=(\d) can=(\d) hw=(\d) seen=(\d{5})")
_P_PDH_CH = re.compile(r"\[PDH\] ch (.*)")
_P_CH_ITEM = re.compile(r"c(\d+)=([\d.]+)")

def _parse(line, s):
    now = time.monotonic()
    m = _P_OK.search(line)
    if m:
        s.sbus.frame_count = int(m.group(1))
        s.sbus.channels = [int(m.group(i)) for i in range(2, 10)]
        s.sbus.failsafe = m.group(10) == "1"; s.sbus.frame_lost = m.group(11) == "1"
        s.sbus.last_seen = now; return
    m = _P_STAT.search(line)
    if m:
        s.stats.ok = int(m.group(1)); s.stats.bad = int(m.group(2))
        s.stats.last_ok_ms = int(m.group(3)); s.stats.last_seen = now; return
    m = _P_SENS.search(line)
    if m: s.sensors.power_v = float(m.group(1)); s.sensors.last_seen = now; return
    m = _P_SCAN.search(line)
    if m:
        s.i2c.detected = [t for t in m.group(1).split() if t.startswith("0x")]
        s.i2c.last_seen = now
        return

    m = _P_PDH_STAT.search(line)
    if m:
        s.pdh.device_id = int(m.group(1))
        s.pdh.voltage_v = float(m.group(2))
        s.pdh.total_current_a = float(m.group(3))
        s.pdh.total_power_w = float(m.group(4))
        s.pdh.energy_wh = float(m.group(5))
        s.pdh.switch_channel = m.group(6) == "1"
        s.pdh.brownout_fault = m.group(7) == "1"
        s.pdh.can_warning_fault = m.group(8) == "1"
        s.pdh.hardware_fault = m.group(9) == "1"
        s.pdh.seen_flags = m.group(10)
        s.pdh.last_seen = now
        return

    m = _P_PDH_CH.search(line)
    if m:
        for chm in _P_CH_ITEM.finditer(m.group(1)):
            idx = int(chm.group(1))
            if 0 <= idx < 24:
                s.pdh.channels_a[idx] = float(chm.group(2))
        s.pdh.last_seen = now

def _reader(port, baud, s):
    while True:
        try:
            with serial.Serial(port, baud, timeout=1.0) as ser:
                with s.lock: s.connected = True; s.conn_err = ""
                while True:
                    try:
                        with s.lock:
                            pending = list(s.tx_queue)
                            s.tx_queue.clear()
                        for cmd in pending:
                            ser.write((cmd + "\n").encode("utf-8"))

                        raw = ser.readline()
                        if not raw: continue
                        line = raw.decode("utf-8", errors="replace").rstrip()
                        with s.lock: _parse(line, s); s.push(line)
                    except serial.SerialException as e:
                        with s.lock: s.connected = False; s.conn_err = str(e); break
        except Exception as e:
            with s.lock: s.connected = False; s.conn_err = str(e)
        time.sleep(RECONNECT_S)

# ── Helpers ──────────────────────────────────────────────────────────────

def _age(t):
    if t is None: return "no data", "t_err"
    a = time.monotonic() - t
    if a > STALE_S: return f"stale ({a:.0f}s)", "t_warn"
    return f"{a:.1f}s ago", "t_ok"

def _axis(raw):
    c = RC_CENTER
    if raw <= c: return max(-1.0, (raw - c) / max(c - RC_MIN, 1))
    return min(1.0, (raw - c) / max(RC_MAX - c, 1))

def _uni(raw): return max(0.0, min(1.0, (raw - RC_MIN) / max(RC_MAX - RC_MIN, 1)))

# ── UI ───────────────────────────────────────────────────────────────────

def _make_themes():
    def t(r, g, b, tag):
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Text, [r, g, b])
    t(100, 210, 130, "t_ok")
    t(220,  80,  80, "t_err")
    t(220, 180,  60, "t_warn")
    t(120, 120, 145, "t_dim")
    t(100, 180, 255, "t_blue")

def _build(port, baud, state):
    _make_themes()

    with dpg.window(tag="main"):

        # Header
        with dpg.group(horizontal=True):
            dpg.add_text("[*] CONNECTING...", tag="conn_text")
            dpg.bind_item_theme("conn_text", "t_dim")
            dpg.add_text(f"      {port}  @  {baud} baud", color=[120, 120, 145])
            dpg.add_text("      rx: 0 lines", tag="rx_text")
            dpg.bind_item_theme("rx_text", "t_dim")
        dpg.add_separator()

        # PDH
        with dpg.child_window(height=135, border=True, no_scrollbar=True):
            dpg.add_text("REV PDH (CAN)", color=[100, 220, 180])
            with dpg.group(horizontal=True):
                dpg.add_button(label="Switch ON", callback=_cmd_pdh_switch_on, user_data=state)
                dpg.add_button(label="Switch OFF", callback=_cmd_pdh_switch_off, user_data=state)
                dpg.add_button(label="Clear Faults", callback=_cmd_pdh_clear_faults, user_data=state)
            dpg.add_text("NO DATA", tag="pdh_text")
            dpg.add_text("last:  no data", tag="pdh_age")
            dpg.bind_item_theme("pdh_age", "t_dim")
            dpg.add_input_text(tag="pdh_channels", multiline=True, readonly=True,
                               width=-1, height=55, default_value="")
        dpg.add_separator()

        # Sensors + Stats
        with dpg.group(horizontal=True):
            with dpg.child_window(width=310, height=75, border=True, no_scrollbar=True):
                dpg.add_text("Sensors", color=[220, 180, 60])
                dpg.add_text("Bus voltage:  --", tag="volt_text")
                dpg.add_text("last:  no data",   tag="volt_age")
                dpg.bind_item_theme("volt_age", "t_dim")
            dpg.add_spacer(width=4)
            with dpg.child_window(height=75, border=True, no_scrollbar=True):
                dpg.add_text("SBUS Stats", color=[100, 200, 230])
                dpg.add_text("--",           tag="stat_text")
                dpg.add_text("last:  no data", tag="stat_age")
                dpg.bind_item_theme("stat_age", "t_dim")
        dpg.add_separator()

        # RC Channels
        dpg.add_text("RC Channels", color=[180, 140, 220])
        with dpg.table(header_row=True,
                       borders_innerV=True, borders_outerV=True,
                       borders_innerH=True, borders_outerH=True,
                       row_background=True):
            dpg.add_table_column(label="CH",    width_fixed=True, init_width_or_weight=28)
            dpg.add_table_column(label="Role",  width_fixed=True, init_width_or_weight=150)
            dpg.add_table_column(label="Raw",   width_fixed=True, init_width_or_weight=50)
            dpg.add_table_column(label="Bar",   width_fixed=True, init_width_or_weight=BAR_W+14)
            dpg.add_table_column(label="Value", width_fixed=True, init_width_or_weight=90)

            for i, (ch, role, kind) in enumerate(CH_DEFS):
                with dpg.table_row():
                    dpg.add_text(ch, color=[120, 120, 145])
                    dpg.add_text(role)
                    dpg.add_text("--", tag=f"raw_{i}")
                    if kind in ("axis", "uni"):
                        dpg.add_progress_bar(
                            tag=f"bar_{i}",
                            default_value=0.5 if kind == "axis" else 0.0,
                            width=BAR_W, overlay="  0.00")
                    else:
                        dpg.add_spacer(width=BAR_W)
                    dpg.add_text("--", tag=f"val_{i}")
                    dpg.bind_item_theme(f"val_{i}", "t_blue")

        dpg.add_text("", tag="rc_footer")
        dpg.bind_item_theme("rc_footer", "t_dim")
        dpg.add_separator()

        # I2C + Log
        with dpg.group(horizontal=True):
            with dpg.child_window(width=360, height=-1, border=True):
                dpg.add_text("I2C Bus", color=[100, 150, 240])
                dpg.add_input_text(tag="i2c_text", multiline=True, readonly=True,
                                   width=-1, height=-28, default_value="NO DATA")
                dpg.add_text("last:  no data", tag="i2c_age")
                dpg.bind_item_theme("i2c_age", "t_dim")
            dpg.add_spacer(width=4)
            with dpg.child_window(height=-1, border=True):
                dpg.add_text("Raw Log", color=[120, 120, 145])
                dpg.add_input_text(tag="log_text", multiline=True, readonly=True,
                                   width=-1, height=-1, default_value="")

# ── Refresh ──────────────────────────────────────────────────────────────

def _refresh(state):
    with state.lock:
        ok = state.connected; err = state.conn_err
        rx = state.rx_count
        # snapshot mutable fields by value to avoid data races
        channels = list(state.sbus.channels)
        failsafe = state.sbus.failsafe
        frame_lost = state.sbus.frame_lost
        frame_count = state.sbus.frame_count
        sbus_last = state.sbus.last_seen
        stats_ok = state.stats.ok; stats_bad = state.stats.bad
        stats_ms = state.stats.last_ok_ms; stats_last = state.stats.last_seen
        power_v = state.sensors.power_v; sens_last = state.sensors.last_seen
        i2c_det = list(state.i2c.detected); i2c_last = state.i2c.last_seen
        pdh_id = state.pdh.device_id
        pdh_v = state.pdh.voltage_v
        pdh_i = state.pdh.total_current_a
        pdh_p = state.pdh.total_power_w
        pdh_e = state.pdh.energy_wh
        pdh_sw = state.pdh.switch_channel
        pdh_brn = state.pdh.brownout_fault
        pdh_can = state.pdh.can_warning_fault
        pdh_hw = state.pdh.hardware_fault
        pdh_seen = state.pdh.seen_flags
        pdh_ch = list(state.pdh.channels_a)
        pdh_last = state.pdh.last_seen
        log_snap = list(state.log)

    dpg.set_value("rx_text", f"      rx: {rx:,} lines")

    if ok:
        dpg.set_value("conn_text", "[*] CONNECTED")
        dpg.bind_item_theme("conn_text", "t_ok")
    else:
        dpg.set_value("conn_text", f"[ ] DISCONNECTED   {err[:60]}")
        dpg.bind_item_theme("conn_text", "t_err")

    at, at_th = _age(sens_last)
    if sens_last is None:
        dpg.set_value("volt_text", "Bus voltage:  NO DATA")
        dpg.bind_item_theme("volt_text", "t_err")
    else:
        ok_v = 10.5 < power_v < 13.5
        dpg.set_value("volt_text", f"Bus voltage:  {power_v:.2f} V")
        dpg.bind_item_theme("volt_text", "t_ok" if ok_v else "t_err")
    dpg.set_value("volt_age", f"last:  {at}")
    dpg.bind_item_theme("volt_age", at_th)

    at, at_th = _age(stats_last)
    if stats_last is None:
        dpg.set_value("stat_text", "NO DATA")
        dpg.bind_item_theme("stat_text", "t_err")
    else:
        pct = stats_bad / max(stats_ok + stats_bad, 1) * 100
        dpg.set_value("stat_text",
            f"ok={stats_ok:,}  bad={stats_bad} ({pct:.1f}%)  last_ok={stats_ms}ms")
        dpg.bind_item_theme("stat_text",
            "t_ok" if pct < 1 else ("t_warn" if pct < 5 else "t_err"))
    dpg.set_value("stat_age", f"last:  {at}")
    dpg.bind_item_theme("stat_age", at_th)

    for i, (_, _, kind) in enumerate(CH_DEFS):
        raw = channels[i]
        dpg.set_value(f"raw_{i}", str(raw))
        if kind == "axis":
            v = _axis(raw)
            dpg.set_value(f"bar_{i}", (v + 1) / 2)
            dpg.configure_item(f"bar_{i}", overlay=f"{v:+.2f}")
            if i == 0:
                txt = f"{'R' if v > 0.05 else ('L' if v < -0.05 else 'C')} {v:+.2f}"
            elif i == 1:
                txt = f"{'UP' if v > 0.05 else ('DOWN' if v < -0.05 else 'HOLD')} {v:+.2f}"
            else:
                txt = f"{v:+.2f}"
            dpg.set_value(f"val_{i}", txt)
            dpg.bind_item_theme(f"val_{i}", "t_blue")
        elif kind == "uni":
            v = _uni(raw)
            dpg.set_value(f"bar_{i}", v)
            dpg.configure_item(f"bar_{i}", overlay=f"{v:.2f}")
            dpg.set_value(f"val_{i}", f"{v:.2f}")
            dpg.bind_item_theme(f"val_{i}", "t_blue")
        elif kind == "tri":
            if i == 4:
                if raw < 400:
                    txt, th = "OFF (LOW)", "t_err"
                elif raw > 1600:
                    txt, th = "AUTONOMOUS (HIGH)", "t_warn"
                else:
                    txt, th = "RC_MANUAL (MID)", "t_ok"
            elif i == 6:
                if raw < 400:
                    txt, th = "MAST RC OFF (LOW)", "t_err"
                elif raw > 1600:
                    txt, th = "RESERVED (HIGH)", "t_warn"
                else:
                    txt, th = "MAST RC ON (MID)", "t_ok"
            else:
                if raw < 400:
                    txt, th = "LOW  [-1]", "t_blue"
                elif raw > 1600:
                    txt, th = "HIGH [+1]", "t_blue"
                else:
                    txt, th = "MID  [ 0]", "t_dim"
            dpg.set_value(f"val_{i}", txt)
            dpg.bind_item_theme(f"val_{i}", th)
        elif kind == "dead":
            armed = raw > RC_CENTER
            dpg.set_value(f"val_{i}", "ARMED" if armed else "disarmed")
            dpg.bind_item_theme(f"val_{i}", "t_ok" if armed else "t_err")

    at, at_th = _age(sbus_last)
    dpg.set_value("rc_footer",
        f"failsafe:{'FAILSAFE' if failsafe else 'ok'}  "
        f"frame:{'LOST' if frame_lost else 'ok'}  "
        f"f={frame_count:,}  last:{at}")
    dpg.bind_item_theme("rc_footer", at_th)

    at, at_th = _age(i2c_last)
    if i2c_last is None:
        i2c_str = "NO DATA"
    else:
        det = set(i2c_det)
        i2c_str = "\n".join(
            f"{addr:<6}  {KNOWN.get(addr,'?'):<14}  {'DETECTED' if addr in det else 'absent'}"
            for addr in sorted(set(list(KNOWN) + list(det))))
    dpg.set_value("i2c_text", i2c_str)
    dpg.set_value("i2c_age", f"last:  {at}")
    dpg.bind_item_theme("i2c_age", at_th)

    at, at_th = _age(pdh_last)
    if pdh_last is None:
        dpg.set_value("pdh_text", "NO DATA")
        dpg.bind_item_theme("pdh_text", "t_err")
        dpg.set_value("pdh_channels", "")
    else:
        dpg.set_value(
            "pdh_text",
            f"id={pdh_id}  v={pdh_v:.3f}V  i={pdh_i:.1f}A  p={pdh_p:.1f}W  e={pdh_e:.3f}Wh  "
            f"sw={'ON' if pdh_sw else 'OFF'}  faults(brn/can/hw)={int(pdh_brn)}/{int(pdh_can)}/{int(pdh_hw)}  seen={pdh_seen}")
        dpg.bind_item_theme("pdh_text", "t_warn" if (pdh_brn or pdh_can or pdh_hw) else "t_ok")
        rows = []
        for i in range(0, 24, 4):
            rows.append("  ".join(f"c{j:02d}={pdh_ch[j]:6.3f}A" for j in range(i, i + 4)))
        dpg.set_value("pdh_channels", "\n".join(rows))
    dpg.set_value("pdh_age", f"last:  {at}")
    dpg.bind_item_theme("pdh_age", at_th)

    dpg.set_value("log_text", "\n".join(log_snap[-80:]))

# ── Entry ────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=DEFAULT_PORT)
    ap.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    ap.add_argument("--list-ports", action="store_true")
    a = ap.parse_args()

    if a.list_ports:
        for p in sorted(serial.tools.list_ports.comports()):
            print(f"  {p.device:30s}  {p.description}")
        return

    state = State()
    threading.Thread(target=_reader, args=(a.port, a.baud, state), daemon=True).start()

    dpg.create_context()
    _build(a.port, a.baud, state)
    dpg.create_viewport(title="OpenDubs Telemetry", width=920, height=750,
                        min_width=800, min_height=600)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.set_primary_window("main", True)

    _last_err = [""]
    while dpg.is_dearpygui_running():
        try:
            _refresh(state)
        except Exception as e:
            msg = str(e)
            if msg != _last_err[0]:  # only update when error changes
                dpg.set_value("conn_text", f"[!] REFRESH ERROR: {msg[:80]}")
                dpg.bind_item_theme("conn_text", "t_err")
                _last_err[0] = msg
        dpg.render_dearpygui_frame()

    dpg.destroy_context()

if __name__ == "__main__":
    main()
