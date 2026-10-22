# The neck — M5Dial (ESP32-S3)

Pan and tilt for the robot's head. The Dial drives two servos through an 8Servo unit at `0x25` on
Port A, takes the head protocol over UDP, and holds the per-robot tilt limits in its own NVS.

**It has no USB cable on the robot.** It is powered from the 24 V bus, and the cable was pulled
once OTA was proved. So everything below is over the network, and that is not a preference: there
is no serial console to fall back on while it is installed.

| | |
|---|---|
| board | M5Dial, ESP32-S3 |
| on the robot | `opendubs-neck.local` · `10.42.2.12` · **UDP 4242** |
| servos | 8Servo at `0x25`, channel 0 tilt, channel 1 pan |
| per-robot values | NVS namespace `opendubs`: `tiltMin`, `tiltMax` · namespace `neck`: `ssid`, `pass` |

## Talking to it

Any UDP line to port 4242 gets one line back. From the Thor:

```sh
python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
s.sendto(b"NECK", ("10.42.2.12", 4242))
print(s.recv(512).decode().strip())
PY
```

`PING` · `NECK` · `GAZE <pan> <tilt> [lease_ms]` / `GAZE OFF` · `TILT <min> <max>` ·
`CHEST <us>` / `CHEST OFF` · `HOLD` / `HOLD HERE` / `HOLD <pan> <tilt>` / `HOLD OFF` ·
`BADGE <STATE> <driver> <rrggbb>` / `BADGE OFF` · `TOUCH HOLD [lease_ms]` / `TOUCH RATE …` / `TOUCH OFF` ·
`OTA <url>` · `REBOOT` · `TOF` (refused: not fitted).
Anything else answers `ERR unknown`.

### Which way is which

Measured on the robot with somebody watching the head, because nothing in the firmware records it
and guessing is the easiest way to drive into a stop:

| | increasing degrees | decreasing degrees |
|---|---|---|
| **pan** | the robot's **left** | the robot's **right** |
| **tilt** | **up** | **down** |

Centre is 90 on both. Pan was measured twice, on separate days and from opposite directions —
`GAZE 130` read as the robot's left, `GAZE 70` as its right — which is worth knowing because the
pan axis has a history of reporting motion it was not performing (below).

**`NECK` and `PING` report the COMMANDED angle. There is no encoder.** A servo that is unplugged,
stalled or wired to the wrong channel reports exactly the same numbers as one that is working —
this cost an afternoon on 2026-10-15, when the pan axis was not driving at all and every log said
it was turning. If a number moves and the head does not, believe the head.

## Updating it — OTA from the Thor

The board **pulls** the image over HTTP from a URL you give it; there is no push listener. Build
on any machine, serve from the Thor, send one line.

```sh
# 1. build (any machine with the repo)
firmware/neck_dial/build.sh image

# 2. put the APP image on the Thor, outside the repo
scp firmware/neck_dial/.pio/build/neck_dial/firmware.bin opendubs2:/tmp/neck.bin

# 3. on the Thor: SIGN it for the neck (writes /tmp/neck.bin.sig), serve both on a free port,
#    send OTA, stop the server. The neck fetches neck.bin.sig beside neck.bin.
ssh opendubs2
sudo /opt/opendubs/stack/firmware-signing/sign-firmware.sh neck /tmp/neck.bin
cd /tmp && python3 -m http.server 8781 --bind 10.42.2.10 &
python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(120)
s.sendto(b"OTA http://10.42.2.10:8781/neck.bin", ("10.42.2.12", 4242))
print(s.recv(512).decode().strip())      # OK ota <bytes> bytes, rebooting key=<id>
PY
kill %1
```

- **`firmware.bin`, the app image — not `firmware.factory.bin`.** The OTA writes an app partition.
- **The reply arrives after the download**, which takes some seconds; the restart is deferred by
  400 ms so a success cannot look like a dead board. Allow a generous timeout.
