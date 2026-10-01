# Documentation review — tracked checklist

Triage of `docs-review-notes.md` (Emma's Apple Note, captured 2026-09-12). That file is the
raw source and stays unedited; this one is the working state.

Buckets:
- **A** — text/structure fixes. No robot, no open decisions. Safe to do.
- **B** — blocked on a decision. Do not touch the docs until Emma rules.
- **C** — needs a measurement, a part number, or a photo.
- **D** — platform projects, not doc edits.

Status: `[ ]` open · `[~]` in progress · `[x]` done · `[-]` dropped

---

## A — text fixes (safe to do now)

### Home (`docs/index.md`)
- [x] A1  "Start here" → "Start Here"
- [x] A2  Remove the **Current Baseline** card
- [x] A3  Remove **Status** — stale, and nothing updates it automatically

### System overview (`docs/system-overview.md`)
- [x] A4  Strike **Purpose** unless it earns its place
- [x] A5  "Embedded control edge" is word salad → "mobile base low-level/edge controller",
          and make clear it is one of several potential low-level controllers
- [x] A6  Mobile base: say it is optimised for mecanum but switchable to 4WD traction in software
- [x] A7  Subsystems: "mast" → **Z-axis** (partially covered by PR #17, which did not touch this page)
- [x] A8  Remove "Implementation reality from May 2026" — stale by construction

### Design philosophy (`docs/design-philosophy.md`)
- [x] A9  "cheaper fixed FWD drive" — FWD conventionally means *front-wheel drive*; we offer
          **4WD**, and the trailing "drive" is redundant

### Mechanical mount interface (`docs/interfaces/mechanical-mount.md`)
- [x] A10  Capitalise **Open Grid** and hyperlink it to the Open Grid project

### Command and telemetry (`docs/interfaces/command-telemetry.md`)
- [x] A11  Control-sources table, RC row: drop "Keep it"

### Lower frame / base frame (`docs/build/base-frame.md`)
- [x] A12  Miter-gear help box: rewrite — the point is that the two sides should mirror each
           other, and that any error is found and corrected at commissioning
- [x] A13  Step 3 "The mesh, seen from…" — reword; "notchiness" is not a word. The photo only
           needs to show correct assembly and that the gears turn smoothly
- [x] A14  Step 4 "orientation sets the origin" / "re-deriving" — meaningless here; the origin
           is the origin, and an upside-down frame is simply installed wrong
- [x] A15  Step 4 "Three sides joined, 4th still open" — caption does not match the photo
- [x] A16  Step 4 diagonal check — the goBILDA quad block forces square; soften or drop
- [x] A17  "Frame propped up…" — wrong: the vertical rails are the start of the **upper frame**,
           not the mast
- [x] A18  "Square it before you torque it" — tone down; hard to build this frame out of square
- [x] A19  Delete the "Leave slack at the corners" caution — too vague to act on
- [x] A20  "Leads brought up through rail" — label visibility is not meaningful and implies it
           matters; also drop "your own wiring log", which appears nowhere else
- [x] A21  Verify/Done-when: delete the "(After electronics)" bullet — confusing this early

### Rivet installation (`docs/build/rivnuts.md` or equivalent)
- [x] A22  Delete "not all locations are known yet" — false once these docs are finished
- [x] A23  Rivnut tool: "The manual takes real hand force" → it takes significant physical force
           to close; also note the drill-driven tool needs a drill
- [x] A24  Note that rivets go in the same positions front and back
- [x] A25  Note that the photos show a more complete frame than the reader's will be

### Electronics bay (`docs/build/electronics-bay.md`)
- [x] A26  Delete the "On the ordering of these steps" box

### Educators (`docs/educators.md`)
- [x] A27  Add a "Reduce, Reuse, Recycle" blurb — many parts are used by FIRST teams (FTC/FRC);
           donate leftovers to a local team

### Notes on how A was applied (2026-09-12)

- **A10** — used the project's own styling **openGrid** (lowercase o, capital G, per
  opengrid.world) rather than "Open Grid". Gets the capitalisation without inventing a brand.
  First mention on the mechanical-mount page is now hyperlinked.
- **A15** — I looked at `11-three-sides.webp`. The caption was wrong: the photo shows the
  rectangle already bolted up, an upright fitted, and loose hardware on the floor — not "three
  sides joined, fourth still open". Rewrote the caption to describe only what is visible. **The
  photo itself may still be the wrong one for this step** — see C5/C7/C8.
- **A21** — also corrected the adjacent verify bullet "Motor leads reach their connector
  positions without strain", which cannot be true before the ESCs exist. It now asks for the
  leads routed with slack and the left-side pair run across and zip-tied. The connector spec and
  length are still C1.

### Raised while doing A — needs Emma

- [ ] A28  **Start Here refocus.** The note says it should get the reader started on ordering
      components, physical build, and bench-setup of the controllers. I only capitalised it
      (A1) — changing what it points newcomers at is a content decision, and the build sequence
      is not finished yet.
- [x] A29  **OpenArm DOF — resolved, no doc edit needed (2026-09-29).** Both numbers describe the
      same hardware: `docs/openarm-setup.md:18` records that each arm carries **8 DAMIAO motors —
      7 joints plus the gripper**. A gripper is not conventionally counted as an arm DOF, so
      **7-DOF is correct** and `system-overview.md`, `hardware-bom.md`, `openarm-setup.md` and
      `camera-selection.md` already agree. Only the Home-page draft copy in the note says 8-DOF;
      if that wording is wanted, say "7-DOF arms, 8 actuators including the gripper".

---

## B — blocked on a decision (do not edit until ruled)

- [x] B1  **RULED 2026-09-29 — recommend the fuse block; keep the fitted PDH as history.** The
          drive bus is 24 V and the PDH is a 12 V FRC part, so a new build should not repeat it.
          This robot's PDH stays: it distributes power, it is not being changed, and whether 24 V
          has degraded it is unknown — the docs say exactly that rather than implying either that
          it is fine or that it must come out. Applied to `build/power-bay.md` (warning admonition
          + at-a-glance), the hand-maintained table in `hardware-bom.md`, and both `parts.csv`
          notes (regenerated via `bom.py --inject`).
- [-] B2  **Reclassified 2026-09-29 — not a doc item.** The M5Stack Dial appears nowhere in
          `docs/` or `parts.csv`, so no published claim depends on it and there is nothing to hold
          back. It remains an open **hardware** decision for the Z-axis controller; it produces
          doc text when it is made, not before.
- [x] B3  **RULED 2026-09-29 — PaHub stays as built.** It is what the firmware drives on Port A
          and what the fitted M5Stack sensor units use. No doc change needed; C21 (the I²C
          colour-code decoder ring) still stands on its own.
- [x] B4  **RULED 2026-09-29 — the robot has no local display, and the kiosk is retired.** The
          Android tablet is the only operator UI. It is functionally equivalent, it frees a USB
          port, and the remaining friction is a styling problem rather than a capability gap:
          `frontend/style.css` is still written base-first for the 1920x480 strip panel with the
          tablet on a `@media` fallback (line 472), so the tablet has been running the secondary
          branch of the stylesheet all along.

          Confirmed off the BOM — `parts.csv` and `hardware-bom.md` have no display, screen,
          panel, monitor or HDMI line — so no BOM change is needed. Docs should state that the
          robot needs no display. **Follow-on work is tracked as B4a/B4b below.**
      - [ ] **B4a deprecate the kiosk code.** Dead: `deploy/kiosk-autostart.sh`,
            `kiosk-session.sh`, `session-panel.sh`, `dashboard-autostart.sh`,
            `opendubs-kiosk.service`, `opendubs-kiosk.desktop`,
            `opendubs-kiosk-autostart.desktop`, `opendubs-dashboard.desktop`,
            `opendubs-panel.desktop`, `opendubs-touch.desktop`, `opendubs-xorg-fix.service`,
            `99-opendubs-touch.conf`, `tools/touch_bridge.py`, `tools/touch_viz.py`.
            Alive and must not be touched: the whole `backend/`, `frontend/`,
            `opendubs-dash.service`, `opendubs-stamplc-hb.service`, `deploy.sh`, `opendubs-sudoers`.
            `provision.sh` is **mixed** — it sets up the venv and backend service the tablet needs
            *and* a kiosk user, gdm autologin, chromium snap, immutable panel xorg and touch
            rotation. It has to be split, not deleted. Removing units from the repo does not remove
            them from the Thor, so the enabled services need disabling there: **do the robot half
            in a session on its network.**
      - [ ] **B4b make the tablet layout primary** — folded into the tablet-UI project,
            `punch-list.md` §0b, which is where the screen-sleep, camera and audio work lives too.
- [~] B7  **Control-authority half RULED and written 2026-09-29; payload contract still to write.**

          The blocker inverted. `docs/control-authority.md` was **fiction**: it published a
          six-mode taxonomy (`manual_drive`, `assisted_drive`, `autonomous_nav`,
          `autonomous_override`, `service`, `fault_recovery`) and **not one of those names existed
          anywhere in firmware or software**. `notes/state-machine-review.md` had flagged exactly
          this as D13 in June and it sat. Meanwhile the "unvetted" draft described the real robot.

          Rewritten around what exists — CH5 authority `OFF`/`RC`/`AUTO`, CH6 deadman, CH7 payload
          multiplexer, leases with expiry, monitors that constrain rather than command — plus three
          rulings:
      - **Intervention scope is mode-dependent.** NORMAL: a hand on the stick quiets the whole
        robot, because bystander-legible behaviour is part of the safety story. RESEARCH: per-axis,
        so a drive correction does not abort an arm policy mid-episode.
      - **Height vs speed is advisory, not a clamp.** The robot has proven stable; it annunciates
        that it is tall rather than silently reducing speed. Generalised into a rule for all
        monitors: *a monitor that changes behaviour must say so* — the robot must never quietly do
        something different while the operator guesses why.
      - **Parked base deferred**, with both candidate mechanisms recorded (latched brake vs
        Pinpoint station-keep) since no arm work depends on it yet.

          D13 closed and the taxonomy-mapping half of R11 withdrawn in
          `state-machine-review.md`.
      - [x] **B7b the payload contract — WRITTEN 2026-09-29** into
            `interfaces/power-data.md`, which already claimed to be "the contract a payload builds
            to" while saying only that a compatible payload stays in budget and uses a standard
            connector. It is six rules now: drive-bus power, mandatory galvanic isolation,
            self-containment, fail-safe on a power cut, watch the state broadcast if you outlive
            the e-stop, and go through the arbiter to claim an axis — which ties it to the control
            authority page written for B7.

            **D9 was wrong on one point.** It said no spare logic-bus fuse slots remain. The Mini
            Power Module has six branches with five committed (StamPLC, e-stop signal, accessory
            bus, Thor at 10 A, PRISM) — one spare position, plus the shared accessory branch. The
            page states the real budget instead of a TODO.

            **D10 is already written, in PR #7.** `docs/interfaces/payload-comms.md` on
            `worktree-payload-comms-impl` specifies the CAN framing, the 11-bit module/type ID and
            fail-safe-by-omission heartbeats. Nothing to write — it needs merging. **Note: that
            branch spells "StamPLC" throughout, so merging it after today's normalisation
            reintroduces the old spelling.**
- [x] B8  **RULED 2026-09-29 — collapsible sections, nothing deleted.** Four long argument
          blocks folded into `??? note` details (`pymdownx.details` was already enabled): the
          goBILDA rationale, "Why not swerve?", the static-vs-dynamic load numbers, and the
          TidyBot++ comparison. Every heading kept, so the TOC, deep links and the in-page
          `#where-opendubs-fits-in-the-ecosystem` anchor still resolve.
- [~] B9  **Split, and partly ruled 2026-09-29.** The original item mixed two axes — a *device*
          name and a *firmware* name — which is why it read oddly.
      - [x] **B9a wording — RULED 2026-09-29: introduce, then name.** Neither device reduces to
            one role. The StamPLC is an edge controller *and* the RC receiver, the e-stop
            moderator and the ESC command source; the Tab5 is an edge controller *and* the front
            control panel, the human–robot interface and the pan/tilt controller. So repeating a
            role label is not meaningful — each device is **introduced once with its functional
            purpose and called by its name thereafter**.
            Applied: `system-overview.md` gained a *Responsibility split* intro and a **Tab5
            section, which did not exist at all**; `command-telemetry.md` now introduces the Tab5
            as a device rather than calling it "the front panel" five times; "base controller"
            is gone as a stand-in name (10 sites incl. one via `parts.csv` + `bom.py --inject`),
            replaced by the introduction pattern with a consistent role phrase, "the mobile
            base's edge controller".
      - [ ] **B9b rename `rc_serial_probe`.** **Ruled: do it at the robot.** 22 files plus
            `/opt/opendubs/rc_serial_probe` in the Thor's systemd units, so repo and robot land
            together with a re-provision. Group with the deferred `MAST CMD` rename.
- [-] B10 **Closed 2026-09-29 — decided by events.** The Astra is gone; a **D415** is fitted and
          `camera-selection.md` now says so at the top. The "what others are doing" survey is that
          page, researched 2026-06-09. Choosing the *permanent* camera is a live question, but it
          is not a documentation defect.
- [~] B11 **Partly ruled 2026-09-29. Structure deliberately left open.**
      - [x] **Topic and service naming — by function, not by device.** `/robot/`, `/power/`,
            `/sensors/`, `/zaxis/`; `/diagnostics` keeps its standard ROS name. Applied to
            `stamplc_bridge.py` and documented in `software/ros/README.md`. The rationale is that a
            topic then survives a function moving between boards, which has already happened once
            when RC moved off the Pixhawk.
      - [x] **`stamplc_udp_bridge.py` deleted.** A second, unused StamPLC bridge publishing a
            different topic set; the systemd unit runs the HTTP-poll one.
      - [x] **Found and fixed while in there:** `/stamplc/relay2` was a service writing the
            scissor lift's UP leg. The backend refuses channels 1 and 2 with a `409` precisely
            because a direct relay write bypasses the mast driver, the pinch guard, the limit
            detection and the deadman gate — so the service was permanently broken while
            advertising lift control. Replaced with `/zaxis/{up,down,stop}` through `MAST CMD`,
            which is the guarded path. **ROS previously had no safe way to move the lift.**
      - [ ] **Structure (package + launch vs. today's 8 units) — OPEN by decision.** Committing to
            Isaac ROS across the robot largely settles it, and the prerequisites are captured in
            `punch-list.md` §0d. Two of them are blockers that make the question moot until
            cleared: there is **no `/cmd_vel` subscriber** and **no `/odom` publisher**, so ROS can
            neither drive the robot nor know where it is.
- [x] B12 **Closed 2026-09-29 — true, and already accommodated.** The premise is correct and the
          build never depended on it: each arm runs its **own CAN-FD bus** at 1 Mbit/s nominal and
          5 Mbit/s data through a **PEAK PCAN-USB Pro FD** on the Thor, one channel per arm
          (`openarm-setup.md:19,32`). The StamPLC's low-speed CAN is not in that path, and
          `interfaces/command-telemetry.md` already says the arms are off that bus and the computer
          forms their CAN traffic itself. The StamPLC CAN is spare capacity for payloads.

---

## C — needs a measurement, part number, or photo

- [ ] C1  **Motor bullet connectors + left-side lead extension.** Motors ship with stock goBILDA
          bullets; our ESC needs different ones, and the left-side leads must reach the
          electronics bay. Needs a length and a connector size. Note the higher-current ESC was
          chosen for margin at a small price delta. *Build-blocking for an outside replicator.*
- [ ] C2  Max groundspeed at 24 V with our mecanum wheels — calculate, then decide where it lives
- [ ] C3  Rack-U verification: 192 mm vertical rails → how many U, and the resulting top-mount
          height. Document that longer rails extend both, that most printed parts are
          parametric, and that specified wire lengths assume 192 mm
- [ ] C4  Grommets wherever wire passes through goBILDA rail (burrs abrade wiring) — add to the
          BOM and/or supply a printed grommet STL; step 5 installs one without mentioning it
- [ ] C5  Base frame photo "everything staged…" — only one of three vertical rails is installed,
          and it may be in the wrong place
- [ ] C6  Step 2 "yellow and black tape is…" — confirm with the team; likely untrue
- [ ] C7  Step 4 "First corner closed" photo — vertical rail in the wrong position
- [ ] C8  Final base-frame photo is the correct one to use, but lacks the left-to-right motor
          wire routing
- [ ] C9  Left-side motor leads should be routed across and zip-tied before the ESCs exist — no
          photo shows this
- [ ] C10 Rivnut hero shot showing every location at that stage of the build
- [ ] C11 Thor page: Thor missing from the BOM
- [ ] C12 Thor "mounted — top view" shows parts not yet installed at that point in the sequence
- [ ] C13 Thor micro-fit power connector + routing toward the electronics bay — no photo
- [ ] C14 Thor → router network cable — no photo
- [ ] C15 Thor → USB-out — no photo
- [ ] C16 OTOS module redesign: mounts outside the frame via rivnuts + printed thumbscrews, not
          as currently documented
- [ ] C17 Third modular interface: 14 mm hole in the goBILDA pattern takes a nutsert for an M10
          blind hole; M10 printed thumbscrews affix claddings and sensor modules. Shear
          behaviour is a feature for low-hanging odo pods and OTOS. Link MakerWorld M10 examples
- [ ] C18 Hero shots/illustrations for every mounting concept
- [ ] C19 Top-deck payload grid — frame it as a car roof rack: rigid, hard-mount
- [ ] C20 CAD for top-deck and scissor-lift hole patterns adapting to common arm bases
- [ ] C21 I²C "secret decoder ring" — Qwiic / M5Stack / goBILDA all colour SCL/SDA differently
- [ ] C22 Power budget column: logic-24 V has a 20 A DC/DC, drive 60 A, 52 V limited by the BMS
          (verify from the battery documents)
- [ ] C23 Note the drive-bus jumpers a payload with its own e-stop can tap
- [ ] C24 Educators: check M5Stack / Adafruit / SparkFun / Pololu for edu discounts
- [ ] C25 Ground-loop warning block on the power-and-data page — isolation across un-isolated
          DC (and AC) rail spans. High visibility

---

## D — platform projects

- [ ] D1  Agentic build support — make the docs highly usable by a build agent (CLAUDE.md-style
          companions?) while staying completely human-readable
- [ ] D2  Community contribution model — MakerWorld/Thingiverse-like remixes, build tips,
          sequence improvements. Lightly moderated discussion and "I built it" threads that do
          **not** modify core pages unless accepted by the core team
- [ ] D3  Configuration wizard → build-specific BOM + parametric STL generation for printed
          parts that vary by configuration. Discuss before implementing
- [ ] D4  Next/previous page buttons, especially through the build sequence
- [ ] D5  The icon is still weird
- [ ] D6  Hero image(s) for the start page
- [ ] D7  Investigate whether repeated abrupt power loss threatens the Thor's storage/OS, and
          what mitigations exist if so
- [ ] D8  Home page copy rewrite — the modular description, CAD/BOM/Figma availability, the
          2.5 kWh battery and two 24 V buses, 19" rack mounting
- [ ] D9  Payload contract rewrite: payloads take power from the **drive** bus, may use any data
          bus, isolation is mandatory, must be self-contained and fail gracefully on e-stop
          power cut. No spare logic-bus fuse slots remain
- [ ] D10 Document Raj's payload comms protocol + physical bus independence (PR #7) and the
          software-mediated e-stop message / state pickup for payloads
