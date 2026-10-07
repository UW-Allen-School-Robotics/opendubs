# Work plan — the arms: WebXR teleop, bring-up and authority

**DRAFT, 2026-10-07 — not yet agreed by the project team.** When it is, this becomes the plan of
record in the manner of [`workplan-tab5-idf.md`](workplan-tab5-idf.md) and
[`workplan-choreography.md`](workplan-choreography.md): work proceeds through these phases, anything
not in a phase, the team requests or the stub register is not being worked on, and finished items
are struck through with a date.

Written to consolidate what had been spread across a month of sessions, three design notes, an
unmerged punch list and a branch-only threads note. **The near-term goal is to close out WebXR
teleop**; authority, IK and manual control follow it.

**The shape of the system.** Two OpenArm **v1** 7-DOF arms on a PEAK CAN-FD adapter, driven by
the upstream ROS 2 impedance controllers inside the `openarm` container on the Thor. An operator in
a Meta Quest opens the upstream **WebXR teleop page** in the Quest Browser (HTTPS :8444, pose
stream over WSS :8766); `opendubs-teleop.service` runs the bridge headless; the tablet's **TELEOP
page** starts and stops it, shows the arms from `/joint_states`, and offers park, hold, activate
and de-energize, all through `openarm-up`. The arms sit on the contactor-switched drive bus, so the
e-stop removes their power.

Companion notes — read before changing anything they cover:
[`design/arm-authority.md`](design/arm-authority.md) (modes, CH5, the arbiter — **the
specification**), [`design/arm-manual-control.md`](design/arm-manual-control.md) (freedrive,
frames, click-to-reach), [`design/arm-telemetry-and-viz.md`](design/arm-telemetry-and-viz.md),
[`design/pinch-guard-consent.md`](design/pinch-guard-consent.md),
[`../docs/openarm-setup.md`](../docs/openarm-setup.md) (bring-up),
[`../software/openarm/patches/README.md`](../software/openarm/patches/README.md) (why upstream
changes are carried as scripts), and the choreography plan, which is the arms' largest consumer.

---

## What "WebXR teleop is closed out" means

The bar for Phases 0–2 and 6.1. Each clause is checked on the robot, with a headset, not at a desk.

1. **The robot runs what the repo says** — `openarm-up`, the unit, the sudoers rules, the patches
   and the certificate procedure are installed from the repo, and nothing on the robot is a hand
   edit the repo cannot see.
2. **An operator can start a session from the tablet alone** — activate, start teleop, open the
   printed URL on the Quest, work both arms and both grippers, stop, park, de-energize — with no
   SSH.
3. **Losing the headset stops the arm.** Stale pose data never drives motion, and re-engaging needs
   a fresh grip.
3a. **The arm answers to CH5.** A session engages only in `MANUAL`; leaving `MANUAL` holds the arm.
   With the tablet START and the grip clutch, this is the defence in depth of DEC-15 — all three
   layers exist, or teleop is not closed out.
4. **Teleop cannot put the arm into the head**, and cannot command a Cartesian speed nobody chose.
5. **Latency and loop health are measured with a headset connected**, against a number written
   down before the run.
6. **A replicator can set it up from the public docs.**

---

## This is not greenfield. Read this table first.

