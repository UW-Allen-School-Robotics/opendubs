# Work plan — the Thor stack and localization: containers, configuration, ROS / Isaac ROS, frames and odometry

**DRAFT, 2026-10-07, widened 2026-10-08 — not yet agreed.** Written in the manner of
[`workplan-arms.md`](workplan-arms.md): once the decisions below are taken it becomes the plan of
record, work proceeds through its phases, and finished items are struck through with a date.

This plan is the functional implementation of design that already exists, scattered across several
notes. It adds no new architecture of its own beyond the decisions it asks for.

| Source note | What it already settles |
|---|---|
| [`punch-list.md` §0d](punch-list.md) | Isaac ROS prerequisites. `/cmd_vel` and `/odom` are done. Open: the TF tree, D415 ownership, "containers change the structure question", NITROS is a type contract, DDS still at defaults |
| [`docs-review-checklist.md` B11](docs-review-checklist.md) | Topic naming by function is done. **The package + launch structure, versus today's 13 systemd units, is open by decision** until Isaac ROS is committed to |
| [`punch-list.md` goals 3 and 4](punch-list.md), [`design/arm-authority.md`](design/arm-authority.md) | The vendor-swap boundary: one adapter per arm vendor, with streaming and trajectory paths kept separate. SSIK can be swapped for cuMotion at that adapter (`FollowJointTrajectory`) |
| [`history/hardening-roadmap.md` track 5](history/hardening-roadmap.md) | Thor utilisation: NITROS zero-copy, TensorRT/DLA, NVENC, nvpmodel policy |
| [`../docs/portability-audit.md`](../docs/portability-audit.md) | The layered model (base flash, host provision, app deploy, per-unit identity), and `deploy-all.sh` / `provision-all.sh` as the source of truth |
| [`../docs/thor-setup.md`](../docs/thor-setup.md) operating notes | Isaac ROS runs from containers; pin nvpmodel; chrony; multi-NIC DDS; golden image |
| [`workplan-arms.md`](workplan-arms.md) 6.3, DEC-18 | The `openarm` image must be reproducible from a pinned org fork. **The arms plan owns that image**; this plan only sets the pattern it should follow |
| [`deployment-drift.md`](deployment-drift.md) | Nothing deploys by itself, so robots drift. Containers are the chance to fix that structurally |
| [`../docs/localization-fusion.md`](../docs/localization-fusion.md) | Two-tier REP-105 (`map→odom→base_link`). `robot_localization` for the local EKF. One primary IMU. Odometry with real covariance, stamped at source as the endgame |
| [`design/lidar-frames-and-fusion.md`](design/lidar-frames-and-fusion.md), [`../docs/naming-frames.md`](../docs/naming-frames.md) | Extrinsics are published from a URDF via `robot_state_publisher`, never as loose static transforms. Do not merge the two scans. **`base_link` placement must be settled before measuring anything** |

## Priorities and machines

**Priority (2026-10-08):** the NVIDIA-stack infrastructure is high priority. T-1, T-2 and the
Phase 0 audit come next. Student experiments are a longer-term need; for now the plan only has to
leave room for them (see *Leaving room for experiments*).

**Machines.** There are three Thors, and only one of them is a test bed.

| Unit | Role | Rule |
|---|---|---|
| **opendubs2** | the working robot | **Never the test bed.** Changes arrive as proven, pinned artifacts through `deploy-all.sh`. No reflash is planned near-term |
| robot 1's Thor | the other robot | not reachable as of 2026-10-08 |
| **the non-robot Thor** | builds and tests the containerized stack | remote SSH expected **2026-10-09**. Which physical unit it is, and its JetPack version, are **unconfirmed**. Check both before anything else (0.3) |

**Lane boundaries** (CLAUDE.md "Lanes"). StamPLC firmware changes, such as faster or stamped odometry and
IMU readings, go **through the orchestrator**, because that board is the safety controller. The
tablet lane publishes the tablet's sensors on topics this plan defines (L.6). Dashboard changes,
such as reading the published extrinsics, are handed to the dashboard's lane rather than made here.

---

## Decisions

**Decided 2026-10-08 (the user, relayed by the orchestrator):**

- **T-1:** JetPack 7.2 with Isaac ROS 4.6 on Jazzy is the common base for Thor and Orin.
  5.0/Lyrical is a later, deliberate milestone.
