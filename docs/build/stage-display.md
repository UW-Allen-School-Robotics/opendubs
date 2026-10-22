# Stage Display Box

!!! warning "In development"
    The software is written and checked on a desk, but not yet run on a Raspberry Pi. The parts
    list and what the box does are settled. The setup steps live with the software, in
    [`software/stage-pi/README.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/software/stage-pi/README.md),
    and are not repeated here.

!!! info "At a glance"
    | | |
    |---|---|
    | **You'll build** | A Raspberry Pi that shows the robot's show on a projector, TV or any HDMI screen |
    | **Time** | *TODO*, once it has been built on a Raspberry Pi |
    | **Difficulty** | Beginner (image a card, run one script over SSH) |
    | **Prerequisites** | A working robot on its own Wi-Fi network; a computer with an SSH key |
    | **Mates to** | Nothing on the robot. It rides on the robot and plugs into a screen and mains power |
    | **Optional?** | Yes. The robot performs shows without it, with sound from the head tablet |

## What this is

A show can have a picture as well as motion and sound: a background video, and live cartoon
skeletons of the people dancing in front of the robot. The **stage display box** puts that
picture on a big screen. It is a "dumb screen": a Raspberry Pi that boots straight into a
full-screen browser on the robot's stage page, and nothing else.

- **Any screen.** A projector, a TV, a building's video wall: anything with an HDMI input. The
  box uses the screen's own preferred resolution, or one you set.
- **No wire back to the robot.** The box talks to the robot over the robot's Wi-Fi, so it can sit
  by the screen anywhere within range. It is carried on the robot and plugged in where the show
  is.
- **Picture and music from one file.** The box plays the show's video and its music together, so
  they cannot drift apart. The head tablet keeps the dialogue and the robot's voice.
- **Plays without Wi-Fi.** While the robot is idle, the box copies its show videos onto a USB
  stick, so a show plays even if the Wi-Fi drops. It never copies during a show.
- **Pull the plug any time.** The system runs read-only, so cutting the power corrupts nothing.
- **Skeletons only.** The skeletons come from a depth camera on the robot as joint positions. No
  camera image is shown, streamed or recorded.

## Bill of materials

!!! note "Generated from `hardware/parts.csv`"
    Edit the CSV (rows tagged `module = stage-display`), then run `tools/bom.py --inject`. These
    parts are optional, so they are left out of the robot's cost total.

<!-- BOM-AUTO:build:stage-display -->
**Stage display box**

| Part | SKU | Qty | Notes |
|---|---|---:|---|
| [Raspberry Pi 5, 4 GB](https://www.raspberrypi.com/products/raspberry-pi-5/) | — | 1 | Optional, for shows. The box's computer. 4 GB is plenty. A Raspberry Pi 4 runs the same image and makes a good spare. |
| [Raspberry Pi 27 W USB-C power supply](https://www.raspberrypi.com/products/27w-power-supply/) | — | 1 | Optional, for shows. The official supply for the Pi 5; a weaker one shows as an under-voltage warning on the stage page. A Pi 4 takes the official 15 W one. |
| [Raspberry Pi Active Cooler (for Pi 5)](https://www.raspberrypi.com/products/active-cooler/) | — | 1 | Optional, for shows. Clips onto the Pi 5. |
| [High-endurance microSD card, 32 GB or more](https://www.sandisk.com/products/memory-cards/microsd-cards/sandisk-high-endurance-uhs-i-microsd) | — | 1 | Optional, for shows. Holds the system, which runs read-only. A high-endurance card is made for constant use. |
| [Micro-HDMI to HDMI cable, short](https://www.raspberrypi.com/products/micro-hdmi-to-standard-hdmi-a-cable/) | — | 1 | Optional, for shows. The Pi's HDMI ports are micro-HDMI. Use HDMI 0, the port nearest the USB-C power. |
| USB 3 flash drive, 32 GB or more | — | 1 | Optional, for shows. The box's own copies of the show videos, so a show plays without Wi-Fi. The setup erases it. Plug it into a blue (USB 3) port. |
| [Sabrent USB external stereo sound adapter](https://sabrent.com/products/au-mmsa) | `AU-MMSA` | 1 | Optional, for shows. Any class-compliant USB audio adapter works. Sends the show's music to a powered speaker instead of the screen. |
| 3.5 mm stereo audio cable | — | 1 | Optional, for shows. From the USB audio adapter to a powered speaker's aux input. |
| 3.5 mm ground-loop isolator | — | 1 | Optional, for shows. Only if the speaker hums. Fits inline on the audio cable. |
<!-- /BOM-AUTO:build:stage-display -->

You also need a **powered speaker with an aux (3.5 mm) input** if the screen's own speaker is not
the one to use, and an **HDMI screen** at the venue. A **Raspberry Pi 4** runs the same image and
makes a good spare; it takes the official 15 W supply instead of the 27 W one.

## Set it up

Follow [`software/stage-pi/README.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/software/stage-pi/README.md).
In outline:

1. Image the microSD card with Raspberry Pi OS Lite (64-bit), with your SSH key and the robot's
   Wi-Fi network set in Raspberry Pi Imager. The Wi-Fi password is entered there, never stored in
   the repository.
2. Boot it on the robot's network, copy the `software/stage-pi` folder onto it, and run its setup
   script with the USB stick plugged in. **The setup erases the stick.**
3. Reboot. The screen shows the stage page's "stage ready" card between shows.

Settings (the robot's address, a forced resolution, which output plays the sound) are in one file,
`stage.conf`, on the card's boot partition, which any computer can edit.

## Verify — done when…

- [ ] After a reboot, the screen shows the "stage ready" card, with no desktop or login prompt.
- [ ] The card shows no under-voltage warning. If it does, use the official supply for that model.
- [ ] A show's video and music play on the screen and its speaker; dialogue plays on the head tablet.
- [ ] A show still plays with the box's Wi-Fi switched off, once the box has had time to copy the videos.

## Design notes

- [`notes/design/display-devices.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/notes/design/display-devices.md):
  why a Raspberry Pi, and how the box is built.
- [`notes/design/choreo-video.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/notes/design/choreo-video.md):
  video in a show, and which output plays it.
- [`notes/design/choreo-stage.md`](https://github.com/UW-Allen-School-Robotics/opendubs/blob/main/notes/design/choreo-stage.md):
  the stage page and its live skeletons.
