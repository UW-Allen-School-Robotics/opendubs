# sbus_framer

Finds the 25-byte SBUS radio frames in the StamPLC's byte stream. One header, no Arduino, so the
firmware and the tests on a laptop run the same code. Written for issue 642: on 2026-10-26 the
framer it replaces locked onto a data byte that happened to be `0x0F` and the robot had no radio
for 70 minutes. The reasoning, and what the framer still cannot do, is at the top of
[`src/sbus_framer.h`](src/sbus_framer.h).

## Run the tests

```sh
firmware/rc_serial_probe/lib/sbus_framer/tests/test_sbus_framer.sh
```

A C++ compiler is all it needs; it takes a few seconds. It prints the old framer's result beside
the new one's on the same bytes, starting with the 10-26 stream (CH7 = 1000, CH8 = 1800, entered
at byte 10): the old framer accepts nothing in 10,000 frames, the new one accepts from the third.
Run it before any image with a change to the framer is offered to a robot.

## What the firmware shows

At boot, once:

```
[SBUS] framer self-test ok
```

That is the 10-26 stream and a lost byte put through a second framer. It says the flashed image
carries this framer; it does not gate anything.

Once a second, after the five fields the dashboard already reads:

```
[SBUS] prof=... bytes= ok= bad= last_ok_ms= resync= hunt= maxhunt= ambig= starve= st=
```

| field | meaning | healthy |
|---|---|---|
| `bad` | frames the framer saw and did not accept | rises by about 3 at each `resync`, otherwise still |
| `resync` | times the framer lost its place | rare; each one is damage on the wire |
| `hunt`, `maxhunt` | bytes spent finding its place again, in all and in the worst case | `maxhunt` about 75 after one damaged byte (50 at boot), more after a burst of noise |
| `ambig` | frames refused because two alignments both passed every check | 0 |
| `starve` | times 1000 bytes arrived with no frame accepted | 0 |
| `st` | the boot self-test | 1 |

`starve` above 0 with `ambig` still is the 10-26 fault back again, and is a bug in this library.
`starve` and `ambig` rising together means the controls sit in a position whose bytes look like a
frame boundary in two places; moving any switch or knob ends it. The framer cannot tell those two
apart without seeing the idle gap between frames, which the firmware does not have yet.