- **Check a free port** before serving. Other sessions run servers on this machine.
- **The head SNAPS to centre on boot**, not slewed: the firmware comes up believing it is at 90/90
  and writes that pulse at once, so a head left at pan 61 jumps ~29° the moment it reboots. Tell
  whoever is standing there before you send the OTA.
- Verify with `PING` — the reply carries the build's commit sha.

### Signed updates

The neck installs an image only if its signature, made on the Thor **for board `neck`**, verifies
against a key compiled into it (`lib/ota_verify`, linked to `firmware/common/ota_verify`; the design is
`notes/design/firmware-signing.md`). Otherwise it answers `ERR ota rejected: <why>` (or
`ERR ota no signature`), and **the firmware it runs stays the one that boots**. `PING` says how it
stands: `otasig=enforce|report otaverify=ok|BROKEN otakeys=<id>[,<id>]`.

Two cases install anyway and say so, `OK ota … WARN would reject: <why>`, because a check that
strands a board with no cable costs opening the head on a desk:
- a **report-only** build (`build.sh image-report-only`), which is the bootstrap image only;
- a build whose own **self-test fails** on the board (`otaverify=BROKEN`).

An enforcing build with no trusted key does not compile.

**The bootstrap, once per neck, with someone at the robot.** Never put the enforcing image on a neck
whose report-only image has not passed steps 2 and 3:

1. Install the **report-only** image (`build.sh image-report-only`) by today's path. `PING` should then
   say `otasig=report otaverify=ok otakeys=<the robot's key id>`.
2. A **signed** update to that same image: the reply should end `key=<id>`.
3. The same update with a **corrupted** `.sig` (flip one byte): `WARN would reject: …`, and it still
   installs. That is harmless, because it is the same firmware.
4. Only then the **enforcing** image (`build.sh image`), signed. `PING`: `otasig=enforce`.
5. On it, a corrupted `.sig` is **rejected**, and `PING`'s `fw=` is unchanged.

A virgin or bricked board needs USB and `tools/flash.py` (app-only at `0x10000`; a merged image at
`0x0` erases the NVS and takes the Wi-Fi credentials with it). Flashing over USB is CLAUDE.md's
rote M5Stack procedure; do not improvise around it.

## The knob and the screen: HOLD and the status badge

The Dial has one job for a person: **HOLD** (`neck/hold.h`; the design is `notes/design/head-ownership.md`).

- **Turn the knob one way**, three detents within about a second: the head is **held** where it is.
  Tracking, shows, touch and the RC stick no longer move it. **Turn it the other way** the same: it is
  released, but only after **3 s** in which the screen says "releasing hold" and the dashboard shows a
  notice. A bump or a slow turn does nothing.
- The dashboard (Head & face: HOLD HERE / RELEASE) and software (`HOLD …` above) set and release the
  same hold. `NECK` reports it: `owner=hold hold=on|releasing|off hold_by=knob|remote`.
- **A reboot always comes back released**: the hold is in RAM only.
- **The button is unchanged**: it only confirms a pending tilt change, and the screen says so.

