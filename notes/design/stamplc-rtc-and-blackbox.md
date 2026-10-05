# StamPLC Phase 3 adoption: absolute time, and an on-board black box

Design for the two additive changes [`stamplc-demo-audit.md`](stamplc-demo-audit.md) §4 recommends
first. **No code was changed.** Desk work, 2026-10-05, read against
`firmware/rc_serial_probe/src/main.cpp` (3,343 lines) and the vendor's published pin map.

The audit's framing stands: *"Items 1–3 are additive and touch no existing control path."* This note
takes that seriously enough to find the two places where it is not quite true, because on this
board "additive" and "harmless" are not the same thing.

---

## 0. The three constraints both designs live inside

Everything below follows from these, so they come first.

**1. One task, one loop, and the loop is the safety function.** There is no `xTaskCreate` anywhere
in `rc_serial_probe`; everything runs in Arduino's `loopTask` at roughly 50 Hz. The loop measures
its own period and acts on it:

```cpp
static constexpr uint32_t kLoopStallMs  = 250;    // an iteration longer than this = a stall
static constexpr uint32_t kWdtTimeoutS  = 8;      // loop hang > this → auto chip reset
```

A stall past 250 ms logs `[SAFE] loop stalled … — drive disabled` and drops the drive-safe gate; a
hang past 8 s panics the chip. The comment above the gate is the whole point: *"a loop that is not
keeping time cannot stop the robot."* **So any blocking call added anywhere in this firmware is a
change to the control path, whatever file it lives in.** That is the constraint that shapes the
black box, and it is why the audit's "touches no existing control path" needs qualifying rather
than accepting.

**2. The RTC shares a bus with the relay and input expander.** From the firmware's own comment,
the internal peripheral bus is `m5::In_I2C`, `I2C_NUM_0` on G13/G15, carrying:

| device | addr | what it is |
|---|---|---|
| INA226 | 0x40 | `getPowerVoltage()`, socket current |
| **RX8130** | **0x32** | the battery-backed RTC |
| PI4IOE5V6408 | 0x43 | **the relays and the digital inputs** |
| LM75B | 0x48 | board temperature |
| AW9523B | 0x59 | status light and the buttons |

Relay writes and DI reads go over the same two wires as the RTC. The e-stop *function* is a
contactor chain in hardware and DI0 is only *reporting* it — the audit establishes that — but bus
time spent on the RTC is bus time not spent reading inputs. One transaction per log line is not an
option; one per minute is free.

**3. The SD card shares the SPI bus with the Ethernet transport and the display.** The vendor pin
map:

```
STAMPLC_PIN_SD_CS   10      STAMPLC_PIN_SD_MOSI  8   MISO 9   SCK 7
STAMPLC_PIN_LCD_CS  12      (same MOSI/MISO/SCLK: 8 / 9 / 7)
```

and our own setup:

```cpp
// --- PoE W5500 Ethernet: shares the LCD/SD SPI bus (G7/G9/G8); leave RST untouched
SPI.begin(7, 9, 8, -1);              // SCK=G7, MISO=G9, MOSI=G8, no HW-SS
Ethernet.init(11);                   // W5500 CS = G11
```

Three chip selects, one bus: SD on 10, W5500 on 11, LCD on 12. **The Ethernet path is how telemetry
leaves, how commands arrive with USB unplugged, and how OTA pulls an image.** Anything that holds
that bus holds all three. And this bus already has a scar: `MAX_SOCK_NUM 2` meant a permanently-open
head socket starved the OTA HTTP client, *"and the board dutifully dropped its drive bus on each
attempt. Recovering needed a physical reset."* Resource contention on the network path has cost a
robot recovery here once already.

---

## 1. Battery-backed RTC (RX8130)

### The problem, stated precisely

Every timestamp this firmware produces is `millis()` — milliseconds since the last boot. So a log
can say *"RC dropped 41 seconds in"* and cannot say *"RC dropped at 14:07:33"*. Two consequences,
and the second is the expensive one:

