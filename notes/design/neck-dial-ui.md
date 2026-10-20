# The neck Dial's screen and knob — a proposal

**A design proposal. Nothing here is built, and this lane has not been confirmed as the owner of
`firmware/neck_dial`.** Written because the maintainers asked why the M5Dial's round screen and
rotary knob are unused.

The short answer: because the firmware deliberately does not start them. `src/main.cpp` says so —
pulling in the M5Dial library would start the display, touch and speaker, and **contend for Port A's
`Wire` with the 8Servo**, which is the only thing on that bus that must never be disturbed, because
it drives the pan and tilt servos and the chest light.

That reason is about the *library*, not about the hardware. The screen and the knob can both be
driven without it.

## (a) Driving them without M5Unified

Every pin below is read from the vendor's own source — M5GFX's `board_M5Dial` branch and the M5Dial
library's header — not from memory. A wrong pin on a board with **no USB cable** is expensive.

| | |
|---|---|
| panel | **GC9A01**, 240 × 240, SPI **3-wire**, `invert = true` |
| MOSI / SCLK / DC / CS / RST | **5 / 6 / 4 / 7 / 8** |
| backlight | **9**, PWM (M5GFX uses channel 7 at 44.1 kHz) |
| write clock | 80 MHz in M5GFX; 40 MHz is a safe start |
| encoder A / B | **41 / 40** |
| button | **42**, active low — *already used*, see below |
| touch | FT5x06, **on I2C** — this is the Port A contention. **Do not enable it.** |

**Use LovyanGFX with a hand-written config class, not M5GFX or M5Unified.** M5GFX is a LovyanGFX
fork whose whole job is to auto-detect the board and start everything on it; configuring LovyanGFX
explicitly is the supported way to drive one M5 panel and nothing else. Declare the bus, the panel
and the backlight exactly as above, declare **no touch**, and never call `M5.begin()`. Nothing then
touches I2C, and the 8Servo keeps Port A to itself.

The ESP32-S3 has two usable SPI hosts and the firmware uses neither today, so the panel can have one
outright.

**The encoder should use the PCNT peripheral**, not interrupts. The S3 has hardware pulse counters;
a quadrature pair on 41/40 is what they are for. It costs no ISR time and cannot miss a step while
the loop is busy driving servos.

### Three constraints the implementation has to respect

**1. The display must never be on the critical path to the network.** There is no USB cable on the
robot — OTA is the only way in. If a panel init hangs or faults before Wi-Fi and the UDP socket are
up, the board is unreachable and only a bench visit with a cable recovers it. So: **Wi-Fi, the UDP
socket and the control loop first; the display last**, and its init wrapped so a failure disables
the screen rather than the board. The firmware already learned this lesson once — an early build
crash-looped because `WiFi.mode()` was only called when credentials existed.

**2. Drawing must not starve the 50 Hz control loop.** The neck has no tasks of its own; it is an
Arduino `loop()`, so a slow draw delays the next servo tick. A full 240 × 240 frame is 115 KB — about
12 ms at 80 MHz, more at lower clocks, and that is per frame. **Redraw only on change**, the same
change-guard discipline the chest light and the Tab5's band already use: a mode that changes every
few minutes costs nothing, and nothing on this screen needs animation.

**3. `POWER_HOLD` is GPIO 46 on this board and the firmware never sets it.** M5Unified's table has
it; our firmware does not touch it. On the robot the Dial is externally powered, so this may be
irrelevant — but it should be checked before anyone concludes the screen is the reason for a reboot.

## (b) What the screen shows

**The robot already has an authority indicator, and the neck should show the same one rather than
invent a second.** The PRISM and the chest light are both driven from the face's `STATES` table, and
that table already carries exactly what was asked for: a mode, a label and a colour.

| state | label | colour |
|---|---|---|
| `INIT` | INIT | pale blue-white |
| `SAFE_IDLE` | SAFE IDLE | green |
| `MANUAL` | MANUAL | near-white |
| `AUTONOMOUS` | AUTONOMOUS | cyan |
| `CHARGING` | CHARGING | purple |
| `DEGRADED` | DEGRADED | amber — reserved, no theme may recolour it |
| `E_STOP` | E-STOP | red — reserved |