State on `main` at `7aa80d8` (PR #50), plus branches. *Desk-verified* means it was tested without
the arms and has not run on the robot.

| Thing | State | Where |
|---|---|---|
| CAN driver (DKMS `peak_usb`), udev names `arm_left`/`arm_right`, CAN-FD link units | **built, on robot** | `software/openarm/provision-openarm.sh`, `70-openarm-can.rules`, `openarm-can@.service` |
| Container + ROS 2 impedance controllers | **built by hand** — recipe in the docs, image not reproducible from this repo, upstream clones unpinned | `docs/openarm-setup.md` |
| Host and container RMW matched (FastDDS, shared memory off) | **built, on robot** | `openarm-up` |
| `openarm-up` start / activate / park / down / status | **built, on robot** | `software/openarm/openarm-up` |
| `openarm-up hold`, gripper-controller activation (#49) | **built, desk-verified only** — the robot's copy predates both | `openarm-up` |
| Stow poses (both arms at zero on opendubs2) | **built, on robot**; per-robot file `/etc/opendubs/openarm-stow.conf` | `arm-authority.md` |
| WebXR teleop bridge | **upstream code, run once on real arms** (2026-10-02) | `openarm.demos.real_teleop_demo` in the container |
| Headless-viewer patch (viewer was 67 % of the loop) | **built, on robot** | `software/openarm/patches/teleop-optional-viewer.py` |
| `opendubs-teleop.service` | **built, never installed** | `software/openarm/` |
| Dashboard TELEOP page: v1 arm view, readiness, loop health, arm ops | **built, merged, never pressed against the arms** | `frontend/teleop.js`, `armview.js`, `backend/openarm.py` |
| HTTPS certificate for the Quest Browser | **upstream mechanism only** — not provisioned, located or documented here | — |
| Analog gripper | **designed** (mapping agreed); binary at every upstream layer today | punch list 0e |
| Freedrive (`FREE`) | **designed**; controller capability unchecked | `arm-manual-control.md` |
| Arm-authority wrapper (lease expiry, `[AUTH]` precondition) | **designed, not built** | `arm-authority.md` |
| `[AUTH]` line from the StamPLC | **built, on robot — no consumer on the Thor** | `rc_serial_probe/src/main.cpp` |
| Head clearance | **show-time only** (`cue.py`, J2 ≤ 157°). **Teleop has no guard; the controller has no self-collision checking** | `cue.py`, `sim/clearance.py` |
| Native IK (`ssik`) | **missing** — the only prebuilt is for OpenArm **v2** | threads note D (branch only) |
| Data recording | **not found** | — |
| Unity / ros-sharp Quest app | **parked** — see P1 | `software/teleop/` |

---

## Standing rules

1. **Desk-verified is not verified.** The robot is behind the repo. Nothing is reported done until
   it has run on the arms, and a result from a desk, a sim or a run with no headset is labelled as
   such.
2. **The arms are v1.** "Supports OpenArm" means v2 unless it says otherwise. That confusion has
   cost two rounds already: the dashboard view first rendered v2 geometry, and the `ssik` prebuilt
   is for v2. Every new artifact states which version it is for.
3. **[`design/arm-authority.md`](design/arm-authority.md) is the specification.** This plan does
   not amend it; where they disagree, fix this plan or argue the change there.
4. **The e-stop cuts arm power, physically. That is settled and not negotiable.** Effort goes into
   authority and mode clarity in normal operation, not into e-stop behaviour. Do not over-rotate on
   crush hazard.
5. **Upstream code is changed on our fork, never in the container** (DEC-18). The teleop stack
   runs from an org fork pinned by the image build; every change is a commit there, never a hand
   edit in a running container. Changes are verified by **liveness** (process alive and ports
   listening 20 s after launch), not by compiling. Generic fixes are offered upstream as PRs from
   the fork; OpenDubs-specific ones (the CH5 gate, the J2 cap) stay on it.
6. **Activation is always deliberate, and the motors never time out.** Activating moves the arm to
   zero. A dead stack leaves an energized arm frozen at its last command indefinitely. Any change
   to bring-up, shutdown or session end is reviewed against both facts.
7. **Per-robot values stay on the robot**: CAN wiring, stow poses, gripper zeros. Never overwrite
   an on-robot file with a repo file, and install single files rather than `deploy-all.sh` where
   `deploy-all` would `rsync --delete` over them.
8. **Every experiment states its question and its kill criterion before it starts.**
9. **A test that needs a human at the bench is not a test**, where it can be avoided. Headset
   dropout, slow clients and stale data can be produced by a scripted WebSocket client. Only
   physical events — an e-stop, a person in a headset judging latency — need a person, and then
   **confirm they are there first**.
10. **Security is LAN-first and not a blocker** (P7).

---

## Decisions of record

Decisions already made and recorded in the companion notes or the sessions; each drives an item
below. Dates are when the decision was made.

| # | Decision | Source | Drives |
|---|---|---|---|
| DEC-1 | **WebXR is the baseline teleop** (2026-10-02). It has issues, mostly latency, but it has demonstrated it works; the Unity app never carried real headset data. | sessions | this plan; P1 |
| DEC-2 | **CH5 is the arm authority.** WebXR teleop is `MANUAL`; a learned policy or a planned motion is `AUTO`. The arms are not on CH7. | `arm-authority.md` | 1.9 |
| DEC-3 | **CH5 off is HOLD, not de-energize.** | `arm-authority.md` | 1.9 |
| DEC-4 | **The teleop session's engagement is the deadman** — the grip clutch plus tracking expiry. CH6 is not required for arm motion. "Teleop heartbeat is the deadman, and include FREE." | `arm-authority.md`, sessions | 1.1, 4.2 |
| DEC-5 | **Modes are OFF / HOLD / TELEOP / AUTO / FREE, and everything falls back to HOLD.** Mode and source are separate; arbitration is select-one with hand-off, never a blend. | `arm-authority.md` | Phase 3 |
| DEC-6 | **The upstream `OwnershipRegistry` is the arbiter, wrapped, not forked.** A thin node of ours adds lease expiry and the `[AUTH]` precondition, and releases leases itself on abort rather than relying on upstream recovery. | `arm-authority.md` | 3.2 |
| DEC-7 | **Arms start inactive; activation is explicit; park is separate from down.** `down` refuses unless the arm is within 5° of stow or `--drop` is given. | `openarm-setup.md` | 0.1 |
| DEC-8 | **Teleop runs as a systemd unit with `Restart=no`**, not as a dashboard child process. Dashboard arm buttons shell out to `openarm-up`, so the rules live in one place. | `opendubs-teleop.service`, `openarm.py` | 0.2 |
| DEC-9 | **The tablet shows the arms from `/joint_states` over rosbridge, not from the viewer**, and the teleop bridge runs headless. | `arm-telemetry-and-viz.md` | 0.4 |
| DEC-10 | **Analog gripper:** trigger 0 → 0.9 opens the jaw proportionally; the top 10 % is full close at grip effort; the command is a normalised `t` so the left/right inversion stays absorbed. Consistent with the choreography plan's 0–1 grippers (its DEC-4). | sessions, punch list 0e | 4.1 |
| DEC-11 | **Freedrive is per arm, needs CH5 `MANUAL`, is latched, auto-exits** (lease end, ~60 s idle, any arm command) **and holds where the arm is** by ramping stiffness back up. Started from an on-screen button. | `arm-manual-control.md` | 4.2 |
| DEC-12 | **The measured J2 182° head contact outranks the simulator**, which is 6° optimistic. | `cue.py`, `sim/clearance.py` | 1.3 |
| DEC-13 | **TELEOP page:** landscape, controls on top, visual rather than text, separate activate and de-energize controls, real v1 geometry. | sessions | built |
| DEC-14 | **The IK solver is swappable at the adapter boundary** — streaming for teleop, trajectory for planned motion, a safe stop, and the modes it honours. | `arm-authority.md` | 5.3 |
| DEC-15 | **Teleop is low-friction, with three layers of defence: an affirmative START on the tablet, the CH5 mode on the RC, and the grip clutch as the deadman** (DEC-4 stands; CH6 is not required). The unit keeps its `[Install]` section and is installed but not enabled (Q1, 2026-10-07). | Q1 | 0.2, 1.8, 1.9 |
| DEC-16 | **Teleop's Cartesian speed limit stays at 1.0 m/s, verified and named, with a lower demo setting for events.** Interim: **revisit once teleop is working better** (after Phase 2's latency and rate work) — not a forever setting. | Q2 | 1.2, 2.6 |
| DEC-17 | **The head guard is a J2 cap at 157° now**, checked by hand for other ways in; **the head goes into the collision model with the native IK** rather than a bridge keep-out zone, unless the hand check finds a way in. | Q3 | 1.3, 5.5 |
| DEC-18 | **The upstream teleop stack is copied into the org as a PRIVATE repo**, not a public GitHub fork, because its first commit publishes the upstream author's unreleased drift (2026-10-07). `UW-Allen-School-Robotics/openarm`: `main` mirrors upstream; **`opendubs`** (default) is what the robots run, starting from the as-found commit `1f0f8f1`. Our changes are ordinary commits on `opendubs`; the image build pins it; `software/openarm/patches/` is retired. Generic fixes go upstream as PRs once DEC-20 is answered. **`openarm_impedance_control` is not copied yet**: the container's checkout is clean at upstream HEAD, and the inactive-start setting was upstreamed (`b517999`); it is copied only if 1.3's J2 cap lands in it. `mj_manipulator`, `mj_manipulator_ros`, `openarm_robot-code` and `ssik` are copied when their phase needs them (3, 4.1, 6.3, 5.1). | Q4 | 0.5, 6.3, rule 5 |
| DEC-19 | **`feat/openarm-integration` is salvaged and closed, together with its author.** It predates the October hardening, so merging it would revert `main` (teleop unit, patches, ~346 lines of `openarm-up`, ~250 of the setup doc). Kept: the submodule pinning (repointed at the fork), `openarm_bringup.sh` (its `bake-image.sh` / `bake_image.sh` call fixed, and any step `openarm-up` lacks folded in), and the `ws_state` exclude in `deploy-all.sh`. The sibling checkout `opendubs-openarm` is retired afterwards. | Q5 | 6.3 |
| DEC-20 | **Ask the upstream author whether the private copy may go public** (as a fork, enabling upstream PRs). Until they answer, it stays private and nothing of theirs is published. | 2026-10-07 | 0.5 |

---

## Decisions needed

Raised by this consolidation and **all decided by the project team on 2026-10-07** (DEC-15 to DEC-19). Kept here so the reasoning stays next to the answer.

| # | Question | Recommendation |
|---|---|---|
| Q1 | ~~Should `opendubs-teleop.service` ever start at boot?~~ | **DECIDED, 2026-10-07: keep `[Install]`, do not enable; start from the tablet.** The arms should work without excessive friction. The defence in depth is **an affirmative START on the tablet, the CH5 mode on the RC, and the grip clutch as the deadman** — not extra mechanism in the unit. Recorded as DEC-15. |
| Q2 | ~~What Cartesian speed may teleop command?~~ | **DECIDED, 2026-10-07: keep 1.0 m/s, verify it is enforced, and add a lower demo setting.** An interim value, to be **revisited once teleop is working better** (after Phase 2) — not a permanent limit. Recorded as DEC-16. |
| Q3 | ~~How is the head kept out of reach during teleop?~~ | **DECIDED, 2026-10-07: a J2 cap now, plus a hand check for other ways in; the head joins the collision model in Phase 5.** Recorded as DEC-17. |
| Q4 | ~~Carry patches, or fork upstream under the org?~~ | **DECIDED, 2026-10-07: copy into the org now, privately (see DEC-18, DEC-20)**, before the Phase 1 changes, so they land as reviewable commits. Counting showed close-out alone brings ~7 upstream changes, past any sensible patch-script threshold. Recorded as DEC-18. |
| Q5 | ~~`feat/openarm-integration`~~ | **DECIDED, 2026-10-07: salvage, then close — with the branch's author.** Recorded as DEC-19. |

---

## Team requests

Asks made across the sessions, tracked so none is lost. Status is updated in place.

| # | Request | Status | Item |
|---|---|---|---|
| R1 | **Teleop setup docs** | open | 6.1 |
| R2 | **Day-to-day bring-up docs** | partial — `docs/openarm-setup.md` | 6.2 |
| R3 | **UI and mode selection for the arms**, including which teleop source they answer to | open | 3.3, 3.5 |
| R4 | **Contracts and modularity, so the IK solver can be swapped** (e.g. for NVIDIA's stack) | designed | 5.3 |
| R5 | **Arm visualisation on the tablet, with efficient telemetry** | built, unverified on robot | 0.4 |
| R6 | **Run the WebXR teleop first-hand and investigate the latency** | run once | Phase 2 |
| R7 | **Analog gripper** | designed | 4.1 |
| R8 | **Freedrive with gravity compensation** for setting poses | designed | 4.2 |
| R9 | **Automatic stowing for long-range navigation** | park built; nothing automatic | 4.3 |
| R10 | **Pendant ideas from UR and xArm** for a bimanual layout | partly in the page | 3.5 |
| R11 | **Teach choreography poses from teleop** (choreography DEC-3) | depends on 0.4 | choreography 6.3 |
| R12 | **Arm-vendor portability** (xArm 5 or YAM on robot 1) | its own conversation | P2 |

---

## Phase 0 — make the robot match the repo

*Install what is already built, then verify it on the arms. Nothing new is designed here.*

| # | Item | Done when |
|---|---|---|
| 0.1 | **Install the current `openarm-up`** by copying the single file, not with `deploy-all.sh`. Verify the gripper controllers come up on activate and that `hold` re-commands the measured pose. | `hold` on a stationary, energized arm produces **no visible motion and < 0.5° on any joint** in `/joint_states`. A twitch means the measured-position read is wrong — stop there. |
| 0.2 | **Sudoers, then the unit, then the page — in that order.** Install `opendubs-sudoers` and `opendubs-teleop.service` (installed, not enabled — DEC-15), and add the dashboard user to `systemd-journal` so loop health can read the journal. Then press every TELEOP-page button against the arms. | Every button does what its label says on the robot, and the 409-when-busy path is seen once. |
| 0.3 | **The certificate, made reproducible.** The cert lives in the container at `/openarm_ws/src/openarm/certs/{cert,key}.pem` (git-ignored; last generated 2026-10-02) — found 2026-10-07.  make generation part of provisioning with a SAN that includes the address the bridge prints, and document accepting it on **both** :8444 and :8766 — a refused WSS handshake fails silently, it does not warn. | A fresh certificate is generated by a repo script; a browser that has never seen the robot gets from the printed URL to a live session by following the docs. |
| 0.4 | **`/joint_states` reaches the tablet** through the host's rosbridge at the throttled rate. | The arm view moves with the arm and goes STALE within 2 s of `openarm-up down`. Measured rate recorded. |
| 0.5 | **The org copy, and the drift captured** (DEC-18). ~~Snapshot the container's tree~~ **DONE, 2026-10-07:** the container's checkout *does* have a `.git` now (the patches README says it does not — stale), sitting at upstream `85b46ce` with 8 uncommitted files, +574/−111. That diff is `1f0f8f1` on `opendubs` in the private org repo. It contains the headless viewer, the 8766/8444 ports and `target_hz` 150 — and **TCP_NODELAY survived**. **Remaining:** repoint the container's checkout at the org repo's `opendubs` branch (no file changes, so the running teleop is unaffected); record each port's reason (8766 presumably avoids Foxglove's 8765 — confirm); retire `software/openarm/patches/`, pointing its README at the org repo. | The container's `git status` is clean on `opendubs`, and liveness (rule 5) is unchanged. **Prerequisite of every Phase 1 change to upstream code.** |
| 0.6 | **Notes that disagree with the code.** Punch-list goals 5 and 7 say unbuilt; `arm-telemetry-and-viz.md` lists the RMW issue as open (fixed in `openarm-up`); `capability-backlog.md` #7, `software/teleop/README.md` and `state-machine-review.md` describe Unity-only teleop and a `TELEOP_CONTROL` state that contradicts DEC-2/DEC-4; `arm-authority.md` says the deadman is already implemented (see 1.1); `docs/openarm-setup.md` never says v1; `docs/hardware-bom.md` lists the arms as planned; `cue.py`'s gripper range disagrees with punch-list 0e; `assets/arm/README.md` gives two mesh sizes. | Each says what the code does. Public docs follow the replicator voice. |
| 0.7 | **Land the threads note's teleop content.** `notes/threads-2026-10.md` exists only on `notes/threads-and-ssik`. Fold threads D (ssik) and F (compliance) into Phase 5 here; route the rest to its own home. | Nothing this plan depends on lives only on an unmerged branch. |

## Phase 1 — a session is safe to hand to someone

*Correctness of the teleop session itself. 1.1 is the most important item in the plan.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 1.1 | **Stale headset data can drive the arm.** In upstream HEAD, `bridge.is_tracked()` (`bridge.py:269`, 0.5 s) is defined and **never called**; `poll_and_update_clutches()` (`webxr_teleop.py:219`) feeds the clutch from `get_state()`, which returns the last payload for ever. The page's 400 ms timeout only greys a card. So a headset that drops out with the grip held leaves the clutch engaged; when the stream resumes, the arm jumps toward the new pose at up to the step clamp. **CONFIRMED in the robot's copy, 2026-10-07 — and worse:** its drift also sets `ping_interval=None`, disabling the WebSocket keepalive, with a comment that `is_tracked()` "already detects a stale/dead connection". Nothing calls it, so a dead headset connection is detected at **neither** level. Then patch: untracked ⇒ disengage ⇒ HOLD; re-engage needs a grip edge on fresh data. | Tested by a **scripted client** that streams poses with grip held and then stops, and later resumes from a distant pose: the arm holds within 0.5 s of the stop and does not move on resume until a new grip press. Then once with the real headset (Wi-Fi off mid-grip). |
| 1.2 | **The speed limit we have is real** (DEC-16). Upstream's `max_cartesian_speed = 1.0` m/s is the working limit — the 5 cm/tick step clamp (7.5 m/s at 150 Hz) only bites on target jumps, which 1.1 removes — but whether the real-arm path enforces it is unmeasured. Make it a named, logged setting, and add a **demo setting** (~0.5 m/s; ISO 10218's 250 mm/s manual reduced speed is the reference point) selectable for events with crowds. | A step input from the scripted client never drives the TCP, measured from `/joint_states`, above the active setting — for both 1.0 and the demo value. |
| 1.3 | **Teleop cannot reach the head** (DEC-12, DEC-17). (a) **Cap J2 at 157°** — the measured 182° contact less `cue.py`'s 25° margin — in the arm's joint limits, mirrored left/right, as a commit on the fork (DEC-18). It costs only the 157–182° band with the elbow folded, which is the contact region; extended, the 1.2 m box already stops J2 near 140°. (b) **The hand check:** the 182° figure was measured one joint at a time, so with the arm de-energized and supported, move it by hand through combined poses (elbow up, forearm folded inward) to find whether anything other than J2 brings the arm to the head. `sim/clearance.py` can pre-screen candidates first; it is 6° optimistic, so it narrows the search and does not replace it. | (a) A teleop session driving J2 toward the head stops at 157° on both arms, with the elbow folded. (b) A recorded answer, with a person present: if no other pose reaches the head, the cap is the whole guard until 5.5; if one does, a keep-out zone for the TCP in the bridge is added to close-out. |
| 1.4 | **One slow client cannot wedge the session.** A non-draining second client filled its socket to 64 KB on 2026-09-24. Bounded send queue with drop-oldest, and a stated policy for a second client (refuse, or observe-only). | The scripted client connects as a second, non-reading client for 60 s: the first client's loop health and latency are unchanged. |
| 1.5 | **The software abort is not an e-stop.** It logs "E-Stop activated". | Renamed; nothing in the stack calls a software stop an e-stop. |
| 1.6 | **What happens across a real e-stop**, observed rather than inferred. Contactor opens mid-session: the arms fall limp; what does the bridge do, and what happens on contactor restore — does a stale target or the activate-to-zero sweep fire? Never observed. | A recorded observation, with a person present (rule 9), and a written recovery procedure. If restore can move an arm unasked, that is a Phase 1 bug. |
| 1.8 | **A Thor-side consumer for `[AUTH]`** (moved from Phase 3, DEC-15). The StamPLC broadcasts the CH5 mode; nothing on the Thor parses it. | Mode changes on CH5 appear on the Thor within one telemetry period, and a missing or stale `[AUTH]` reads as *not* `MANUAL`. |
| 1.9 | **Teleop answers to CH5** (moved from Phase 3; DEC-2, DEC-3, DEC-15). Outside `MANUAL` a session cannot engage; CH5 leaving `MANUAL` ⇒ HOLD. Does **not** need the lease node (3.2) — a gate in the bridge, as a commit on the fork (DEC-18), is enough for close-out. | Flipping CH5 mid-grip holds the arm, every time, from the RC alone; re-engaging needs `MANUAL` and a fresh grip. |
| 1.7 | **Loop health cannot say "good" when it does not know.** No warning currently reads as 0 % over budget, a multi-line warning would not match, and the 60-line window can show an old one. | No data reads as *no data*; a stale reading carries its age. |