- A reboot resets the origin, so **two boots' logs cannot be put on one timeline** — which is
  exactly the shape of the intermittent `RC DISCONNECTED` hunt, where the interesting question is
  whether a dropout correlates with something outside the firmware.
- `millis()` wraps at ~49.7 days. The firmware already knows this and handles it in its deadline
  tests; a *log* has no such defence, and a wrap mid-session silently reorders events.

The library gives us two calls and nothing else:

```cpp
void getRtcTime(struct tm* time);
void setRtcTime(struct tm* time);
```

### Reading: once at boot, then once a minute, and derive the rest

**Never read the RTC per event.** Hold an offset and do arithmetic:

```
boot_epoch = rtc_epoch_utc − (millis() / 1000)        // established at boot
timestamp(ms) = boot_epoch + ms / 1000                 // every event, no I2C
```

Re-read every 60 s and re-establish `boot_epoch`. That re-read does three jobs for one
transaction: it corrects the ESP32's own crystal drift against the RTC's, it detects an RTC that
has stopped or lost its backup, and it makes the `millis()` wrap a non-event, because the offset is
recomputed from scratch each time rather than accumulated.

Cost: one I2C transaction per minute on the internal bus, against a 50 Hz loop. Below measurement
noise. Compare the alternative — a read per log line during an RC-dropout storm is hundreds of
transactions on the bus that reads the inputs, which is the kind of change that gets blamed for a
fault six weeks later.

**UTC on the wire, always.** `struct tm` has no zone, and a robot that is demonstrated in one place
and debugged in another must not have ambiguous logs. The StamPLC stores and emits UTC; rendering
local time is the Thor's and the dashboard's job. Writing this down because the opposite choice is
the one people make by accident.

### Setting: the Thor asks, and the claim travels with the value

A new host command, through the existing dispatcher — `handleHostCommand()` already serves both USB
and UDP via `pollHostCommands()` / `pollNetCommands()`, so this is one `else if` and no new
transport:

```
TIME <epoch_utc_seconds> <source>        # source ∈ ntp | host | manual
TIME                                      # query: replies [TIME] epoch=… src=… age=…
```

The Thor sends it on every reconnect, not once — it is idempotent, it costs nothing, and it means a
head that rebooted gets its clock back without anyone noticing it had lost it. The dashboard's
StamPLC reader is the natural place; it already holds the link and already sends `TAB5IP` the same
way, so the pattern exists.

**The firmware records the claim and never upgrades it.** The StamPLC cannot verify that the Thor
is NTP-synced, so it stores what it was told and emits it with every absolute timestamp it
produces. This matters here specifically: per `thor-agx-host`, the robot LAN has no internet of its
own and the Thor reaches the outside world over UW-MPSK Wi-Fi, so **the Thor's own clock is
sometimes wrong**, and a confidently-wrong timestamp is worse than an honest relative one. A
timestamp whose provenance is unknown gets trusted; one labelled `src=host` does not.

So the time source is a small ladder, and it is part of the record:

| `src` | means | how a log renders |
|---|---|---|
| `none` | never set, or the RTC's value failed the sanity check | **relative only** — `t+41.203` and no absolute field at all |
| `rtc` | read from the RX8130, which someone set at some point | absolute, flagged as un-resynced |
| `host` | the Thor sent a time it did not claim was NTP-synced | absolute |
| `ntp` | the Thor sent a time and claimed NTP sync | absolute, trusted |

**When `src=none`, emit no absolute timestamp.** Not a zero, not 1970, not a guess. The failure mode
to design against is a field that looks like an answer.

### Detecting an RTC that has lost its time

Two checks, one certain and one not:

- **A plausibility window**, certain and free: accept the RX8130's value only if the year is within
  a sane band (say 2025–2100). A dead backup typically reads as an epoch near zero or as garbage,
  and both fail this. Outside the window → `src=none`, and `[TIME] rtc=implausible`.
