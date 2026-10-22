# Who owns the head

**Current, 2026-10-22.** The head is the Galaxy tablet (the face) on the M5Dial neck
(`firmware/neck_dial`), which carries the pan and tilt servos. The neck decides who moves it; the
dashboard asks and shows what the neck reports. First decided 2026-10-06 for the Tab5 head, now
retired; what carried over is kept below, and the neck's **HOLD** and its **status badge** are new
(the user's design, 2026-10-22). Built in the neck firmware and the dashboard; **not yet flashed to the
robot** (that happens with the user there, by `firmware/neck_dial/README.md`'s recipe).

## 1. Precedence

Highest first. The control law (`neck/pantilt_core.cpp`) enforces it, not the senders:

    HOLD  >  touch  >  RC stick  >  a show's GAZE  >  the tracker

| owner | what it is | how it reaches the neck | kind |
|---|---|---|---|
| **HOLD** | a person at the knob, the dashboard, or software: the head stays where it is | the knob; `HOLD` over UDP 4242 | **latch** (§3) |
| **touch** | someone working at the tablet: a brief pause | `TOUCH HOLD`, renewed by the dashboard while touched, lapsing about 4 s after the last touch | lease |
| **RC** | the operator's stick | the StamPLC's `0x107` rate frames, UDP 4242, 20 Hz | lease (600 ms) |
| **GAZE** | a show, or the dashboard's tracking (`neck_follow.py`) | `GAZE <pan> <tilt> <lease>` | lease (≤ 1000 ms) |
| **tracker** | a tracker beside the head | `TRACK` (agreed, not built on the Dial) | lease |

**On a lease's expiry the head never jumps**: the next live source down takes it at the slew rate, or
it stays where it is (head-protocol.md §4).

## 2. HOLD — the neck's one job for a person

**What it is.** A held head **stays where it is**: tracking, shows, touch and the RC stick do not move
it. It is a **pause, never a motion command**. The one exception is software that needs the head
somewhere particular (a manipulation task aiming the head-top depth camera): `HOLD <pan> <tilt>`
slews there at the head's ordinary rate, within its limits, and holds.

**The knob, not the button.** Turned one way, deliberately (**three detents within a second**), it
holds the head where it is; turned the other way, it releases. A bump or a slow turn does nothing.
The button is hard to press, above all while the neck moves, and it already has a job: confirming a
tilt-limit change. While one is pending, the press belongs to that alone, and the screen says
"press to confirm tilt …".

**Anything releases it**: the knob, the dashboard (Head & face page, RELEASE), software (`HOLD OFF`).
No idle timeout (§3).

**A release is announced.** For 3 s after a release the head stays held while the neck's screen says
"releasing hold" and the dashboard shows a notice; only then may it move. (A face caption as well is
for Face & HRI to agree.)

**Always comes back released.** The hold lives in the neck's RAM: a reboot, an OTA or a power cycle
(an e-stop cuts the neck's power) always starts released.

**Reported outward.** `NECK` carries `owner=hold`, `hold=on|releasing|off` and `hold_by=knob|remote`;
the dashboard polls it (`neck_state.py`) for its notice and its controls. The Tab5's lesson stands: the
head must say who owns it, or a stale page and a busy tracker disagree about it.

## 3. Lease vs latch

- **Lease**: a transient input takes the head for a hold window and hands it back. Right for nudging,
  tracking and shows, and why "nudge the head while tracking carries on" needs nobody to change mode.
- **Latch**: held until explicitly released. **HOLD is the only latch.** An authority that expires is
  not authority, so a latch is released easily (any of the ways above) and never by a timeout, which
  would only be the original problem on a longer fuse. Nor does it need a reboot: the head cannot
  realistically hurt anyone, so over-gating it is paternalistic rather than careful.

## 4. The neck is the authority; the dashboard asks

The hold is shared state, so one thing must be its source of truth: **the neck**. The dashboard
requests (`POST /api/head/hold`) and shows what the neck reports. A dashboard that kept its own copy
could be made wrong by a reload, a second browser or a neck that rebooted.

## 5. The status badge

The Dial's round screen is a **status badge** and nothing else (the user rejected the knob menus and
pages of the 2026-10-19 proposal): a ring in the robot state's colour, the state's label, and who
drives the head. HOLD while held, "releasing hold" during the announcement. No animation. **Blank at
boot until told.**

The dashboard tells it with `BADGE <STATE> <driver> <rrggbb>` (head protocol v9) on a change and every
5 s, like the chest light. The **colour crosses the wire**, from the same map the PRISM and the chest
light use (`state_out._MAP`), so there is one map of state to colour and not a second copy in firmware
to disagree with it. The neck greys the ring when it has heard nothing for 20 s. What the neck knows
better than the dashboard, it shows itself: HOLD, RC, touch, a pending tilt change.

**The screen never stands between the board and the network.** The neck has no USB cable, so OTA is
the only way in: the screen starts **last**, after the UDP socket is listening; its start runs under
the task watchdog, so a hang resets the board; and a board whose last reset was a crash or a watchdog
comes up **without** the screen. A screen fault costs the screen, never the board. It draws only on a
change, so the 50 Hz control loop is late by one tick a few times a minute at most.

## 6. The authority boundary

**The head cannot realistically damage people or property**, unlike the base and the arms, so its
safety bar is lower. What stays unconditional: slew limits, hard bounds (pan 0–180, tilt within this
robot's commissioned band) and the narrow tilt fallback for an uncommissioned neck; a neck with no
network does not move on its own. A tilt-limit change needs a person at the button.

## 7. Keeping video off the control path

Commands are small and latency-sensitive on UDP 4242. Camera streams take their own path (the tablet
streams to the Thor over its socket; `tablet_relay.py`) and never share the control port.
