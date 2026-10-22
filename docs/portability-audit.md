# Portability Audit & Reproducible Bring-up

How to go from a **fresh Thor** to a **working OpenDubs robot**, what's currently
reproducible vs. hand-built, and the gaps to close. Companion to
hardening-roadmap.md track 6 (deploy/ops).

Audited against the live unit `opendubs2` on 2026-06-09.

## TL;DR

**Status 2026-06-09: the gaps below are now closed.** A fresh Thor (flashed to the
pinned base) is reproducible via `deploy-all.sh` → `provision-all.sh`, which chains
three idempotent provisioners:
- `kiosk-dashboard/deploy/provision.sh` — the dashboard: python venv + backend
  deps, the service account and its sudoers rule, `opendubs-dash.service` and the
  StamPLC heartbeat. It installs no display, browser or X session; the robot has
  none.
- `ros/provision-ros.sh` — ROS 2 apt repo + Jazzy base + rplidar/rosbridge/
  foxglove + the 3 `opendubs-*` ROS services.
- `rc_serial_probe/provision-firmware.sh` — PlatformIO venv + udev rules + flashing
  group membership.

Validated on `opendubs2`: the ROS and firmware provisioners run idempotently
(clean no-ops). The updated **kiosk `provision.sh` was NOT re-run on the live unit**
(to avoid disrupting the running session) — it's syntax-checked and its changes
are idempotent; validate it on the next fresh unit or a maintenance window.

Still per-unit (never in git): hostname, secrets, ROS domain id (see §4).

## Layered model (target)

1. **Base flash** (NVIDIA, outside our repo) — pin the version.
2. **Host provision** (repo scripts, run on the Thor, idempotent) — repos,
   packages, services, configs, users, groups.
3. **App deploy** (rsync from dev machine) — `/opt/opendubs/*` code.
4. **Per-unit identity** — hostname, secrets, anything unique per robot.

## 1. Base layer — PIN THIS

A second Thor must be flashed to the **same** base before any provisioning:

- **JetPack 7 / L4T R38 REVISION 4.0** (`/etc/nv_tegra_release`: `R38 … REVISION: 4.0`)
- **Ubuntu 24.04.3 LTS, aarch64**
- → dictates **ROS 2 Jazzy** and all the display/GL assumptions.

This is the NVIDIA layer (SDK Manager / flash), not in our repo. Document the
exact JetPack build used so units match.

## 2. Inventory (component → captured?)

| Component | On the live Thor | In repo? | Provisioned by |
|---|---|---|---|
| Hostname `opendubs2` | set | ❌ | manual (`hostnamectl`) |
| python venv `/opt/opendubs/venv` + deps | fastapi/uvicorn/pyserial/websockets/psutil | ✅ `backend/requirements.txt` | `provision.sh` |
| Service account `kiosk` + sudoers | `opendubs-dash.service` runs as it | ✅ | `provision.sh` |
| ~~Panel xorg, touch bridge, chromium, gdm autologin~~ | — | **retired 2026-09-29** | the robot has no display; an Android tablet browses :8080 |
| sudoers (kiosk poweroff/reboot) | `/etc/sudoers.d/opendubs-kiosk` | ✅ `deploy/opendubs-sudoers` | **install is manual** |
| `opendubs-dash.service` | enabled | ✅ | `provision.sh` |
| **ROS 2 apt repo + key** | `ros2.list` + keyring | ❌ | **manual** |
| **`ros-jazzy-ros-base` + rplidar + rosbridge + foxglove-bridge** (212 ros pkgs) | installed | ❌ | **manual** |
| **`opendubs-rplidar/rosbridge/foxglove` services** | enabled | ✅ `ros/*.service` | **install is manual** (`software/ros/README.md` shows the cp/enable) |
| **PlatformIO venv `/opt/opendubs/piovenv`** | installed | ❌ | **manual** |
| **`99-platformio-udev.rules`** | installed | ❌ | **manual** |
| **The service user's groups** dialout, plugdev (flashing) | set | ❌ | **manual** |
| App code `/opt/opendubs/{kiosk-dashboard,ros,rc_serial_probe}` | present | ✅ | rsync deploy |