- **The RX8130's own voltage-low flag**, which is the correct way and is **not reachable through the
  API above.** The part has a data-loss / voltage-low status bit, and `M5StamPLC.h` exposes only
  `getRtcTime` / `setRtcTime`. The audit notes direct `RX8130` class access exists but marks it
  ignore. **I have not confirmed whether M5Unified's RX8130 class exposes the flag, or whether
  reading it means a raw I2C register read at 0x32.** Settled by reading the M5Unified source and
  the RX8130 datasheet — a desk task, no hardware. Until then the plausibility window is the whole
  check, and it is enough to catch a flat battery, which is the case that will actually happen.

Also worth one line in the record and nearly free: **`esp_reset_reason()` at boot.** It
distinguishes a power cycle from a watchdog panic from an OTA reboot, and the watchdog case is the
one you cannot otherwise see — the firmware that would have logged it is the firmware that stopped.
`[BOOT] reason=TASK_WDT` next to an absolute time answers "did it reset itself, and when" in one
line.

### How timestamps reach the logs

`tprintf()` is already the single choke point — `telem.h` is explicit that it *"tees to BOTH the
USB serial console and the UDP telemetry socket"* and that anything a remote operator needs must go
through it. The temptation is to prefix every line there. **Don't**, for two measured reasons:

- The USB CDC path drops rather than blocks (`Serial.availableForWrite() >= n`, and
  `setTxTimeoutMs(0)`). Adding ~25 characters to every line makes the stream larger and so makes
  drops *more* likely, on the path used to diagnose the faults.
- The Thor already timestamps on receipt, with far better resolution than this adds. A duplicate
  absolute time on a 1 Hz `[SBUS]` counter line buys nothing.

So the rule is **timestamp events, not telemetry.** One new function beside `tprintf()`:

```c
void tevent(const char* fmt, ...);   // == tprintf(), plus a record in the black box
```

`tevent()` emits exactly what `tprintf()` would — byte-identical on the wire — and additionally
appends the line to the ring buffer of §2 with the absolute time and the `millis()` value attached
there rather than on the wire. Converting a call site is a **rename**, and the acceptance test is
that the telemetry stream is unchanged except for the new `[TIME]` and `[BOOT]` lines.

That is also what makes the two halves of this note one change: `tevent()` is where the RTC's answer
and the black box's input meet, and it is the only new code either feature needs in the control
loop.

A periodic `[TIME] epoch=… ms=… src=…` line at the existing 1 Hz status cadence lets anything
reading the live stream anchor the `millis()`-relative lines without touching them.

### What this costs, and the exit

- One I2C read per minute, one more `else if` in `handleHostCommand()`, one new emitter, one line at
  boot. No existing call site changes behaviour.
- Behind `#ifndef OPENDUBS_RTC` / `#define OPENDUBS_RTC 1` so it compiles out, matching the
  `DRIVE_ENABLE` pattern already in this file.
- **The acceptance test already exists**: `g_worstLoopMs` and the absence of `[SAFE] loop stalled`.
  If enabling the RTC moves the worst loop iteration at all, something is wrong with the design and
  not with the budget.

---

## 2. SD black-box logging

### What it is for, which is narrower than "logging"

The audit's target: *"a fault/RC-dropout history that does not depend on the Thor, the network, or a
host draining a serial port"*, with the sharp reason — on the Tab5 *"a console write blocks the
writing task when nothing drains the port, which makes serial telemetry a suspect in the very faults
it is meant to record."*

That is a **black box**, not a log. The difference is that a black box's job is to hold the events
*before* the fault, and it is sized and triggered for that rather than for completeness. Designing
for completeness is how you end up with a logger that stalls the loop it is watching.

### The medium: internal flash first, SD second — differing from the audit's order

The audit recommends the SD card. I think the first implementation should write to the **internal
SPIFFS partition** and the SD card should be the second step, and the reason is constraint 3.

The property that matters is independence **at write time**, because the write happens during the
fault. On that property they are equal: neither needs the Thor, the network, or a host. They differ
on two other axes, and the comparison is not close:

