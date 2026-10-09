# Work plan — the arms: WebXR teleop, bring-up and authority

The plan of record, **agreed by the project team on 2026-10-07**, in the manner of
[`workplan-tab5-idf.md`](workplan-tab5-idf.md) and
[`workplan-choreography.md`](workplan-choreography.md): work proceeds through these phases, anything
not in a phase, the team requests or the stub register is not being worked on, and finished items
are struck through with a date.

Written to consolidate what had been spread across a month of sessions, three design notes, an
unmerged punch list and a branch-only threads note. **The near-term goal (2026-10-08): a stable, documented baseline that a student builds WebXR teleop on** (DEC-26). The project team's role on teleop becomes support: keep the arm interfaces under that work steady and documented, and fix what blocks it. Building out the remaining teleop items is the student's work; authority, IK and manual control follow.

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
3a. **The arm answers to CH5** (DEC-21). Nothing moves an arm with CH5 OFF; a session engages only
   in `MANUAL`; leaving `MANUAL` holds the arm.
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
10. **No arm motion unless CH5 permits it, verified — not assumed** (DEC-21). Until 0.8–0.9 and
    1.9 enforce it, the procedure is: read `[AUTH]` off the wire (below), and do not activate, park,
    engage or run a motion test unless it shows the mode DEC-21 requires. Re-check immediately before
    each motion step. A person saying the switch is set is not the check; the line is.
