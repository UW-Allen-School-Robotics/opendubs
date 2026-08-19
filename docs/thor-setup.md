# Thor AGX Setup — Provisioning, Disk Strategy, and ROS2

Status: decisions recorded, June 2026. Companion: `control-architecture.md` (Pixhawk/StampPLC
split), `control-authority.md` (who may command actuators), `system-overview.md` (Thor/edge
split), `../rc_serial_probe/docs/telemetry-spec.md` (topic data model).

The **NVIDIA Jetson AGX Thor Dev Kit** is the robot-level autonomy brain — perception,
planning, behavior, and supervision — sitting above the **Pixhawk 6C** pilot and the
**M5StampPLC** plant controller. This page records how we provision, back up, and run
software on the Thor, and the ROS2 plan that rides on top. Nothing safety-critical runs
here: ROS2 is not hard real-time, and the safety authority stays in hardware + StampPLC per
`control-authority.md`.

## As-provisioned baseline (record before changing anything)

| Property | Value |
|---|---|
| Board | Jetson AGX Thor Dev Kit |
| BSP / L4T | **R38.4.0** (JetPack 7) |
| OS | Ubuntu 24.04 (Noble), kernel 6.8-tegra, aarch64 |
| Compute | 14 cores, 122 GiB RAM |
| Storage | 953 GB NVMe (`nvme0n1`), single ext4 root on `p1` (~28 GB used), EFI on `p4` |
| Bootloader | A/B redundant slots (capsule update), currently slot **A** |
| Network | wired + WiFi on the dev LAN (per-unit IPs; DHCP-reserved) |
| Access | key-only SSH as the dev user (per-unit key + `ssh` alias) |

The **bootloader lives in QSPI-NOR, separate from the NVMe.** A live image of the NVMe
captures the OS and apps but not QSPI; a complete bare-metal clone needs both (see below).

## Backup / rollback / replace / duplicate — three needs, three tools

These are distinct problems. One giant `dd` image solves none of them well. **Decision: code +
containers + snapshots**, with an occasional full clone as the bare-metal seed.

| Need | Tool | Why |
|---|---|---|
| **Rollback** (undo a bad change) | Filesystem snapshots (Btrfs/`timeshift`) | Seconds to take/revert, local |
| **Replace** (board dies → new HW) | Full golden image (NVMe **+ QSPI**) + provisioning script | Restore to identical board |
| **Duplicate** (build robot #2) | Same golden image, per-unit config parameterized | Hostname/IP/calibration differ |

### Layered strategy (committed)

1. **Base OS = pinned, not snowflaked.** BSP version recorded above. Treat the flashed base
   as disposable; everything on top is **code** — a provisioning script (shell/Ansible) in
   this repo. If the machine can be re-derived from git, "replace" and "duplicate" stop
   depending on a fragile binary blob.
2. **App layer = Docker containers.** ROS2 + perception run in containers off NVIDIA's Thor
   base images. The image is the versioned, reproducible artifact (push to a registry, pull
   onto any robot). Rollback of the app = pull the previous tag. This is also how Isaac ROS
   ships.
3. **Rollback safety net = snapshots.** Root is ext4 today with ~861 GB free. Either convert
   root to **Btrfs** (then `snapper`/`timeshift` give near-instant pre-change snapshots) or
   take periodic **Clonezilla/`dd` images of `nvme0n1p1`** to spare space or a NAS. For a
   robot under active development, prefer Btrfs+snapshots for the "that update broke things"
   case.
4. **Full golden image = occasional.** After reaching a known-good state, take one complete
   host-side clone (QSPI+NVMe) as the bare-metal restore / duplication seed. Re-take at
   milestones, not daily.

Summary: **snapshots for daily rollback, containers + provisioning script for reproducibility,
one periodic full clone for bare-metal/duplication.**

### Host constraint for the full clone

The complete, restore-to-bare-metal clone (QSPI + NVMe together) uses NVIDIA's host-side flow:
an **x86_64 Ubuntu host** running the BSP, Thor in **force-recovery over USB-C**, then
`flash.sh` clone / `tools/backup_restore/l4t_backup_restore.sh`. **macOS cannot run this** —
the dev machine is darwin. Options: a cheap x86 Ubuntu box, or a Linux x86 VM with USB
passthrough (finicky in recovery mode). On-device Clonezilla/`dd` of the NVMe covers
rollback/duplication of the *software* state without a host, but not QSPI — acceptable as long
as the BSP version is recorded so QSPI can be reflashed if ever needed.

## ROS2 plan

**Distro: ROS 2 Jazzy Jalisco** — the Tier-1 LTS paired with Noble 24.04 (supported to 2029).
Do not mix in Humble (targets 22.04) outside a container.

### How ROS2 maps onto the committed control architecture

Per `control-architecture.md`, the Thor supervises a **Pixhawk pilot** and a **StampPLC plant
controller**. Two bridges, not one:

- **Thor ↔ Pixhawk = MAVLink.** Use a mature MAVLink↔ROS2 bridge (mavros for ROS2, or the
  PX4 uXRCE-DDS path if PX4 is chosen) to send autonomy setpoints and read vehicle state /
  EKF estimate. This is the main autonomy command channel.
- **StampPLC → Thor = custom serial + bridge node (committed).** The StampPLC keeps a
  simple, deterministic serial protocol; a ROS2 node on the Thor translates it into the
  topics already named in the telemetry spec (`/telemetry/...`, `/status/safety`). **Decision:
  do the ROS-ification on the Thor, not micro-ROS on the ESP32** — keep the safety controller's
  firmware minimal and not version-coupled to ROS. (StampPLC telemetry may alternatively reach
  the Thor relayed via the Pixhawk; the bridge-node contract is the same either way.)

The telemetry spec's ROS topic naming means the bridge is a translation, not a redesign.

### GPU-accelerated perception = Isaac ROS

The reason to run a Thor at all: NVIDIA's GPU-accelerated ROS2 packages (visual SLAM, nvblox,
DNN inference). **Verify Thor / JetPack 7 support before designing perception around it** —
Thor + JP7 is new (2026) and Isaac ROS tracks specific JetPack versions, shipped as Docker
containers. Open item below.