- **T-2:** option (b). Every ROS node runs in a container; the dashboard stays on the host for now.
  Migrate one unit at a time (3.3).

**Open under T-1 — T-1a: what the non-robot Thor starts on.** opendubs2 is on R38.4 (7.1), and no
reflash is planned near-term.

- **Start on 7.2:** we build on the target base, but nothing reaches opendubs2 until it is reflashed.
- **Start on R38.4:** work lands on opendubs2 now, but has to move to 7.2 later.
- **Settle it during the first session on the non-robot Thor:** test whether Container Toolkit 1.19's
  CUDA forward compatibility lets a 7.2-era image run on a 7.1 host. If it does, start on 7.2.

The sections below were written before the decision. They are kept for the reasoning.

## Decision record (as proposed)

**T-1 · Which version line.** The robot is on R38.4, which is JetPack 7.1, running ROS 2 Jazzy.

- **Isaac ROS 4.x** is the Jazzy line. 4.2 added JetPack 7.1.
- **Isaac ROS 5.0** (21 Sep 2026) moves to ROS 2 **Lyrical**. It is tested on JetPack 7.2 (R39.2) and
  **removes NITROS**.
- Going from R38 to R39 is an **image-based OTA**, not an apt upgrade.

*Recommendation, revised 2026-10-08:* **develop on R38.4 with the newest Isaac ROS 4.x that supports
JetPack 7.1, on Jazzy.** opendubs2 stays on R38.4 for the foreseeable future, so work done on a
different base would not transfer to it.

- **Set up the non-robot Thor to match R38.4**, even if it shipped newer.
- **Converging Thor and Orin on JetPack 7.2 with Isaac ROS 4.6 (still Jazzy)** is the next
  milestone. It is gated on a *planned* reflash of opendubs2.
- **Test on the non-robot Thor** whether Container Toolkit 1.19's CUDA forward compatibility lets
  7.2-era images run on a 7.1 host. If it does, the gate softens.
- **Make the move to 5.0/Lyrical a later, deliberate milestone.** Everything running on the robot today is Jazzy: the ROS units, the
Astra port and the `openarm` container. The NITROS removal also undercuts the "zero-copy is the
Thor advantage" premise in roadmap track 5 and §0d, so read what 5.0 replaces it with before
designing around either.

**T-2 · The host/container boundary.**

| Option | What goes where |
|---|---|
| **(a) Hybrid** | Thin hardware bridges and the dashboard stay host systemd units. GPU perception runs in an Isaac ROS-based container, and the arms in their own container |
| **(b) Everything in containers** | Every node runs under compose |
| **(c) Isaac ROS bare-metal or venv** | Officially supported since 4.1, but NVIDIA says "experts only" |

*Recommendation, revised 2026-10-08:* lean toward **(b) for every ROS node**, while the dashboard
stays on the host for now.

On 2026-10-07 this plan recommended (a): the bridges are plain Python that already work on the host. Two
new goals changed that:

- **An Orin build:** with ROS in containers, the host is only JetPack, Docker, udev and systemd, so
  porting is mostly pulling the same images.
- **A reset to a known stack:** containers cannot drift in place.

The cost is debugging through `docker exec`, and one more moving part in the drive path.
**Migrate one unit at a time, with each old host unit kept until its replacement is proven
(3.3).**

**T-3 · How a production image is built and run.** NVIDIA documents the development workflow:
the apt-installed `isaac-ros` CLI with image-key layering (`Dockerfile.<key>`,
`additional_image_keys`, `.isaac_ros_dev-dockerargs`). It does **not** document production
deployment for 4.x; `docker_deploy.sh` was retired after 3.2.

*Recommendation:* build our image with the CLI's layering (`Dockerfile.opendubs` on the Isaac ROS
base), push it pinned by digest, and run it in production from a systemd unit or compose file that
uses the same flags the CLI uses. Development and production would then differ only in the
launcher.

**T-4 · D415 ownership** (carried over from §0d `[DEC]`). The YOLO tracker opens the camera
directly, so `realsense-ros` cannot publish it at the same time. Either the tracker moves to
subscribing on ROS topics, or the D415 stays out of the Isaac graph. This blocks Phase 5's camera
input, but not Phases 1–4.

