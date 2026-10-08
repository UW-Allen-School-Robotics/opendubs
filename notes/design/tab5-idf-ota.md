# Phase 4 — OTA and flashing for the IDF Tab5 build

Written 2026-10-05. Everything below is read from the pinned ESP-IDF 5.4.2 tree and the demo's own
sources, not recalled. **Nothing here has been tested over the air** — the bench Tab5 is on USB and
not associated with any network, so the one thing that cannot be checked yet is the only thing that
matters in the end. Said plainly up front so nobody reads this as "done".

## The answer to "does the IDF route change flashing?"

**The architecture does not change at all. Only the firmware-side implementation does.**

The existing `firmware/tab5_face/ota-from-thor.sh` does not use ArduinoOTA — it was abandoned
because its listener sits behind the ESP-Hosted co-processor and fails silently. What it does
instead is:

```
Thor: build → serve the .bin over a throwaway HTTP server
Thor: UDP :4242 → "OTA http://<thor>:8000/firmware.bin"
head: HTTP GET → write to the inactive slot → reboot
Thor: watch it come back
```

**That is a pull, driven by a text command on a UDP socket, and it maps onto ESP-IDF one-for-one.**
The host script's structure, its port numbers, its contactor pre-check and its recovery advice are
all still correct. The parts that change are the build command (`pio run` → `idf.py build`) and the
binary path (`.pio/build/tab5/firmware.bin` → `build/m5stack_tab5.bin`).

## Prerequisites — all now satisfied

| need | status |
|---|---|
| two app slots + otadata | **done.** `firmware/tab5_idf/partitions.csv`: `ota_0`/`ota_1` at 5 MB each, `otadata` at 0xe000. The build emits `ota_data_initial.bin`, which is the 4th section the flash now writes. Image is 2.8 MB → 44% headroom in a slot. |
| `app_update` (`esp_ota_*`) | present in IDF 5.4.2. |
| `esp_http_client`, `esp_https_ota` | both present. Plain HTTP works; TLS is not required for a link-local pull. |
| Wi-Fi on the P4 at all | **present and proven.** `espressif__esp_hosted` + `espressif__esp_wifi_remote` are in `managed_components/`, and `platforms/tab5/main/hal/components/hal_wifi.cpp` brings up a working AP through them. This is the question that would have sunk the whole plan, and it is answered by existing vendor code rather than by us. |
| NVS preserved across the switch | `nvs` is pinned at 0x9000/0x5000, byte-for-byte matching the Arduino `default_16MB.csv`, so anything already stored there survives. |

## API mapping

| Arduino (current) | ESP-IDF | note |
|---|---|---|
| `HTTPClient` + `Update.begin/write/end` | `esp_https_ota_begin` → `esp_https_ota_perform` → `esp_https_ota_finish` | — |
| `Update.writeStream()` | **do not use the blocking form** | see below |
| `ESP.restart()` | `esp_restart()` | — |
| `WiFi.status() == WL_CONNECTED` | `esp_wifi_sta_get_ap_info()` or an `IP_EVENT_STA_GOT_IP` flag | — |

**The stall deadline must survive the port, and `esp_https_ota` is shaped to allow it.** The Arduino
version carries a hard-won comment: `Update.writeStream()` blocks forever if the source goes quiet,
and killing the serving side mid-download once left the head wedged on "OTA: writing" —
network task and all — recoverable only by pulling power. The lesson was written down as *nothing
that writes firmware should be able to hang the device it runs on.*

`esp_https_ota_perform()` is the non-blocking form: it returns `ESP_ERR_HTTPS_OTA_IN_PROGRESS` and
expects to be called again. So the loop stays ours, and so does the deadline:

```
handle = esp_https_ota_begin(...)
loop:
    err = esp_https_ota_perform(handle)
    if err != ESP_ERR_HTTPS_OTA_IN_PROGRESS: break
    if no progress for kStallMs:  esp_https_ota_abort(handle); fail
esp_https_ota_finish(handle)
```