## OS / ROS pitfalls to plan around

1. **Never `apt full-upgrade` blindly.** L4T/kernel/BSP packages are JetPack-managed; a stock
   dist-upgrade can break the CUDA/graphics stack. `apt-mark hold` the `nvidia-l4t-*` packages;
   move BSP only via NVIDIA's path. This is the #1 way a working Jetson gets bricked.
2. **CUDA/cuDNN/TensorRT are pinned to the JetPack version.** Perception packages must match —
   the argument for running them in NVIDIA's containers rather than pip-installing onto the host.
3. **Multi-NIC DDS (active now).** Wired + WiFi are both up. ROS2 default discovery is multicast
   across all interfaces; two NICs (WiFi multicast often flaky) cause phantom/duplicate discovery
   and drops. Plan: fixed `ROS_DOMAIN_ID`, one RMW (CycloneDDS or **Zenoh**, better on wireless),
   and **bind discovery to one interface** (or a discovery server / Zenoh router). Decide which
   NIC is the robot network.
4. **Time sync.** Sensor fusion needs aligned timestamps across Thor / Pixhawk / StampPLC. Run
   `chrony` on the Thor. The StampPLC has no RTC — likely timestamp-on-arrival at the Thor, with
   the accuracy limit stated explicitly in the telemetry spec.
5. **Power/thermal modes affect determinism.** `nvpmodel` / `jetson_clocks` change clock
   behavior; lock a fixed power mode for repeatable timing.
6. **Logging/storage.** rosbag is write-heavy. Use a dedicated data partition/volume so a
   runaway recording can't fill `/` and wedge the OS.
7. **Hostname.** Currently `localhost.localdomain`. Set a real hostname (e.g. `opendubs-thor-01`)
   before building robot #2 — matters for multi-robot ROS and logs.

## Recommended first moves (order)

1. Set a real hostname.
2. Decide the robot-network NIC + lock down DDS (RMW choice, domain ID, interface binding).
3. Record/pin the BSP version; stand up the provisioning-script + container approach.
4. Choose snapshot strategy (Btrfs convert vs periodic image).
5. Verify Isaac ROS / Thor (JP7) support before designing perception.

## Open items

1. **Isaac ROS on JetPack 7 / Thor** — confirm supported package set and container tags.
2. **RMW choice** — CycloneDDS vs Zenoh `rmw_zenoh` for the wired+WiFi robot network.
3. **Robot-network NIC** — wired vs WiFi as the ROS/DDS interface; the other for ops/SSH.
4. **Btrfs conversion vs periodic imaging** — pick the rollback mechanism.
5. **MAVLink bridge** — mavros (ArduPilot) vs uXRCE-DDS (PX4), tied to the flight-stack choice.
6. **x86 host for full clone** — dedicated box vs VM, or accept NVMe-only on-device backups.
7. **StampPLC telemetry path** — direct serial to Thor vs relayed via Pixhawk.

## Sources

- JetPack / L4T BSP and flashing (`flash.sh`, backup/restore): https://docs.nvidia.com/jetson/
- ROS 2 Jazzy (Noble 24.04 Tier-1): https://docs.ros.org/en/jazzy/Releases/Release-Jazzy-Jalisco.html
- Isaac ROS (GPU-accelerated ROS2, container-based): https://nvidia-isaac-ros.github.io/
- mavros (MAVLink ↔ ROS2 bridge): https://github.com/mavlink/mavros
- micro-ROS (alternative embedded path, not chosen): https://micro.ros.org/
- rmw_zenoh (DDS-alternative RMW, wireless-friendly): https://github.com/ros2/rmw_zenoh
- Jetson power modes (`nvpmodel`/`jetson_clocks`): https://docs.nvidia.com/jetson/archives/
