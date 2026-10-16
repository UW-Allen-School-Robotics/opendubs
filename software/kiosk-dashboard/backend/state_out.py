"""Push the authoritative robot FSM state (robot_state) to the operator-facing
state indicators, so the PRISM strip and the Tab5 face always agree with the
dashboard:

  - goBILDA PRISM: a solid color via the StamPLC's I2C LED_COLOR command (ch0).
  - Tab5 face:     a "STATE <NAME>" line, over UDP to the Tab5 and over its USB CDC serial.
  - chest light:   the PRISM's colour as a servo pulse, "CHEST <us>", to the NECK (a separate neck only).

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
    # Not an FSM state: how a charging robot is SHOWN (present() below). Purple, breathing on the PRISM.
    "CHARGING":           ("CHARGING",   (109, 48, 224),  70),   # the face's CHARGING purple (0x6d30e0)
}

# ---- charging: presentation only ---------------------------------------------------------------
# robot_state folds a charging robot into SAFE_IDLE (a drive inhibit, not a state of its own), so
# the face and the PRISM never showed it. It is SHOWN as CHARGING here, and only where the robot
# would otherwise show SAFE_IDLE: E_STOP, DEGRADED and INIT always win. The FSM, its reasons and the
# drive inhibit are untouched. Charging means current flowing INTO the pack (status._charger): a full
# pack still on the charger reads as not charging.
BREATH_PERIOD_S = 4.0
BREATH_STEPS = 8         # brightness steps per breath: a write every 0.5 s at most (2 Hz), well under
                         # the loop's own pace, so the StamPLC link and a state change are never held up
BREATH_FLOOR = 0.3       # the dimmest step, as a fraction of the state's brightness: never dark


def present(rsd) -> str | None:
    """The state to SHOW for robot_state's rollup `rsd`: its own, or CHARGING for a charging SAFE_IDLE."""
    if not rsd:
        return None
    rs = rsd.get("state")
    if rs == "SAFE_IDLE" and (rsd.get("inputs") or {}).get("charging"):
        return "CHARGING"
    return rs


def breath(bright: int, now: float) -> int:
    """`bright`, stepped along a slow breath (BREATH_STEPS per BREATH_PERIOD_S)."""
    import math
    step = int((now % BREATH_PERIOD_S) / (BREATH_PERIOD_S / BREATH_STEPS))
    level = 0.5 - 0.5 * math.cos(2 * math.pi * step / BREATH_STEPS)
    return int(round(bright * (BREATH_FLOOR + (1 - BREATH_FLOOR) * level)))

_TAB5_GLOB = os.environ.get(
    "OPENDUBS_TAB5_DEV_GLOB",
    "/dev/serial/by-id/*30:ED:A0:E1:5D:B6*if00",  # Tab5 ESP32-P4
)
_REFRESH_S = 3.0   # re-push at least this often (Tab5 link timeout is 5 s)

# The head over the network, by role (head.py): the FACE takes the indicator lines, the NECK takes
# motion. Resolved once per push attempt so a DHCP change or a reboot is picked up without
# restarting the dashboard.
_TAB5_UDP_PORT = int(os.environ.get("OPENDUBS_TAB5_UDP_PORT", "4242"))
_tab5_sock = None
_PEER_REINTRO_S = 20.0        # re-assert the pairing this often; both ends hold it in RAM
_peers_last_intro = 0.0
# A failed lookup of an absent head takes 5 s. Remember the failure rather than paying it
# on every write, which is what turned the state loop from 0.3 s into 10-15 s.
_TAB5_RESOLVE_RETRY_S = 30.0
_NECK_RETRY_S = 10.0
_tab5_q: "queue.Queue[str]" = queue.Queue(maxsize=64)


