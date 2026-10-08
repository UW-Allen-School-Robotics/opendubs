# What has to come out before the Tab5 face ships

Written 2026-10-05. The IDF face has been developed by flashing diagnostics at it all day, and
several of those are things that would be actively wrong on a robot rather than merely untidy.
This is the list, so none of it rides along by being forgotten.

**The rule: a release build is every instrument ABSENT, not quiet.** An instrument that is merely
disabled at runtime is still in the image, still costs flash, and can still be switched on by a
mistake. Each one is wrapped in its own `#if` with no-op fallbacks — the pattern that measured
byte-identical when off.

## Gated now (was unconditional, would have shipped)

| | why it must not ship |
|---|---|
| **Automatic state cycling** — the face walked all 7 states every 6 s | **The worst one.** On a robot this is a face cycling through DEGRADED and E-STOP on a timer, announcing authority changes that did not happen, with the Mode earcon firing each time. It exists only because the bench has no StamPLC link. Now `#if OPENDUBS_DBG_UI`. |
| **Automatic emote cycling** | Same scaffolding; the face should emote from state and attention, not a timer. |
| **The HUD line** — state/emote/theme/heap/wifi | A robot's face is not a telemetry console. Useful at a bench, noise on a machine. |
| **The yellow status label** | Diagnostic only. |

All four are behind `OPENDUBS_DBG_UI`, default **0**. The default is defined *after*
`percept_config.h` deliberately: putting it before would shadow the flag and silently force every
build to bench behaviour.

## Already flag-gated, default off — verify, do not assume

- `OPENDUBS_WALKIN` — the full-screen walk-in instrument. Hides the face entirely; catastrophic if
  it shipped on.
- `OPENDUBS_DBG_PERCEPT` / `OPENDUBS_DBG_SERIAL` — perception preview, reticle, serial telemetry.
- `OPENDUBS_NO_MARK_VALID` — **the dangerous one.** It builds an image that never calls
  `esp_ota_mark_app_valid_cancel_rollback()`, which exists to *test* rollback. Shipped on, every
  OTA would revert after one boot and the head would look unflashable. It should arguably move out
  of `build.sh` entirely once rollback is trusted.

## Still temporary, needs a real decision before shipping

**`kCuesEnabled = false` in `cues.cpp`.** Audio is hard-disabled by a compile-time constant,
because the Mode tone was too loud and I was asked to stop the beeps. That was the right immediate
action and is the wrong permanent state: it is a compile-time lie about a runtime setting. **1.3b
is the real fix** — a beep enable and volume resident on the Tab5, settable from the dashboard, so
it behaves correctly with no link. The retuned Mode cue (950 Hz, 0.22 gain) has still never been
heard by anyone and is a guess.

**Plain-HTTP OTA (`CONFIG_ESP_HTTPS_OTA_ALLOW_HTTP=y`).** Parity with the Arduino firmware, but it
means anyone who can reach the head can tell it to pull firmware from any URL, on a network that
will have students and whose dashboard API has no authentication either. **The fix is signing, not
TLS**: `SECURE_SIGNED_APPS_NO_SECURE_BOOT` verifies the image itself, so it defends against a
hostile image however it arrived. Before student exposure.

**Build-time Wi-Fi credentials.** `OPENDUBS_WIFI_SSID/PASS` generate a header in the ephemeral
build tree only, never in the repo. A ship build must not carry them: `esp_wifi` persists to NVS
after one association, so provisioning is once per board.

## Not yet built at all — this is the gap to "the real thing"

The IDF image is a face plus perception. The Arduino firmware it replaces is a complete head
controller. Missing: **pan/tilt** (and therefore 1.6, turning to look), the **front ToF**, the
**lift control pages**, the **StamPLC `STATE` link** (which is what retires the state cycling
above), and the **touch UI** (written on `feat/tab5-touch-ui`, never built for the device or run).

## Working and verified, for the avoidance of doubt

OTA over the air with rollback proven by reverting a deliberately-broken image; Wi-Fi STA; the UDP
command port; motion detection at significant distance; face detection at close range; the IMU and
its self-motion gate threshold (measured, not reasoned).

## The release build

`build.sh release` must build with every instrument off, and **fail** if any diagnostic symbol
survives into the image. Two builds that differ only in a debug flag should be byte-identical when
that flag is off — that is the check, and it is the only way "absent, not quiet" stays true as
things are added.