| | internal flash (`spiffs` in `default.csv`) | SD card |
|---|---|---|
| **shares a bus with the Ethernet transport?** | **no** — the flash has its own controller | **yes** — SD CS 10, W5500 CS 11, one bus |
| how it stalls the loop | cache suspension during a page write or sector erase: short, bounded, documented | SPI bus arbitration behind a card doing internal wear-levelling: **unbounded and card-dependent** |
| survives a board that will not boot | no — you need the board to read it | **yes, pull the card** |
| capacity | ~1.4 MB (see below) | gigabytes |
| wear | finite, shared with the firmware's own flash | the card's problem, and the card is replaceable |

A flash write on the ESP32 suspends the instruction cache, so a task executing from flash — which
is every task here — stalls for the duration. That is a real stall and it must be budgeted. But it
is *bounded and known*: a page write is on the order of a millisecond and a sector erase tens of
milliseconds, against `kLoopStallMs` = 250. An SD card's internal housekeeping has no such bound,
and the thing it blocks is the bus that carries the commands and the telemetry.

So: **both media stall the loop, by different mechanisms, and neither has been measured here.** The
bounded mechanism is the one to take first. The SD card's one decisive advantage — reading a log off
a board that will not boot — is real, which is why it is the second step and not discarded; and note
that `default.csv` also provides a `coredump` partition, which already covers the specific case of a
panic.

**Unverified and load-bearing:** the sizes. `platformio.ini` pins
`board_build.partitions = default.csv`, and the Arduino ESP32 `default.csv` for a 4 MB map places a
`spiffs` partition of roughly 1.4 MB and a 64 KB `coredump` after the two ~1.31 MB app slots — which
is consistent with the comment in `platformio.ini` ("app0/app1 ~1.31MB each"). **Confirm the actual
offsets and sizes from the build's partition dump before sizing anything against them**; this is a
one-command check and guessing it is how a design acquires a wrong number that nobody re-examines.

**If the SD card is used**, two things must be established before enabling it, and neither should be
assumed:

- **That the W5500 driver and the SD driver both bracket their access in `SPI.beginTransaction()` /
  `endTransaction()`.** If either does not, they corrupt each other, and the corrupted one is the
  command and telemetry path. Verifiable by reading `M5-Ethernet` 4.0.0's W5500 source and whichever
  SD library is chosen.
- **That `M5StamPLC.config().enableSdCard` does not re-initialise SPI underneath us.** `setup()`
  calls `M5StamPLC.begin()` *before* our explicit `SPI.begin(7, 9, 8, -1)` and `Ethernet.init(11)`.
  Turning on the vendor's SD init changes what happens in that window, and the audit has already
  shown that this `begin()` sequence has load-bearing ordering side-effects nobody has root-caused.
  Prefer initialising the card ourselves, after Ethernet, with our own `SD.begin(10, SPI)`.

### The structure: a ring in RAM, a writer that is not the loop

```
tevent()  →  append to an 8 KB ring in RAM  →  [writer task, core 0, low priority]  →  medium
            (a memcpy; microseconds; never blocks; never fails)
```

- **The ring never blocks and never refuses.** It overwrites its oldest entry. A full ring must
  never be able to stall or drop an event in the loop — the loop's job is to stop the robot, not to
  record that it did.
- **The writer is a dedicated FreeRTOS task**, low priority, pinned to the core `loopTask` is not
  on. This is the one structural change in this design: `rc_serial_probe` has no `xTaskCreate`
  today and is single-task by construction. Adding a task is the real cost, and it is the reason
  this goes behind a build flag with the loop-stall detector as its gate.
- **Flush policy**, which is what makes it a black box:
  - on any event at or above a severity threshold — flush now, which captures the N events
    *preceding* the fault that are already sitting in the ring;
  - otherwise every ~10 s, or when the ring passes half full;
  - batch into whole pages so a burst of events is one write, not twenty.