11. **Security is LAN-first and not a blocker** (P7).

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
| DEC-15 | **Teleop is low-friction, with three layers of defence: an affirmative START on the tablet, the CH5 mode on the RC, and the grip clutch as the deadman** (DEC-4 stands; CH6 is not required). The unit keeps its `[Install]` section and is installed but not enabled (Q1, 2026-10-07). | Q1 | 0.2, 0.8, 1.9 — **tablet START superseded by DEC-24 once Phase A lands** |
| DEC-16 | **Teleop's Cartesian speed limit stays at 1.0 m/s, verified and named, with a lower demo setting for events.** Interim: **revisit once teleop is working better** (after Phase 2's latency and rate work) — not a forever setting. | Q2 | 1.2, 2.6 |
| DEC-17 | **The head guard is a J2 cap at 157° now**, checked by hand for other ways in; **the head goes into the collision model with the native IK** rather than a bridge keep-out zone, unless the hand check finds a way in. | Q3 | 1.3, 5.5 |
| DEC-18 | **The upstream teleop stack is copied into the org as a PRIVATE repo**, not a public GitHub fork, because its first commit publishes the upstream author's unreleased drift (2026-10-07). `UW-Allen-School-Robotics/openarm`: `main` mirrors upstream; **`opendubs`** (default) is what the robots run, starting from the as-found commit `1f0f8f1`. Our changes are ordinary commits on `opendubs`; the image build pins it; `software/openarm/patches/` is retired. Generic fixes go upstream as PRs once DEC-20 is answered. **`openarm_impedance_control` is not copied yet**: the container's checkout is clean at upstream HEAD, and the inactive-start setting was upstreamed (`b517999`); it is copied only if 1.3's J2 cap lands in it. ~~`mj_manipulator`, `mj_manipulator_ros`, `openarm_robot-code` and `ssik` are copied when their phase needs them (3, 4.1, 6.3, 5.1).~~ **Narrowed by DEC-27 (2026-10-09): `openarm` is the only repository copied into the org.** Everything else is consumed from its upstream. | Q4 | 0.5, 6.3, rule 5 |
| DEC-19 | **`feat/openarm-integration` is salvaged and closed, together with its author.** It predates the October hardening, so merging it would revert `main` (teleop unit, patches, ~346 lines of `openarm-up`, ~250 of the setup doc). Kept: the submodule pinning (repointed at the fork), `openarm_bringup.sh` (its `bake-image.sh` / `bake_image.sh` call fixed, and any step `openarm-up` lacks folded in), and the `ws_state` exclude in `deploy-all.sh`. The sibling checkout `opendubs-openarm` is retired afterwards. | Q5 | 6.3 |
| DEC-20 | **Ask the upstream author whether the private copy may go public** (as a fork, enabling upstream PRs). Until they answer, it stays private and nothing of theirs is published. | 2026-10-07 | 0.5 |
| DEC-21 | **CH5 gates every arm motion.** OFF: hold and status only — de-energize only from stow; **nothing that moves an arm** (activate drives to zero, so it counts). MANUAL: activate, park, teleop engage, freedrive, bench tests. AUTO: activate, park, shows and planners — not teleop engage. **A missing or stale `[AUTH]` is OFF.** Enforced where it cannot be bypassed: the Thor's view of CH5 (0.8), `openarm-up` (0.9) and the teleop bridge (1.9). Raised 2026-10-07 when a bench test was about to activate the arms with CH5 off and nothing would have stopped it. **Amended by DEC-28, IN EFFECT 2026-10-09: activation is off this list** — A1 was verified on the robot that day (a joint 42° off zero moved 0.017°), so activating no longer moves an arm. Everything else here stands: nothing that moves an arm in OFF. | 2026-10-07 | 0.8, 0.9, 1.9, rule 10 |
| DEC-22 | **Target design: the Quest headset mounted on the robot, the operator unhooded.** The headset only tracks the controllers and gives the operator nothing back, so it rides on the robot's upper back facing rearward, rigid with the arm bases; the operator stands **behind the robot facing the way it faces**, within its view. Controller poses are taken **relative to the headset** and mapped through one fixed mount calibration, so the session's starting orientation stops mattering. **Pending M1** — if a stationary headset cannot track the controllers steadily at operator distance, this is dropped. A worn headset returns only if telepresence (camera video to the operator) is wanted. | 2026-10-07 | Phase M |
| DEC-23 | **Q6 decided: release and dropout stop the arm where it is** (measured joints). **Q7 deferred** — the corrected session felt right. **Home is hold-to-run on X (left) / A (right)**, one straight line in joint space to the robot's stow pose at 15 deg/s, CH5 MANUAL only. | 2026-10-07 | 4.3 |
| DEC-24 | **The arms follow the base's model.** The base arms its ESCs at power-on with CH5 OFF and drives only in MANUAL with the deadman held. The arms do the same: **power-on → energized and holding; motion only in MANUAL, and for teleop only with the clutch held.** Bring-up leaves routine operation; PARK, HOLD and DE-ENERGIZE become maintenance actions. **Prerequisite: activation that does not move the arm** (Phase A) — today activation drives every joint to zero. Consistent with DEC-3 (OFF is HOLD). Once A1–A3 hold, the teleop bridge runs always, which **supersedes DEC-15's tablet START**. | 2026-10-07 | Phase A |
| DEC-25 | **Dashboard arm actions must answer at once, or say what they are doing.** Measure where the time goes, stream progress to the page now, then replace per-step command-line calls with a long-running arm service. | 2026-10-07 | 0.10–0.12 |
| DEC-26 | **Teleop is handed to a student to build; the project team supports it.** The deliverable from this plan is a stable, documented baseline: [`docs/teleop-setup.md`](../docs/teleop-setup.md) (setting up and operating teleop) and [`docs/arm-dashboard.md`](../docs/arm-dashboard.md) (every tablet arm button and message, each under its registry code). **The interfaces under the student's work stay steady** — `openarm-up`'s commands and messages, the two arm controllers' names, `/joint_states`, the bridge's ports (8444, 8766) and service, and the tablet's arm buttons; a change to any of them is announced and documented in the same PR. | 2026-10-08 | 6.1, 6.2 |
| DEC-27 | **The arm stack's canonical setup: upstream-first with a manifest, forking only `openarm`** ([`design/arm-sources.md`](design/arm-sources.md), option B). Every component we only consume comes from its maintainer at a release or tag, pinned by full commit in #107's `repos.lock`: enactic's `openarm_ros2`, `openarm_description` and `openarm_can`; the lab's (personalrobotics) `mj_manipulator`, `mj_environment`, `mj_manipulator_ros`, `asset_manager`, `mj_viser`, `prl_assets`, and `ssik`, `sstsr`, `sscbirrt` from PyPI. **The student forks of the lab's libraries are dropped**; their generic changes (ArmGroup, the hardware context, the environment fix, the SSIK fix if still needed) go to the lab as PRs credited to their author, carried meanwhile as `.patch` files in the manifest's overlay against the pinned upstream commit. A1 goes to enactic the same way. **`openarm` stays in the org** (DEC-18, narrowed): `main` tracks the author's repository, `opendubs` is rebased onto it, the student works on feature branches with PRs into `opendubs` (DEC-26 unchanged). SSIK is used as its authors intend: from PyPI, with our V1 solvers generated by `ssik build` from a pinned enactic V1 URDF and committed with the command and version that made them. `setup.sh`'s clone-at-tips is replaced by the manifest. **No upstream PR is opened until the author consents** (the message is with the user). | 2026-10-09 | 6.3, Q9, 5.1 |
| DEC-28 | **The arms heal themselves, and only movement is gated** ([`design/arm-authority.md` § Amendment, 2026-10-09](design/arm-authority.md#amendment-2026-10-09--adopted-self-healing-arms-motion-gated-like-the-base-leases)). An arm is either **HOLDING** (energized at its measured pose) or **moving under a live lease**; motion needs CH5 permitting the source's kind, that source's lease, and its presence signal (clutch, deadman, hold-to-run), and losing any of them returns the arm to HOLD at its measured pose — a dropped lease never causes motion. **Activation stops being a safety layer once A1 is verified on the robot.** **Self-heal after an e-stop with no second human act**: clearing the e-stop is the one act, as for the base. **Tablet ownership controls**: the holder per arm, RELEASE to HOLD, TAKE OVER = RELEASE plus the new source's own acquire; the tablet is never a motion source. Phase 1 (A1 + A7) started 2026-10-09. The maintenance lock is **Q10, open**. **Confirmed 2026-10-09: self-heal undoes disruptions, never a person's deliberate choice.** An arm someone de-energized stays limp, through e-stops and power returns, until someone presses ACTIVATE. | 2026-10-09 | A1, A7–A10, DEC-21, DEC-24 |

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
| Q6 | ~~Release holds the last target~~ | **DECIDED (DEC-23): stop where it is**, built in `4530665`/`5ffb96a`, robot retest pending. |
| Q7 | ~~World-frame wrist rotation~~ | **DEFERRED (DEC-23)** — after the fixes the wrist felt right; revisit only if it recurs. |
| Q8 | **Should a dashboard restart drop arm power?** The supervisor treats stale telemetry during a restart as a fault and asserts a software e-stop, which opens the contactor (F17). Fail-safe, but it costs an arm recovery every time the dashboard is redeployed. | Decide with Phase A: once A2 makes recovery automatic and motion-free, a brief e-stop on restart costs nothing; until then, consider a grace period for a restart the supervisor itself initiated. |
| Q9 | ~~Mirror the upstream repositories into the org?~~ | **RETIRED, 2026-10-09 (DEC-27):** only `openarm` is copied; every other repository is pinned at its maintainer's release. The risk Q9 addressed — a pin breaking if an owner deletes or rewrites a repository — is covered by archiving each built image with its source (the backup, on the project team's machine, at the next robot session). |
| Q10 | **A maintenance lock for the arms?** A tablet toggle keeping both arms limp, with no self-healing, while someone works on them, surviving power cycles (A9). | **OPEN, 2026-10-09** — not selected, not rejected. Until decided, the self-heal-near-hands risk is covered as stated in the amendment's *Residual risks*: a deliberate DE-ENERGIZE is not a disruption, so the keeper leaves it alone until ACTIVATE; otherwise the risk is accepted, and clearing the e-stop is the act that re-energizes the arms, as it does the drive. Decide before phase 2's robot check. |

---

## Team requests

Asks made across the sessions, tracked so none is lost. Status is updated in place.

| # | Request | Status | Item |
|---|---|---|---|
| R1 | **Teleop setup docs** | **done, 2026-10-08** — `docs/teleop-setup.md` | 6.1 |
| R2 | **Day-to-day bring-up docs** | **done for the tablet, 2026-10-08** — `docs/arm-dashboard.md`, with `docs/openarm-setup.md` for the shell | 6.2 |
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
| R13 | **Arm buttons are slow and silent** — park and activate take a long time to start moving, with nothing shown meanwhile. Speed up if possible; otherwise annunciate the in-progress state. | **silent: fixed 2026-10-08 (#84)** — buttons show progress and refusals; **slow: open** | 0.10–0.12 |
| R14 | **Arms behave like the base**: armed at power-on with CH5 OFF, moving only in MANUAL and, for teleop, with the clutch held. | adopted (DEC-24) | Phase A |

---

## Phase 0 — make the robot match the repo

*Install what is already built, then verify it on the arms. Nothing new is designed here.*

| # | Item | Done when |
|---|---|---|
| 0.1 | ~~Install the current `openarm-up`~~ **DONE, 2026-10-07/08.** Installed by single-file copy, then updated with #59, #73 and #84; robot and `main` match. Verified on the robot: the gripper controllers come up on activate (2026-10-07), activate starts a stopped stack (2026-10-08, first used 12:43), and activate recovers an arm after an e-stop (2026-10-08, F19). **Still open:** the `hold` no-motion check. | `hold` on a stationary, energized arm produces **no visible motion and < 0.5° on any joint** in `/joint_states`. |
| 0.2 | **Sudoers, then the unit, then the page — in that order.** Install `opendubs-sudoers` and `opendubs-teleop.service` (installed, not enabled — DEC-15), and add the dashboard user to `systemd-journal` so loop health can read the journal. Then press every TELEOP-page button against the arms. | Every button does what its label says on the robot, and the 409-when-busy path is seen once. |
| 0.3 | **The certificate, made reproducible.** The cert lives in the container at `/openarm_ws/src/openarm/certs/{cert,key}.pem` (git-ignored; last generated 2026-10-02) — found 2026-10-07.  make generation part of provisioning with a SAN that includes the address the bridge prints, and document accepting it on **both** :8444 and :8766 — a refused WSS handshake fails silently, it does not warn. | A fresh certificate is generated by a repo script; a browser that has never seen the robot gets from the printed URL to a live session by following the docs. |
| 0.4 | **`/joint_states` reaches the tablet** through the host's rosbridge at the throttled rate. | The arm view moves with the arm and goes STALE within 2 s of `openarm-up down`. Measured rate recorded. **Re-investigate (F15):** on the tablet itself, in OFF and MANUAL, record the ROSBRIDGE badge, LIDAR, and the arm-view badge after a reload; if the page is at fault, make it say *which* — no connection, no view, or no messages — instead of one "no joint data". |
| 0.5 | **The org copy, and the drift captured** (DEC-18). ~~Snapshot the container's tree~~ **DONE, 2026-10-07:** the container's checkout *does* have a `.git` now (the patches README says it does not — stale), sitting at upstream `85b46ce` with 8 uncommitted files, +574/−111. That diff is `1f0f8f1` on `opendubs` in the private org repo. It contains the headless viewer, the 8766/8444 ports and `target_hz` 150 — and **TCP_NODELAY survived**. ~~Repoint the container's checkout~~ **DONE, 2026-10-07:** moved to `opendubs` by refs only (fetched from a git bundle, `symbolic-ref` + `reset`; no file written), `git status` clean; remote `origin` is now the org repo and `upstream` the original. The container holds no GitHub credentials, so updates arrive the same way — a bundle, or a fetch on the Thor host — until 6.3 builds the image from the repo. `/openarm_ws/src` is a **host mount** of the Thor's `openarm/openarm_robot-code` workspace directory (verified with `docker inspect`), so the checkout survives recreating the container. Bench scripts are in the org repo's `bench/`. **Remaining:** record each port's reason (8766 presumably avoids Foxglove's 8765 — confirm); retire `software/openarm/patches/`, pointing its README at the org repo. | The container's `git status` is clean on `opendubs`, and liveness (rule 5) is unchanged. **Prerequisite of every Phase 1 change to upstream code.** |
| 0.6 | **Notes that disagree with the code.** Punch-list goals 5 and 7 say unbuilt; `arm-telemetry-and-viz.md` lists the RMW issue as open (fixed in `openarm-up`); `capability-backlog.md` #7, `software/teleop/README.md` and `state-machine-review.md` describe Unity-only teleop and a `TELEOP_CONTROL` state that contradicts DEC-2/DEC-4; `arm-authority.md` says the deadman is already implemented (see 1.1); `docs/openarm-setup.md` never says v1; `docs/hardware-bom.md` lists the arms as planned; `cue.py`'s gripper range disagrees with punch-list 0e; `assets/arm/README.md` gives two mesh sizes. | Each says what the code does. Public docs follow the replicator voice. |
| 0.7 | **Land the threads note's teleop content.** `notes/threads-2026-10.md` exists only on `notes/threads-and-ssik`. Fold threads D (ssik) and F (compliance) into Phase 5 here; route the rest to its own home. | Nothing this plan depends on lives only on an unmerged branch. |
| 0.8 | **CH5 visible on the Thor** — *the teleop bridge now reads `[AUTH]` itself (`authority.py`, fail-closed, `4530665`); the dashboard and ROS bridge still do not. The interim check script was in `/tmp` on the Thor and **was lost at the 2026-10-08 reboot** — install `bench/ch5_check.py` somewhere persistent until this lands.* (DEC-21; was 1.8). The StamPLC broadcasts `[AUTH] ch5=… deadman=… estop_sw=… estop_btn=… contactor=… rc_live=… rc_ok=…` about every 110 ms over UDP 5005, but **nothing on the Thor keeps it**: the dashboard parses the drive state and drops `[AUTH]`, and the StamPLC ROS bridge publishes e-stop, motion-enabled and power but not the mode. Parse it into the dashboard's `/api/stamplc` and publish it from the bridge. *Interim, verified 2026-10-07:* a raw-socket read of UDP 5005 on the Thor (`AF_PACKET`, stdlib only, no listener on the dashboard's port) shows the line; that read is rule 10's check until this lands. | `/api/stamplc` and a ROS topic carry `ch5` with its age; stale beyond ~0.5 s reads OFF. |
| 0.9 | **`openarm-up` refuses motion CH5 does not permit** (DEC-21): `activate` and `park` need MANUAL or AUTO. Every arm path — the TELEOP page included — goes through `openarm-up`, so one gate covers them. `down` from stow, `hold` and `status` are always allowed. | With CH5 OFF, `openarm-up activate right` refuses and says why, from the shell and from the tablet. |
| 0.10 | **Measure the button-to-motion time** (DEC-25) of every dashboard arm action — activate, park, hold, de-energize — split by step. Every step of `openarm-up` today is a separate `ros2` command-line call through `docker exec`, each starting a Python process and rediscovering the ROS graph; activate makes about ten. | A table of where the seconds go, per action, before anything is changed. |
| 0.11 | **Annunciate in-progress actions** (DEC-25, R13). **PARTLY DONE, 2026-10-08 (#84):** the arm buttons follow the backend's op — the pressed one reads "ACTIVATING… 12 s" until it ends, then DONE or FAILED; the others are disabled meanwhile with a line saying what is running; a refused press reads "BUSY — WAIT" with the reason; failures show the whole message. Verified against a mock arm API. **Remaining:** stream `openarm-up`'s step lines ("energizing… loading gripper…") as they happen. The backend collects `openarm-up`'s output only when it exits, so the page shows nothing until the end. Stream its progress lines to the page as they happen, with the step and elapsed time ("LEFT: energizing… loading gripper… 12 s"); a failure shows its reason on the button. | No arm action leaves the page silent for more than a second. |
| 0.12 | **A long-running arm service** (DEC-25): one process on the robot that keeps its controller-manager and action clients open, so a step costs milliseconds, not seconds. The teleop bridge already holds those connections and is the natural host; `openarm-up` and the dashboard call it. | Activate and park start moving within ~1 s of the press, measured with 0.10's method. |
| 0.13 | **The bridge's refusals reach the tablet.** Found writing the teleop docs: a refused grip (CH5 not MANUAL), a lost hand, a refused home and an unreachable target appear only in `journalctl -u opendubs-teleop` — a student gripping with CH5 OFF sees nothing happen and no reason. Surface the bridge's latest refusal per hand on the TELEOP page. **DESK WORK DONE, 2026-10-09:** the bridge reports each hand's *current* condition (not its last log line: a refused grip is logged once but holds for as long as CH5 is out of MANUAL) with its registry code, as JSON to the dashboard over localhost UDP 5011, every 0.25 s and at once on a change (`status.py`, org fork PR); the dashboard serves it at `/api/arms/teleop` with no CAN traffic, and the TELEOP page polls it every 0.4 s while the bridge runs — hand tiles, the notice with its **?**, the last cleared one for 15 s. A gripper timeout, which ends the bridge before it can report, comes from the journal. Tested: 60 fork tests (10 new, each new guard mutation-checked), a backend check of the listener, and every scene against a mock API in a browser. Fixed in passing: a held home button with no `/joint_states` logged its refusal on every tick (150/s). **Remaining:** deploy the fork commit and the dashboard, then see each message on the tablet with CH5 and the headset. | Every message under `docs/arm-dashboard.md` § Teleop messages is visible on the tablet within a second of it happening. |
| 0.14 | **Tests and CI on the arm guard path** (hygiene Q6). `openarm-up` is the one place the arm guards live — every dashboard button runs it — and neither it nor `backend/openarm.py` has a test or a CI job. Add a path-filtered CI job: ShellCheck on `software/openarm/`, a no-hardware mode for `openarm-up` that exercises its refusals against fake `docker` / `openarm-motors` output (as the motor-count parser was tested in #73), and unit tests for `openarm.py`'s refusals and error extraction. | A change that breaks a refusal fails CI, with no robot. |
| 0.15 | **Verify the CAN driver source before building it as root** (hygiene S11). `provision-openarm.sh` fetches the `peak_usb` source files by kernel tag (`v${KVER}`) and builds them into a kernel module as root, with no check of what arrived. Commit SHA-256 sums per kernel version and refuse a mismatch, or a kernel version with no recorded sums. | Provisioning refuses a tampered or unexpected file, and a new kernel needs its sums added deliberately. |

## Phase 1 — a session is safe to hand to someone

*Correctness of the teleop session itself. 1.1 is the most important item in the plan.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 1.1 | **Stale headset data can drive the arm.** **FIXED AND ROBOT-VERIFIED (scripted), 2026-10-07 — `99f671e`.** Scripted client on opendubs2: followed 2.23°, held 0.20° on loss, **0.00° on resume with the grip held** (the bug case), 0.00° on re-engage — PASS. The real-headset half could not be judged (F10); turning off the Quest's Wi-Fi mid-grip is not physically possible, so the headset method is the Meta button, which empties the page's input sources. In upstream HEAD, `bridge.is_tracked()` (`bridge.py:269`, 0.5 s) is defined and **never called**; `poll_and_update_clutches()` (`webxr_teleop.py:219`) feeds the clutch from `get_state()`, which returns the last payload for ever. The page's 400 ms timeout only greys a card. So a headset that drops out with the grip held leaves the clutch engaged; when the stream resumes, the arm jumps toward the new pose at up to the step clamp. **CONFIRMED in the robot's copy, 2026-10-07 — and worse:** its drift also sets `ping_interval=None`, disabling the WebSocket keepalive, with a comment that `is_tracked()` "already detects a stale/dead connection". Nothing calls it, so a dead headset connection is detected at **neither** level. Then patch: untracked ⇒ disengage ⇒ HOLD; re-engage needs a grip edge on fresh data. | Tested by a **scripted client** that streams poses with grip held and then stops, and later resumes from a distant pose: the arm holds within 0.5 s of the stop and does not move on resume until a new grip press. Then once with the real headset (Wi-Fi off mid-grip). |
| 1.2 | **The speed limit we have is real** (DEC-16). Upstream's `max_cartesian_speed = 1.0` m/s is the working limit — the 5 cm/tick step clamp (7.5 m/s at 150 Hz) only bites on target jumps, which 1.1 removes — but whether the real-arm path enforces it is unmeasured. Make it a named, logged setting, and add a **demo setting** (~0.5 m/s; ISO 10218's 250 mm/s manual reduced speed is the reference point) selectable for events with crowds. | A step input from the scripted client never drives the TCP, measured from `/joint_states`, above the active setting — for both 1.0 and the demo value. |
| 1.3 | **Teleop cannot reach the head** (DEC-12, DEC-17). (a) **Cap J2 at 157°** — the measured 182° contact less `cue.py`'s 25° margin — in the arm's joint limits, mirrored left/right, as a commit on the fork (DEC-18). It costs only the 157–182° band with the elbow folded, which is the contact region; extended, the 1.2 m box already stops J2 near 140°. (b) **The hand check:** the 182° figure was measured one joint at a time, so with the arm de-energized and supported, move it by hand through combined poses (elbow up, forearm folded inward) to find whether anything other than J2 brings the arm to the head. `sim/clearance.py` can pre-screen candidates first; it is 6° optimistic, so it narrows the search and does not replace it. | (a) A teleop session driving J2 toward the head stops at 157° on both arms, with the elbow folded. (b) A recorded answer, with a person present: if no other pose reaches the head, the cap is the whole guard until 5.5; if one does, a keep-out zone for the TCP in the bridge is added to close-out. |
| 1.4 | **One slow client cannot wedge the session.** A non-draining second client filled its socket to 64 KB on 2026-09-24. Bounded send queue with drop-oldest, and a stated policy for a second client (refuse, or observe-only). | The scripted client connects as a second, non-reading client for 60 s: the first client's loop health and latency are unchanged. |
| 1.5 | **The software abort is not an e-stop.** It logs "E-Stop activated". | Renamed; nothing in the stack calls a software stop an e-stop. |
| 1.6 | **What happens across a real e-stop**, observed rather than inferred. Contactor opens mid-session: the arms fall limp; what does the bridge do, and what happens on contactor restore — does a stale target or the activate-to-zero sweep fire? Never observed. | A recorded observation, with a person present (rule 9), and a written recovery procedure. If restore can move an arm unasked, that is a Phase 1 bug. |
| 1.8 | ~~Thor-side `[AUTH]` consumer~~ **moved to 0.8** (DEC-21 — needed before routine arm work, not after close-out). | — |
| 1.9 | ~~Teleop answers to CH5~~ **DONE in the bridge, 2026-10-07 — `7fcd3f9` (F14); hands-on check pending.** **Teleop answers to CH5** (moved from Phase 3; DEC-2, DEC-3, DEC-15). Outside `MANUAL` a session cannot engage; CH5 leaving `MANUAL` ⇒ HOLD. Does **not** need the lease node (3.2) — a gate in the bridge, as a commit on the fork (DEC-18), is enough for close-out. | Flipping CH5 mid-grip holds the arm, every time, from the RC alone; re-engaging needs `MANUAL` and a fresh grip. |
| 1.10 | **A tilted reference frame cannot pass silently** (F7). WebXR fixes `local-floor` at session start; if its up is not gravity's up, every hand motion is rotated. The page checks it on entering AR and asks for a recenter beyond a few degrees. | Entering AR with a deliberately tilted frame is refused or warned on the page, and the bridge logs the measured tilt. |
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
| 3.1 | ~~Thor-side `[AUTH]` consumer~~ **moved to 0.8** (DEC-15, DEC-21). | — |
| 3.2 | **The arm-authority node** (DEC-6): lease expiry, the `[AUTH]` precondition, release on abort. Absorbs 1.9's bridge gate and 0.9's `openarm-up` gate. | A lease holder that dies loses the lease within its expiry; nothing needs upstream recovery. |
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

## Phase A — the arms behave like the base (DEC-24)

*A1 is the prerequisite for everything after it: until activation stops moving the arm, activating at power-on is unattended motion.*

*ADOPTED 2026-10-09 (DEC-28): [arm-authority.md § Amendment, 2026-10-09](design/arm-authority.md#amendment-2026-10-09--adopted-self-healing-arms-motion-gated-like-the-base-leases) — self-healing to HOLD, motion gated by CH5 plus presence, ownership as leases. It adds A7–A10 below and orders the phase: **1** A1 + A7, **started 2026-10-09** · **2** A2, A3, A8 (the keeper; A9 only if Q10 says so) · **3** A10 (leases) · **4** A4.*

| # | Item | Kill criterion / done when |
|---|---|---|
| A1 | **Activation that holds where the arm is.** Upstream's hardware interface (Enactic `openarm_ros2`) on activate enables every motor, steps to 0 rad at production gains, ramps both arms to zero over ~2 s and snaps the grippers. Change it to command each joint's **measured** position, grippers included. Carried as a copy in the org, like `openarm` (DEC-18) — check the container's checkout for drift first. | Activating an arm left at a non-zero pose (e.g. a wrist 40° off) moves no joint more than 0.5°, both arms, grippers included. |
| A2 | **Power loss and return.** **PARTLY DONE, 2026-10-08 (#84):** one press of ACTIVATE now recovers an arm after an e-stop — it recognises "all motors answering, none enabled, under an arm the stack thinks is active", resets that arm and activates it; verified on opendubs2 after a real e-stop. **Remaining:** recovery with no press at all, when power returns. The arms are on the contactor-switched drive bus, so an e-stop removes their power. Establish what the hardware interface does when motor power goes and when it returns; then make the return re-activate **automatically and motion-free**. | E-stop, restore: the arms are energized and holding with no command from anyone and no motion. Observed with a person present. |
| A3 | **Bring-up at boot.** A unit chains CAN links → stack → motion-free activation (A1) after power-on. PR #59's activate-starts-the-stack is the manual-path version of the same idea. | Power-on to "both arms holding" with no human action, ten boots in a row. |
| A4 | **Teleop bridge always running** (supersedes DEC-15's tablet START). Motion stays gated by CH5 MANUAL and the clutch (1.9, DEC-4). | The bridge comes up at boot; a grip in OFF is refused; a grip in MANUAL moves. |
| A5 | **Holding cost over a day.** Arms energized and idle for a working day: rotor temperature and current per arm. | Logged numbers; a decision on whether long idle periods should drop to a lower-gain hold. |
| A6 | **The dashboard shows state, not bring-up.** Per arm: energized / holding / commanded-by, CH5 mode, faults. PARK, HOLD, DE-ENERGIZE move to a maintenance section with their purpose stated. | An operator never needs a bring-up button in normal use. |
| A7 | **VERIFIED ON THE ROBOT 2026-10-09 (phase 1) — `openarm-up activate` restarts a link that is not `ERROR-ACTIVE`** before starting, when the contactor is closed. Seen 2026-10-09: `arm_left` BUS-OFF after an e-stop and loose connectors; only a shell `systemctl restart openarm-can@…` cleared it. Ships with A1 (phase 1). Built: a link that is up but not `ERROR-ACTIVE` gets one `systemctl restart` per ACTIVATE, on both the start path and the stack-running path; one that still won't come back stops with the fix named (power, contactor, connectors). `software/openarm/tests/test_heal_links.sh`: 4 cases off the robot, mutation-checked. **Robot check, opendubs2, 2026-10-09:** with the arms down and `arm_left` set administratively down ("NOT READY (state STOPPED)"), ACTIVATE L on the tablet finished rc 0 in 27.6 s with no shell — the log reads "STOPPED -- restarting the link (motion-free)", then `ERROR-ACTIVE`, the stack start, 8/8 enabled — and with A1 installed the left arm's pose was unchanged within 0.02° (J5 at −42°). | With a link forced to BUS-OFF (`ip link set … down`, or a connector pulled and reseated), ACTIVATE succeeds with no shell. |
| A8 | **APPROVED (phase 2) — the keeper**: one long-running service (0.12) reconciling each arm every 0.5 s to `HOLD`, motion-free: restart a bus-off link, restart a dead stack, activate with A1; `FAULT` naming its one action for a latched motor fault, a missing motor, or repeated failure. Never acts with the contactor open. | E-stop, restore: both arms holding again with no human action and **no joint moving more than 0.5°**, ten cycles in a row, sampled from `/joint_states`, a person watching. |
| A9 | **OPEN (Q10) — the maintenance lock**: a tablet toggle that keeps both arms `OFF` (no self-healing) while someone works on them; survives power cycles. | With the lock set, an e-stop/restore leaves the arms limp; releasing it heals them to `HOLD`. |
| A10 | **APPROVED (phase 3) — leases**: our wrapper around `OwnershipRegistry` with expiry and the `[AUTH]` precondition, the only path to `acquire`/`release`; teleop, the show runner, `park` and homing go through it; tablet RELEASE / TAKE OVER per arm. Expiry, release and CH5 changes go to `HOLD` at the measured pose. | GAZE-style motion check on the robot: ownership never flaps while renewed; after expiry every joint stays within 0.5° for the whole wait; a second writer is refused naming the holder. |

## Phase M — the headset on the robot (DEC-22)

*Experiments first; nothing is mounted until M1 passes.*

| # | Item | Kill criterion / done when |
|---|---|---|
| M1 | **Can a stationary headset track the controllers?** Headset on a tripod or the robot's back, rear-facing, kept awake (developer-mode proximity override), session started remotely. Operator behind it at 0.5 / 1 / 1.5 / 2 m. Log dropouts per minute and pose noise with head-relative poses (the page sends the head pose since `5ffb96a`). | Write the acceptable dropout rate and noise **before** the run. If no distance meets them, DEC-22 is dropped. |
| M2 | **Head-relative mapping in the bridge**: controller pose in the headset's frame × a fixed mount calibration → the arms' world frame, ignoring the session origin. One-time calibration procedure. | The same hand motion produces the same arm motion across sessions started with the headset rotated differently on the bench. |
| M3 | **Unattended start.** WebXR needs a user gesture to enter a session: try an adb-driven tap (Wi-Fi or USB). If that is not reliable across reboots and OS updates, a native app (launched by adb at boot) replaces the page — the parked Unity work (P1) becomes relevant. | Robot power-on to tracking-ready with no hands on the headset, ten times in a row. |
| M4 | **USB tether to the Thor** instead of Wi-Fi (`adb reverse` for 8444/8766). | Dropouts (F10) and latency compared against Wi-Fi with the same bench script. |
| M5 | **The mount**: rigid with the arm bases, out of the arms' sweep, USB-C power, heat over an hour. | Calibration from M2 holds after an hour of driving; headset temperature logged. |

## Phase 6 — documentation and reproducibility (R1, R2)

| # | Item | Done when |
|---|---|---|
| 6.1 | ~~A public teleop page~~ **DONE, 2026-10-08** — `docs/teleop-setup.md` (DEC-26). **A public teleop page**: network, certificate (0.3), Quest Browser steps, control mapping (grip = clutch, trigger = gripper), what the tablet shows, troubleshooting (silent WSS refusal, stale arm view, loop health). | An outside replicator gets to a live session from it. |
| 6.2 | ~~Finish the bring-up doc~~ **DONE for the tablet, 2026-10-08** — `docs/arm-dashboard.md`: every button, the daily sequences, and every message under a stable name (registry codes such as `ARM_OP_RUNNING`, `ARM_POWER_RESTORED`, `ARM_TELEOP_GRIP_REFUSED_MODE`, anchored as the code lower-cased) for the troubleshooting registry, whose `docs/reference/registry/arms.yaml` this lane owns. **Finish the bring-up doc** — v1 stated, the daily sequence (up → activate → teleop → park → down), and the hazards of rule 6. | Matches the tablet's buttons one to one. |
| 6.3 | **A reproducible container image** — **DESK WORK DONE, 2026-10-08:** design in [`design/arm-image.md`](design/arm-image.md); `software/openarm/image/` has the Dockerfile, `build.sh` (refuses any unpinned input), `capture-pins.sh` (records a robot's commits read-only, and refuses uncommitted changes), `run.sh` and the tracked FastDDS profile. Base settled with the Thor stack lane: a pinned `ros:jazzy-ros-base` (`ROS_BASE_IMAGE` in its `software/stack/versions.env`), not the Isaac base, so the JetPack line does not matter. **Remaining:** capture the pins from a robot (read-only), fill `ROS_BASE_IMAGE`, build and test on the non-robot Thor (never opendubs2), the `vcan` test for running unprivileged, and DEC-27's manifest: `repos.lock` holds upstream URLs (the lab, enactic, the author's own repositories, the org `openarm`), and `ssik` is installed **non-editable** from its `v8.1.0` tag, the only way it gets its native backend on aarch64 (no Linux aarch64 wheel exists; editable runs pure Python, about 40× slower).: the org fork pinned (DEC-18), the other upstream clones pinned, gripper-controller config included. Salvage `feat/openarm-integration` with its author (DEC-19), close it, retire the sibling checkout. | A second robot builds the image from the repo; no unmerged branch carries arm work `main` lacks. |

---

## On-robot findings, 2026-10-07

Raised at the robot, with the project team present. Each points at the item that owns it.

| # | Finding | State | Item |
|---|---|---|---|
| F1 | **A bench test was about to activate the arms with CH5 OFF**, and nothing would have stopped it: no part of the Thor keeps `[AUTH]`. | Rule 10 + DEC-21 adopted; `bench/ch5_check.py` (org repo) is the interim check | 0.8, 0.9, 1.9 |
| F2 | **The dropout fix holds on the robot** (scripted). | done | 1.1 |
| F3 | **The arm keeps moving after the clutch is released** — it finishes to the last target. | decision | Q6 |
| F4 | **From the zero/stow pose, forward and up are "unreachable"**: the arm is straight with the elbow (J4, 0–140°) on its stop. Only backward worked. Teleop should start from a **ready pose** with the elbow bent, not from stow. | open | 4.3 |
| F5 | **"The wrist feels mirrored" is the world-frame clutch, not a sign error.** Quest pitch measured receive-only and correct (level ≈ +46°, tipped down ≈ +0.5°, tipped up ≈ +74°, unchanged by the bridge's axis mapping); a scripted tip-down tilted the hanging hand backward about a fixed fingertip, as predicted. | decision | Q7 |
| F6 | **J7 is a bending joint on V1** (axis y; J6 axis x — a two-axis wrist). An earlier session's "J3/J5/J7 are roll joints" is wrong for this arm — V1/V2 again (rule 2). | corrected | — |
| F7 | **The "keep-out in front" was the IK finding no solution**, not a configured zone: the 1.2 m box never rejected a point (the controller validates each and logs `Rejected:`), and collision checking runs in `ALLOW` and never fired. After the headset was restarted — its window had been tilted — **zero unreachable events** in a full session. Likely a tilted WebXR reference frame at session start rotating every hand motion into unreachable poses. One good session against one bad; `470ff3c` now logs the cause of every unreachable event, so a recurrence explains itself. | instrumented; reference-frame check open | 1.10 |
| F8 | **The trigger moved a gripper with that hand's clutch released** (both sides). Gated on the clutch in `2c18fef`; host-tested (the two cases fail without it), **not yet tested on the robot**. | fixed, robot test pending | DEC-4 |
| F9 | **`systemctl stop opendubs-teleop` returns before the bridge has exited** — the unit wraps `docker exec`, so the process inside lives on through its cleanup, unlogged. A park started ~2 s later may have overlapped it. `openarm-up park` must refuse while a bridge runs, and the unit's stop must wait for the process to be gone. | open | 0.2, 0.9 |
| F10 | **The Quest's stream drops out for > 0.5 s, repeatedly**, in the first session (left side ~8 times in 26 s, unused). Latency felt much better after the headset restart; "some lagging movement" remains. INFO logs are hidden, so re-arm events cannot be seen in the journal. | open | Phase 2, 1.7 |
| F11 | ~~The robot's `openarm-up` predates the gripper-controller fix~~ **FIXED, 2026-10-07** — installed, gripper controllers verified (0.1). Default park is still 4 s, too fast for large sweeps (122° of elbow); 10 s was used. | park speed open | 0.1 |
| F12 | **The arm's model was never synced to the real arm.** On hardware the arm object reads a MuJoCo shadow that only commands move, starting at zero on every bridge start — measured 35.2 deg (right J4) off at startup. Teleop took the grip-time pose, IK seed and speed clamps from it, so the first grip after a restart drove the real arm toward the model, and the IK solved from a straight hanging arm: **the likely real cause of "resisting forward"**, and of A reporting "at stow" with the arm raised. Fixed in `aa79db1` (sync at start, at grip, at homing, after stops, 10 Hz idle); verified 35.2 → 0.0 deg at startup with no arm motion. Hands-on retest pending. | fixed, retest pending | 1.1, 4.3 |
| F13 | **The homing first built moved each joint independently** (bent path, confusing next to `openarm-up park`); now one straight joint-space line, all joints arriving together (`5ffb96a`). | fixed, retest pending | DEC-23 |
| F14 | **The arms moved under teleop with CH5 in OFF** — the bridge read CH5 but only homing used it. Gated in `7fcd3f9`: no grip engages outside MANUAL, leaving MANUAL mid-grip stops the arm where it is and needs a fresh grip, the reader fails closed. 50 host tests; with the gate removed the OFF/AUTO and mid-grip cases fail. **Hands-on check pending** (grip in OFF must log `grip refused`). | fixed, robot check pending | 1.9 |
| F15 | **The tablet showed "no joint data" in OFF and live data in MANUAL.** The robot side is proven ungated: with CH5 OFF on the wire, `/joint_states` ran at 400 Hz at the source and 41 messages / 4 s through both rosbridge paths (direct :9090 and the dashboard's HTTPS `/rosbridge` proxy). Nothing in the page or the proxy reads the mode. The page drops joint messages until its arm view has loaded, and its rosbridge path changed today (HTTPS proxy, not yet on `main`), so a stale page or a dropped connection is the leading suspect — **unproven**. The desktop browser pane could not stand in for the tablet (its `ws://:9090` fails though the same handshake succeeds from the shell; the HTTPS page needs the self-signed cert accepted). | open | 0.4 |
| F16 | **Power loss left the arm stack "active" over disabled motors** (2026-10-08). After a Thor reboot and a later activation, an e-stop dropped the contactor; the motors came back DISABLED, the arm stack still reported both arms and controllers active, and every ACTIVATE from the dashboard failed. Which e-stop did it is not established (F17). Recovery was manual: `down`, then ACTIVATE. **A2 is not hypothetical.** | **FIXED for the press, 2026-10-08:** one ACTIVATE recovers (F19, #84); automatic recovery open | A2 |
| F17 | **The dashboard's supervisor fires brief software e-stops** (`E-STOP ASSERT source=STALE TELEMETRY`) — on 2026-10-08 at 11:32:27 (held **7 s**), 12:19, 12:36 and 12:56 (each released within a second), around dashboard restarts; **not every restart fires one** (none for four deploys between 12:58 and 13:11). Which event disabled the motors in F16 is **not established**: the 12:56 one fired with both arms active and left all 16 motors ENABLED, so a sub-second e-stop may not drop them; the 7 s one at 11:32 is the likelier cause. A deploy restart at 13:20:24 fired another sub-second e-stop with both arms active, and again all 16 motors stayed ENABLED. Whether restarting a UI process should be able to drop arm power at all is a design question. | decision; cause to confirm | Q8, A2 |
| F18 | **ACTIVATE's failure message was wrong and truncated.** It blamed CAN wiring for motors that were answering, and the dashboard showed only its last line ("is last in the chain…"). Now `activate` reads the motors once and names the case and its fix — answering but disabled after a power loss (DE-ENERGIZE, then ACTIVATE), silent (e-stop, contactor, then which ids), faulted (power-cycle with the e-stop) — the dashboard shows the whole message, and `down` no longer refuses an arm with no motor energized, since it is already limp. | fixed (#73), then **superseded by F19** — its "DE-ENERGIZE, then ACTIVATE" advice is what left the operator asking de-energize what | 0.11 |
| F19 | **After a real e-stop (2026-10-08, a power-rail fix), the recovery advice failed.** The message said "DE-ENERGIZE, then ACTIVATE"; the operator could not tell what to de-energize, and DE-ENERGIZE needs a second, confirming tap ("CONFIRM DROP") that nothing mentioned — the dashboard log shows three ACTIVATE requests and no de-energize ever reached the robot. **Fixed in #84:** ACTIVATE resets an arm whose motors all answer but none is enabled, then activates it — **verified on the robot**: one press each recovered both arms, the right in 27 s ("power was lost and restored… resetting this arm" → 8/8 enabled → both controllers active). Every remaining fallback names the exact buttons. | fixed, robot-verified | A2, 0.1 |
| F20 | **Arm buttons hid what was running and swallowed refusals.** The shared confirm button discarded the server's reply and restored its label after 1.2 s, so a 27 s ACTIVATE looked finished, and ACTIVATE R pressed while ACTIVATE L ran was refused (409) without any sign — it had to be pressed again. **Fixed in #84** (0.11). | fixed; tablet check pending | 0.11 |
| F21 | **Arm actions are slow** — a successful ACTIVATE took 27 s; failing ones took 123–144 s before reporting. | open | 0.10, 0.12 |
| F22 | **The first measured arm dynamics** (2026-10-08, the choreography speed test, opendubs2's right arm; log `/var/log/opendubs-runner/speed-test-20261008-161349.jsonl`). One joint at a time, ±20° about J2 30 / J4 60, on the show path (FollowJointTrajectory). Steady speeds passed / failed, in deg/s: J1 60/120, J2 60/120 (off the path), J3 180/240 (252 measured, over 80% of its 312 trip), J4 120/180, J5–J7 120/180 (5–8° behind). **Lag 40–75 ms throughout; torque under 60% of each joint's limit everywhere**, so speed is limited by tracking, not torque. **Coming out of a reversal the joints overrun the commanded speed by 1.5–1.6×** (J2 120 → 193). Nothing in the arm stack clamps velocity; only trips stop it (velocity, torque, wrench; the tip-speed trip is off). For teleop: the lag is a floor under Phase 2's latency budget, and the overrun argues for blended corners there too. The show caps set from this are the choreography plan's (#90); the left arm is assumed to mirror the right until it gets its own run. | measured | Phase 2, 1.2 |

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
