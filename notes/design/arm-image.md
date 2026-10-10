# The `openarm` image, reproducible from the repo — design

Arms plan 6.3 and DEC-18; the Thor stack plan's T-3 and 4.3 set the pattern this follows.

**The problem.** The arm container on a robot was made by hand: upstream's dev-container Dockerfile,
then its `post-create.sh` run inside a temporary container, then `docker commit`. The source it runs
is not in the image at all — it is a host directory that `setup.sh` filled by cloning about fifteen
repositories at whatever their tips were that day, plus a Python virtual environment built on the
host. Nothing records which commits those were. If a robot's disk dies, the arm stack cannot be
rebuilt, only approximated.

**The goal.** One command builds the image from this repo alone — pinned base, pinned packages,
pinned source commits — and one command runs it with the same flags in development and production.

## What goes where

| | Today | In the image design |
|---|---|---|
| Base | `ros:jazzy-ros-base`, unpinned | **the same `ros:jazzy-ros-base`, by digest**: `ROS_BASE_IMAGE` in the Thor stack's version manifest, `software/stack/versions.env`. The build refuses an empty or tag-only value. Not the Isaac ROS base: the arm stack has no GPU code, and GPU motion planning arrives as its own module talking to the arms over FollowJointTrajectory, so a CUDA-free image runs the same on JetPack 7.1, 7.2 and Orin and does not wait on the JetPack choice (Thor stack T-1a) |
| ROS and system packages | upstream's apt list, unpinned; then whatever `rosdep` resolved on the day | the same apt list, recorded with versions in `packages.lock`; `rosdep` runs at build time and its result is recorded too |
| OpenArm CAN library | `ppa:openarm/main`, unpinned | the same PPA, pinned to a version |
| Source | a host directory, cloned at unrecorded tips by a fork of the lab's workspace root | **our workspace root** (`workspace/`), each repository **cloned at its `repos.lock` commit** by the build script, the **overlay** (`overlay/<name>/*.patch`, the student's changes to the lab's libraries) applied, then copied into the image (DEC-27). The org's private `openarm` is cloned on the build host with its credentials |
| Workspace build (`colcon`) | host-mounted `build/`, `install/`, `log/` | built into the image |
| Python environment | `uv sync` on the host into `src/.venv`, no lock committed anywhere | `uv sync --frozen` in the image, into the same path, from **`workspace/uv.lock`** (ours, committed). SSIK is a uv git source at its `v8.1.0` tag, built non-editable, so it compiles its native backend on aarch64 |
| FastDDS profile | written into the container at runtime by `openarm-up` | baked into the image at the same path (until the Thor stack's shared DDS profile, its item 3.2, replaces it) |
| Teleop certificate | in the host-mounted source tree | **per machine, never in the image**: a host directory mounted over `certs/`, generated on first start if empty |
| Stow pose, CAN naming | `/etc/opendubs/`, udev on the host | unchanged: per-robot values stay on the robot |
| `openarm-up` logs | host-mounted `log/` | a host directory mounted at the same path |

Paths inside the image stay exactly as today — `/openarm_ws/src`, `/openarm_ws/install`,
`/openarm_ws/src/.venv`, container name `openarm` — so `openarm-up`, `openarm-motors` and
`opendubs-teleop.service` work unchanged.

## The files

| File | What |
|---|---|
| `software/openarm/image/Dockerfile.openarm` | the image, built with plain `docker buildx build` — no Isaac CLI layering is needed without the Isaac base |
| `software/openarm/image/repos.lock` | every source repository: name, kind (`uv` / `ros`), URL and the full commit it is built at, each with its provenance on the comment line above. Two enactic pins are **provisional** until `capture-pins.sh` reports what a robot runs |
| `software/openarm/image/capture-pins.sh` | run on a robot (read-only): **compares** the robot's checkouts with `repos.lock` (SAME / DIFFERS per repository), reports the workspace root and `ssik` the manifest replaces and anything the manifest leaves out, compares the container's versions with `pins.env`, and **refuses** while a repository has uncommitted changes |
| `software/openarm/image/build.sh` | refuses an unpinned base, an unpinned or unknown-kind repository, an overlay for a repository not in the lock, or a missing workspace root -- before cloning; copies the workspace root, clones each repository at its commit, checks then applies the overlay, marks the uv members `COLCON_IGNORE`, builds, prints the image digest |
| `software/openarm/image/run.sh` | creates the `openarm` container with today's flags and the mounts above; `--dev NAME=PATH` bind-mounts one repository's checkout over its place in the image (for example the teleop bridge's `openarm`) — the only difference between development and production. One repository at a time, not the whole tree: the Python environment lives inside `/openarm_ws/src`, so mounting all of it would hide that |
| `software/openarm/image/fastdds-udp-only.xml` | the profile `openarm-up` writes today, as a tracked file |

## Updating the teleop bridge

Today a change to the org's `openarm` copy reaches the robot by fetching the commit into the
host-mounted checkout. With the source in the image, a change is a new `repos.lock` entry and a new
image. For iterating, `run.sh --dev openarm=<checkout>` mounts that checkout over the image's copy. The
workspace's Python packages are installed in editable mode, so edits are live, which keeps today's
fast loop and makes it explicit.

## Settled with the Thor stack lane (2026-10-08)

1. **Base:** a pinned `ros:jazzy-ros-base` digest (`ROS_BASE_IMAGE`), not the Isaac ROS base and not
   the robot's `Dockerfile.opendubs`. What must match across containers is the ROS distribution
   (Jazzy) and the RMW (FastDDS), not the base image.
2. **Manifest:** `software/stack/versions.env`, plain `KEY=value`. Refuse empty and tag-only values.
3. **The JetPack line** stops mattering for this image: it builds on any arm64 machine.
4. **`--privileged`:** the target is host networking **without** it. The host's
   `openarm-can@.service` already raises the links and sets the bitrate, so the container only opens
   SocketCAN sockets, which needs neither extra capabilities nor device nodes — an inference, to be
   tested with a `vcan` interface on the non-robot Thor. `run.sh` keeps `--privileged` by default
   until that passes; `--unprivileged` runs the test. Real-time scheduling for the 400 Hz
   controller manager, if wanted (arms plan 2.5), is `--ulimit rtprio=99 --cap-add SYS_NICE`.
5. **Supervision:** `--restart unless-stopped` for now; the Thor stack's 3.1 decides between Docker
   and systemd. Never both on one container.
6. **The private repository** is cloned on the build host with its own credentials, so no secret
   reaches the build at all.

## The manifest, filled (DEC-27), 2026-10-09

**Each repository from its maintainer at a recorded commit:** a release where it is current, else
main. The lab's releases are months behind what the teleop was built on (`mj_manipulator` v2.0.0 is
from April, `mj_environment` v1.1.0 from March), and the lab renamed two repositories in late
September (`pycbirrt` became `sscbirrt`, `tsr` became `sstsr`), after which `sscbirrt` no longer
provides the `pycbirrt` that `mj_manipulator` imports. The one set that holds together is the lab's
main **as of 2026-09-21**, the student's setup date and exactly where the student's forks branch
from. `mj_manipulator` is pinned at the fork's own upstream base, `b5ade5d`: main's two later
commits collide with ArmGroup's refactor of `arm.py`, and neither matters to OpenArm (one affects
joints with more than 2π of range, the other packaging). Moving to the lab's current releases is a
later, separate step.