- **The residual risk is bus arbitration, not CPU.** A writer on the other core does not help if it
  holds the SPI mutex while a card erases, because the Ethernet path then queues behind it. On
  internal flash the equivalent is cache suspension, which no amount of task placement avoids
  either. **Both are measurable with the instrument the firmware already has**, and that is the
  acceptance test: enable logging, drive the robot, and if `[SAFE] loop stalled` appears or
  `g_worstLoopMs` moves, the logging is wrong. Nothing else needs to be instrumented for this.

### What is logged

**Events, in the existing `[TAG] key=value` text contract**, so `grep` still works and the Thor's
parser needs no new format. One line each:

```
2026-10-05T19:42:17Z ms=41203 src=ntp [SAFE] drive DISABLED — waiting for a stable loop + RC
2026-10-05T19:42:17Z ms=41207 src=ntp [RC] link lost after 00:04:11 (gap 512ms, frames ok=12841 bad=3)
```

The event set, taken from the lines the firmware already prints — this is a selection, not new
instrumentation:

| group | events |
|---|---|
| **boot** | power-on with `esp_reset_reason()`, the firmware build id, the boot counter from NVS, and the time source |
| **drive safety** | every `g_driveSafe` transition with its reason; every `[SAFE] loop stalled` with the phase, SBUS byte count and worst-byte figures it already prints |
| **RC** | link up / link down with the gap duration and the frame counters at the transition; authority (CH5) and deadman (CH6) changes |
| **e-stop and faults** | assert, clear attempt, clear refused with the blockers, contactor state, latched fault set and clear |
| **mast** | pinch-guard hold, consent granted, consent expired, limit reached, heartbeat expiry |
| **power** | PDH faults, BMS faults, SOC and pack voltage at each of the above (cheap: it is cached) |
| **network / OTA** | link up / down, the self-heal path firing, OTA start and result |
| **I²C** | bus recovery firing, a device going absent |

Explicitly **not** logged: the 1 Hz `[SBUS]`, `[SENS]`, `[IO]`, `[ODO]` counters and anything
per-frame. Those are a stream; the Thor records them when it is up, and recording them here would
fill the medium with the periods nobody asks about. The asymmetry is deliberate — **the quiet hours
should cost nothing and the interesting minutes should be dense.**

A boot counter in NVS (one more `Preferences` namespace, alongside the five this firmware already
uses) makes every line attributable to a boot, which is what lets two boots share a timeline even
when `src=none`, and makes a reboot loop visible as a reboot loop.

### Retention

- **A segmented ring on the medium**: a fixed number of fixed-size segments — say 8 × 128 KB on
  internal flash — written in rotation, with a sequence number and a CRC in each segment header.
  When the last is full, the oldest is overwritten.
- **Fixed size, always.** Never grow, never fill the filesystem. A medium that fills and starts
  failing writes silently is the classic black-box failure, and the one that loses exactly the
  recording you wanted.
- **State capacity in events, not days.** At a handful of events a minute, ~1 MB of text is months.
  During an RC-dropout storm it could be hours. That spread is the design working, and the figure
  to quote is "the last ~N thousand events", with N computed from the measured mean line length
  once there is one.
- A segment is append-only until it is recycled, and the header is written when the segment is
  opened, so a power cut mid-segment loses the tail of one segment and nothing else.

### Retrieval

Three paths, in the order they will actually be used.

**1. Over the existing command dispatcher, pulled by the Thor.** One more command, no new socket, no
server on the board:

```
LOG LIST                              → [LOG] seq=… bytes=… first=… last=…   (one line per segment)
LOG GET <seq> <offset> <len>          → [LOG] seq=… off=… crc=… data=<hex>
LOG WIPE                              → erases all segments; requires a confirm token
```

**The firmware stays a pure responder and the Thor owns the retry logic.** That is the right split
because the alternative — a push or a file-transfer state machine on the StamPLC — puts timing and
retransmission state inside the loop that must keep time. It is also why there is no HTTP server
here: `MAX_SOCK_NUM 2` and the head-socket incident say plainly that this board has no spare socket
to hold open.