class _Peer:
    """One half of the head, found by name."""

    def __init__(self, host: str, retry_s: float = None):
        self.host = host
        self.retry_s = _TAB5_RESOLVE_RETRY_S if retry_s is None else retry_s
        self.addr = None
        self.addr_ts = 0.0
        self.fail_ts = -_TAB5_RESOLVE_RETRY_S

    def resolve(self):
        """Where it is right now, or None: a cached mDNS answer (30 s), else a new lookup — but
        never more than once per _TAB5_RESOLVE_RETRY_S after a failure. Called only from the
        head's own thread (and the on-demand PING probe), because a lookup can block for 5 s.

        (The Arduino head's packets to UDP 5006 used to teach this its address. The ESP-IDF head
        sends the Thor nothing, so a name is all there is: head-protocol.md §8.)"""
        import socket
        now = time.monotonic()
        if self.addr is not None and now - self.addr_ts < 30:
            return self.addr
        if now - self.fail_ts < self.retry_s:
            return None
        try:
            self.addr = socket.gethostbyname(self.host)
            self.addr_ts = time.monotonic()
            return self.addr
        except Exception:
            self.addr = None
            self.fail_ts = time.monotonic()
            return None


def _make_peers(face_host: str, neck_host: str):
    """With the Tab5 fitted both halves are one box, so they share ONE peer — the same lookups,
    exactly the behaviour from before the split."""
    face = _Peer(face_host)
    if neck_host is None:                   # no neck fitted (OPENDUBS_HEAD_NECK_HOST=none)
        return face, None
    # A separate neck is looked up on its own thread (_neck_sender), where a slow lookup costs the
    # face nothing, so it is retried sooner: a neck that is switched on is found within ~10 s.
    return face, (face if neck_host == face_host else _Peer(neck_host, retry_s=_NECK_RETRY_S))


import head as _head
_face, _neck = _make_peers(_head.face_host(), _head.neck_host())


def face_addr():
    """The face's address now, or None — for whoever must ask it something (PING)."""
    return _face.resolve()


# The old name, kept because backend/choreo_proxy.py (the Choreography lane's) still calls it for
# its PING. It always meant "where to PING", which is the face.
_tab5_resolve = face_addr


def _tab5_udp(line: str) -> None:
    """Best-effort line to the head's FACE over UDP. Silent on failure:
    the face is an indicator, not a control path, and a missing head must not disturb the
    dashboard."""
    global _tab5_sock
    import socket
    addr = _face.resolve()
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
# means a Tab5 reflash needs the dash stopped first, like the StamPLC.)
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


def _neck_sender() -> None:
    """The neck's introduction to the StamPLC (TAB5IP), on its OWN thread. Finding the neck by name
    can block for 5 s when it is absent, and while that shared the face's thread the face got no
    STATE for over 5 s every 30 s and went grey mid-show (2026-10-13). The face never waits on it now."""
    if _neck is None:
        import sys
        sys.stderr.write("NECK: none fitted (OPENDUBS_HEAD_NECK_HOST=none): the StamPLC is told of no neck\n")
        return                              # nothing to introduce, ever
    while True:
        try:
            _introduce_peers()
        except Exception:
            pass
        time.sleep(1.0)


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


def _prism_target(rs: str, now: float = None):
    """The colour the PRISM should show in state `rs`: the state's own, or a running show's light
    layered over it (show_light.py — a reserved state always wins). ((r, g, b), brightness) or None.
    CHARGING breathes: its brightness steps slowly (breath()); a show's light still wins over it."""
    ent = _MAP.get(rs)
    if not ent:
        return None
    _, rgb, bright = ent
    if rs == "CHARGING":
        bright = breath(bright, time.monotonic() if now is None else now)
    try:
        import show_light
        return show_light.winning(rs, (rgb, bright))
    except Exception:
        return rgb, bright


def _push_prism_colour(colour) -> None:
    (r, g, b), bright = colour
    try:
        import stamplc
        stamplc.reader.send(f"LED_COLOR {r} {g} {b} {bright}")   # PRISM via StamPLC I2C (ch0)
    except Exception:
        pass


def _push_prism(rs: str) -> None:
    c = _prism_target(rs)
    if c is not None:
        _push_prism_colour(c)


_tab5_logged_state = None
_face_state = None                     # the STATE line's name last sent to the face (face_door.py)


def face_state():
    """The state the face should be showing now, e.g. "SAFE_IDLE", or None before the first push.
    face_door.py sends it to a face page as it connects, so a reconnect is never a grey gap."""
    return _face_state