## Phase 2 — latency and rate (R6)

*The thing DEC-1 named as WebXR's weakness. Measure first; every figure so far was taken with no
headset connected.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 2.1 | **A latency number, with a method.** Timestamp the pose in the browser, on receipt at the bridge, on command publish, and on the `/joint_states` sample that first reflects it. Write the acceptable figure down **before** the run. | Per-stage latency, headset connected, both arms, reported as median and p95. If the bridge-to-command stage dominates, Phase 5 is pulled forward. |
| 2.2 | **`target_hz` 250 with the viewer off.** The container runs 150 against upstream's 250; the lower rate was a workaround for the viewer cost. | < 1 % of ticks over budget for 5 minutes with a headset connected, or 150 stays and the reason is recorded. |
| 2.3 | **`jetson_clocks` policy.** It cut tick drops roughly threefold on 2026-09-24 (the CPU had been parked at its 972 MHz floor) and does not persist. | A decision — on for teleop sessions, always, or never — and the mechanism that applies it. |
| 2.4 | **Sleep overshoot.** At 250 Hz the residual overruns were `time.sleep()` waking late, not compute. Options: spin-wait the last 0.5 ms, `SCHED_FIFO` for the loop, or raise the warning threshold. | Choose by 2.2's numbers, not ahead of them. |
| 2.5 | **Controller-manager deadlines under teleop load.** 400 Hz in Docker with no real-time scheduling; two late cycles in ~8,000 at idle on 2026-09-21. | Measured during a 2.2 run. Acceptable or not is stated against the controller's own tolerance. |
| 2.6 | **Revisit the speed limit** (DEC-16) once 2.1–2.4 have made teleop work better. | A new default chosen from operator experience at the improved latency, or 1.0 m/s kept with the reason recorded. |

