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
`OTA <url>` · `REBOOT` · `TOF` (refused: not fitted). Anything else answers `ERR unknown`.

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

# 3. on the Thor: serve it on a free port, send OTA, stop the server
ssh opendubs2
cd /tmp && python3 -m http.server 8781 --bind 10.42.2.10 &
python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(120)
s.sendto(b"OTA http://10.42.2.10:8781/neck.bin", ("10.42.2.12", 4242))
print(s.recv(512).decode().strip())      # OK ota <bytes> bytes, rebooting
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

A virgin or bricked board needs USB and `tools/flash.py` (app-only at `0x10000`; a merged image at
`0x0` erases the NVS and takes the Wi-Fi credentials with it). Flashing over USB is CLAUDE.md's
rote M5Stack procedure; do not improvise around it.

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

A 1.5° deadband means the head parks about a degree inside whatever you command, so the limits
carry that much margin on their own.

**Known rough edge:** `commissioned` goes true on the *first* `TILT` write, which during a
measurement is a provisional number. A procedure abandoned halfway leaves the robot claiming to be
commissioned on a throwaway band.

## The control law

`neck/pantilt_core.{h,cpp}` is pure compute — no I2C, no NVS, no Arduino — so the identical
translation unit runs on a laptop. `./build.sh pantilt` compiles and runs its host test with no
board attached. Arbitration, the slew limit and the bounds all live there, deliberately, rather
than in the caller; `src/main.cpp` only moves bytes.

Two clamps, often confused: the **band** is the mechanical limit, enforced on every target. The
**envelope** (`kPanRangeDeg`, `kTiltRangeDeg`) is much narrower and applies only to autonomous
tracking, so a tracker cannot swing the head as far as a show can. If tracking will not look far
enough, it is the envelope, not the band.