The suggested palette (green standby, white manual, blue auto, amber fault, red e-stop) is **already
this table**, to within a shade. Reusing it rather than approximating it means one vocabulary across
the chest, the PRISM, the face and the neck — and it inherits the rules that took work to settle:
reserved states cannot be themed, and charging is purple everywhere.

So: **a filled ring in the state's colour, the label across the middle, and the controller beneath
it.** On a 240 px round panel a short label reads from a couple of metres, which is what the neck is
for — it faces outward, at head height, where the chest plate is not visible from the side.

The controller line is the new information: who is driving — `RC`, `SHOW`, `TRACKER`, `TOUCH`,
`NOBODY`. The neck already knows its *own* owner (`NECK` reports `owner=`), but not the robot's, so
that part has to be told.

**What the screen should not do:** no animation, no spinner, nothing that moves. A moving indicator
on a robot reads as the machine running, which is the reasoning `armed.js` already records.

## (c) What the knob does

**There is a conflict to resolve first, and it is the most important thing in this note.**

**The Dial's button is already bound to the tilt-commissioning interlock.** A `TILT` sent over UDP
arms a pending band change and waits up to 15 s for a press of GPIO 42. If the knob's press also
drives a menu, then a press meant for the menu **confirms a band change** — defeating an interlock
the maintainers specifically asked for.

So the press needs stated arbitration: **while a `TILT` is pending, the press belongs to the
interlock and nothing else, and the screen should say so** ("press to confirm tilt 71–150"), which
also makes the interlock visible instead of invisible. That is a strict improvement on today, where
the operator must be told out of band what the press is for.

For the knob itself, the test is: **it may not start motion, and it must do something the neck can
honour alone with the network down.** That rules out volume (the face owns audio, and `VOLUME` is
already a head-protocol message to the *face*) and anything that commands the robot.

Two that pass, in order of preference:

1. **Screen brightness**, including fully off. Local, instant, harmless, and genuinely useful: a lit
   screen at head height is a nuisance in a dark room and during a show. The backlight is already a
   PWM pin.
2. **Page select** — status / network (SSID, IP, RSSI) / version (the build sha `PING` reports).
   Useful exactly when the robot is unreachable and nobody can read a `PING`, which is the one
   moment a screen on the neck earns its place.

A privacy or quiet toggle was suggested. **I would not put it here.** A mute that only the neck knows
about is a mute nobody else can see or honour — the microphone belongs to the tablet, and a consent
control that does not reach it is worse than none, because people would trust it. If a privacy
control is wanted, it belongs where the microphone is, with the camera indicator that already exists.

## (d) The new head message

The neck must be told two things it cannot know: the robot's state, and who is driving.

It already receives the state *implicitly* — `CHEST <us>` carries the state's colour as a pulse. That
is not enough for a screen: a pulse has no label, and deriving "AUTONOMOUS" from 1594 µs would mean a
second mapping that could disagree with the first.

**Proposed, for the Tablet lane to accept, amend or replace — they own the protocol:**

```
BADGE <state> <controller>     ->  OK badge <state> <controller>
BADGE OFF                      ->  OK badge off          (blank the screen)
bad args                       ->  ERR badge <state> <controller> | OFF
```

- `<state>` is one of the agreed `STATES` names, so the neck looks up the same table everyone else
  uses and no colour crosses the wire.
- `<controller>` is a short token: `rc`, `show`, `tracker`, `touch`, `none`.
- **No lease**, holding the last value as the PRISM and the chest light do, with the same periodic
  refresh so a rebooted neck recovers without anyone noticing.
- At boot: **blank**, not `INIT`. A neck that lights up claiming a state before it has been told one
  is claiming something it does not know — the same reasoning as the chest light's dark boot.

It would also be reasonable to carry this on the existing `CHEST` cadence rather than add a message
at all; that is the Tablet lane's call and the cheaper option if they prefer it.

## What this costs, honestly

A screen and a knob are not free: a LovyanGFX dependency, a draw path that must stay off the control
loop's back, a second meaning for the button, and a new message to agree. The benefit is a status
indicator at head height that works when the robot is unreachable, and an interlock that finally says
what it wants.

If the maintainers would rather the Dial stayed a headless controller, that is a perfectly good
answer and this note is the record of why it was left that way.
