# Team workflow and per-robot management (proposal)

**Status: PROPOSAL, open for review until Wednesday 2026-10-28, end of day.** Comment on the PR.
Unless a comment changes it, the plan below starts on Thursday 2026-10-29, when opendubs1 comes up
alongside opendubs2.

## Why

More people now work on the robots at once: maintainers, students and developers in other repos
(arm drivers, models, planners) whose work lands here when it's ready. Two robots are about to
run, and they will differ in hardware (arms, heads, drives), in firmware and in which tests apply.
Today four things live in people's heads or in chat:

- what is on each robot;
- what was merged but never tried on hardware;
- what differs between the robots;
- who is doing what.

This proposal puts them on rails, with standard practice for a small hardware team: one main
branch, every deploy recorded as a release, an explicit "verified on hardware" step, and robot
differences kept as data, never as forks.

## 1. Work is tracked in GitHub issues

- **Issue templates:** *bug seen on a robot*, *task*, *at-robot task*.
- **Labels:**
  - `lane:<area>` (`lane:arms`, `lane:thor`, `lane:tablet`, …), the owner of the work;
  - `robot:opendubs1`, `robot:opendubs2`, for anything tied to one robot;
  - `needs-robot`, `needs-decision`, `blocked-upstream`, `good-first-issue`.
- **One project board:** Backlog → In progress → In review → **Merged, awaiting robot** →
  **Verified on robot**.
- **Kept out of issues** (the repository will become public): schedules and priorities, security
  findings (GitHub's private advisories), and anything about a person.

## 2. Pull requests say what they do to a robot

- Open as a **draft**; mark ready when local tests pass (CI runs then). Sign off every commit
  (`git commit -s`).
- **A PR template** asks:
  - what it changes, and its **risk class** (below);
  - how it was tested;
  - what must be checked on which robot;
  - which areas it touches;
  - any other repo it depends on, at which commit.
- **CODEOWNERS** routes each area's PRs to its reviewers.
- **Other repositories** (arm drivers, models, PRL packages) are pinned to a commit. A pin moves only
  through a PR here with its own tests. Announce upcoming upstream changes in an issue.

## 3. Merge policy by risk

| Class | What | Merging |
|---|---|---|
| A | docs, simulation, tests, tools | merge when green |
| B | robot software that does not move anything (dashboard, face, voice) | merge when green; label `needs-robot` |
| C | motion, safety gates, firmware, robot configuration | merge only with a robot visit scheduled, or when the simulator fully covers it; label `needs-robot` |

## 4. Every deploy is a release, per robot

- **The deploy tags** the commit it ships: `<robot>/<date>.<n>`. It moves `deployed/<robot>` to that
  tag.
- **A deploy note**, posted to a pinned issue per robot, lists the PRs it includes and their
  `needs-robot` checks.
- **After a robot visit** confirms it, the tag becomes `known-good/<robot>`: the rollback target.
- **What's merged but not on a robot:** `git log deployed/<robot>..main`.

## 5. Each robot visit starts the same way

1. Deploy.
2. Work through that robot's `needs-robot` list: each PR names its check.
3. Record each outcome on the PR: `robot-verified`, or a new `regression` issue.
4. Mark the tag known-good.

Firmware flashes and safety checks go through this loop, not around it. A published **robot
schedule** tells contributors when their change can reach hardware.

## 6. Robots differ by data, not by branch

- **A hardware manifest per robot** in the repo, `robots/<robot>.yaml`, lists what is fitted:
  - arms, with their type and CAN layout;
  - head, neck and camera;
  - drive, lift and sensors;
  - each controller board.

  Each fitted part names a module in the robot description, so each robot's simulation model, URDF,
  clearance sweep and show validation are built from its own manifest.
- **Code checks what is fitted** ("the arms are OpenArm"), never which robot it runs on. A show
  declares what it needs, and a robot refuses a show it can't run.
- **Calibration stays on each robot** (drive signs, spans, odometry offsets, speeds, the tilt band).
  Each deploy backs it up outside the repository and records a fingerprint of it in the deploy note.
- **Firmware:** each board reports its build, signing key and settings fingerprint. The deploy
  audit builds a firmware table per robot and flags drift from the manifest. Both robots use one
  signing key; each board is already bound to its own robot.
- **Network:** each robot has its own router and network, with the same naming scheme:
  `<device>.<robot>.internal`, with router-reserved addresses. Nothing uses a dynamic IP address.
- **CI** runs the simulator checks against every robot's model. Checks for one robot carry that
  robot's label, so a check passed on one robot doesn't count for the other.

## 7. One page per robot

A generated page per robot shows:
- its hardware, from the manifest;
- the software deployed (its tag);
- its firmware table and calibration fingerprints;
- open `needs-robot` items and known differences from the other robot.

Read it before working on that robot. A short weekly digest lists merged, deployed and
awaiting-robot changes.

## Rollout, if accepted

| Week of | What | Who |
|---|---|---|
| 2026-10-26 | labels, templates, CODEOWNERS, the board, seed issues; opendubs1's router, Thor and StamPLC come up on the naming scheme | maintainers, with the network, Thor and docs owners |
| 2026-10-26 | `robots/opendubs1.yaml` and `robots/opendubs2.yaml`, the deploy tags and deploy note | Thor stack owners |
| 2026-11-02 | per-robot page and firmware table; CI against each robot's model | Thor, hygiene, choreography owners |
| later | the modular robot description (each robot's model assembled from its manifest) | Thor and choreography owners |

## Open questions

- opendubs1's arms aren't chosen yet. Its manifest starts without arms, and arm code checks what is
  fitted.
- How students take part in opendubs1's bring-up is still being decided.