The **screen** is a status badge: a ring in the robot state's colour, the state's label, and who drives
the head (from the dashboard's `BADGE`, every 5 s), or what the neck knows itself: HOLD, "releasing
hold", RC, TOUCH, "press to confirm tilt …". Blank until the dashboard first sends a badge; the ring
goes grey after 20 s without one.

**The screen can never cost the board** (`src/badge_screen.h`): it starts last, after the UDP socket
is listening; its start runs under the task watchdog, so a hang resets the board; and after a crash or
watchdog reset the board comes up **without** the screen. The serial report and `screen=` in the
10 s report line say which. It is LovyanGFX configured by hand, with no touch controller, so nothing
touches Port A's I2C.

**To check at the Dial, the first time** (by eye, with the user there):

1. **The knob's direction.** Clockwise should hold. If the screen says RELEASE (or nothing while held),
   flip `kKnobUpHolds` in `neck/hold.h`.
2. **A detent is four counts.** If three detents do nothing and six do, set `kKnobCountsPerDetent` to 2.
3. **The screen's colours.** If the ring's red reads as blue, the panel wants `rgb_order = true` in `src/badge_screen.cpp`.

## The chest light

A goBILDA RGB Indicator Light (`3118-0808-0002`) behind the chest plate, on **8Servo channel 2**.
It takes an ordinary servo pulse and **the pulse width is the colour**, published by goBILDA
(Product Insight #4):

| µs | 500 | 1100 | 1200 | 1300 | 1400 | 1500 | 1600 | 1700 | 1800 | 1900 | 2500 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | off | red | orange | yellow | sage | green | azure | blue | indigo | violet | white |

Smooth gradient between. **Below 1100 is off; above 1900 is solid white.** There is no brightness
channel — hue only — so nothing on this part can dim, fade or breathe.

This is goBILDA's published table, and it is the map the robot uses.

`CHEST OFF` sends **500 µs**, a defined off, rather than stopping the pulses: silence leaves the
light in a state nobody has characterised, and an off that might not be off is not off.

**The colour is not decided here.** The Thor maps the robot's winning state colour — the same one
the PRISM shows — to a pulse and sends the pulse, so there is one map, in the backend, next to the
PRISM table, instead of two that can disagree about what red is.

Two margins the backend should keep, because of where the thresholds sit:

- **Red is 1100 and below 1100 is off**, so the most safety-critical colour is one step above
  darkness. Any rounding down turns a fault into an unlit plate, which reads as "powered down".
  Map it to about **1130**.
- **Violet is 1900 and above 1900 is white**, so charging's purple should be about **1880**.

The light is on the neck, which is on the e-stop branch: **it goes dark on a real e-stop**, by
design, the same as the head. Red therefore mostly indicates a software-latched fault.

**The cable** is goBILDA's included TJC8 ↔ JST PH, 300 mm: JST PH at the light, which exits the
back of the plate through a ø14 hole (the side TJC8 ports are boxed in by the pocket), and TJC8 at
the 8Servo's channel 2 header.

> **BEFORE IT IS PLUGGED IN — two checks, both on the bench, both cheap:**
>
> **1. The rail voltage.** Put a meter on the 8Servo's servo header. M5's spec gives the rail as
> `DC 5V@3A` across all eight channels, and the light wants **5–15 V** — inside the range but at
> the very bottom, so it may be dim and any droop puts it out of spec. The 3 A is shared with both
> pan/tilt servos. Nobody has measured it on this robot.
>
> **2. The pin order.** Confirm signal, V+ and ground land where the light expects them, rather
> than assuming the two connectors agree because the cable came in the box. A servo header carries
> power, so a reversed run does not fail quietly — and the cable is an adapter between two
> different connector families, which is exactly where an assumed convention goes wrong.

## Commissioning the tilt range

The tilt limits are a property of **this machine** and live in its NVS, never in the repo. With
none set the firmware uses a deliberately narrow fallback (80..100) — being wrong in the narrow
direction costs range, being wrong in the wide direction drives the head into the frame.

`NECK` reports `tilt_band=<min>,<max>` and `commissioned=0|1`, so an uncommissioned robot is
visible from the wire.

**The method: the band is the fence.** `GAZE` is clamped to the current band, so the head cannot be
commanded past it. Widen it ONE NOTCH AT A TIME and drive to each new edge, and at every instant a
typo or a stale packet cannot reach beyond the notch just opened.

1. Somebody at the head, hands free, able to call stop. **RC transmitter off** — RC outranks a show
   and the Dial takes rate frames at CAN id `0x107`. **Ask the Tablet lane to pause `neck_follow`**
   and get confirmation, rather than assuming the robot is idle.
2. **Establish the direction first**, with one small step inside the existing band. Nothing in the
   firmware tells you which way the degrees run, and assuming is the easiest way into a stop.
   (On this robot, measured: **increasing degrees is up**.)
3. Step outward in small notches — 3° near an expected stop, larger while far from one. Each notch:
   `TILT <new> <max>`, then `GAZE 90 <new>`, then look.
4. Stop **well short** of any hard stop, on the first sign of contact, strain or sound.
5. Write the final pair with one `TILT`, and confirm `commissioned=1` and the band in `NECK`.

**A `TILT` sent over the network does not take effect until somebody presses the Dial's button.**
The reply is `OK tilt pending <min> <max>, press the button within 15s`; `NECK` then carries
`tilt_pending=<min>,<max> in=<n>s` until it is confirmed or the window closes. A button already
held does not count — it has to be released and pressed, or anything resting on the Dial would
satisfy the interlock. A second `TILT` replaces the first, so during a creep the press always
confirms the notch you just sent. **A serial `TILT` is not gated**: a cable already proves
physical access, and asking for the button too would ask the same person the same question twice.

A 1.5° deadband means the head parks about a degree inside whatever you command, so the limits
carry that much margin on their own.

**Known rough edge:** `commissioned` goes true on the *first* `TILT` write, which during a
measurement is a provisional number. A procedure abandoned halfway leaves the robot claiming to be
commissioned on a throwaway band.

## Where this stands — 2026-10-15

Written here rather than in a workplan because this is where anyone touching the neck will look.

**On the robot:** main `faab662` (`fw=faab66262b87`), OTA'd from the Thor. `tilt_band=71,150`,
`commissioned=1`, held in the Dial's own NVS and deliberately not in the repo.

**Commissioned today** with the maintainers at the head: increasing degrees is **up**; down verified
free to 72.2 with no contact; the ceiling set to 150 **by choice**, not at a stop — the mechanism
has more travel. So neither end is a hard stop and no margin is written against one.

**The button interlock is UNTESTED ON HARDWARE.** The logic is covered on the host; the pin
(`GPIO 42`, from M5Unified's board table) has never had a button pressed against it. Offered as a
15-second check and declined — the maintainers are satisfied with the bounds and nothing needs
re-commissioning soon. If the pin is wrong the symptom is that a UDP `TILT` always expires; the
band on the robot is unaffected and the serial path still works with a cable.

**The pan servo has failed once and may be intermittent.** On the morning of the 15th it was not
driving at all while every log said it was turning — `NECK` reports the command and there is no
encoder. The maintainers found and fixed a hardware fault; later the same day the Tablet lane saw
the same signature again (a confident face holding still in the image while pan was commanded
~30°). **Unresolved.** If tracking looks wrong, rule this out first: command a large pan and have
somebody watch the head, rather than trusting any number.

**Tracker signs are measured, not guessed:** `PAN_SIGN = +1` (correct as it stands) and
`TILT_SIGN = -1`. Measured open-loop with the follow ranges clamped to 0 so the head could not move
while targets were read. An earlier suspicion that pan was inverted was **wrong** — pan saturated
because the servo was dead, and a dead axis saturates even with a correct sign, because the camera
rides the head and the error never closes.

**Known rough edges:** `commissioned` goes true on the first `TILT` write, so a procedure abandoned
halfway leaves the robot claiming to be commissioned on a provisional band. And the autonomous
envelope (`±18°` tilt) is now the limiter rather than the band — if tracking cannot look up far
enough at somebody close, that is why.

## The control law

`neck/pantilt_core.{h,cpp}` is pure compute — no I2C, no NVS, no Arduino — so the identical
translation unit runs on a laptop. `./build.sh pantilt` compiles and runs its host test with no
board attached. Arbitration, the slew limit and the bounds all live there, deliberately, rather
than in the caller; `src/main.cpp` only moves bytes.

Two clamps, often confused: the **band** is the mechanical limit, enforced on every target. The
**envelope** (`kPanRangeDeg`, `kTiltRangeDeg`) is much narrower and applies only to autonomous
tracking, so a tracker cannot swing the head as far as a show can. If tracking will not look far
enough, it is the envelope, not the band.