def _push_tab5(rs: str) -> None:
    global _tab5_logged_state, _face_state
    ent = _MAP.get(rs)
    if ent:
        _face_state = ent[0]
        if rs != _tab5_logged_state:
            import sys
            sys.stderr.write(f"TAB5: Pushing state {rs} -> {ent[0]}\n")
            _tab5_logged_state = rs
        _tab5_write(f"STATE {ent[0]}")


# ---- the pack's charge, for the face's gauge (head protocol: BATT <soc|-> <0|1>) --------------------
_BATT_REFRESH_S = 30.0
_batt_line = None                       # the BATT line last sent; face_door.py sends it as a page connects
_batt_at = 0.0
_batt_bad_logged = False


def batt_line(pack: dict, charger: dict):
    """`BATT <soc 0-100|-> <charging 0|1>` from status._pack() and status._charger()."""
    soc = pack.get("soc") if pack.get("present") else None
    try:
        soc = None if soc is None else int(round(float(soc)))
    except (TypeError, ValueError):
        soc = None
    if soc is not None and not 0 <= soc <= 100:
        # A reading out of range is a bug upstream (the BMS or its parser): say "unknown", and say so,
        # rather than clamp it into a plausible number the face would show.
        global _batt_bad_logged
        if not _batt_bad_logged:
            import sys
            sys.stderr.write(f"BATT: the BMS reports state of charge {soc} %: sent as unknown\n")
            _batt_bad_logged = True
        soc = None
    return f"BATT {'-' if soc is None else soc} {1 if charger.get('charging') else 0}"


def face_batt():
    """The BATT line the face should have now, or None before the first."""
    return _batt_line


def _push_batt(status_mod, now: float) -> None:
    """On a change (whole percent, or charging), and every _BATT_REFRESH_S."""
    global _batt_line, _batt_at
    line = batt_line(status_mod._pack() or {}, status_mod._charger() or {})
    if line != _batt_line or now - _batt_at >= _BATT_REFRESH_S:
        _batt_line, _batt_at = line, now
        _tab5_write(line)


# ---- the chest light (head protocol v7: CHEST <us> | CHEST OFF, a NECK message) ----------------------
# A goBILDA RGB Indicator Light behind the chest plate, on the neck's 8Servo. A servo pulse
# (500–2500 µs) sets its HUE; nothing sets its brightness. It shows what the PRISM shows: the same
# winning colour (_prism_target: the state's, charging purple, or a show's lease over it), as the
# pulse whose hue is nearest on the light's chart. It cannot dim, so charging is SOLID purple there.
#
# The chart is goBILDA's published one (Product Insight #4), not yet checked on the fitted light:
# below 1100 off; 1100 red, 1200 orange, 1300 yellow, 1400 sage, 1500 green, 1600 azure, 1700 blue,
# 1800 indigo, 1900 violet, a smooth gradient between; above 1900 white. The hues of the named points
# are a reading of those names, which is where a correction from the fitted light would go.
CHEST_HUES = ((1100, 0), (1200, 30), (1300, 60), (1400, 90), (1500, 120),
              (1600, 210), (1700, 240), (1800, 265), (1900, 285))      # (pulse µs, hue °)
# Coloured pulses stay inside this: red sits one step above OFF and violet one below white, and an
# E_STOP that rounds down to a dark chest would read as "powered down", not "emergency stop".
CHEST_LIT_US = (1130, 1880)
CHEST_WHITE_US = 2500
CHEST_WHITE_SAT = 0.25        # a colour paler than this has no hue to show: white (INIT, MANUAL)
CHEST_OFF_BRIGHT = 5          # a colour this dim (0–100), or black, is CHEST OFF: a show's blackout
_CHEST_REFRESH_S = 5.0        # the neck holds the last value in RAM: a rebooted neck has it back within this
_chest_line = None
_chest_at = 0.0
_chest_addr = None
_neck_q: "queue.Queue[str]" = queue.Queue(maxsize=8)
_neck_sock = None


