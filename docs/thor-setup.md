# Thor setup

The **NVIDIA Jetson AGX Thor** is the robot's autonomy brain — it runs ROS 2, perception, and the
touchscreen dashboard. Set the Thor up **first**: it's the canonical host for flashing the StamPLC
firmware and for running the whole software stack. The StamPLC (the mobile base's edge controller — RC over
SBUS, drive mixer, safety, power/BMS) talks to the Thor over USB or Ethernet; there is no separate flight
controller.

## Baseline

| | |
|---|---|
| Board | Jetson AGX Thor Dev Kit |
| BSP / L4T | JetPack 7 / **L4T R38.4** (kernel 6.8-tegra, aarch64) |
| OS | Ubuntu 24.04 (Noble) |
| ROS | ROS 2 Jazzy Jalisco (Tier-1 with 24.04) |
| Operator display | none on the robot — an Android tablet browses the dashboard on :8080 |
| StamPLC | M5Stack, over USB → Ethernet |

## 1 · Flash the base OS

Jetsons are flashed with **NVIDIA SDK Manager**, which runs on an **x86_64 Ubuntu host** *or* on
**Windows** — the Windows installer runs SDK Manager under WSL2. macOS is not supported, so use a
Linux or Windows PC.

1. Install **NVIDIA SDK Manager** on the host (Ubuntu, or Windows via WSL2) and select **JetPack 7 (L4T R38.4)**.
2. Put the Thor into **force-recovery** (hold the recovery button while powering on) and connect it
   to the host over **USB-C**.
3. Flash the OS + components with SDK Manager (or `flash.sh` from the BSP). Pin JetPack 7 — don't let
   it pull a newer BSP.

## 2 · First boot

1. Complete the Ubuntu first-boot setup and create your dev user.
2. **Set a real hostname** (e.g. `opendubs-thor-01`) — it matters for multi-robot ROS and logs.
3. Bring up networking (wired and/or Wi-Fi) and enable **key-only SSH**; add an `ssh` host alias so
   the deploy step can reach it by name.
4. **Hold the L4T packages** so a routine upgrade can't break the CUDA/graphics stack — the most
   common way a working Jetson gets bricked:
   ```bash
   sudo apt-mark hold 'nvidia-l4t-*'
   ```
   Never `apt full-upgrade` blindly; move the BSP only via NVIDIA's flow.

## 3 · Deploy + provision from this repo

From a dev machine with the repo checked out:

```bash
./deploy-all.sh <ssh-host>     # default host: thor
```

This rsyncs the subsystems to `/opt/opendubs` and runs `provision-all.sh` (idempotent — re-run any
time to push updates). It sets up three subsystems:

- **Dashboard** — the FastAPI backend on :8080, which the operator's tablet browses (below).
- **ROS 2 bringup** — RPLIDAR drivers, rosbridge, Foxglove bridge, and the scan watchdogs.
- **Firmware tooling** — PlatformIO in `/opt/opendubs/piovenv` and `flash-from-thor.sh`, so you can
  flash the StamPLC directly from the Thor (see [Flash a StamPLC](flashing-stamplc.md)).

If the robot carries OpenArm arms, set them up afterwards with [OpenArm setup](openarm-setup.md).
The arms are an optional payload, so `provision-all.sh` doesn't include them.

## 4 · The operator interface

**The robot has no local display.** The dashboard is served on **:8080** and the operator opens it
from an **Android tablet** on the robot's network. Nothing needs to be installed on the robot for
this — no browser, no display manager, no X session.

An HDMI panel and a custom touch bridge were used previously and are retired: the tablet is
functionally equivalent, and dropping the panel frees a USB port. Provisioning no longer installs
any of it.

!!! note "Retiring it on a robot that was provisioned earlier"
    Removing the files from this repo does not undo what an older `provision.sh` already did on
    the machine. On a Thor that ran the kiosk, also disable the leftovers by hand:

    ```bash
    sudo systemctl disable --now opendubs-xorg-fix.service
    sudo chattr -i /etc/X11/xorg.conf && sudo rm -f /etc/X11/xorg.conf
    sudo rm -f /etc/X11/xorg.conf.d/99-opendubs-touch.conf
    sudo rm -f /home/kiosk/.config/autostart/opendubs-{dashboard,touch,vnc}.desktop
    ```

    Leave the `kiosk` **user** alone — `opendubs-dash.service` still runs as it. Then turn gdm
    autologin off in `/etc/gdm3/custom.conf` if nothing else wants it.

## 5 · Verify

`deploy-all.sh` prints a service-status readout at the end — expect `opendubs-dash` and the ROS
services **active**. Open the dashboard from the tablet to confirm it is serving. From here,
flash the StamPLC: [Flash a StamPLC](flashing-stamplc.md).

## Operating notes

- **GPU perception (Isaac ROS)** — run NVIDIA's GPU-accelerated ROS 2 packages from their **Docker
  containers**, not host `pip`: CUDA/cuDNN/TensorRT are pinned to the JetPack version. Confirm the
  Isaac ROS package set + container tags support your JetPack before building perception around it.
- **Power mode** — lock a fixed `nvpmodel` profile (and `jetson_clocks` if you need max clocks) for
  repeatable timing.
- **Time sync** — run `chrony`. If the robot's network has no internet, point NTP at the local
  router as the time source; the StamPLC has no reliable RTC, so its telemetry is timestamped on
  arrival at the Thor.
- **Multi-NIC DDS** — with wired + Wi-Fi both up, default ROS 2 multicast discovery can produce
  phantom/duplicate peers. Pin a fixed `ROS_DOMAIN_ID` and bind discovery to one interface (or use a
  discovery server / Zenoh router). Decide which NIC is the robot network.
- **Golden image** — once the Thor is in a known-good state, take one full host-side clone (NVMe +
  the QSPI bootloader, which lives separately) as a bare-metal restore / duplication seed. Re-take at
  milestones, not daily.