**2. Pull the card** (SD only). The case the SD card exists for: a board that will not boot.

**3. USB at the bench — and this one has a trap.** You cannot `cat` the log to `Serial`.
`Serial.setTxTimeoutMs(0)` makes writes non-blocking and therefore **lossy**: a bulk write past
roughly the 256-byte CDC FIFO silently discards the remainder. That decision is deliberate and must
not be reverted — it exists because a blocking `Serial.printf` stalled the loop and starved SBUS,
which is the `RC DISCONNECTED` bug. So a USB dump must be **line by line with a few milliseconds
between lines**, paced by the firmware, and read with a self-terminating `head -c <bytes>` per the
bench procedure in `CLAUDE.md`. A dump written as one big block looks exactly like a dead card.

### Scope discipline

- Behind `OPENDUBS_BLACKBOX=0|1`, default off until it has been through the bench and then the
  robot. With it off, the ring, the task and every `tevent()` extra are compiled out and `tevent()`
  is `tprintf()`.
- `tevent()` emits byte-identical output, so the only wire change is the new `[TIME]`, `[BOOT]` and
  `[LOG]` lines.
- **No control path is read or written by any of this.** Nothing here touches the mixer, the arm
  gate, the mast state machine, the relays or the SBUS decode. The one place it brushes the control
  path is latency, which is exactly what §0 constraint 1 is about and exactly what the acceptance
  test measures.
- **The buttons stay stubbed.** The audit's item 4 changes the safety behaviour of the safety
  controller and is explicitly out of scope; nothing in this note depends on it.

---

## 3. Order, and the acceptance test for each

| # | step | where | done when |
|---|---|---|---|
| 1 | `tevent()` as a pure alias of `tprintf()`, call sites renamed on the event list | bench | telemetry stream byte-identical; `g_worstLoopMs` unchanged |
| 2 | RTC read at boot + 60 s, `TIME` command, `[TIME]`/`[BOOT]` lines, plausibility window | bench | `TIME` sets it, a power cycle keeps it, an unset clock reports `src=none` and emits **no** absolute field |
| 3 | Ring buffer + writer task + internal-flash segments, flag off by default | bench | a forced fault flushes the preceding events; power cut mid-write loses one segment tail and nothing else |
| 4 | Enable on the robot | robot | an hour of driving with no `[SAFE] loop stalled` and `g_worstLoopMs` within its pre-change range |
| 5 | `LOG LIST` / `LOG GET`, Thor-side retrieval | robot | a segment retrieved and CRC-verified over UDP with the dashboard running |
| 6 | SD card as a second sink — **only after** the two SPI questions in §2 are answered by reading the libraries | bench, then robot | same loop-stall criterion, plus an OTA pull completing while the writer is active |

Step 4's criterion is the one that matters, and it is worth saying why it is phrased as a comparison
rather than a threshold: `g_worstLoopMs` has a value today that nobody has written down. **Record it
before step 1.** A benchmark taken after a change is not a benchmark.

## 4. What is uncertain

- **Whether the RX8130's voltage-low flag is reachable through M5Unified's class** or needs a raw
  register read. Settled by reading the M5Unified source and the RX8130 datasheet. Desk task.
- **The actual `spiffs` and `coredump` offsets and sizes** under the pinned `default.csv`. Settled
  by the build's partition dump. One command.
- **Whether `M5-Ethernet` 4.0.0 and the chosen SD library both use SPI transactions.** Settled by
  reading both sources. Desk task, and it gates step 6 rather than the whole design.
- **How long a flash sector erase actually suspends the cache on this part at this clock**, and
  therefore how much of the 250 ms stall budget a flush spends. Settled by step 3's bench run with
  `g_worstLoopMs`, which is the measurement rather than a number to look up.
- **Whether the Thor's clock is trustworthy often enough for `src=ntp` to be the common case.**
  Unknown, and it is the difference between absolute timestamps being useful and being decorative.
  Settled by asking the Thor what it thinks its sync state is — which is why `TIME` carries a
  source field rather than just a number.
