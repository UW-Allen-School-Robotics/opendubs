"""Push the authoritative robot FSM state (robot_state) to the operator-facing
state indicators, so the PRISM strip and the Tab5 face always agree with the
dashboard:

  - goBILDA PRISM: a solid color via the StampPLC's I2C LED_COLOR command (ch0).
  - Tab5 face:     a "STATE <NAME>" line over the Tab5's USB CDC serial.

Runs as a browser-independent background thread so the indicators follow state
even with no kiosk browser open. Pushes on every state change plus a periodic
refresh (keeps the Tab5's link-alive from timing out, and re-asserts the PRISM
color over any transient PWM write from the firmware's local state-sync).

The Tab5 port is opened once and held open to prevent continuous resets. 
DTR/RTS must be explicitly asserted (True), otherwise the ESP32-P4 HWCDC driver
will silently drop incoming data packets. The first connection will cause a
single reboot of the Tab5.
"""
from __future__ import annotations

import glob
import os
import threading
import time

# robot_state FSM name -> (Tab5 STATES[].name, PRISM (r,g,b), PRISM brightness 0-100).
# Colors track robot_state.PRESENTATION (white/green/white/cyan/amber/red).
_MAP = {
    "INIT":               ("INIT",       (200, 210, 225), 55),
    "SAFE_IDLE":          ("SAFE_IDLE",  (30, 210, 70),   70),   # clear GREEN
    "MANUAL_CONTROL":     ("MANUAL",     (235, 240, 248), 80),
    "AUTONOMOUS_CONTROL": ("AUTONOMOUS", (0, 150, 255),   80),   # clear CYAN (blue) — distinct from green
    "DEGRADED":           ("DEGRADED",   (240, 150, 40),  85),
    "E_STOP_LATCH":       ("E_STOP",     (255, 35, 35),   95),
}

_TAB5_GLOB = os.environ.get(
    "OPENDUBS_TAB5_DEV_GLOB",
    "/dev/serial/by-id/*30:ED:A0:E1:5D:B6*if00",  # Tab5 ESP32-P4
)
_REFRESH_S = 3.0   # re-push at least this often (Tab5 link timeout is 5 s)


# Persistent Tab5 serial handle. IMPORTANT: opening the Tab5's USB CDC port RESETS it once
# (even with DTR/RTS de-asserted) — so we open ONCE and HOLD it; subsequent writes never reset
# it. Reopening per push was the "screen blacks every couple seconds" bug. (Holding the port
# means a Tab5 reflash needs the dash stopped first, like the StampPLC.)
_tab5_ser = None


def _tab5_write(line: str) -> None:
    global _tab5_ser
    import serial

    try:
        if _tab5_ser is None or not _tab5_ser.is_open:
            import sys
            matches = glob.glob(_TAB5_GLOB)
            sys.stderr.write(f"TAB5: glob matches={matches}\n")
            if not matches:
                return
            s = serial.Serial()
            s.port = matches[0]
            s.baudrate = 115200
            s.timeout = 0.2
            s.dtr = True
            s.rts = True
            s.open()          # one-time reset of the Tab5 on (re)connect; then held open = stable
            sys.stderr.write(f"TAB5: Port {matches[0]} opened.\n")
            _tab5_ser = s
        _tab5_ser.write((line + "\n").encode())
        _tab5_ser.flush()
    except Exception as e:
        import sys
        sys.stderr.write(f"TAB5_ERROR: {e}\n")
        try:
            if _tab5_ser:
                _tab5_ser.close()
        except Exception:
            pass
        _tab5_ser = None


def _push_prism(rs: str) -> None:
    ent = _MAP.get(rs)
    if not ent:
        return
    _, (r, g, b), bright = ent
    try:
        import stamplc
        stamplc.reader.send(f"LED_COLOR {r} {g} {b} {bright}")   # PRISM via StampPLC I2C (ch0)
    except Exception:
        pass


def _push_tab5(rs: str) -> None:
    ent = _MAP.get(rs)
    if ent:
        import sys
        sys.stderr.write(f"TAB5: Pushing state {rs} -> {ent[0]}\n")
        _tab5_write(f"STATE {ent[0]}")


def _loop() -> None:
    import status as status_mod
    import stamplc

    prev = None
    last = 0.0
    prev_turn = None
    prev_conn = False
    while True:
        try:
            # StampPLC (re)connect edge — e.g. after a firmware flash or replug the MCU reboots
            # with a blank PRISM. Force a base-color re-push so the strip isn't dark (and so the
            # turn-signal overlay has a real color to reveal when it clears) until the next state flip.
            conn = bool(stamplc.reader.snapshot().get("connected"))
            if conn and not prev_conn:
                prev = None
            prev_conn = conn
            rs = (status_mod._robot_state() or {}).get("state")
            now = time.monotonic()
            if rs:
                if rs != prev:
                    _push_prism(rs)          # PRISM color ONLY on real change (no periodic re-render flicker)
                    _push_tab5(rs)
                    prev = rs
                    last = now
                elif (now - last) >= _REFRESH_S:
                    _push_tab5(rs)           # refresh keeps the Tab5 link-alive; held port → no reboot
                    last = now
            # turn-signal (movement intent) → Tab5 chevrons, on change
            turn = (stamplc.reader.snapshot().get("drive") or {}).get("turn")
            if turn != prev_turn:
                _tab5_write(f"TURN {turn or '0'}")
                prev_turn = turn
        except Exception:
            pass
        time.sleep(0.3)


_started = False


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, name="state_out", daemon=True).start()