**The overlay** carries the student's changes to three of the lab's libraries -- ArmGroup and the
`sstsr` switch (`mj_manipulator`), the real-robot fixes (`mj_environment`), the hardware context
and bimanual refactor (`mj_manipulator_ros`) -- each the fork's net change over the pinned upstream
commit, code only. **Checked: base + overlay equals the fork's tree, file for file.** Each patch is
deleted when upstream has it.

**Left out:** `mujoco_menagerie` (only the lab's demos for other arms use it).

**Desk-tested, 2026-10-09** (from GitHub tarballs, so no git on the desk): every repository at its
pin, the overlay applied, `uv lock` and `uv sync --frozen` on the development Mac (macOS arm64; the
lock is universal). SSIK installs from its tag with `_ssik_native`. Test suites in that environment:
`mj_manipulator` 439 passed, `mj_environment` 129 passed, `mj_manipulator_ros` 2 skipped (no ROS
here), `openarm` 190 passed / 30 failed. Of the 30, **27 fail with the student's SSIK fork too**
(they are the suite's own state, not this change); the other 3 are planner tests that 8.1.0 sends
within the tests' 0.05 rad margin of a joint limit -- see the plan's 0.16. `build.sh` refuses each
bad input before cloning (10 cases); ShellCheck clean; the three patches parse with `git apply`.

**Found while locking:** the old workspace root's `toppra` override applied only on aarch64, and a
uv override replaces every requirement on its package, marker included -- so on any other machine
`toppra` was not installed at all. `workspace/pyproject.toml` adds the non-aarch64 line.

## What opendubs2 actually runs, and the manifest's parity — 2026-10-10

`capture-pins.sh` ran on opendubs2, read-only, on 2026-10-09, and the source tree was backed up
the same day. The manifest was corrected from both. **It now rebuilds the robot's arm source tree
file for file.** Each repository was assembled from `repos.lock` and `overlay/` as committed, then
compared with the backup:
- **11 of 14 repositories are identical.**
- **`openarm` differs, by design:** the lock pins the org copy's newer `opendubs` (the teleop status
  report and the SSIK 8.1.0 modules), which the robot has not been moved to yet.
- **`openarm_ros2` differs by exactly the A1 patch,** which the robot runs since that evening.
- **`openarm_impedance_control` differs by one robot-only file,** below.

What the capture found, and what was done about each:

| Found on the robot | Done |
|---|---|
| `mj_manipulator` is the fork's **`armgroup` branch at `0858fef`**, 10 commits past the fork's `main` the first overlay was built from | `0001-armgroup.patch` is rebuilt from `0858fef`, against the same upstream `b5ade5d` |
| an **uncommitted `use_measured_dt`** in `mj_manipulator`'s `teleop.py`, which the teleop bridge relies on | carried as `0002-teleop-measured-dt.patch`. Who wrote it is unrecorded |
| an **uncommitted bug fix** in `mj_viser` (a numpy `int32` cast, without which the viewer's scene is empty) | carried as `mj_viser/0001-geom-type-int-cast.patch` |
| `openarm_description` is **the student's fork**: enactic's `14ff67b` plus one commit, the CAN interface args | carried as `openarm_description/0001-can-interface-args.patch` on enactic's `14ff67b`. **Pending the user's choice** (plan Q11) |
| `openarm_ros2` at `c15982d` | pinned there (#152), no longer provisional |
| `openarm_assets` at `31eebb9`, 6 commits behind its head, which changes the MuJoCo models and finger actuators | pinned at `31eebb9`. Moving to the head is a separate, deliberate step. The V1 URDF the IK modules are built from is the same in both |
| **uv 0.12.17**, and a host-made `uv.lock` | `pins.env` is 0.12.17. `workspace/uv.lock` was re-locked **starting from the robot's lock**, so 135 of 136 packages are at the robot's exact versions, MuJoCo 3.13.0 included. The one change is SSIK 8.1.0 from its tag, the intended one. A lock made fresh had 26 drift, MuJoCo 3.13 → 3.15 among them |
| **`ros-jazzy-ros2controlcli`** added by hand to the robot's container build, and never committed | added to `Dockerfile.openarm`. Without it `openarm-up`, which calls `ros2 control` for every step, would fail in the new image |
| an untracked `openarm_impedance_control_autostart.launch.py`: activates the hardware, then the controllers, from the launch | **not carried.** Nothing launches it. It is input for phase 2's boot bring-up (A3), now that activation is motion-free |
| untracked `scripts/run_teleop.sh` and `run_teleop_sim.sh` in the old workspace root | not carried. The teleop service runs the bridge directly |
| eight "dirty" checkouts that only had a `COLCON_IGNORE` marker | `capture-pins.sh` now ignores that marker. It is setup's, not configuration |

Tested again in the corrected environment, on the development Mac:
- **Install:** `uv sync --frozen` with uv 0.12.17 succeeds, giving MuJoCo 3.13.0 and SSIK 8.1.0 with
  its native backend.
- **Library suites:** `mj_manipulator` 439 passed; `mj_environment` 129 passed.
- **`openarm`:** 188 passed, 32 failed. Of the 32, 29 fail with the student's SSIK too, and 3 are
  0.16's planner cases.
- **`build.sh`** refuses all 10 bad inputs; ShellCheck is clean; every patch parses with
  `git apply`.

## Not done here

- **A build on the non-robot Thor.** Desk work only until that machine is reachable; never on
  opendubs2.