## Phase 3 — authority and modes (R3)

*After close-out. Specified in `arm-authority.md`; this is the build.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 3.1 | ~~Thor-side `[AUTH]` consumer~~ **moved to 1.8** (DEC-15). | — |
| 3.2 | **The arm-authority node** (DEC-6): lease expiry, the `[AUTH]` precondition, release on abort. Absorbs 1.9's bridge gate. | A lease holder that dies loses the lease within its expiry; nothing needs upstream recovery. |
| 3.3 | ~~Teleop answers to CH5~~ **moved to 1.9** (DEC-15); 3.2 later replaces the gate with the lease. | — |
| 3.4 | **One CH5 vocabulary**, owned by the choreography plan's 0.6 (`STANDBY / MANUAL / AUTO`). Note the conflict it records: `robot_state.py`'s `ENABLE` matrix forbids the arms in the middle position. | Closed there; this plan consumes it. |
| 3.5 | **The tablet says who has each arm**: mode, lease holder, source. | Readable at arm's length on the 10″ tablet. |
| 3.6 | **Report the abort-recovery gap upstream**: `abort_all()` keeps ownership, so a re-engage can neither acquire nor preempt. | Issue filed; our node does not depend on the fix. |

## Phase 4 — grippers and manual control

| # | Item | Kill criterion / done when |
|---|---|---|
| 4.1 | **Analog gripper** (DEC-10). Needs a new API through the upstream manipulator library's three gripper implementations and its ROS client, which is in a repo we do not have. Per-side calibration stays on the robot. | Same trigger, same jaw opening on both sides; no calibration value committed. |
| 4.2 | **Freedrive** (DEC-11). First question: **can the impedance controller do it** — stiffness to near zero with gravity compensation? Its hazard is unique: a stack that dies in FREE leaves near-zero torque, so the arm falls. | If the controller cannot, the design is re-scoped before any UI is built. If it can, the heartbeat (DEC-4) covers FREE. |
| 4.3 | **Stow, properly.** The page's "° from stow" is measured from zero, not from `openarm-stow.conf` — right on opendubs2, wrong for any robot whose stow is not zero. Then define what triggers **automatic stow** before long-range driving (R9). | The readout uses the stow file; the trigger is a written rule. |
| 4.4 | **Click-to-reach vs jogging** — prototype one against the other. | Per `arm-manual-control.md`. |

