# Display devices: showing the stage page on a projector

**Status, 2026-10-21: the STAGE BOX (below) is the standard way to put the show on a screen, approved by
the user and built in `software/stage-pi/`, not yet run on a Pi. Route (a), the kiosk app on the projector's
Google TV (0.4.0, `display` role), is built and kept for that projector; not yet tried on it.** Tablet &
head interface lane.

## The stage box — the standard (approved 2026-10-21)

A Raspberry Pi 5 (a Pi 4 as the spare, one image for both) kept on the robot and plugged into whatever
screen is there: a projector, a video wall, a TV, within the robot's Wi-Fi range. A "dumb screen": a
full-screen browser on `/stage/`, playing the show's video **and its music** from one file, so they cannot
drift; the tablet keeps dialogue and voice. The user's decisions: 64-bit Raspberry Pi OS Lite, no desktop;
a "stage ready" card between shows (the page's), black once a show is armed; sound to a USB adapter if one
is plugged in, else HDMI, with a settings override; hostname `opendubs-stage`; AC power (the official
supply), with under-voltage reported to the page; the robot's Wi-Fi only, SSH key-only, no remote desktop.

How it is built (`software/stage-pi/`, README): `cage` + Chromium kiosk as its own user from a systemd unit;
the screen's EDID mode or a forced one (`stage.conf`, applied as the kernel's `video=`), a display change
restarting the browser (udev), a virtual output when no screen is attached so the sound still plays; a
read-only root (overlay); one settings file on the boot partition; a local agent on `127.0.0.1:8095`.

**The contract with the page** (agreed with the Choreography lane): the agent serves `/device.json`
(`device`, `name`, `model`, `audio_output` usb|hdmi|none, `audio_name`, `under_voltage_now`,
`under_voltage_since_boot`, `throttled_now`, `screen {connected, mode}`) and takes the page's heartbeat at
`POST /alive`, with CORS and the Private Network Access preflight answered. A page that stops checking in
for 30 s is restarted, backing off to 2 minutes. The page reports its device upward to the dashboard over
its own socket, so the dashboard's projector card can show it. The page itself (`/stage/`:
the show video and live skeletons) is the Choreography lane's.

The goal (the user, 2026-10-21): the projector (an Anker Nebula P1i, Google TV inside) shows the stage page,
**with no extra wires from the robot**. Every route below shows the same page from the same dashboard and
keeps time by the show's own clock over the dashboard's WebSockets, so the routes differ only in what runs
the browser.

## (a) The kiosk app on the projector — chosen, built

The robot tablet's kiosk app runs on Android TV / Google TV in a **`display` role** (`software/tablet-kiosk`
README, *Set up a display*): no self-updater (a set-up TV cannot make the app its device owner, so an update
would prompt on screen; `adb install` updates it instead), no sensor bridge, no lock task; keep-awake,
autoplay with sound, the waiting page and the heartbeat watchdog as on the tablet. A Google TV home-screen
tile lets the remote open it. The Thor provisions and launches it over **wireless adb**, after a one-time
pairing done with the remote.

**To check at the projector, with someone there** (nothing has been tried on it):

| check | pass |
|---|---|
| the APK installs and shows the page full-screen with sound | the stage page plays a show, music from the projector's speaker |
| wireless debugging after a **reboot** and after a **Wi-Fi change** | the Thor reconnects without pairing again. Android often turns wireless debugging off on both, which would make the Thor unable to relaunch the app; then the tile and the remote are the way back |
| the projector's own **power saving** (auto-off, eco or sleep timers) | the page stays up through a 30-minute idle; if not, the setting that stops it is recorded |
| the heartbeat | a page that stops drawing is reloaded within about 2 minutes |

## Fallbacks, in order

### (1) Cast to a public web receiver

The Choreography lane is testing it in parallel. No app on the projector at all; its limits are that lane's to report.

### (2) A small Linux box at the projector — now the stage box, the standard (above)

The user's idea: a small computer **on the robot's network**, with a **short HDMI cable to the projector
only** (no wire from the robot), that boots straight into a full-screen browser on `/stage/`. Like the
projector app, it just shows the page and keeps the show's time.

| question | plan |
|---|---|
| **board** | Choose by one test before buying more than one: play the show's own video file full-screen in Chromium at 1080p with the skeleton canvas over it, and read dropped frames in `chrome://media-internals`. Candidates: a **Raspberry Pi 5 (4 GB)** first; an **Intel N100-class mini PC** if the Pi drops frames (x86, hardware video decoding in Chrome). |
| **kiosk boot** | A minimal OS (Raspberry Pi OS Lite, or Debian on the mini PC) with **`cage`** (a Wayland compositor that shows one app full-screen) running **Chromium** in kiosk mode with `--autoplay-policy=no-user-gesture-required`, no error dialogs and no crash bubble, as a systemd service with `Restart=always`. No desktop, no login prompt, no screensaver (cage has none). |
| **the URL** | One line in `/etc/opendubs/display.env` (`STAGE_URL=http://<thor>:8080/stage/`), set at setup; the page retries by itself while the Thor is down. |
| **network** | The robot's Wi-Fi, or Ethernet to the robot's router where the projector stands close enough. |
| **keeping the page alive** | Chromium has no kiosk bridge, so the page's `OpenDubsKiosk.alive()` does nothing there. A small watchdog asks Chromium's local debugging port every 30 s whether the page still draws, and restarts the service if not. Same rule as the app's watchdog. |
| **power** | Its own supply at the projector (a Pi 5 needs 5 V 5 A; the projector's USB port is unlikely to supply that, to be checked). The root filesystem is read-only (an overlay), so pulling the plug at the end of a day corrupts nothing. |
| **updates** | By hand over SSH from the robot's computer (key only), never automatically: the image is fixed between deliberate updates. |
| **sound** | Over HDMI to the projector's speaker, which is where the show's music goes (Choreography's audio design). |
| **owner** | This lane: a display device like the tablet. A setup script and its README would live in `software/display-box/` when built. |

### (3) HDMI from the robot's computer — last resort

A cable from the Thor to the projector, with a full-screen browser on the Thor's own display. It is the one
route with a wire from the robot, and it brings back a local display the robot otherwise does without.
