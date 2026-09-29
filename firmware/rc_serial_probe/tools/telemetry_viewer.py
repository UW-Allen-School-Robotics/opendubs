#!/usr/bin/env python3
"""
OpenDubs telemetry decoder/visualizer.

Reads M5StamPLC rc_serial_probe firmware output over USB serial and renders
a live terminal dashboard using Rich.  Fails gracefully when individual
telemetry streams are absent or the serial port is unavailable.

Usage:
    python telemetry_viewer.py                         # default port from platformio.ini
    python telemetry_viewer.py --port /dev/cu.usbmodem2121101
    python telemetry_viewer.py --port /dev/cu.usbmodem2121101 --baud 115200
"""

import argparse
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed.  Run:  pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)

try:
    from rich import box
    from rich.columns import Columns
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("ERROR: rich not installed.  Run:  pip install -r requirements.txt", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_PORT = "/dev/cu.usbmodem2121101"
DEFAULT_BAUD = 115200
STALE_THRESHOLD_S = 8.0
RECONNECT_DELAY_S = 2.0
REFRESH_HZ = 4

RC_RAW_MIN = 200
RC_RAW_MAX = 1820
RC_RAW_CENTER = 1000

# Known I2C addresses on the StamPLC external bus for this robot.
KNOWN_DEVICES = {
    "0x25": "8Servo",
    "0x31": "Pinpoint (plan)",
    "0x32": "?",
    "0x40": "Encoder (builtin?)",
    "0x43": "?",
    "0x48": "?",
    "0x59": "ExtEncoder",
    "0x63": "custom",
    "0x70": "PaHub",
}


# ---------------------------------------------------------------------------
# Telemetry state (shared between reader thread and display loop)
# ---------------------------------------------------------------------------

@dataclass
class SbusFrame:
    channels: List[int] = field(default_factory=lambda: [RC_RAW_CENTER] * 8)
    failsafe: bool = False
    frame_lost: bool = False
    frame_count: int = 0
    last_seen: Optional[float] = None


@dataclass
class SbusStats:
    bytes_total: int = 0
    ok: int = 0
    bad: int = 0
    last_ok_ms: int = 0
    last_seen: Optional[float] = None


@dataclass
class SensorData:
    power_v: float = 0.0
    last_seen: Optional[float] = None


@dataclass
class I2cData:
    direct_0x63: Optional[bool] = None
    detected: List[str] = field(default_factory=list)
    last_seen: Optional[float] = None


@dataclass
class ConnectionState:
    connected: bool = False
    error: str = ""
    port: str = ""


class TelemetryState:
    """Thread-safe container for all telemetry streams."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sbus = SbusFrame()
        self.sbus_stats = SbusStats()
        self.sensors = SensorData()
        self.i2c = I2cData()
        self.conn = ConnectionState()
        self.raw_log: List[str] = []

    def append_log(self, line: str) -> None:
        self.raw_log.append(line)
        if len(self.raw_log) > 200:
            self.raw_log = self.raw_log[-200:]


# ---------------------------------------------------------------------------
# Line parsers
# ---------------------------------------------------------------------------

# [SBUS] OK f=638420 prof=SBUS (inverted) ch1=992 ch2=992 ... fs=0 lost=0
_RE_SBUS_OK = re.compile(
    r"\[SBUS\] OK f=(\d+) prof=.+ "
    r"ch1=(\d+) ch2=(\d+) ch3=(\d+) ch4=(\d+) "
    r"ch5=(\d+) ch6=(\d+) ch7=(\d+) ch8=(\d+) "
    r"fs=(\d+) lost=(\d+)"
)

# [SBUS] prof=SBUS (inverted) bytes=12345 ok=6789 bad=0 last_ok_ms=123
_RE_SBUS_STATS = re.compile(
    r"\[SBUS\] prof=.+ bytes=(\d+) ok=(\d+) bad=(\d+) last_ok_ms=(\d+)"
)

# [SENS] power=11.96V
_RE_SENS_POWER = re.compile(r"\[SENS\] power=([\d.]+)V")

# [I2C] direct(0x63)=yes on StamPLC external I2C bus
_RE_I2C_DIRECT = re.compile(r"\[I2C\] direct\(0x63\)=(yes|no)")

# [I2C] scan: 0x32 0x40 0x59  OR  [I2C] scan: none
_RE_I2C_SCAN = re.compile(r"\[I2C\] scan:(.*)")


def _parse_line(line: str, state: TelemetryState) -> None:
    """Parse one firmware output line and update state in-place (lock must be held)."""
    now = time.monotonic()

    m = _RE_SBUS_OK.search(line)
    if m:
        state.sbus.frame_count = int(m.group(1))
        state.sbus.channels = [int(m.group(i)) for i in range(2, 10)]
        state.sbus.failsafe = m.group(10) == "1"
        state.sbus.frame_lost = m.group(11) == "1"
        state.sbus.last_seen = now
        return

    m = _RE_SBUS_STATS.search(line)
    if m:
        state.sbus_stats.bytes_total = int(m.group(1))
        state.sbus_stats.ok = int(m.group(2))
        state.sbus_stats.bad = int(m.group(3))
        state.sbus_stats.last_ok_ms = int(m.group(4))
        state.sbus_stats.last_seen = now
        return

    m = _RE_SENS_POWER.search(line)
    if m:
        state.sensors.power_v = float(m.group(1))
        state.sensors.last_seen = now
        return

    m = _RE_I2C_DIRECT.search(line)
    if m:
        state.i2c.direct_0x63 = m.group(1) == "yes"
        state.i2c.last_seen = now
        return

    m = _RE_I2C_SCAN.search(line)
    if m:
        tokens = m.group(1).strip().split()
        state.i2c.detected = [t for t in tokens if t.startswith("0x")]
        state.i2c.last_seen = now
        return


# ---------------------------------------------------------------------------
# Serial reader thread
# ---------------------------------------------------------------------------

def _serial_reader(port: str, baud: int, state: TelemetryState) -> None:
    """Runs forever in a daemon thread.  Reconnects automatically on errors."""
    while True:
        try:
            with serial.Serial(port, baud, timeout=1.0) as ser:
                with state.lock:
                    state.conn.connected = True
                    state.conn.error = ""
                    state.conn.port = port

                while True:
                    try:
                        raw = ser.readline()
                        if not raw:
                            continue
                        line = raw.decode("utf-8", errors="replace").rstrip()
                        with state.lock:
                            _parse_line(line, state)
                            state.append_log(line)
                    except serial.SerialException as exc:
                        with state.lock:
                            state.conn.connected = False
                            state.conn.error = str(exc)
                        break

        except serial.SerialException as exc:
            with state.lock:
                state.conn.connected = False
                state.conn.error = str(exc)
        except Exception as exc:  # pragma: no cover
            with state.lock:
                state.conn.connected = False
                state.conn.error = f"Unexpected: {exc}"

        time.sleep(RECONNECT_DELAY_S)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _staleness(last_seen: Optional[float]) -> Text:
    """Return a coloured age label for a stream's last_seen timestamp."""
    if last_seen is None:
        return Text("NO DATA", style="bold red")
    age = time.monotonic() - last_seen
    if age > STALE_THRESHOLD_S:
        return Text(f"STALE ({age:.0f}s)", style="bold yellow")
    return Text(f"{age:.1f}s ago", style="green")


def _norm_axis(raw: int) -> float:
    """Map raw SBUS value → [-1.0, 1.0] with center at RC_RAW_CENTER."""
    center = RC_RAW_CENTER
    if raw <= center:
        span = max(center - RC_RAW_MIN, 1)
        return max(-1.0, (raw - center) / span)
    span = max(RC_RAW_MAX - center, 1)
    return min(1.0, (raw - center) / span)


def _norm_uni(raw: int) -> float:
    """Map raw SBUS value → [0.0, 1.0]."""
    span = max(RC_RAW_MAX - RC_RAW_MIN, 1)
    return max(0.0, min(1.0, (raw - RC_RAW_MIN) / span))


def _tristate(raw: int) -> Text:
    if raw < 400:
        return Text("LOW  [-1]", style="cyan")
    if raw > 1600:
        return Text("HIGH [+1]", style="cyan")
    return Text("MID  [ 0]", style="dim cyan")


def _bar(value: float, width: int = 22, lo: float = -1.0, hi: float = 1.0) -> Text:
    """Render an ASCII bar.  Works for bipolar [-1,1] and unipolar [0,1] ranges."""
    span = max(hi - lo, 1e-9)
    frac = max(0.0, min(1.0, (value - lo) / span))
    filled = int(frac * width)

    chars = list("─" * width)

    # mark zero/center if within range
    if lo < 0 < hi:
        center_pos = int((-lo / span) * width)
        if 0 <= center_pos < width:
            chars[center_pos] = "┼"

    # fill from center (or from left for unipolar)
    if lo < 0 < hi:
        center_pos = int((-lo / span) * width)
        lo_i, hi_i = (min(filled, center_pos), max(filled, center_pos))
        for i in range(lo_i, hi_i + 1):
            chars[i] = "█"
    else:
        for i in range(filled):
            chars[i] = "█"

    color = "green" if abs(value) < 0.1 else ("yellow" if abs(value) < 0.7 else "red")
    bar_str = "".join(chars)
    return Text(f"[{bar_str}] {value:+.2f}", style=color)


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def _panel_serial(state: TelemetryState) -> Panel:
    if state.conn.connected:
        body = Text.assemble(
            ("● CONNECTED  ", "bold green"),
            (state.conn.port, "white"),
            (f"  {DEFAULT_BAUD} baud", "dim"),
        )
    else:
        msg = state.conn.error or "port not found – retrying…"
        body = Text.assemble(("○ DISCONNECTED  ", "bold red"), (msg, "yellow"))
    return Panel(body, title="[bold white]Serial Connection[/bold white]", border_style="white", padding=(0, 1))


def _panel_sensors(state: TelemetryState) -> Panel:
    age = _staleness(state.sensors.last_seen)
    if state.sensors.last_seen is None:
        body = Text.assemble(("Bus voltage: ", "dim"), ("NO DATA", "bold red"))
    else:
        v = state.sensors.power_v
        color = "green" if 10.5 < v < 13.5 else "bold red"
        body = Text.assemble(
            ("Bus voltage: ", "dim"),
            (f"{v:.2f} V", color),
            ("  last: ", "dim"),
            age,
        )
    return Panel(body, title="[bold yellow]Sensors[/bold yellow]", border_style="yellow", padding=(0, 1))


def _panel_sbus_stats(state: TelemetryState) -> Panel:
    age = _staleness(state.sbus_stats.last_seen)
    s = state.sbus_stats
    if state.sbus_stats.last_seen is None:
        body = Text.assemble(("SBUS stats: ", "dim"), ("NO DATA", "bold red"))
    else:
        total = s.ok + s.bad
        bad_pct = s.bad / max(total, 1) * 100
        color = "green" if bad_pct < 1 else ("yellow" if bad_pct < 5 else "bold red")
        body = Text.assemble(
            (f"bytes={s.bytes_total:,}  ", "white"),
            (f"ok={s.ok:,}  ", "green"),
            (f"bad={s.bad} ({bad_pct:.1f}%)  ", color),
            (f"last_ok={s.last_ok_ms} ms  ", "white"),
            ("last: ", "dim"),
            age,
        )
    return Panel(body, title="[bold cyan]SBUS Stats[/bold cyan]", border_style="cyan", padding=(0, 1))


def _panel_rc(state: TelemetryState) -> Panel:
    age = _staleness(state.sbus.last_seen)
    ch = state.sbus.channels

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta", expand=True)
    t.add_column("CH", width=3, justify="right")
    t.add_column("Role", width=20)
    t.add_column("Raw", width=5, justify="right")
    t.add_column("Value / Bar", min_width=32)

    def _axis_row(idx: int, role: str) -> None:
        raw = ch[idx - 1]
        t.add_row(str(idx), role, str(raw), _bar(_norm_axis(raw), width=24))

    def _uni_row(idx: int, role: str) -> None:
        raw = ch[idx - 1]
        t.add_row(str(idx), role, str(raw), _bar(_norm_uni(raw), width=24, lo=0.0, hi=1.0))

    def _deadman_row(idx: int) -> None:
        raw = ch[idx - 1]
        active = raw > RC_RAW_CENTER
        label = Text("ARMED (ON)", style="bold green") if active else Text("DISARMED (off)", style="bold red")
        t.add_row(str(idx), "Deadman", str(raw), label)

    def _tri_row(idx: int, role: str) -> None:
        raw = ch[idx - 1]
        t.add_row(str(idx), role, str(raw), _tristate(raw))

    _axis_row(1, "—  (unassigned)")
    _axis_row(2, "Rotate  L / R")
    _axis_row(3, "FWD / RVS")
    _axis_row(4, "Strafe  L / R")
    _tri_row(5,  "Rocker (unassigned)")
    _deadman_row(6)
    _tri_row(7,  "Rocker (unassigned)")
    _uni_row(8,  "Speed control")

    fs = state.sbus
    flags = Text.assemble(
        ("  Failsafe: ", "dim"),
        Text("ACTIVE", style="bold red") if fs.failsafe else Text("ok", style="green"),
        ("  Frame: ", "dim"),
        Text("LOST", style="bold red") if fs.frame_lost else Text("ok", style="green"),
        (f"  f={fs.frame_count:,}  ", "dim"),
        ("last: ", "dim"),
        age,
    )

    return Panel(
        t,
        title="[bold cyan]RC Channels[/bold cyan]",
        subtitle=flags,
        border_style="cyan",
    )


def _panel_i2c(state: TelemetryState) -> Panel:
    age = _staleness(state.i2c.last_seen)

    if state.i2c.last_seen is None:
        body = Text.assemble(("I2C scan: ", "dim"), ("NO DATA", "bold red"))
        return Panel(body, title="[bold blue]I2C Bus[/bold blue]", border_style="blue", padding=(0, 1))

    detected = set(state.i2c.detected)
    all_addrs = sorted(set(list(KNOWN_DEVICES.keys()) + list(detected)))

    t = Table(box=box.SIMPLE, show_header=True, header_style="bold blue", expand=True)
    t.add_column("Addr", width=6)
    t.add_column("Device", width=18)
    t.add_column("Status", width=12)

    for addr in all_addrs:
        present = addr in detected
        label = KNOWN_DEVICES.get(addr, "unknown")
        status = Text("DETECTED", style="bold green") if present else Text("absent", style="dim red")
        t.add_row(addr, label, status)

    d63 = state.i2c.direct_0x63
    direct_text = (
        Text("yes", style="green") if d63 is True
        else Text("no", style="dim") if d63 is False
        else Text("?", style="dim")
    )
    footer = Text.assemble(
        ("0x63 direct probe: ", "dim"),
        direct_text,
        ("  last: ", "dim"),
        age,
    )
    return Panel(t, title="[bold blue]I2C Bus[/bold blue]", subtitle=footer, border_style="blue")


def _panel_log(state: TelemetryState, lines: int = 8) -> Panel:
    tail = state.raw_log[-lines:] if state.raw_log else ["(no data yet)"]
    body = Text("\n".join(tail), style="dim white", no_wrap=True)
    return Panel(body, title="[dim]Raw Log[/dim]", border_style="dim", padding=(0, 1))


def _build_display(state: TelemetryState) -> Group:
    """Build the full terminal display from current state (lock held by caller)."""
    return Group(
        _panel_serial(state),
        Columns([_panel_sensors(state), _panel_sbus_stats(state)], equal=True, expand=True),
        _panel_rc(state),
        Columns([_panel_i2c(state), _panel_log(state)], equal=True, expand=True),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OpenDubs telemetry decoder/visualizer")
    p.add_argument(
        "--port", default=DEFAULT_PORT,
        help=f"Serial port (default: {DEFAULT_PORT})"
    )
    p.add_argument(
        "--baud", type=int, default=DEFAULT_BAUD,
        help=f"Baud rate (default: {DEFAULT_BAUD})"
    )
    p.add_argument(
        "--list-ports", action="store_true",
        help="Print available serial ports and exit"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if args.list_ports:
        ports = serial.tools.list_ports.comports()
        if not ports:
            print("No serial ports found.")
        for p in sorted(ports):
            print(f"  {p.device:30s}  {p.description}")
        return

    state = TelemetryState()
    state.conn.port = args.port

    reader = threading.Thread(
        target=_serial_reader,
        args=(args.port, args.baud, state),
        daemon=True,
        name="serial-reader",
    )
    reader.start()

    console = Console()
    try:
        with Live(console=console, refresh_per_second=REFRESH_HZ, screen=False) as live:
            while True:
                with state.lock:
                    display = _build_display(state)
                live.update(display)
                time.sleep(1.0 / REFRESH_HZ)
    except KeyboardInterrupt:
        pass
    finally:
        console.print("\n[dim]Viewer stopped.[/dim]")


if __name__ == "__main__":
    main()
