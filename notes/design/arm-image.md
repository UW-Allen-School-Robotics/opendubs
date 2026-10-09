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
| Source | a host directory, cloned at unrecorded tips | **cloned at the commits in `repos.lock`** by the build script, then copied into the image. The org's private `openarm` copy is cloned on the build host with its credentials, so no secret enters the image |
| Workspace build (`colcon`) | host-mounted `build/`, `install/`, `log/` | built into the image |
| Python environment | `uv sync` on the host into `src/.venv` | `uv sync --frozen` in the image, into the same path, from the source's own `uv.lock` |
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
| `software/openarm/image/repos.lock` | every source repository: name, URL, and the full commit it is built at |
| `software/openarm/image/capture-pins.sh` | run on a robot (read-only): writes the commits that robot's arm stack actually runs into `repos.lock` format, and **refuses** a repository with uncommitted changes — those are untracked configuration and must become commits first |
| `software/openarm/image/build.sh` | refuses an unpinned base or any unpinned repository; clones each at its commit into a build context; applies `setup.sh`'s workspace preparation; builds; prints the image digest |
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

## Not done here

- **DEC-27's manifest** (design: `arm-sources.md`). `repos.lock` gets the upstream URLs: the lab's
  and enactic's repositories at releases, the author's own three, and the org `openarm`. The
  student forks of the lab's libraries go. `setup.sh`'s clone-at-tips goes too.
- **SSIK installed non-editable, from its `v8.1.0` tag**, not as `ssik` beside the workspace. That
  layout is installed editable through the workspace root's `path` source. SSIK publishes no Linux
  aarch64 wheel. A non-editable build from the tag compiles its native backend (0.08 ms per solve,
  measured on the development Mac). Editable or from the sdist, it runs pure Python (3.1 ms). The
  teleop solves IK on every engaged control step, at 6.7 ms per tick.

- **The pins themselves.** `repos.lock` ships with the repositories named and the commits blank;
  `capture-pins.sh` fills them from a robot, and the build refuses until it has.
- **A build on the non-robot Thor.** Desk work only until that machine is reachable; never on
  opendubs2.
