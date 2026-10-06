# Tab5 touch: the ESP-IDF bring-up never started the GT911

Resolved 2026-10-05. Kept because the *finding* is one line and the *hunt* cost a whole sitting,
and the reason it cost that is reusable.

## The fault

The ESP-IDF GT911 bring-up leaves the touch controller ACKing I2C without ever starting its scan
engine. The chip answers every poll, reports no coordinates, and the display is unaffected — so it
presents as "touch is broken", never as a fault.

Measured at the driver layer: **~3,200 consecutive `esp_lcd_touch_read_data()` calls at ~25 Hz,
zero I2C errors, and `esp_lcd_touch_get_coordinates()` never once returning true.**

## The fix

Replace `esp_lcd_touch_exit_sleep(tp)` with the bring-up the Arduino firmware uses — the path that
has never failed on this board. Transcribed from `M5GFX/src/M5GFX.cpp:2687-2729` and
`lgfx/v1/touch/Touch_GT911.cpp:51-126`:

1. INT (GPIO23) driven **HIGH push-pull** before and across the reset — the I2C address is
   selected by a driven level, not by a weak pull-up.
2. TP_RST (**PI4IOE1 P5**) low 10 ms, then high.
3. INT to plain input, 100 ms.
4. Clear the buffer-status register `0x814E`.
5. Wake: INT low 58 ms, high 2 ms, then **input with pull-up, and left there**.

Three differences from the IDF path, all at the pin the chip latches its mode from: driven-vs-
pulled-up across reset; `exit_sleep` driving INT push-pull for 5 ms *against a running chip that
is itself driving INT*; and INT left floating open-drain rather than pulled up.

Implemented as a BSP patch applied by `firmware/tab5_idf/build.sh` from
`firmware/tab5_idf/patches/gt911_selftest.c.inc`. P5 only — the panel is already initialised when
this runs, so LCD_RST (P4) must not move.

### Proof, same board, consecutive boots

| register | before | after |
|---|---|---|
| `xres` / `yres` | 0 / 0 | **720 / 1280** |
| `xmax` / `ymax` | 0 / 0 | **720 / 1280** |
| `touches` | 0 | **10** |
| `refresh` / `chksum` / `vendor` | 00 / 00 / ff | **05 / 76 / 02** |

Then `presses=2 errs=0 last=438,745`, raw coordinates in native-portrait range, and the UI
responded.

## What was NOT the cause — each disproven by measurement, not argument

- **The LVGL lock structure.** `onRunning` takes the lock ~10x per frame. A nine-flash
  "correlation" with lock-pair count was noise consistent with a 25–50% base failure rate.
  Collapsing it to one RAII-held lock per frame was tried and **also** failed; reverted.
- **Event routing.** `LV_EVENT_CLICKED` vs `PRESSED`, z-order, `LV_OBJ_FLAG_SCROLLABLE`, screen-
  level vs child targets. `veil=1 scr=0` is ordinary LVGL 9 (hit-testing returns one object and
  PRESSED does not bubble without `LV_OBJ_FLAG_EVENT_BUBBLE`), not a dispatch fault.
- **The board's touch-controller revision.** Three exist (GT911 → ST7123 → ST7121). Ours is
  GT911, detection is cached once and feeds both display and touch, so they cannot diverge.
- **Cold vs soft reset.** "A cold power-cycle restores touch" was n=1 and later contradicted by a
  cold pull that did not.
- **A zeroed config block.** The chip holds a valid config (`ver 50`, 720x1280, `touches 10`,
  `chksum 76`); the zero readback was a **symptom** of an un-started chip. M5GFX writes no config
  either — its only config writers are never called — so writing one would have been a new
  experiment with a one-way hardware component, in the wrong direction. Nothing was ever written
  to the chip's NVM; `config_fresh` stayed `00`.

## The lesson, which is the point of keeping this

**Instrument the lowest layer first — with an instrument that cannot alter what it observes.**

Every candidate above sits ABOVE the layer where the fault lived, and each was investigated
before anyone looked at what the driver actually delivered. The measurement that settled it —
counters inside the vendor's own `lvgl_read_cb`, counting what the GT911 hands to LVGL before any
hit-testing — cost one flash and one press, and it exonerated the entire UI stack in a single
observation. It should have been the first step, not the twentieth.

Two instrument failures worth naming, because both produced confident wrong answers:

- A probe polling `lv_indev_get_next(nullptr)` read the **USB mouse**, not the touchscreen:
  `lv_indev_create` inserts at the HEAD of the list and `hal_usb.cpp hid_init()` registers a mouse
  indev *after* the touch one.
- A liveness test wrote `0xAA` to `0x8040` — the GT911 **command** register — and never restored
  `0x00`, so the instrument changed what it measured. Its pass criterion also required
  `0x8041 == 0xAA` while nothing in this stack arms ESD, so it could not pass whatever the chip
  did. It reported FAIL on boots indistinguishable from working ones.

Both failed the same way: each measured something other than what it claimed, and one of them
changed the thing under test. **An instrument that writes to the thing it measures is worse than no
instrument**, and a verdict from an uncalibrated test is worse than no verdict. The driver-layer
counters were decisive precisely because they write nothing to the chip.

## Residual, open

`presses=2` for three one-second presses, where ~25 Hz polling predicts ~75. The chip reports a
touch for only one or two polls per press, so a fast tap may be missed. Touch functions; this is
quality, not a blocker. Candidates: the `0x814E` handshake, or the chip deasserting early. Also
`0x8040` reads `0xff` after bring-up while coordinates flow — unclear whether the sequence should
end by writing `0x00` to the command register.

## Recommended next steps, not done

Carried over from the analysis rather than acted on, because touch works and the sitting ended.

1. **Settle the press-cadence anomaly from data already on disk.** The two serial lines both read
   `TP RAW press #1`, and that cannot come from one monotonic counter: the condition is
   `(od_tp_presses++ % 25) == 0` printing the post-increment value, so the next qualifying line
   must read `#26`. A second `#1` means the counter was zero again — different boots, a reset, or
   a mangled capture — so the `presses=2` figure is not yet trustworthy evidence of anything. The
   heartbeat lines each carry the running total, so the series pins it down at no cost. If it
   shows ~1 press-poll per hold, the chip is reporting one frame per touch rather than one per
   refresh cycle (`refresh 05` implies ~10 ms) and that is a real question; if ~25 per hold, the
   PONG was simply read mid-session and there is no anomaly.
2. **Do NOT write `0x00` to `0x8040`.** The `0xff` read happens within 2 ms of the wake pulse while
   the firmware is still starting, and M5GFX never writes that register at all except `0x05` to
   *enter* sleep. A `0x00` write would be a new variable with no known-good precedent. Observe it
   read-only instead — adding `0x8040` to the heartbeat is enough.
3. **A determinism series, five boots, covering all three reset paths** that previously produced
   dead boots: UDP `REBOOT`, a cold power pull, and an esptool flash reset. The register dump is a
   sound *necessary-condition* proxy (`xmax=0` guarantees no coordinates), so four of the five
   cost no human interaction; one press at the end closes the sufficiency gap. If any boot comes
   up zero, the base rate is not 100% and a bounded retry of `od_gt911_m5gfx_bringup()` — up to
   three attempts on a FAIL verdict, each logged — becomes required rather than insurance. Worth
   doing before this ships either way, together with turning the dump into an explicit
   `GT911 TOUCH PASS/FAIL` line keyed on `xmax && ymax && touches`.
