# Phase 3 — StamPLC: what did we leave on the table?

**Read-only audit. No code was changed in `firmware/rc_serial_probe`.** Done 2026-10-05 against
`m5stack/M5StamPLC-UserDemo` (via the GitHub API) and the pinned `M5StamPLC` 1.2.0 +
`M5Unified` 0.2.15 sources already in `.pio/libdeps/`.

The question was not "should we rewrite it" — it works, and nobody is anxious to change it. The
question was whether building bespoke left **better-performing code or unused hardware capability**
behind.

---

## 0. The headline, because it inverts the Tab5 conclusion

**We are already on the vendor's stack for the StamPLC, and we picked the renderer M5Stack picked.**

| | Tab5 demo | StamPLC demo |
|---|---|---|
| app framework | mooncake | mooncake |
| animation | smooth_ui_toolkit | smooth_ui_toolkit |
| **renderer** | **LVGL 9** | **M5GFX — zero LVGL anywhere in the repo** |
| screen | 1280×720 | 135×240 |

Two things follow, and they matter more than any single row below.

**First: the renderer choice is screen-driven, not a vendor-wide standard.** M5Stack shipped LVGL on
the Tab5 and M5GFX on the StamPLC. That retires a question that has been open for a long time — the
earlier "LVGL is a dead end" call was not wrong *in general*, it was wrong *for the Tab5*, and it is
still right for the StamPLC. There is no single correct answer to carry between the two boards.

**Second: our StamPLC screen code is not homebrew-instead-of-better.** Our hand-rolled `M5Canvas`
two-page UI uses the *same renderer the vendor uses on this board*. The only thing we are missing
from their UI stack is `smooth_ui_toolkit`, which we now know well from the Tab5. So the Tab5 lesson
("you built a worse version of what the demo already did") **does not transfer here.** Worth saying
plainly, because the premise going in was that it would.

Architecture is otherwise identical to the Tab5 demo — `app/` + `platforms/`, dual desktop/device
build, an asset-pool partition — so a subtractive port *would* be mechanically familiar. It is still
not recommended; see §4.

---

## 1. The buttons — root-caused by reading, in full

Our firmware stubs `uiButtons()` with this note:

> Buttons stubbed: `M5.begin()`+`M5StamPLC.begin()` double-init breaks M5Unified's StamPLC button
> state, so touching BtnA/B/C faults.

**That diagnosis is incomplete, and the part it misses is the whole bug.** The call chain, read from
the pinned sources:

```
M5StamPLC.h       Button_Class& BtnA = M5.BtnA;        // just a reference to M5Unified's button
M5StamPLC.cpp     void M5_STAMPLC::update() { M5.update(); }   // literally nothing else
Button_Class.hpp  bool wasClicked() const { return _currentState == state_clicked; }
```

`wasClicked()` is **not a latch**. It reports the state machine's verdict *from the most recent
`setRawState` call*, and `M5.update()` recomputes that verdict every time it runs.

Our loop does this:

```cpp
M5.update();          // samples the AW9523, computes _currentState = state_clicked
M5StamPLC.update();   // == M5.update() again, microseconds later: no raw change -> state_nochange
...
uiButtons();          // reads wasClicked() -> false. Always.
```

**The duplicate update consumes and discards every click edge before any code can observe it.** A
real press is detected correctly and then thrown away ~10 µs later. This is sufficient on its own to
explain "the buttons never worked," and it has nothing to do with the double `begin()`.

On the double `begin()` separately, since the note raises it: `M5StamPLC.begin()`'s *first line* is
`M5.begin()` — with **no argument**, so M5Unified's default config. Our explicit `M5.begin(cfg)`
beforehand sets exactly one field, `serial_baudrate = 115200`, which the vendor's second
`M5.begin()` then discards. And the board is built `-DARDUINO_USB_CDC_ON_BOOT=1`, where USB CDC
ignores baud rate entirely. **So our `M5.begin(cfg)` configures one value that is overwritten and
would be meaningless anyway.** Whatever makes removing it panic is an ordering side-effect, not that
config — and worth finding before anyone concludes the double-init is load-bearing.

**Recommendation: worth knowing, do not act unprompted.** The likely fix is deleting one line
(`M5StamPLC.update()` from the loop, keeping `M5.update()`) and un-stubbing `uiButtons()`. But
`uiButtons()` contains a **hold-to-confirm local e-stop on BtnC that opens R0**, so enabling it
changes the safety behaviour of the safety controller. That is a deliberate decision with a bench
test attached, not a drive-by edit, and it is explicitly out of this phase's scope.

---

## 2. `M5StamPLC` library API — used vs unused

We call 15 of the library's entry points. The unused ones, with a verdict each:

| capability | verdict | why |
|---|---|---|
| `getRtcTime` / `setRtcTime` (RX8130, battery-backed) | **adopt — best value on this list** | Our logs are `millis()`-relative, so after a reboot nothing says *when* a fault happened. A battery-backed RTC gives absolute fault timestamps with no Thor and no network — exactly what the intermittent "RC DISCONNECTED" hunt lacked. |
| `enableSdCard` | **adopt — second best** | On-board black-box logging, independent of the Thor, the network and the USB host. We learned on the Tab5 that a console write *blocks the writing task when nothing drains the port*, which makes serial telemetry a suspect in the very faults it is meant to record. An SD log has no such coupling. |
| AW9523B / PI4IOE5V6408 **IRQ** (vendor examples) | **worth knowing** | We poll DI at 50 Hz → up to 20 ms to notice an input edge. The vendor's `io_expander_a_init()` explicitly does `resetIrq(); disableIrq();`, so interrupts are available and deliberately off. Not a safety gap — the e-stop is a contactor chain and our DI0 read is *reporting*, not the safety function — but it caps how precisely we can timestamp an edge. |
| `setBacklight(bool)` | **worth knowing** | Blank the panel on a parked robot. Small power and attention win; trivial to add. |
| `enableModbusSlave` / `modbusSlaveId` | **ignore** | Modbus RTU is how industrial gear talks to a PLC. Nothing in this robot speaks Modbus. |
| `getIOExpanderA()` | **ignore** | Status light and buttons, both already wrapped. |
| `INA226` / `LM75B` / `RX8130` direct class access | **ignore** (except the RTC above) | We use the wrapped `getTemp` / `getPowerVoltage` / `getIoSocketOutputCurrent`, which is what they wrap. |
| `M5StamPLC_AC`, `M5StamPLC_IO` modules | **ignore** | Expansion modules we do not have. |
| `noTone()` | **ignore** | We use the duration-limited `tone()`. |

---

## 3. Demo-firmware capabilities with no counterpart in ours

| what the demo has | verdict |
|---|---|
| **`trigger_relay`: a persisted rule engine** — `interpreter/{conditions,actions,interpreter}.cpp`, `serialization.cpp`, `fs.cpp`, with an on-device if / then / else editor and save/load | **worth knowing, and point the choreography work at it.** This is a working reference for *exactly* the shape we specified for choreography configuration: a small condition→action language, serialized to the filesystem, selectable and editable without a reflash. Read it before designing ours. See `notes/workplan-choreography.md`. |
| **`app_timer_relay`** — interval/duration sequencing per relay, configured on-device | worth knowing. A timeline primitive, same family as the above. |
| **`app_log_monitor`** — a log viewer on the device panel | worth knowing. Directly relevant: our telemetry is USB-serial-only, and reading it has repeatedly been a suspect in the faults being investigated. A panel log is self-contained. |
| **PsychicHttp + `assets/web/syscfg.html`** — an async HTTP server serving a config page from the device | **worth knowing, and it answers a question we asked on the Tab5.** On a 135×240 panel the vendor does not draw a dashboard; it **serves HTML to a browser**. If we ever want on-device config UI without building widgets, this is the vendor's own answer. |
| localization (en/cn/jp with a CSV→header generator), a `theme/` abstraction, `app_generator.py` scaffolding | ignore for now; the theme abstraction is worth a glance when our themes grow. |
| `app_ezdata` (M5 cloud), QR generation, MQTT | ignore. MQTT is a reasonable transport, but we already have working UDP to the Thor and no reason to churn it. |
| desktop simulator target (`platforms/desktop`, SDL) | worth knowing — the StamPLC demo has one too, with the same value and the same blind spots we documented for the Tab5. |

---

## 4. Recommendation

**Do not port `rc_serial_probe` to the IDF demo structure.** The Tab5 case for switching was that
the bespoke renderer had hit a wall the vendor's had not. **There is no such wall here** — we are
already on the vendor's renderer (§0), and our firmware is ~3,000 lines of Arduino-framework code
carrying SBUS, TWAI/CAN, M5-Ethernet UDP, the mast state machine and the e-stop logic. That is a
large, safety-relevant rewrite with no demonstrated upside.

Harvest instead, in this order:

1. **RTC** — absolute fault timestamps. Smallest change, largest forensic payoff.
2. **SD black-box log** — fault/RC-dropout history that does not depend on the Thor, the network, or
   a host draining a serial port.
3. **Read `trigger_relay`** before designing choreography configuration. No porting; just read it.

Designs for 1 and 2 are in [`stamplc-rtc-and-blackbox.md`](stamplc-rtc-and-blackbox.md), which
qualifies "touches no existing control path" in one place: the loop measures its own period and
disables drive past 250 ms, so a blocking write anywhere in this firmware is a change to the
control path, and the SD card shares its SPI bus with the Ethernet transport. `trigger_relay` was
read; what it is a good reference for and the four specifics not to copy from it are in
[`face-config-data-model.md`](face-config-data-model.md) §1.
4. **Buttons** — one line plus un-stubbing, *but* it changes e-stop behaviour, so it needs a
   deliberate decision and a bench test. Not a drive-by.

Items 1–3 are additive and touch no existing control path. Item 4 does.

## 5. What this audit did not do

It did not run anything on the robot, did not flash, and did not change a line of
`firmware/rc_serial_probe`. The button root cause is derived **entirely from reading** the pinned
library sources — which is the point: it cost no flash, no robot and no observer, and it found a
complete mechanism where four rounds of symptom-matching on the Tab5 had each found only part of
one. The claim "the duplicate `update()` eats the click edge" is a reading of
`Button_Class::wasClicked()` and `M5_STAMPLC::update()`; **it has not been confirmed on hardware**,
and it should be, before anyone relies on the one-line fix.