## Phase 5 — IK and the adapter boundary (R4)

| # | Item | Kill criterion / done when |
|---|---|---|
| 5.1 | **Build `ssik` against the v1 URDF.** The native backend solves in 0.195 ms against a 6.7 ms budget, but its only prebuilt is v2; v1 has different joint origins and axes, a −90° mount, different link names (`openarm_*_link0`, `openarm_*_hand_tcp`) and a gripper past the flange. | Agrees with the current Python IK within a stated tolerance on a stated pose set, both arms. |
| 5.2 | **Move teleop onto `ssik.teleop`.** It brings a world/tool clutch (the frame selector `arm-manual-control.md` wanted), OK / LIMITED / HELD status with reasons (better than loop health: it says *why* the arm stopped following), and 7-DOF redundancy control. | The TELEOP page shows the status reason; our local clutch and IK code is deleted. |
| 5.3 | **Write the adapter contract down** (DEC-14) — streaming, trajectory, safe stop, honoured modes — so a vendor or solver swap is one implementation. | A second implementation could be written from the document alone. |
| 5.4 | **Force-triggered compliance** (threads F), after 5.2. | Argued from the ~1–2° steady-state error the impedance controller already shows. |
| 5.5 | **The head in the collision model** (DEC-17). Upstream's collision shadow has no head, lift or chassis, and its teleop `safety_mode` is `allow`. Give it the head from `sim/` and turn checking on, alongside 5.2. | A teleop target that would put any link into the head is held, in any pose — and the J2 cap can then be relaxed or kept as a backstop, by decision. |

