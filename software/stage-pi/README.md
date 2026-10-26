# The stage box

A small computer that **shows the robot's stage page on any screen**: a projector, a building's video wall,
a TV. It is a "dumb screen". It boots straight into a full-screen browser on the robot's `/stage/` page
(the show's background video and the live skeletons, by the Choreography lane) and plays the **show's video
and its music** from one file, so the two cannot drift apart. The robot's tablet keeps the dialogue and the
voice. The music plays through **one output you choose**: the screen's speaker, a USB audio adapter, a
Bluetooth speaker, or (on a Raspberry Pi 4) the 3.5 mm jack. The box stays **on the robot** and is taken off to plug into a screen within the robot's Wi-Fi range,
with no wire back to the robot.

The design and why: `notes/design/display-devices.md`. Owned by the Tablet & head interface lane.

**Status:** the first box ran on the robot from 2026-10-22. **The sound outputs (PipeWire and Bluetooth,
2026-10-25) are checked on a desk only, not yet on a box.**

## What it does

| | |
|---|---|
| **boots into the page** | no desktop, no login prompt: `cage` (a compositor that shows one app full screen) running Chromium in kiosk mode, as its own `stage` user. If the robot is not up yet, it waits for it rather than showing an error page. |
| **any screen** | the screen's preferred mode from its EDID, or a mode forced in the settings. Plugging in, unplugging or swapping a screen restarts the browser at the new screen's mode. **With no screen at all it still plays the show's sound.** |
| **sound** | one output for the show's music, chosen on the dashboard (Shows page, **Stage box sound**): the screen's speaker (HDMI), a USB audio adapter, a Bluetooth speaker, or the 3.5 mm jack (a Pi 4 only), or `auto`. See *Sound*. |
| **a watchdog** | the page checks in every 5 s; a page that goes quiet for 30 s is restarted, backing off to 2 minutes. systemd restarts the browser whenever it ends. |
| **tells the page about itself** | `http://127.0.0.1:8095/device.json`, loopback only: the sound output, the screen and its mode, **under-voltage** from the Pi's own power monitor (so the page's ready card can warn about a weak supply), and how many show videos it holds. `/media.json` has the sync in detail. |
| **keeps its own copies of the show videos** | like a streaming service's downloads: while the robot is idle, the box copies the robot's show videos (then its songs) onto a **USB stick**, newest first, so a show plays with **no Wi-Fi**. The page asks the box for a copy by its content hash and streams from the robot only when the box has none. Never during an armed or running show; each file is checked against the robot's checksum before it is used; a stick that fills up drops the oldest first. |
| **pull the plug any time** | the system is read-only, with an overlay in RAM, so cutting the power corrupts nothing. The USB stick is the one writable part: a file cut off by a power cut is thrown away and fetched again, and a stick that will not mount is repaired, or reformatted and filled again from the robot. It is never needed to boot. |
| **locked down** | the robot's Wi-Fi only, SSH with a key only, no remote desktop of any kind. Bluetooth is on for a speaker, but never discoverable: the box pairs only when the dashboard asks it to, and only with the speaker chosen there. |

## What to buy

| part | why |
|---|---|
| **Raspberry Pi 5** (the standard; 4 GB is plenty), a **Raspberry Pi 4** as the spare; one image runs on both | |
| **a USB stick, 32 GB or more** (a USB 3 one, plugged into a blue port) | the box's own copies of the show videos; it lives in the Pi and is erased by the setup |
| **the official 27 W USB-C supply** for the Pi 5 (the 15 W one for a Pi 4) | AC is the power source; a weaker supply shows as under-voltage on the page |
| **a Pi 5 active cooler** | |
| **a high-endurance microSD card**, 32 GB or more | |
| **a micro-HDMI to HDMI cable**, short | the Pi's HDMI ports are micro-HDMI; use the port nearest the USB-C power (HDMI 0) |
| **a class-compliant USB audio adapter** (e.g. Sabrent AU-MMSA), and a 3.5 mm cable into the speaker's aux input (e.g. a Soundcore Boom 2 Plus) | when the screen's own speaker is not the one to use. **A Raspberry Pi 5 has no 3.5 mm jack**, so on a Pi 5 a wired speaker goes through this adapter |
| **or a Bluetooth speaker** (e.g. the same Soundcore Boom 2 Plus) | no cable; it adds about 0.2 s of delay, which the show allows for |
| **a ground-loop isolator** on that 3.5 mm cable, only if it hums | |

## Make one

1. **Image the card** with Raspberry Pi Imager: **Raspberry Pi OS Lite (64-bit)**. In its settings:
   - hostname `opendubs<N>-stage`, after its robot (`opendubs2-stage` for opendubs2): one box per
     robot, so each is told apart on the network. The setup keeps this name;
   - a user (any name; `opendubs<N>` by the same rule) **and your SSH public key**;
   - the robot's Wi-Fi network and its password;
   - SSH on.

   The Wi-Fi password is entered there and **never goes in the repo**. Ask the Network lane for the box's
   reserved address on the robot's router.