def chest_line(colour):
    """`CHEST <us>` for a PRISM colour ((r, g, b), brightness), `CHEST OFF`, or None for no colour."""
    import colorsys
    if colour is None:
        return None
    (r, g, b), bright = colour
    if bright < CHEST_OFF_BRIGHT or max(r, g, b) == 0:
        return "CHEST OFF"
    h, sat, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if sat < CHEST_WHITE_SAT:
        return f"CHEST {CHEST_WHITE_US}"
    hue = h * 360
    top = CHEST_HUES[-1][1]
    if hue > top:                           # magenta: the light has none, so the nearer end
        hue = 0 if hue >= (top + 360) / 2 else top
    us = CHEST_HUES[-1][0]
    for (u0, h0), (u1, h1) in zip(CHEST_HUES, CHEST_HUES[1:]):
        if hue <= h1:
            us = u0 + (u1 - u0) * (hue - h0) / (h1 - h0)
            break
    lo, hi = CHEST_LIT_US
    return f"CHEST {int(round(min(max(us, lo), hi)))}"


def _neck_write(line: str) -> None:
    """Queue a line for the NECK; never blocks. A full queue drops its oldest line, which is stale."""
    try:
        _neck_q.put_nowait(line)
    except queue.Full:
        try:
            _neck_q.get_nowait()
            _neck_q.put_nowait(line)
        except (queue.Empty, queue.Full):
            pass


def _neck_tx() -> None:
    """The only thread that sends the neck a line, so an absent neck (a 5 s lookup) holds up nothing
    else. Best effort and silent, like the face's: the chest light is an indicator."""
    global _neck_sock
    import socket
    while True:
        try:
            line = _neck_q.get(timeout=1.0)
            addr = _neck.resolve()
            if addr is None:
                continue
            if _neck_sock is None:
                _neck_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            _neck_sock.sendto((line + "\n").encode(), (addr, _TAB5_UDP_PORT))
        except queue.Empty:
            pass
        except Exception:
            time.sleep(0.2)


def _push_chest(colour, now: float) -> None:
    """On a change of the mapped pulse, when the neck (re)appears, and every _CHEST_REFRESH_S.
    Nothing at all with no separate neck: none fitted, or the Tab5, which is both halves and has no
    chest light."""
    global _chest_line, _chest_at, _chest_addr
    if _neck is None or _neck is _face:
        return
    line = chest_line(colour)
    if line is None:
        return
    addr = _neck.addr                       # the cached answer only: this loop never waits on a lookup
    if line != _chest_line or addr != _chest_addr or now - _chest_at >= _CHEST_REFRESH_S:
        _chest_line, _chest_at, _chest_addr = line, now, addr
        _neck_write(line)


# ---- the state in plain words (head protocol: CAPTION <text> | CAPTION OFF; state_words.py) ---------
_caption_line = None                    # the CAPTION line last sent; face_door.py sends it as a page connects


def face_caption():
    """The CAPTION line the face should have now, or None before the first."""
    return _caption_line


def caption_line(rs: str, rsd: dict, status_mod) -> str:
    """The CAPTION line for the shown state `rs`, under the Head & face setting `state_words`."""
    import head_settings
    import state_words
    try:
        mode = head_settings.load().get("state_words", state_words.DEFAULT_MODE)
    except Exception:
        mode = state_words.DEFAULT_MODE
    try:
        pack = status_mod._pack() or {}
        soc = pack.get("soc") if pack.get("present") else None
    except Exception:
        soc = None
    return state_words.line(state_words.caption(rs, rsd, soc, mode))


def _push_caption(rs: str, rsd: dict, status_mod, with_state: bool) -> None:
    """With every STATE line (the face drops a caption not renewed), and at once when the words change:
    a new reason, a whole percent of charge, or the setting (so it applies live)."""
    global _caption_line
    line = caption_line(rs, rsd, status_mod)
    if line != _caption_line or with_state:
        _caption_line = line
        _tab5_write(line)


_neck_seen = None                       # what the log last said: an address, False (absent), or None


def _report_neck(addr) -> None:
    """Say once that the neck is absent, and once where it is when it appears — not every retry."""
    global _neck_seen
    import sys
    if addr is None and _neck_seen is not False:
        sys.stderr.write(f"NECK: {_neck.host} not found; pan/tilt from the RC waits for it\n")
        _neck_seen = False
    elif addr is not None and addr != _neck_seen:
        sys.stderr.write(f"NECK: {_neck.host} at {addr}\n")
        _neck_seen = addr


