# First USB flash of the robot's Tab5 — the exact procedure

The robot's head currently runs the **Arduino** firmware (`firmware/tab5_face`). This replaces it
with the ESP-IDF build, **over USB from a Mac**, writing the partition table and the Wi-Fi
credentials explicitly rather than depending on anything already on the board.

**This does not change the canonical flash path.** `firmware/tab5_face/ota-from-thor.sh`, the Thor,
the StamPLC and `CLAUDE.md`'s flash table are all untouched until the IDF image has been seen
working on the robot. Nothing in this document runs on the Thor.

---

## Before you start

**The Tab5 is powered from the e-stop-protected drive branch.** With the contactor open it has no
power — it is OFF, not broken, and the USB port will simply not appear. If `/dev/cu.usbmodem*` is
missing, that is the first and usually only thing to check. **This is a footnote, not a fault:** a
missing port here is not evidence of a firmware, cable or driver problem and must not turn into an
investigation.

**Nothing is erased.** The flash writes four regions only — bootloader `0x2000`, partition table
`0x8000`, otadata `0xe000`, app `0x10000`. It does **not** write `nvs` at `0x9000`, which is where
per-robot values live. Do **not** run `esptool erase_flash`: that would destroy them.

---

## 1. Build the image — WITHOUT touching Wi-Fi credentials

**Pass no credentials.** The robot's head already has the robot's network (`opendubs`) stored in
NVS, the flash does not write NVS, and with no build-time credentials the firmware uses what is
stored. That is the safe path and it needs no secret.

**Why this matters, and it is not hypothetical.** Build-time credentials do not fall back to NVS —
they **override** it. `net.cpp` logs exactly that: `provisioning '<ssid>' from build-time
credentials -- overriding the stored network`. So a stale `OPENDUBS_WIFI_SSID` left in a shell from
bench work would write a HOME network onto the robot, where it is meaningless, and the head would
sit retrying a network that is not present. Unset them deliberately:

```sh
cd firmware/tab5_idf
export OPENDUBS_DEMO=/path/to/M5Tab5-UserDemo        # see README.md
unset OPENDUBS_WIFI_SSID OPENDUBS_WIFI_PASS          # NOT optional -- see above
OPENDUBS_FACEDET=1 ./build.sh image
```

`OPENDUBS_FACEDET=1` is required — face detection is wanted on the robot and is NOT on by default.

Expect `flags: facedet=1` and an image of roughly 3.6 MB against a 5 MB slot.

**Confirm on the panel after flashing** that the status line reads `wifi: seeking 'opendubs'` (or
shows an `opendubs` IP), **not** `wifi: provisioning ...`. "provisioning" means build-time
credentials were compiled in and the stored network was overwritten.

## 2. Flash it

```sh
OPENDUBS_FACEDET=1 ./build.sh device
```

Expect four `Hash of data verified.` lines and `Done`. The board resets itself.

## 3. Confirm, in this order

1. **The face comes up.** OpenDubs wordmark on black, then closed eyes in the state colour, then
   the eyes open. No teal eyes, no white blob, no black square — if any of those appear, the
   startup fix did not land.
2. **Touch works.** Press and hold a full second: the Lift Module screen should appear. It reverts
   to the face after 30 s idle.
3. **Wi-Fi.** The status line on the panel carries the IP once associated.
4. **The StamPLC link.** The face should leave `NO LINK` once the StamPLC starts sending
   `STATE <NAME>` on UDP 4242.

## What will NOT work, by design

Do not read these as faults — they are unported, and they are the robot session's actual work list:

| | why |
|---|---|
| Pan / tilt | not ported from the Arduino firmware (8Servo `0x25`) |
| Front ToF | not ported (`0x29`) |
| Lift RAISE / LOWER, soft e-stop | the HMI renders them **greyed with a "NO COMMAND LINK" banner** — there is no outbound command transport to the StamPLC in this firmware yet |
| HEIGHT, FRONT ToF, relay/limit lamps | shown as unwired rather than as a fake zero |
| Audio cues | present but default OFF; enable from the HMI's SETTINGS page |

## If it does not boot

USB flash again. If the port is missing, check the contactor first (see above). The Arduino image
can be restored by checking out `firmware/tab5_face` and flashing it over USB the same way — which
is the reason the first flash is USB rather than OTA.
