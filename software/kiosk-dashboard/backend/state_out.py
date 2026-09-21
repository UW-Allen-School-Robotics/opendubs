"""Push the authoritative robot FSM state (robot_state) to the operator-facing
state indicators, so the PRISM strip and the Tab5 face always agree with the
dashboard:

  - goBILDA PRISM: a solid color via the StampPLC's I2C LED_COLOR command (ch0).
  - Tab5 face:     a "STATE <NAME>" line, over UDP to the Tab5 and over its USB CDC serial.

The Tab5 has been wireless since the ESP-Hosted work, and the serial glob has been matching
nothing ever since — so the face has had no state feed at all and simply showed whatever it
booted with. UDP is the path that actually exists now; serial is kept because it costs
nothing and still works when a cable happens to be plugged in.

The line is sent as TEXT rather than a numeric CAN_ID_ROBOT_STATE frame on purpose: the
numeric form is an index into the Tab5's STATES[] table, and the two codebases would drift
silently the first time anyone reordered it. A name cannot drift.

Runs as a browser-independent background thread so the indicators follow state
even with no kiosk browser open. All Tab5 I/O runs on a SEPARATE thread behind a queue:
reaching the head can block for seconds (a failed mDNS lookup of an absent head takes
5 s), and when that shared the PRISM's loop, a missing Tab5 made the PRISM trail the mode
rocker by 10-15 s. A missing head must not slow any other indicator. Pushes on every state change plus a periodic
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
import queue
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

# Tab5 over the network. Resolved once per push attempt so a DHCP change or a reboot is
# picked up without restarting the dashboard.
_TAB5_HOST = os.environ.get("OPENDUBS_TAB5_HOST", "opendubs-tab5.local")
_TAB5_UDP_PORT = int(os.environ.get("OPENDUBS_TAB5_UDP_PORT", "4242"))
_TAB5_RX_PORT = int(os.environ.get("OPENDUBS_TAB5_RX_PORT", "5006"))
_CAN_ID_LIFT_CMD = 0x102
_tab5_sock = None
_PEER_REINTRO_S = 20.0        # re-assert the pairing this often; both ends hold it in RAM
_peers_last_intro = 0.0
_tab5_addr = None
_tab5_addr_ts = 0.0
# A failed lookup of an absent head takes 5 s. Remember the failure rather than paying it
# on every write, which is what turned the state loop from 0.3 s into 10-15 s.
_TAB5_RESOLVE_RETRY_S = 30.0
_tab5_resolve_fail_ts = -_TAB5_RESOLVE_RETRY_S
# The head's own packets to _TAB5_RX_PORT say where it is, more reliably than mDNS.
_TAB5_LEARNED_TTL_S = 10.0
_tab5_learned = None           # (ip, monotonic time) — written by the rx thread
_tab5_q: "queue.Queue[str]" = queue.Queue(maxsize=64)


def _tab5_resolve():
    """Where the head is right now, or None. In order of trust: the address its own packets
    came from (fresh within _TAB5_LEARNED_TTL_S), a cached mDNS answer (30 s), then a new
    lookup — but never more than once per _TAB5_RESOLVE_RETRY_S after a failure. Called only
    from the Tab5 thread, because a lookup can still block for 5 s."""
    global _tab5_addr, _tab5_addr_ts, _tab5_resolve_fail_ts
    import socket
    now = time.monotonic()
    learned = _tab5_learned
    if learned is not None and now - learned[1] < _TAB5_LEARNED_TTL_S:
        return learned[0]
    if _tab5_addr is not None and now - _tab5_addr_ts < 30:
        return _tab5_addr
    if now - _tab5_resolve_fail_ts < _TAB5_RESOLVE_RETRY_S:
        return None
    try:
        _tab5_addr = socket.gethostbyname(_TAB5_HOST)
        _tab5_addr_ts = time.monotonic()
        return _tab5_addr
    except Exception:
        _tab5_addr = None
        _tab5_resolve_fail_ts = time.monotonic()
        return None


def _tab5_udp(line: str) -> None:
    """Best-effort state push to the Tab5 over UDP. Silent on failure: the face is an
    indicator, not a control path, and a missing head must not disturb the dashboard."""
    global _tab5_sock
    import socket
    addr = _tab5_resolve()
    if addr is None:
        return
    try:
        if _tab5_sock is None:
            _tab5_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _tab5_sock.sendto((line + "\n").encode(), (addr, _TAB5_UDP_PORT))
    except Exception:
        pass


# Persistent Tab5 serial handle. IMPORTANT: opening the Tab5's USB CDC port RESETS it once
# (even with DTR/RTS de-asserted) — so we open ONCE and HOLD it; subsequent writes never reset
# it. Reopening per push was the "screen blacks every couple seconds" bug. (Holding the port
# means a Tab5 reflash needs the dash stopped first, like the StampPLC.)
_tab5_ser = None
_TAB5_SER_RETRY_S = 5.0
_tab5_ser_try_ts = -_TAB5_SER_RETRY_S
_tab5_ser_last_matches = None


def _tab5_write(line: str) -> None:
    """Queue a line for the head. Never blocks the caller. If the head has been unreachable
    long enough to fill the queue, the oldest line goes — a stale indicator line is worthless."""
    try:
        _tab5_q.put_nowait(line)
    except queue.Full:
        try:
            _tab5_q.get_nowait()
        except queue.Empty:
            pass
        try:
            _tab5_q.put_nowait(line)
        except queue.Full:
            pass


def _tab5_sender() -> None:
    """The only thread that talks to the head, so the only one that can be held up by it."""
    while True:
        try:
            line = _tab5_q.get(timeout=1.0)
            _tab5_udp(line)            # the path that actually exists on a wireless Tab5
            _tab5_serial(line)
        except queue.Empty:
            pass
        except Exception:
            time.sleep(0.2)
        _introduce_peers()             # needs the head's address, so it lives here too


def _tab5_serial(line: str) -> None:
    global _tab5_ser, _tab5_ser_try_ts, _tab5_ser_last_matches
    import serial

    try:
        if _tab5_ser is None or not _tab5_ser.is_open:
            import sys
            now = time.monotonic()
            if now - _tab5_ser_try_ts < _TAB5_SER_RETRY_S:
                return
            _tab5_ser_try_ts = now
            matches = glob.glob(_TAB5_GLOB)
            if matches != _tab5_ser_last_matches:
                sys.stderr.write(f"TAB5: glob matches={matches}\n")
                _tab5_ser_last_matches = matches
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


_tab5_logged_state = None


def _push_tab5(rs: str) -> None:
    global _tab5_logged_state
    ent = _MAP.get(rs)
    if ent:
        if rs != _tab5_logged_state:
            import sys
            sys.stderr.write(f"TAB5: Pushing state {rs} -> {ent[0]}\n")
            _tab5_logged_state = rs
        _tab5_write(f"STATE {ent[0]}")


def _tab5_listen() -> None:
    """Receive the Tab5's lift REQUESTS and turn them into MAST CMD on the StampPLC.

    The head's up/down buttons are hold-to-move and it republishes its request every 50 ms,
    which is exactly the heartbeat `MAST CMD` wants: let go of the button, or lose the head,
    and the lift stops within 250 ms. Everything still goes through ScissorRelayMast, so the
    pinch guard applies to the touchscreen the same as to the stick.

    This lives in the dashboard because the dashboard is what actually runs on every robot
    and already owns the StampPLC link. The ROS bridge can do the same job, but it is
    optional, and the head's buttons should not depend on ROS being up.
    """
    global _tab5_learned
    import socket
    import struct
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Deliberately NOT SO_REUSEPORT: if something else grabs this port we want a loud
    # failure, not datagrams silently load-balanced away from us.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", _TAB5_RX_PORT))
    except OSError as e:
        import sys
        sys.stderr.write(f"TAB5: cannot bind UDP {_TAB5_RX_PORT}: {e}\n")
        return
    sock.settimeout(1.0)
    last = None
    while True:
        try:
            data, addr = sock.recvfrom(64)
            if len(data) < 13:
                continue
            _tab5_learned = (addr[0], time.monotonic())   # the head is here, right now
            can_id, dlc, payload = struct.unpack("<IB8s", data[:13])
            if can_id != _CAN_ID_LIFT_CMD or dlc < 1:
                continue
            v = {1: 1.0, 2: -1.0}.get(payload[0], 0.0)
            # Re-send every frame, not just on change: this IS the heartbeat.
            import stamplc
            stamplc.reader.send(f"MAST CMD {v:.2f}")
            if v != last:
                import sys
                sys.stderr.write(f"TAB5: lift request {v:+.0f}\n")
                last = v
        except socket.timeout:
            continue
        except Exception:
            time.sleep(0.2)


def _introduce_peers() -> None:
    """Tell each device where the other one is, once each.

    The head takes pan/tilt commands straight from the StampPLC — direct, so it keeps
    answering the sticks even if this dashboard stops — but only from an address the Thor
    has vouched for. The Thor is the one machine that can see both, so it does the
    introduction; after that the data path does not involve it at all.

    RE-ASSERTED PERIODICALLY, not once. Both devices hold the pairing in RAM, so either
    one rebooting forgets it — and a firmware flash reboots the StampPLC every time. A
    one-shot handshake left it with nowhere to send and no way to say so: the sticks simply
    did nothing, with every other indicator healthy. Cheap to repeat, and it means the
    pairing heals itself instead of needing someone to remember.
    """
    global _peers_last_intro
    now = time.monotonic()
    if now - _peers_last_intro < _PEER_REINTRO_S:
        return
    _peers_last_intro = now
    tab5 = _tab5_resolve()
    if tab5 is None:
        # Retry in ~5 s rather than on every pass: each attempt used to cost a 5 s lookup.
        _peers_last_intro = now - _PEER_REINTRO_S + 5.0
        return
    try:
        import stamplc
        plc = os.environ.get("OPENDUBS_STAMPLC_IP", "10.42.2.20")
        _tab5_write(f"PLCIP {plc}")            # head <- "the StampPLC may command you"
        stamplc.reader.send(f"TAB5IP {tab5}")  # PLC  <- "the head lives here"
    except Exception:
        _peers_last_intro = now - _PEER_REINTRO_S + 5.0


def _push_lift() -> None:
    """Send the StampPLC's measured Z-axis state to the Tab5, so the head's height readout is
    the SAME number the pinch guard acts on rather than a second opinion from its own sensor.

    Sent from here rather than from the ROS bridge because this thread actually runs on every
    robot; the bridge is optional."""
    try:
        import stamplc
        d = (stamplc.reader.snapshot().get("drive") or {})
    except Exception:
        return
    h = d.get("mast_h_mm")
    height = -1 if (h is None or h < 0) else int(h)
    motion = 0
    mast = d.get("mast") or ""          # e.g. "scissor:2"
    if ":" in mast:
        try:
            motion = int(mast.split(":")[1])
        except ValueError:
            motion = 0
    flags = 0x04 if d.get("pinch") == "HOLD" else 0
    _tab5_write(f"LIFT {height} {motion} {flags}")


def _loop() -> None:
    import status as status_mod
    import stamplc

    prev = None
    last = 0.0
    prev_turn = None
    prev_pmode = None
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
            _push_lift()          # every pass — the head's height should not go stale
            # CH7 payload mode. The head shows it because the OPERATOR needs to know whether
            # the right stick is aiming them or strafing the robot, and their eyes are on the
            # machine rather than on the transmitter.
            pmode = (stamplc.reader.snapshot().get("rc") or {}).get("pmode")
            if pmode and pmode != prev_pmode:
                _tab5_write(f"PMODE {pmode}")
                prev_pmode = pmode
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
    # All Tab5 I/O — separate so an absent or slow head can never delay the PRISM.
    threading.Thread(target=_tab5_sender, name="tab5_tx", daemon=True).start()
    # Receive side: the head's lift buttons. Separate thread because it blocks on recv.
    threading.Thread(target=_tab5_listen, name="tab5_rx", daemon=True).start()