def _introduce_peers() -> None:
    """Tell the StamPLC where the head's NECK is (`TAB5IP`, the StamPLC's own word for "where RC
    pan/tilt goes"; kept as is so the firmware needs no change).

    The StamPLC sends the RC stick's pan/tilt rate (0x107) straight to the neck — direct, so it
    keeps answering the sticks even if this dashboard stops. The Thor is the one machine that
    can find both, so it does the introduction; after that the data path does not involve it.

    One way only. The head finds the StamPLC by name itself (`opendubs-stamplc.local`) and
    takes 0x107 from any sender, so the old `PLCIP` line back to it vouched for nothing and is
    retired (head-protocol.md §8).

    RE-ASSERTED PERIODICALLY, not once. The StamPLC holds the address in RAM, so it forgets it
    on every reboot — and a firmware flash reboots it every time. A one-shot handshake left it
    with nowhere to send and no way to say so: the sticks simply did nothing, with every other
    indicator healthy. Cheap to repeat, and it heals itself instead of needing someone to
    remember.
    """
    global _peers_last_intro
    if _neck is None:                       # no neck fitted: never hand the StamPLC an address
        return
    now = time.monotonic()
    if now - _peers_last_intro < _PEER_REINTRO_S:
        return
    _peers_last_intro = now
    neck = _neck.resolve()
    _report_neck(neck)
    if neck is None:
        # Retry in ~5 s rather than on every pass: each attempt used to cost a 5 s lookup.
        _peers_last_intro = now - _PEER_REINTRO_S + 5.0
        return
    try:
        import stamplc
        stamplc.reader.send(f"TAB5IP {neck}")  # PLC <- "the neck lives here"
    except Exception:
        _peers_last_intro = now - _PEER_REINTRO_S + 5.0


def _loop() -> None:
    import status as status_mod
    import stamplc

    prev = None
    prev_led = None                  # the colour last written to the PRISM: written again only on a change
    last = 0.0
    prev_conn = False
    while True:
        try:
            # StamPLC (re)connect edge — e.g. after a firmware flash or replug the MCU reboots
            # with a blank PRISM. Force a base-color re-push so the strip isn't dark until the
            # next state flip.
            conn = bool(stamplc.reader.snapshot().get("connected"))
            if conn and not prev_conn:
                prev = prev_led = None       # ... and the WINNING colour, a show's if its lease is fresh
            prev_conn = conn
            rsd = status_mod._robot_state() or {}
            rs = present(rsd)
            now = time.monotonic()
            if rs:
                # PRISM: the winning colour (the state's, or a show's layered over it), written ONLY
                # when it changes — at most once per pass of this 0.3 s loop. The strip shares the
                # StamPLC's I2C with the drive ESCs, so nothing writes it faster than this.
                want = _prism_target(rs)
                if want is not None and want != prev_led:
                    _push_prism_colour(want)
                    prev_led = want
                _push_chest(want, now)       # the chest light shows the same colour, on the neck
                pushed = False
                if rs != prev:
                    _push_tab5(rs)
                    prev, last, pushed = rs, now, True
                elif (now - last) >= _REFRESH_S:
                    _push_tab5(rs)           # refresh keeps the Tab5 link-alive; held port → no reboot
                    last, pushed = now, True
                _push_caption(rs, rsd, status_mod, pushed)
            _push_batt(status_mod, now)
            # TURN, LIFT and PMODE (turn chevrons, lift height, CH7 mode) went to the Arduino face
            # from here. The ESP-IDF head answers them ERR unknown, so they are retired
            # (head-protocol.md §8); a face message is specified there first if wanted again.
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
    # The neck's introduction: its own thread, so a neck that cannot be found never delays the face.
    threading.Thread(target=_neck_sender, name="neck_intro", daemon=True).start()
    if _neck is not None and _neck is not _face:   # the chest light's line: a separate neck only
        threading.Thread(target=_neck_tx, name="neck_tx", daemon=True).start()
    # No receive side. The Arduino head sent its lift buttons here on UDP 5006; the ESP-IDF head
    # sends `MAST CMD` straight to the StamPLC (head-protocol.md §7), so nothing listens on 5006.