## Phase 6 — documentation and reproducibility (R1, R2)

| # | Item | Done when |
|---|---|---|
| 6.1 | **A public teleop page**: network, certificate (0.3), Quest Browser steps, control mapping (grip = clutch, trigger = gripper), what the tablet shows, troubleshooting (silent WSS refusal, stale arm view, loop health). | An outside replicator gets to a live session from it. |
| 6.2 | **Finish the bring-up doc** — v1 stated, the daily sequence (up → activate → teleop → park → down), and the hazards of rule 6. | Matches the tablet's buttons one to one. |
| 6.3 | **A reproducible container image**: the org fork pinned (DEC-18), the other upstream clones pinned, gripper-controller config included. Salvage `feat/openarm-integration` with its author (DEC-19), close it, retire the sibling checkout. | A second robot builds the image from the repo; no unmerged branch carries arm work `main` lacks. |

---

## Stub register

Everything that pretends, so nothing is forgotten at robot time.

| Stub | Where | Replaced by |
|---|---|---|
| `[AUTH]` broadcast with no consumer | StamPLC firmware | 3.1 |
| Loop health reads 0 % when it has no data | `backend/openarm.py` | 1.7 |
| Hand-tracking dots never updated | `index.html` `tpHandL` / `tpHandR` | remove, or 5.2 |
| "° from stow" measured from zero | `frontend/teleop.js` | 4.3 |
| Teach's fake joint source | choreography composer | choreography 6.3, after 0.4 |
| `ssik` prebuilt pinned for v2 | `feat/openarm-integration`, sibling checkout | 5.1 |

## Parked

Recorded so they stop recurring as surprises, and not being worked on.

- **P1 — The Unity / ros-sharp Quest app.** A later nice-to-have. State as found: a Unity Version
  Control workspace, not git (its two scripts are mirrored in `software/teleop/unity/`); five
  `/quest/*` topics reached rosbridge at ~50 Hz in editor Play mode on 2026-06-12 with zero poses;
  an APK was built and installed but never connected; it publishes input only and receives
  nothing; its rosbridge URL is hard-coded to a retired address. Revisit only with a reason WebXR
  cannot meet.
- **P2 — Arm-vendor portability** (xArm 5 or YAM on robot 1). Its own conversation; 5.3 is the
  hook.
- **P3 — Data recording** (rosbag, LeRobot). Nothing exists. Add when a policy or a study needs it.
- **P4 — Quest teleop into Isaac Sim** for data collection.
- **P5 — A full-body planner.** The arms are planned alone today; the lift cannot be reached by
  them.
- **P6 — Physical freedrive buttons**, on the arm or the robot. On-screen first (DEC-11).
- **P7 — Robot security.** LAN and Wi-Fi first, Thor login second; one parked discussion, shared
  with the choreography plan.
