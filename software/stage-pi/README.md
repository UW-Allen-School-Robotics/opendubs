# The stage box

A small computer that **shows the robot's stage page on any screen**: a projector, a building's video wall,
a TV. It is a "dumb screen". It boots straight into a full-screen browser on the robot's `/stage/` page
(the show's background video and the live skeletons, by the Choreography lane) and plays the **show's video
and its music** from one file, so the two cannot drift apart. The robot's tablet keeps the dialogue and the
voice. The box stays **on the robot** and is taken off to plug into a screen within the robot's Wi-Fi range,
with no wire back to the robot.

The design and why: `notes/design/display-devices.md`. Owned by the Tablet & head interface lane.

**Status: written and checked on a desk (the agent, the settings parser, the scripts' syntax and
shellcheck), not yet run on a Raspberry Pi.** The first build on real hardware is done with someone at it.

## What it does

| | |
|---|---|
| **boots into the page** | no desktop, no login prompt: `cage` (a compositor that shows one app full screen) running Chromium in kiosk mode, as its own `stage` user. If the robot is not up yet, it waits for it rather than showing an error page. |
| **any screen** | the screen's preferred mode from its EDID, or a mode forced in the settings. Plugging in, unplugging or swapping a screen restarts the browser at the new screen's mode. **With no screen at all it still plays the show's sound.** |
| **sound** | `auto`: a USB audio adapter if one is plugged in, else the HDMI screen. The settings can force either. |
| **a watchdog** | the page checks in every 5 s; a page that goes quiet for 30 s is restarted, backing off to 2 minutes. systemd restarts the browser whenever it ends. |
| **tells the page about itself** | `http://127.0.0.1:8095/device.json`, loopback only: the sound output, the screen and its mode, and **under-voltage** from the Pi's own power monitor, so the page's ready card can warn about a weak supply. |
| **pull the plug any time** | the root filesystem is read-only, with an overlay in RAM, so cutting the power corrupts nothing. |
| **locked down** | the robot's Wi-Fi only, SSH with a key only, no remote desktop of any kind, Bluetooth off. |

## What to buy

| part | why |
|---|---|
| **Raspberry Pi 5** (the standard; a **Raspberry Pi 4** is the spare, and one image runs on both) | |
| **the official 27 W USB-C supply** for the Pi 5 (the 15 W one for a Pi 4) | AC is the power source; a weaker supply shows as under-voltage on the page |
| **a Pi 5 active cooler** | |
| **a high-endurance microSD card**, 32 GB or more | |
| **a micro-HDMI to HDMI cable**, short | the Pi's HDMI ports are micro-HDMI; use the port nearest the USB-C power (HDMI 0) |
| **a class-compliant USB audio adapter** (e.g. Sabrent AU-MMSA), and a 3.5 mm cable into the speaker's aux input (e.g. a Soundcore Boom 2 Plus) | when the screen's own speaker is not the one to use |
| **a ground-loop isolator** on that 3.5 mm cable, only if it hums | |

## Make one

1. **Image the card** with Raspberry Pi Imager: **Raspberry Pi OS Lite (64-bit)**. In its settings:
   - hostname `opendubs-stage`;
   - a user (any name) **and your SSH public key**;
   - the robot's Wi-Fi network and its password;
   - SSH on.

   The Wi-Fi password is entered there and **never goes in the repo**. Ask the Network lane for the box's
   reserved address on the robot's router.
2. **Boot it on the robot's network**, then copy this folder onto it and run the setup:

   ```bash
   scp -r software/stage-pi <user>@opendubs-stage.local:
   ```

   ```bash
   ssh <user>@opendubs-stage.local sudo ./stage-pi/setup.sh
   ```

   It installs the browser and the box's programs, sets the name, turns password logins and Bluetooth off,
   starts the browser at boot, and makes the root read-only. It refuses to run without your SSH key in
   place, since it turns password logins off.
3. **Reboot.** The screen shows the stage page (its "stage ready" card between shows).

## Settings

One file on the card's boot partition, **`stage.conf`**, editable from any computer (the partition is FAT).
`stage.conf.example` has every setting and its default:

- `ROBOT_URL` and `ROBOT_FALLBACK`: the robot's page, by name, then by address;
- `MODE`: empty for the screen's own mode, or forced, e.g. `1920x1080@60`;
- `AUDIO`: `auto`, `usb` or `hdmi`.

The box reads it at every start, so take the card out, edit, put it back, and power on.

## Updating

The root is read-only, so turn the overlay off first, then update, then turn it back on:

```bash
ssh <user>@opendubs-stage.local sudo raspi-config nonint do_overlayfs 1
```

Reboot, copy the new folder over and run `sudo ./stage-pi/setup.sh` again, then reboot. Nothing updates
itself: the box stays as it was set up until someone does this.

## When something is wrong

| you see | it means |
|---|---|
| a black screen after boot | the browser is waiting for the robot: is the robot on, and is the box on its Wi-Fi? |
| the page's card warns of **under-voltage** | the supply is too weak: use the official one for that model |
| no sound | check `AUDIO` in `stage.conf`, and the adapter's cable; with `auto`, an adapter plugged in takes the sound from the screen |
| the wrong resolution | force one with `MODE` |