**T-5 · Power mode policy.** On Thor, **120 W (mode 1) is the default**. MAXN (mode 0) is labelled
experimental by the R38.4 power page and throttles above TDP, yet NVIDIA's own Isaac ROS and GR00T
tutorials set it. *Recommendation:* pin 120 W for the robot. Treat MAXN as a measured exception,
not the default.

---

## Phase 0 — baseline (read-only)

| # | Item | Done when |
|---|---|---|
| 0.1 | Audit both Thors read-only: Docker version and source, `nvidia-container-toolkit` version (needs ≥ 1.17.7 for Thor `csv` mode), `daemon.json` (default runtime, log rotation), data-root, CDI files, images and running containers, `nvpmodel -q`, swap, `apt-mark showhold`. **opendubs2 was unreachable from the dev Mac on 2026-10-07** | One table per robot, recorded in this file |
| 0.1a | **Compare against the expected state below**, which is inferred from `main` @ `b007de7` without touching the robot | Each row marked match / differs / hand-made |
| 0.2 | Note which Thor is the clean-room unit. The factory-fresh Thor at 192.168.88.252 is a candidate, but it may be the same unit as "the non-robot Thor". Confirm | Named here |
| 0.3 | **Non-robot Thor, first contact (2026-10-09):** which physical unit it is (hostname, serial, Wi-Fi MAC against the memory notes); `/etc/nv_tegra_release`; whether it shipped on R38.4 or newer. Decide whether to reflash it to match opendubs2 (T-1) | Identity and base recorded here; the base matches opendubs2, or the difference is deliberate |

### Expected state of opendubs2, inferred from the repo (2026-10-08)

What `deploy-all.sh` → `provision-all.sh` would leave, plus what the docs say was done by hand.
**Rows marked "not in repo" are the interesting ones:** they exist on the robot, but nothing here
recreates them.

| Layer | Expected | Put there by | Check |
|---|---|---|---|
| Base | L4T R38.4 (JetPack 7.1), Ubuntu 24.04, `nvidia-l4t-*` held | SDK Manager + `thor-setup.md` (manual) | `head -1 /etc/nv_tegra_release; apt-mark showhold` |
| Docker engine | present: the `openarm` container runs, and avahi once published `docker0` | **not in repo**; source and version unknown | `docker --version; apt-cache policy docker-ce docker.io` |
| NVIDIA container runtime | unknown. The `openarm` run line passes no `--runtime nvidia` or `--gpus`, so the arms may never have needed it | **not in repo** | `dpkg -l \| grep nvidia-container; cat /etc/docker/daemon.json` |
| Host ROS | apt Jazzy: `ros-base`, `rplidar-ros`, `rosbridge-suite`, `foxglove-bridge`, `diagnostic-msgs` | `provision-ros.sh` | `dpkg -l 'ros-jazzy-*' \| wc -l` (portability audit counted 212) |
| Host ROS from source | Astra driver in `~emmar/orbbec_ws` | `astra-jazzy-build.sh` (manual) | `ls ~/orbbec_ws/install` |
| Host Python | `/opt/opendubs/venv` with **`ultralytics` + `torch` from plain pip**, and `/opt/opendubs/piovenv` | `provision.sh`, `provision-firmware.sh` | `venv/bin/python -c 'import torch;print(torch.__version__, torch.cuda.is_available())'`. **Expect `False`**: generic aarch64 wheels are CPU-only, and `yolo_tracker.py` silently falls back to the CPU |
| systemd units | 9 ROS units, plus dash, stamplc-hb, yolo-tracker, runner and teleop | the provisioners; the teleop unit by hand | `systemctl list-units 'opendubs-*'` |
| Containers | `openarm` (`openarm-ros2:<host>`, made by `docker commit`, `--privileged --network=host`, `--restart unless-stopped`) | `openarm-setup.md` (manual) | `docker ps -a; docker images` |
| DDS | domain 0, FastDDS. The container is forced to FastDDS per `docker exec` | `openarm-up` | `/etc/opendubs-fastdds-udp-only.xml`, used by the teleop unit, is **not in repo**: untracked runtime config |
| Kernel / devices | `peak_usb` DKMS, `70-openarm-can.rules`, PlatformIO udev rules, LIDAR `by-path` drop-ins | `provision-openarm.sh`, `provision-firmware.sh`, drop-ins by hand | `dkms status; ls /etc/systemd/system/opendubs-rplidar*.d` |
| Power | unknown; nothing in the repo sets it | — | `nvpmodel -q` |