## 3. Gaps — CLOSED (2026-06-09)

All of the previously-manual items now live in provisioning:

1. ✅ **ROS provisioning** — `ros/provision-ros.sh` (apt repo+key, ros-base +
   rplidar/rosbridge/foxglove, installs+enables the 3 services). *Validated.*
2. ➖ **xorg-fix** — was captured to `kiosk-dashboard/deploy/` and installed by
   `provision.sh`. **Retired 2026-09-29** with the panel; a Thor provisioned
   before then still has it enabled and needs it disabled by hand — see
   [Thor setup §4](thor-setup.md).
3. ✅ **Autostart install** — `provision.sh` installs the three
   `~kiosk/.config/autostart/*.desktop`; the custom-Xsession override is removed
   (drift reconciled → stock GNOME on Xorg).
4. ✅ **Chromium** — `snap install chromium` in `provision.sh`.
5. ✅ **sudoers** — `provision.sh` installs `opendubs-sudoers` → `/etc/sudoers.d/`.
6. ✅ **Firmware/PlatformIO** — `rc_serial_probe/provision-firmware.sh` (piovenv +
   platformio, fetches the canonical udev rules, adds flashing user to
   dialout,plugdev). *Validated.*
7. ✅ **Top-level `provision-all.sh`** — chains kiosk → ros → firmware.
8. ✅ **`deploy-all.sh`** — rsyncs the subsystems + `provision-all.sh`, runs
   provisioning. It never overwrites a robot's own files (drive calibration in
   `rc_serial_probe/src/robot_config.h`, `platformio.ini`): the list is `tools/deploy-audit.py`'s
   `PER_ROBOT` entries. The dashboard goes through its own `deploy.sh`. Repo `*.sh` are kept +x so `rsync -a` carries the bit (the
   strips-+x footgun from track 6).

## 4. Per-unit identity (do NOT bake into the repo)

- **Hostname / mDNS** (`opendubs2` → `opendubs3`…) and the dev `ssh thor` alias.
- **Secrets**: VNC password (set per-unit, e.g. via `OPENDUBS_VNC_PASS` at provision —
  not committed), any keys — template/generate at provision, keep out of git.
- **Device matching**: StamPLC (`*Espressif*if00`)
  already matches by *pattern* (portable). LIDAR CP2102 reports generic `0001`
  (portable, but a 2nd CP210x collides → pin by `by-path`). No per-unit serials
  are hardcoded — good.
- **ROS**: standardize `ROS_DOMAIN_ID` per robot once multiple units share a LAN.

## 5. Fresh-Thor runbook

```
1. Flash JetPack 7 / L4T R38.4 (NVIDIA SDK Manager).            # base layer
2. Set hostname (opendubsN) + per-unit secrets (VNC pw, etc.). # identity
3. From the dev machine:  ./deploy-all.sh <host>               # code + provision
   (rsyncs kiosk-dashboard/ ros/ rc_serial_probe/ → /opt/opendubs,
    then runs provision-all.sh: kiosk → ros → firmware)
4. ssh <host> sudo reboot  → kiosk + ROS services come up.     # verify
```

`deploy-all.sh` is also the steady-state update path (re-run any time; idempotent).

## 6. Image-clone (shortcut, not source of truth)

Cloning the Thor's storage to an identical unit is the *fastest* way to replicate,
but it carries machine-specific state (hostname, machine-id, SSH host keys, logs)
and drifts from the repo. Use it only as a fast-replication shortcut **derived from
a freshly-provisioned reference unit** — never as the canonical definition. The
repo + `provision-all.sh` is the source of truth.