Use `esp_https_ota_get_image_len_read()` as the progress signal, exactly as `written` is used today.
**Do not reach for the one-shot `esp_https_ota()` convenience call** — it hides the loop, and hiding
the loop is how the original bug happened.

## The one piece of genuinely new work: station mode

`hal_wifi.cpp` implements **AP** mode. OTA needs the head to be a **station** on the robot's
network. That is `esp_wifi_set_mode(WIFI_MODE_STA)` + `esp_wifi_connect()` with credentials — not
hard, but it is new code, and it needs credentials, which is why it stops here.

Credentials belong in **NVS**, not in the source. Two reasons beyond the obvious: a per-robot value
must never be committed (`notes/per-robot-calibration.md`), and NVS at 0x9000 is preserved by the
new partition table specifically so provisioned values survive a firmware switch.

A possible shortcut, flagged as **unverified speculation**: Arduino's WiFi library is a wrapper over
`esp_wifi`, and `esp_wifi` persists its own config to NVS under the `nvs.net80211` namespace. If the
Arduino build ever associated this board, those credentials may already be in the NVS we preserved,
and `esp_wifi_set_config` with `WIFI_STORAGE_FLASH` may pick them up with no provisioning at all.
That would be convenient and it is cheap to test. It is also exactly the kind of neat theory that has
been wrong before in this project, so it should be *tested*, not assumed, and the explicit
provisioning path should be built regardless.

## Finding: rollback is off, and that matters more here than usual

```
# CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE is not set
```

With two slots and no rollback, an image that **flashes and boots but is broken** has no automatic
way back. The old slot still physically holds the previous firmware, but nothing switches to it.

This is worse on the head than it sounds, for a reason that is already written down in `CLAUDE.md`:
**the Tab5 is powered from the e-stop protected drive branch.** When the contactor is open it has no
power — it is off, not offline. So "just reflash over USB" means closing the contactor, finding a
cable and physically reaching the head.

`esp_ota_mark_app_valid_cancel_rollback()` and `esp_ota_mark_app_invalid_rollback_and_reboot()` are
both available. **But enabling the Kconfig option without also calling `mark_app_valid` is a
footgun**: every new image would then be reverted after one boot, which is a self-inflicted
unflashable head. The two changes are a single decision and must land together, with a bench test
that deliberately OTAs a knowingly-broken image and confirms the revert.

**Recommendation: do it, as one paired change, on the bench, before the head ever runs IDF
firmware on the robot.** Not now — it cannot be tested now.

## Does `CLAUDE.md`'s flash table need updating?

**Eventually yes, and deliberately not yet.** That table exists because these procedures were
re-derived from first principles repeatedly, at real cost — once resetting the robot's safety
controller twice. Its authority comes from every row being a procedure that is *known to work*.

Adding an IDF row that has never completed an over-the-air flash would put an untested procedure in
the one document that is supposed to be trusted without re-checking, which damages exactly the
property that makes it useful. The IDF bench procedure is documented in
`firmware/tab5_idf/README.md`, where it is clearly bench-scoped. It graduates to `CLAUDE.md` when an
OTA has actually succeeded end to end.

## Checklist for when a network is available

1. Bring up STA mode; confirm an IP. (Test the NVS-credential shortcut here — it is one call.)
2. Add the UDP :4242 command listener; `REBOOT` first, because it is the recovery path and the
   simplest thing to get right.
3. Add `OTA <url>` with the non-blocking loop and the stall deadline above.
4. OTA a known-good image. Confirm the slot alternates (`esp_ota_get_running_partition()`).
5. Enable rollback **and** `mark_app_valid` together; OTA a deliberately-broken image; confirm the
   revert. Do this on the bench, on USB, where recovery is free.
6. Update `firmware/tab5_face/ota-from-thor.sh` for the new build command and binary path.
7. Only then add the row to `CLAUDE.md`.