2. **Boot it on the robot's network**, then copy this folder onto it and run the setup:

   ```bash
   scp -r software/stage-pi <user>@opendubs<N>-stage.local:
   ```

   With the USB stick plugged in (find it with `lsblk`: the disk whose TRAN is `usb`):

   ```bash
   ssh <user>@opendubs<N>-stage.local sudo ./stage-pi/setup.sh --media /dev/sda --erase-media
   ```

   `--media` **erases that stick** and makes it the box's video store; it refuses a disk that is not
   USB, or the one the system runs from, and needs `--erase-media` to go ahead. Without `--media` the
   box works too, streaming every show from the robot. It installs the browser and the box's programs, sets the name, turns password logins off, sets up the sound (PipeWire and Bluetooth),
   starts the browser at boot, and makes the root read-only. It keeps the name the card was imaged
   with (`--name <name>` changes it), and refuses a card still called `raspberrypi`. It refuses to run without your SSH key in
   place, since it turns password logins off.
3. **Reboot.** The screen shows the stage page (its "stage ready" card between shows).

## Settings

One file on the card's boot partition, **`stage.conf`**, editable from any computer (the partition is FAT).
`stage.conf.example` has every setting and its default:

- `ROBOT_URL` and `ROBOT_FALLBACK`: the robot's page, by name, then by address;
- `MODE`: empty for the screen's own mode, or forced, e.g. `1920x1080@60`;
- `AUDIO`: where the music goes until the dashboard sets it: `auto`, `hdmi`, `jack`, `usb` or `bt`.

The box reads it at every start, so take the card out, edit, put it back, and power on.

## Sound

The show's music and sound play from the box, through **one output**. The robot's dialogue always plays on
its head tablet. Set it on the dashboard: **Shows** page, **Stage box sound** card.

| output | what | added delay (about) |
|---|---|---|
| **HDMI** | the screen's own speaker (a TV, a projector) | 40 ms |
| **USB** | a USB audio adapter, cabled to a speaker's aux input | 20 ms |
| **Bluetooth** | a Bluetooth speaker paired to the box | 200 ms |
| **jack** | the Pi's 3.5 mm jack, cabled to a speaker's aux input: **a Raspberry Pi 4 only** | 20 ms |
| **auto** | HDMI if the screen says it has a speaker, else USB if plugged in, else the Bluetooth speaker if it is connected, else the jack | |

A building's video wall usually has no speaker and says so, so `auto` plays elsewhere. The card shows what
is **actually** playing where: a chosen output that is not there (a speaker switched off) falls back the
`auto` way, and the card says so. The stage page is told each output's delay, so the music stays on the
beat; the stage audio lead on the projector card fine-tunes it by ear.

**Pair a Bluetooth speaker:** switch it on and put it in pairing mode (on a Soundcore Boom 2 Plus, hold its
Bluetooth button until the light flashes quickly). On the card, press **PAIR SPEAKER**, wait about 15 s, then
press **PAIR** with the speaker's name. The box keeps one speaker, and reconnects it whenever it is on.

**TEST SOUND** plays two short beeps through the output in use. The output, pairing and the test are
refused while a show is loaded or playing; the volume (one per output) can change any time.

**What is kept:** the output, the volumes and the paired speaker are kept on the media stick, so they
survive a reboot and a re-imaged card. With no stick they last until the box is switched off.

Under the hood: PipeWire with WirePlumber (Raspberry Pi OS's own sound stack) in the `stage` user's session,
with Bluetooth through bluez; `bin/stage_sound.py` sets PipeWire's default output, so the change is live and
the page never reloads. The box pulls the dashboard's commands from the robot (`/api/stage/box/report`),
so it still opens no port to the network.

## Updating

The root is read-only, so turn the overlay off first, then update, then turn it back on:

```bash
ssh <user>@opendubs<N>-stage.local sudo raspi-config nonint do_overlayfs 1
```

Reboot, copy the new folder over and run `sudo ./stage-pi/setup.sh` again, then reboot. Nothing updates
itself: the box stays as it was set up until someone does this.

## When something is wrong

| you see | it means |
|---|---|
| a black screen after boot | the browser is waiting for the robot: is the robot on, and is the box on its Wi-Fi? |
| the page's card warns of **under-voltage** | the supply is too weak: use the official one for that model |
| no sound | the dashboard's **Stage box sound** card says where it is playing. With `auto`, a screen that says it has a speaker takes the sound, then an adapter plugged in. Either micro-HDMI port works: the sound follows the one a screen is on |
| the Bluetooth speaker paired but is silent | is it switched on and in range? The card says "paired, not connected" until it is; the box retries every 20 s |
| a console instead of the page after the screen was replugged | a box set up before 2026-10-22 gave up restarting its browser: power-cycle it, then update it (Updating) |
| the wrong resolution | force one with `MODE` |
| a show stutters | the box had no copy of that show's video and the Wi-Fi was slow: leave it idle on the robot's network a while to copy the videos, and check `device.json`'s `media` |
