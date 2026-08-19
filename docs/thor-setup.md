# Thor setup

The **NVIDIA Jetson AGX Thor** is the robot's autonomy brain — it runs ROS 2, perception, and the
touchscreen dashboard. Set the Thor up **first**: it's the canonical host for flashing the StampPLC
firmware and for running the whole software stack. The StampPLC (base controller — RC over SBUS,
drive mixer, safety, power/BMS) talks to the Thor over USB or Ethernet; there is no separate flight
controller.

## Baseline

| | |
|---|---|
| Board | Jetson AGX Thor Dev Kit |
| BSP / L4T | JetPack 7 / **L4T R38.4** (kernel 6.8-tegra, aarch64) |
| OS | Ubuntu 24.04 (Noble) |
| ROS | ROS 2 Jazzy Jalisco (Tier-1 with 24.04) |
| Touch panel | Hosyond 8.8″ **1920×480** HDMI (no-EDID; driven as a forced mode) |
| Base controller | M5Stack StampPLC over USB → Ethernet |

## 1 · Flash the base OS

Jetsons are flashed from an **x86_64 Ubuntu host** — macOS and Windows cannot flash a Jetson, so
you need a Linux PC (or a spare Ubuntu box).

1. Install **NVIDIA SDK Manager** on the Ubuntu host and select **JetPack 7 (L4T R38.4)**.
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

- **Dashboard + kiosk** — the FastAPI backend and the touchscreen kiosk (below).
- **ROS 2 bringup** — RPLIDAR drivers, rosbridge, Foxglove bridge, and the scan watchdogs.
- **Firmware tooling** — PlatformIO in `/opt/opendubs/piovenv` and `flash-from-thor.sh`, so you can
  flash the StampPLC directly from the Thor (see [Flash a StampPLC](flashing-stamplc.md)).

## 4 · Touchscreen + kiosk

Provisioning is what makes the panel light up and boot straight into the dashboard.

**The panel.** The Hosyond 8.8″ 1920×480 HDMI panel provides **no EDID**, so a forced HDMI
`xorg.conf` feeds it the correct mode. Two quirks the provisioner handles for you:

- **The Jetson reverts `xorg.conf` late in boot**, so the config is written to `/etc/X11/xorg.conf`
  and locked **immutable** (`chattr +i`), with a oneshot (`opendubs-xorg-fix.service`) that
  re-asserts it before the display manager starts.
- **Blanking/standby is disabled** — this combo panel powers its *touch* board from the display, so
  if the panel sleeps, touch stops too. The panel is rotated to 1920×480 landscape at runtime, and
  the touch coordinates are rotated to match.

**Kiosk boot.** A passwordless `kiosk` user is created and **auto-logs-in** (GDM, stock GNOME on
Xorg with Wayland disabled). GNOME autostart launches **Chromium fullscreen** on the dashboard, plus
the touch bridge and a VNC server for remote viewing. After provisioning, **reboot** (or
`sudo systemctl restart gdm`) and the panel comes up showing the dashboard within a few seconds.

!!! note "Working with the kiosk"
    - **The panel is 1920×480 — very wide and short.** Dashboard views must lay out horizontally.
    - **To reload the kiosk after a frontend change** (there's no keyboard on the robot), do it over
      SSH — focus the kiosk Chromium window and send `Ctrl+Shift+R` with `xdotool`.
    - **Panel stays black?** Check that `/etc/X11/xorg.conf` is present + immutable and
      `opendubs-xorg-fix.service` is enabled. **Touch not registering?** Watch raw events with
      `libinput debug-events` (touch may work even when `xinput` shows nothing).

## 5 · Verify

`deploy-all.sh` prints a service-status readout at the end — expect `opendubs-dash` and the ROS
services **active**. On the robot itself, the panel should be showing the live dashboard. From here,
flash the base controller: [Flash a StampPLC](flashing-stamplc.md).

## Operating notes

- **GPU perception (Isaac ROS)** — run NVIDIA's GPU-accelerated ROS 2 packages from their **Docker
  containers**, not host `pip`: CUDA/cuDNN/TensorRT are pinned to the JetPack version. Confirm the
  Isaac ROS package set + container tags support your JetPack before building perception around it.
- **Power mode** — lock a fixed `nvpmodel` profile (and `jetson_clocks` if you need max clocks) for
  repeatable timing.
- **Time sync** — run `chrony`. If the robot's network has no internet, point NTP at the local
  router as the time source; the StampPLC has no reliable RTC, so its telemetry is timestamped on
  arrival at the Thor.
- **Multi-NIC DDS** — with wired + Wi-Fi both up, default ROS 2 multicast discovery can produce
  phantom/duplicate peers. Pin a fixed `ROS_DOMAIN_ID` and bind discovery to one interface (or use a
  discovery server / Zenoh router). Decide which NIC is the robot network.
- **Golden image** — once the Thor is in a known-good state, take one full host-side clone (NVMe +
  the QSPI bootloader, which lives separately) as a bare-metal restore / duplication seed. Re-take at
  milestones, not daily.