## Phase 1 — container host as code

| # | Item | Done when |
|---|---|---|
| 1.0 | **All of Phases 1–3 happen on the non-robot Thor first.** A step reaches opendubs2 only as a merged, pinned artifact deployed by `deploy-all.sh` | — |
| 1.1 | Write `provision-container-host.sh`, idempotent, and chain it from `provision-all.sh`. It does NVIDIA's documented baseline: Docker from get.docker.com, the `nvidia-container` package, `nvidia-ctk runtime configure --runtime=docker`, `"default-runtime": "nvidia"`, docker group membership. Additions: json-file log rotation, `net.core.rmem_max` (Isaac ROS camera guidance), the nvpmodel pin (T-5), and the `apt-mark hold 'nvidia-l4t-*'` that `thor-setup.md` already prescribes | It runs cleanly twice on the reference Thor, and `docker run --rm nvcr.io/nvidia/cuda:13.0.x-runtime-ubuntu24.04 nvidia-smi` sees the GPU |
| 1.2 | Write a version manifest, `software/stack/versions.env` or similar: L4T release, Container Toolkit, Isaac ROS release tag, and base-image **digests**. This is the "pin the base" layer from the portability audit, extended to images | Every image reference in the repo resolves through it |
| 1.3 | Have `tools/deploy-audit.py` report image digests against the manifest | Drift in a container is reported like drift in a file |

## Phase 2 — the OpenDubs ROS image

| # | Item | Done when |
|---|---|---|
| 2.1 | Install the `isaac-ros` CLI pinned to the chosen 4.x release, and stand up a development workspace from it on the Thor | `isaac-ros activate` gives a shell with Jazzy and CUDA 13 |
| 2.2 | Add a `Dockerfile.opendubs` image key: our ROS dependencies plus a colcon workspace. **This answers B11:** one `opendubs_bringup` package with launch files. Python bridges stay outside the zero-copy graph by design (§0d) | The image builds from the repo alone, with no hand steps and no `docker commit` |
| 2.3 | The description package from L.2 builds into the image | `robot_state_publisher` runs from the image |

## Phase 3 — runtime composition and the boundary

| # | Item | Done when |
|---|---|---|
| 3.1 | One run definition (compose or a systemd unit) using NVIDIA's own flags: `--runtime nvidia --network host --ipc=host`, plus privileged or a `/dev/bus/usb` mount for USB sensors. Map devices through the **host's** udev symlinks, never by enumeration order | The container survives a reboot and a replug |
| 3.2 | **Standardise DDS robot-wide:** one `ROS_DOMAIN_ID` per robot (per-unit identity, not in git), FastDDS everywhere, and one shared XML profile. `openarm-up` already forces FastDDS with shared memory off; decide whether `--ipc=host` lets shared memory come back on | Host nodes, the ROS image and the `openarm` container all see each other's topics, with no phantom peers on the two-NIC Thor |
| 3.3 | Move the host-side nodes that T-2 sends into containers, one at a time, keeping each old unit until its replacement is proven | Each move is a separate PR with a liveness check |

## Phase 4 — modularity as a contract

| # | Item | Done when |
|---|---|---|
| 4.1 | Define a **stack module** as: an image, or a host package; its topic, service and action contract; its launch arguments; its device needs. Same discipline as the hardware interfaces in `design-philosophy.md` | A one-page spec in `docs/interfaces/` |
| 4.2 | Write the arm adapter contract from `arm-authority.md` as a module spec, so SSIK and cuMotion are interchangeable at `FollowJointTrajectory` and the streaming path is named | Accepted by the arms plan (goal 4) |
| 4.3 | Bring the `openarm` image (arms plan 6.3) onto the Phase 1–3 pattern: manifest pin, image-key build, shared DDS profile | The arms plan marks 6.3 done |

## Phase L — localization: frames, odometry, IMU (runs alongside Phases 1–3)

This is the stack's prerequisite track (§0d). Most of it is desk work and does not wait on
containers. Steps marked **robot** need opendubs2 or the CAD.

| # | Item | Done when |
|---|---|---|
| L.1 | ~~**[DEC] `base_link` placement**~~ **DECIDED 2026-10-08: the centre of the wheel rectangle, at axle height** (CAD 324, 0, −24 mm). Each robot's `PPCAL OFFSETS` must be set to match, at the robot. That is on the user's at-robot list and is not changed from here. Was: the CAD datum (rear-centre hole), or the wheelbase centre with a fixed transform to the datum (`naming-frames.md`). It has to be settled **before** measuring, because every extrinsic depends on it | Recorded in `naming-frames.md` |
| L.2 | **One description package, generated from `description/opendubs/dimensions.yaml`.** It emits the URDF that `robot_state_publisher` needs alongside the existing MJCF. One geometry source; no hand-authored second copy | `base_link`→`laser`, `laser_rear`, `tof_front_link`, `pinpoint_link` and the camera frames resolve in Foxglove |
| L.2a | **Started 2026-10-08.** `description/opendubs/gen_urdf.py` reads the new *ROS FRAMES* section of `dimensions.yaml` and writes `opendubs.urdf`; `--check` fails if the URDF is stale. Present now: `base_link`, `base_footprint`, `datum`, wheels, `lift_platform` (`zaxis`), `arm_mount`, and the Tab5 head as a swappable assembly (`head_pan`, `head_tilt`, `camera_link`, `head_screen_link`). `camera_link` matches the MJCF's independently derived `d415_face` site. **Awaiting CAD numbers** for `laser`, `laser_rear`, `pinpoint_link`, `gps_link`, `tof_front_link` and `head_imu_link`; until then they are left out, not guessed. The Galaxy head is a placeholder that emits no head frames | All six filled from CAD; `check_urdf` passes on the non-robot Thor; CI runs `--check` (the workflow file is not this lane's) |
| L.2b | **A `joint_states` source** for `zaxis`, `head_pan` and `head_tilt`, or `robot_state_publisher` publishes nothing below them. Lift height comes from StamPLC telemetry; pan and tilt from the head, whose protocol the tablet lane owns. In the meantime, `joint_state_publisher` at zero | The camera frame follows the head on Foxglove |
| L.3 | **robot:** measure the extrinsics: both RPLIDARs (x, y, z, yaw; the rear's π yaw is an assumption), the Pinpoint pod and its heading sign, the ToF, and the D415 (already in the MJCF) | Values in `dimensions.yaml` with the method noted, not guesses |
| L.4 | **`/odom` made fusion-grade.** Measure the velocity scaling (C2) and characterise the covariance; it is a placeholder today. Note: `drive_bridge.py` now receives `[ODO]` over UDP 5005 directly, not through the dashboard's 5 Hz poll that `localization-fusion.md` still describes, but it still **stamps on arrival** | Covariance from a logged drive; the doc updated |
| L.5 | **[DEC] Primary IMU** (Pinpoint, camera, or the tablet's), then a local `robot_localization` EKF publishing `odom→base_link`. **`drive_bridge` stops publishing that TF when the EKF takes over**: two publishers of one transform is the failure the fusion doc warns about | Smooth `odom→base_link` from the EKF; one publisher |
| L.6 | **Tablet sensor topic contract:** names, types and frames (e.g. `sensor_msgs/Imu` in a `tablet_link` frame), handed to the tablet lane to implement | A short spec the tablet lane accepts |
| L.7 | **Stamp-at-source odometry** (hardening tracks 3/4, micro-ROS or a stamped line) is a **StamPLC firmware change**, raised through the orchestrator, never made here | Proposal sent |
| L.8 | Hand the dashboard's lane the change that makes it read the published extrinsics and drop the guessed facings in `app.js` | Handed over |
| L.9 | Global tier (`map→odom`): SLAM from the scans, or cuVSLAM in Phase 5. **[DEC]** arbitration between global sources comes later | Out of scope until L.5 is done |

## Leaving room for experiments (long-term; constraints only for now)

Nothing to build yet. These rules keep the infrastructure from closing the door on experiments:

- **Layers.**
  - **Core** owns the hardware and publishes the contract topics: `/cmd_vel` in; `/odom`, `/scan`,
    TF and the cameras out.
  - **Baseline** is our pinned stack.
  - **Experiments** get topics only: no devices, no `--privileged`.
- **Safety never depends on this layering.** CH5 authority, the e-stop and the 250 ms
  heartbeat already hold whatever runs on the Thor.
- **Drive commands from experiments go through a selector** (e.g. `twist_mux`), never straight onto
  `/cmd_vel`.
- **Docker group membership is root-equivalent.** Who may launch experiments, and how, is a
  decision for when experiments become real.

## Phase 5 — first Isaac ROS graph

| # | Item | Done when |
|---|---|---|
| 5.1 | cuVSLAM, then nvblox, on the camera chosen at T-4, consuming `/odom` and TF (after L.2 and L.5) | Pose and map on Foxglove from a drive around the lab |
| 5.2 | Measure 120 W against MAXN under that graph (T-5) | A number, recorded here |

## Phase 6 — documentation

| # | Item | Done when |
|---|---|---|
| 6.1 | `thor-setup.md`: replace the one-line Isaac ROS note with the container-host step. Correct "JetPack 7 (L4T R38.4)" to name **JetPack 7.1** | `mkdocs build --strict` passes |
| 6.2 | `portability-audit.md`: add the container-image layer to the layered model | — |
| 6.3 | `camera-selection.md`: "Isaac ROS 4.0 is GA for Thor" is stale and should name the pinned release | — |
| 6.4 | `localization-fusion.md`: the transport caveat describes the retired 5 Hz dashboard path (see L.4) | — |

---

## Sources (researched 2026-10-07)

**V** means read on an NVIDIA page or in NVIDIA source. **F** means an NVIDIA staff forum reply.
**I** means inferred.

- Thor dev kit guide: [Docker setup](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_docker.html),
  [CUDA setup](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_cuda.html),
  [JetPack setup](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/setup_jetpack.html),
  [Quick start](https://docs.nvidia.com/jetson/agx-thor-devkit/user-guide/latest/quick_start.html). **V**
- R38.4: [power and performance](https://docs.nvidia.com/jetson/archives/r38.4/DeveloperGuide/SD/PlatformPowerAndPerformance/JetsonThor.html),
  [CAN](https://docs.nvidia.com/jetson/archives/r38.4/DeveloperGuide/HR/ControllerAreaNetworkCan.html),
  [packages and updates](https://docs.nvidia.com/jetson/archives/r38.4/DeveloperGuide/SD/SoftwarePackagesAndTheUpdateMechanism.html). **V**
- Container Toolkit: [release notes 1.19](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.19.0/release-notes.html),
  [CDI](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html). CDI exists, but no Thor page asks for it. **V**
- Base images:
  - NGC is the production recommendation, not jetson-containers: [thread](https://forums.developer.nvidia.com/t/official-inquiry-is-jetson-containers-the-recommended-production-workflow-for-jetpack-6-x-7-x-on-ubuntu/362177). **F**
  - `l4t-jetpack` is not maintained on Thor: [thread](https://forums.developer.nvidia.com/t/cant-find-specified-l4t-jetpack-container-in-ngc/374302). **F**
  - Thor uses SBSA images, with no `-igpu` tag: [JetPack 7.0 post](https://forums.developer.nvidia.com/t/jetpack-7-0-jetson-linux-38-2-for-nvidia-jetson-thor-is-now-live/343128). **V**
- Isaac ROS:
  - [release notes](https://nvidia-isaac-ros.github.io/releases/index.html)
  - [getting started](https://nvidia-isaac-ros.github.io/getting_started/index.html) (Docker is recommended; venv and bare metal are the alternatives)
  - [dev-env concepts](https://nvidia-isaac-ros.github.io/concepts/dev_env/index.html) (image keys)
  - [`isaac-ros-cli`](https://github.com/NVIDIA-ISAAC-ROS/isaac-ros-cli): its 4.2 base is `cuda-dl-base:25.08-cuda13.0-devel-ubuntu24.04`; its run flags are `--privileged --network host --ipc=host --pid=host --runtime nvidia`; it sets `RMW_IMPLEMENTATION=rmw_fastrtps_cpp`
  - [RealSense setup](https://nvidia-isaac-ros.github.io/getting_started/sensors/realsense_setup.html) (RSUSB backend, pinned firmware). **V**
- [GR00T Isaac ROS setup on Thor](https://docs.nvidia.com/learning/physical-ai/gr00t-e2e-workflow/latest/real-robot-workflow/isaac-ros-setup.html):
  `isaac-ros init docker`, `--ulimit rtprio=99`, `nvpmodel -m 0`. **V**
- Gaps no NVIDIA page covers: production deployment for 4.x; multi-container ROS or compose;
  CAN inside containers (**I**: host networking should suffice); Orbbec; headless, swap or zram
  configuration for Thor.
