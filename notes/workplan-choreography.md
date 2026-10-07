# Work plan — choreography: composer, player and digital twin

The plan of record, **agreed by the project team on 2026-10-07**. Work proceeds through these phases; anything
not in a phase, in the team requests, or in the stub register is not being worked on. Finished items
are struck through with a date, as in [`workplan-tab5-idf.md`](workplan-tab5-idf.md).

Background, benchmarks and citations (Spot Choreographer, QLC+, Choregraphe, Cozmo, Furhat, BML,
Ableton) live in [`design/choreo-prior-art.md`](design/choreo-prior-art.md), which is the plan this
replaced. *PA-n.n* below cites an item there. The format is specified in
[`design/choreo-format-v1.md`](design/choreo-format-v1.md); the hardware experiments are in
[`design/choreo-teach-and-sync.md`](design/choreo-teach-and-sync.md).

**The shape of the system.** Shows are authored in the **composer** on a laptop, previewed on the
**digital twins** (MuJoCo for the body, the Tab5 desktop build for the face), and played on the
robot from the **tablet**, with the **runner** on the Thor holding the clock and the lease. One
artifact connects them all: `cue.py export`, the resolved timeline.

---

## Standing rules

1. **The authority model is not negotiable and the composer is not special.** A show is a
   lease-holding motion source through the same arbiter and monitors as a stick or a planner.
   [`docs/control-authority.md`](../docs/control-authority.md) is the specification. **Shows run in
   `AUTO`; teaching happens in `MANUAL`.**
2. **E-stop and abort are different things.** An **e-stop** stops the show **instantly** — all
   motion and all audio, whatever device it plays on. An **abort** (deadman release, stick
   deflection, monitor trip, fault, operator stop) leaves every slot in a state defined *per slot*
   and **fades audio over ~250 ms**. Preemption aborts; it never pauses (PA-2.4).
3. **Validation is a gate, not hygiene.** The whole show is validated before any of it is sent.
   **Every bound in `cue.py` is traceable to a measurement or labelled a sanity check** — a
   validator turns guesses into constraints on other people's work.
4. **Anything safety-bearing is not authorable**, and the receiver *rejects* rather than
   discourages ([`design/face-config.md`](design/face-config.md)).
5. **Every experiment states its question and its kill criterion before it starts.**
6. **Shows are text, in the repo, diffable, with no layout data.** An editor save is a minimal diff.
7. **No new control path.** The runner emits commands the robot already accepts.
8. **Preview before robot.** The composer's default output is the digital twin; live output is a
   separately armed binding.
9. **No robot conclusions from the twin.** Its geometry is real; its dynamics, timing and lift speed
   are not, and it is 6° optimistic on shoulder/head clearance. The face twin cannot reproduce the
   device's threading or frame budget.
10. **The composer is a laptop tool; the tablet is the player.** Nobody is expected to compose on a
    10″ screen.
11. **Security is LAN-first and not a blocker.** The robot's LAN and Wi-Fi are the first line of
    defence, Thor login the second. Robot security is one parked item (P1), not a caveat on every
    feature.

---

## Decisions of record — 2026-10-07

From the hand-off report ([`choreo-cloud-run-report.md`](choreo-cloud-run-report.md)), walked
through by the team. Each now drives an item below.

| # | Decision | Drives |
|---|---|---|
| DEC-1 | **Speed caps: measure, then decide.** Until measured, the **average** segment speed is the hard gate and **1.875 × average** (the worst case if the controller eases) is a **warning**; the caps are labelled *unsourced*. *Amended 2026-10-07: first agreed as a hard check, changed to a warning once it was seen to fail both demo shows, including moves already run on the robot.* | 1.1, 6.1 |
| DEC-2 | **Dancing is accepted, simulator first.** The bounded open-loop `dance:` design (format §12) is adopted; moves are authored and previewed in MuJoCo now; robot execution waits for the runner and a floor-space test. `DRIVE` still does not enter `SLOTS` as free motion. | 1.4, 2.4, 6.6 |
| DEC-3 | **Teaching is decoupled from freedrive, with automatic timing.** Capture reads measured joints however the arm got there — teleop today. An *earliest legal time* helper places a taught pose at the soonest time the arm can reach it. | 1.3, 6.3, 7.1 |
| DEC-4 | **Grippers are normalised 0–1** (0 closed, 1 open, both sides), with each robot's per-side calibration kept **on the robot**, not in the repo. | 1.2, 6.2 |
| DEC-5 | **Audio output is selectable** among three devices — tablet, Tab5, Thor (with a speaker). A show may declare its output at composition time and the operator may override at run time. **Default: the tablet** (no Thor speaker exists; Tab5 audio load is a concern). | 1.5, 4.2 |
| DEC-6 | **E-stop = instant stop of the show; abort = fade (~250 ms) and stop.** | Rule 2, 5.4 |
| DEC-7 | **A tablet disconnect stops the music only**; the show continues under the RC deadman. | 4.5 |
| DEC-8 | **HTTPS is folded into the tablet-as-managed-device workstream**, needed because the mic and wake lock require a secure context — not as authentication. | 4.1 | ~~The tablet as a managed robot device (E9, DEC-8)~~ **DECIDED, 2026-10-07 — [`design/tablet-device.md`](design/tablet-device.md)**: HTTPS from the Thor with a local CA (TD-1); our own minimal open-source kiosk app (TD-2); the app as device owner on a dedicated, account-free tablet (TD-3); the Galaxy Tab A11 in the BOM (TD-4). | A tablet that runs a 10-minute show without a single interruption (TB-6). |
| 4.1a | ~~Check for an existing open-source kiosk browser~~ **DONE, 2026-10-07.** Six examined (`tablet-device.md`). FreeKiosk (MIT) met nearly everything; **the team chose to write its own anyway** — small and fully understood. Learn from Webview Kiosk (AGPL: read, don't copy); FreeKiosk's MIT update-policy code may be reused with its notice. | — |
| 4.1b | **BUILT, 2026-10-07 — [`software/tablet-kiosk/`](../software/tablet-kiosk/README.md).** One WebView screen, device-owner receiver, lock task, postpone-update policy, launcher role, mic granted only to the dashboard's origin, autoplay, user-CA trust, and a five-method `window.OpenDubsKiosk` bridge (`keepAwake`, `wake`, `screenState`, `isDeviceOwner`, `version`). Debug APK builds (AGP 9.4.1, Gradle 9.8.1, SDK 36); debug builds are testOnly so a device owner can be removed without a reset. **Open:** run on the tablet (TB-1–TB-6); a release signing key in the team's password manager. | Installs as device owner over adb, opens the dashboard locked and awake, and passes TB-1. |
| 4.1c | **BUILT, 2026-10-07; not yet deployed.** HTTPS on **8443** beside HTTP on 8080, from one backend process (`backend/serve.py`), so the six on-robot tools on `http://127.0.0.1:8080` — the flash scripts among them — are untouched. `deploy/make-robot-ca.sh` makes each robot's own CA on the robot (private key never leaves it) and the dashboard certificate. Over HTTPS the page reaches rosbridge through a `/rosbridge` proxy. The kiosk app installs the CA itself as device owner (by hand Android requires a lock-screen PIN). Verified locally: both ports, CA-verified HTTPS, refusal without the CA, `wss` proxy and feed, one SIGTERM stops both. **Open:** deploy to the Thor and run the script there. | `isSecureContext` true on the tablet; the mic and wake lock available. |
| 4.1d | **Galaxy Tab A11 in `parts.csv`** (TD-4), with a buy link and this note as the setup guide. | `bom.py --check` passes with the line in place. |
| DEC-9 | **CH5 positions are `STANDBY / MANUAL / AUTO`.** Low: powered and reporting, nothing commanded. Middle: a human commands live — RC for base, lift and head; teleop or hands for the arms. High: software commands; shows run here. The robot state `SAFE_IDLE` becomes `STANDBY`. | 0.6 |
| DEC-10 | **`FACE` and `LIGHT` are kept**; the face twin is their first receiver. Vocabulary follows the Tab5 emote decision (Tab5 plan F1). | 3.3 | **BUILT, 2026-10-07, against a stand-in face** — `software/choreo/face_bridge.py` follows `/api/twin` and sends `EMOTE` / `CUE` lines (FT-3); `face_stub.py` stands in until the twin listens. Local only by construction (non-loopback targets refused). Verified end to end with `beat-demo`: cues arrive within one 50 ms poll; a seek sends the state in force and no sounds. LIGHT and sound files deliberately not sent. **Open: the real face twin**, which needs the Tab5 plan's TW1 (listener) and TW2 (verbs). | `FACE` and earcon `SOUND` play on the face twin in step with the body twin; a seek leaves the face correct. |
| DEC-11 | **`SOUND` is split from music.** Music is the show's `music:` track, routed to the chosen output. `SOUND` becomes named sound effects played on the chosen output — the Tab5's six earcons are one source, tablet clips another. | 1.5 |
| DEC-12 | **The composer is not mounted on the robot dashboard.** The tablet gets a *player* surface instead. | 4.4 |
| DEC-13 | **Capture-a-performance is in the plan, after single-pose teaching works.** | 7.2 |
| DEC-14 | **The robot body twin starts with a native MuJoCo window**, driven by the composer's playhead. | 2.2 | ~~Native MuJoCo window driven by the composer (E1, DEC-14)~~ **BUILT, 2026-10-07** — `software/choreo/twin.sh`. The composer publishes time and play state (`/api/twin`) and the resolved timeline (`/api/twin/timeline`); the viewer samples it with `sim_preview.py`'s code and extrapolates on its own clock. Verified by its log: play from 0, pause (held at 18.12 s), seek (30 s), and an unsaved edit (head pan at 12 s, 60°→30°) all followed. | **Open:** a 3-minute show judged in step by eye. |
| DEC-15 | **The face simulator becomes a full digital twin**, a choreography player, and the customisation/theme tool. Its intent is written before it is built. | Phase 3 |

---

## Team requests

Feature and function asks, tracked here so none is lost in a phase table. Status is updated in
place; each points at the item that delivers it.

| # | Request | Status | Item |
|---|---|---|---|
| E1 | Visualise robot movements in a **native MuJoCo window** while composing | built 2026-10-07; judge by eye | 2.2 |
| E2 | **Face simulator as a digital twin** — as faithful to the head as feasible | open | 3.1 |
| E3 | Face simulator as a **choreography player** | bridge built; waits on Tab5 TW1/TW2 | 3.3 |
| E4 | Face simulator as the **customisation / theme tool**, including intake of background images, themes and accessories | open | 3.4 |
| E5 | **Consolidate the robot models into this repo** — base, lift, head and arms | done 2026-10-07 (robot redeploy pending) | 2.1 | ~~Consolidate the robot description (E5)~~ **DONE, 2026-10-07.** One folder, [`description/`](../description/README.md): `opendubs/` (base, lift, head, `dimensions.yaml`, moved from `sim/`) and `openarm/` (arm MJCF and collision meshes vendored unchanged from `openarm_assets`; visual meshes decimated by `tools/vendor_openarm.py` from 41 MB to 6 MB, ~1.6 MB in git; the dashboard's URDF and `chain.json`). The dashboard's own mesh copy was byte-identical and is gone: `frontend/assets/arm` is a symlink, and `deploy.sh` copies the files to the robot. Verified: the scene loads from a fresh path, renders match upstream (0.5 % of pixels differ), clearance is identical, and the arm view passes its FK checks. **Not yet verified on the robot:** a dashboard redeploy. | A redeploy shows the arm view on the tablet. |
| E6 | **Audio output selectable** (tablet / Tab5 / Thor), tablet default; at composition or run time | open | 1.5, 4.2 |
| E7 | **How does the Thor pipe audio out of the tablet?** | open question | 4.2 |
| E8 | **Tablet audio in and out** as a workstream | open | 4.2, 4.3 |
| E9 | **The tablet behaves as a robot device**: no sleeping, no update nags or interruptions during a run, interactive and available according to robot state; APK vs browser app vs dashboard changes | decided 2026-10-07: own kiosk app | 4.1–4.1d |
| E10 | **The tablet plays shows; laptops compose them** | adopted | 4.4 |
| E11 | **CH5 names fixed everywhere**, and the middle position meaning "a human commands" for arms too | open | 0.6 |
| E12 | **Shows can use real songs** — MP3 and other common formats, loaded locally | in scope; works today | 1.6 |
| E13 | **A leaner composer screen** — hover text on the less obvious controls, and the long validator notes minimised | done 2026-10-07 | 1.7 |

---

## Phase 0 — corrections to what exists

*Cheap, bounded, and every later phase builds on them.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 0.2 | ~~`theme::reserved()` as magic numbers~~ **DONE on `feat/tab5-idf-subtractive`** — `reserved` is now a field of each state in `app_face.cpp`. Lands on `main` when that branch merges. | — |
| 0.3 | ~~`FACE`/`LIGHT` unvalidated~~ **DONE, 2026-10-06** — closed vocabularies, "valid but not executable" notice. | — |
| 0.4 | ~~`hold_s` undocumented~~ **DONE, 2026-10-06** — defined in format §4. | — |
| 0.5 | **`kPrismServoPin = 0` declared and unused** in `rc_serial_probe` — 8Servo pin 0 is now a drive ESC. | Constant removed, or commented with what it would do if used. Firmware: flash per `CLAUDE.md`. |
| 0.6 | **CH5 vocabulary (DEC-9).** Five vocabularies exist today: `[AUTH]` `off/manual/auto`, the StamPLC mode string `OFF/MANUAL/AUTO`, the dashboard's `OFF/RC_MANUAL/AUTONOMOUS`, robot states `SAFE_IDLE/MANUAL_CONTROL/AUTONOMOUS_CONTROL`, and `docs/control-authority.md`'s `OFF/RC/AUTO`. **And a real conflict:** `robot_state.py`'s `ENABLE` matrix forbids both arms in the middle position, which contradicts DEC-9 and the freedrive design. | One vocabulary — `STANDBY / MANUAL / AUTO` — in firmware telemetry, dashboard, face/PRISM state names, docs and `cue.py`; arms permitted in `MANUAL`. Sequenced as its own PR because it touches the StamPLC, the dashboard, the face and the public docs. |
| 0.7 | ~~`requires:` is not validated~~ **DONE, 2026-10-07.** Known keys only; `rc_authority` must be `STANDBY`/`MANUAL`/`AUTO` (old names refused with the new one named); a show that moves must require `AUTO`; arm and gripper cues need `arms: active`; head and face cues need `tab5: true`. | — |
| 0.8 | **Notes that disagree with the code** (found by the cloud run): the emote count (five in `kEmotes[]`, six or seven in the notes); `arm-manual-control.md`'s "freedrive may already exist" (not in the dashboard). | Each note says what the code does, or is marked as describing the Arduino image. |
| 0.9 | ~~`sim/view.sh` says `mjpython` crashes on this Mac~~ **DONE, 2026-10-07.** The claim was false. MuJoCo 3.15.0's passive viewer runs under `mjpython`, and drove the full robot model from a script (lift and right arm animated) at ~40 fps. The comments in `view.sh` and `scene.py` now say so. | — |

## Phase 1 — format, validator and composer

*The format spec v1, `cue.py` v1 and the composer are built, with tests in CI. These are the agreed changes.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 1.1 | ~~Interim speed check (DEC-1)~~ **DONE, 2026-10-07.** The eased peak (`PEAK_FACTOR` = 1.875 × average) is checked as a warning; the average stays the gate; every cap message says *unsourced*. As a hard gate it failed `hello.yaml` (14) and `beat-demo` (12) — reported, then downgraded by decision. Both shows are valid with those 26 warnings, which name the time each move would need. | — |
| 1.2 | ~~Normalised grippers (DEC-4)~~ **DONE in the repo, 2026-10-07.** `GRIP_*` takes `open: 0..1` or `do: open\|close`; the old `pos: m` is refused by name. `software/choreo/grippers.py` loads and checks a robot's calibration (`/etc/opendubs/grippers.yaml`, or `$OPENDUBS_GRIPPER_CAL`) and converts both ways; the repo holds only a template and a made-up test fixture. The committed per-side metre ranges are gone from `cue.py`. **On the robot:** the file itself is item 6.2. | — |
| 1.3 | ~~Earliest-legal-time helper (DEC-3)~~ **DONE, 2026-10-07.** `Show.earliest_time()` in `cue.py` (one copy of the speed rule): the soonest a pose can arrive after the keyframe it actually follows — re-solved if moving later passes another keyframe — plus the time that also avoids the eased-peak warning, the binding joint, and whether the NEXT keyframe becomes too fast. Teach places captured poses with it and says why when it moves one; the inspector's **⇤ soonest** re-times an arm keyframe. `/api/earliest`. | — |
| 1.4 | ~~`dance:` moves (DEC-2)~~ **DONE, 2026-10-07.** A restricted `DANCE` slot (library moves only — `sway rock twist shuffle box` — never a velocity), `dance: { floor_radius_m }` required, `amp` ≤ the autonomy ceiling, 0.5–4 s per move and 30 s per show, every move re-checked to return to its start. A dance with no declared floor is refused; a move that may stray past it warns. Valid but twin-only until 6.6. `beat-demo` dances in bars 5–8. | — |
| 1.5 | **Audio routing (DEC-5, DEC-11):** `music:` gains an `output:` (tablet / tab5 / thor, default tablet); `SOUND` becomes named effects with an output; the run-time override is the player's (4.4). | A show plays on any output without being edited, and an effect a given output cannot play is a validation error, not silence. |
| 1.6 | **Song files (E12).** `music: file:` takes any format the playing device decodes — MP3, M4A/AAC, OGG, FLAC or WAV. Song files live beside the show and are **never committed** (the audio folder's `.gitignore` excludes them), because songs are usually copyrighted; the generated click track is the only committed demo. *Verified 2026-10-07: an M4A loads and decodes through the composer; the browser reports MP3, OGG and FLAC as playable. No MP3 was decoded, for want of an encoder on the test machine.* | An MP3 plays in the composer and on the tablet, in step with the grid. |
| 1.7 | ~~Composer legibility (E13)~~ **DONE, 2026-10-07.** Hover text (and a long-press equivalent for touch) on every non-obvious control — snap, tap tempo, beat 1, click, mute, teach, the time-form fields. Validator notices collapse to a one-line count per kind, expandable; problems stay visible, on the offending cue. | The timeline gets the screen; a first-time user can learn every control by hovering. |

## Phase 2 — the body twin (MuJoCo)

| # | Item | Kill criterion / done when |
|---|---|---|
| 2.1 | **Consolidate the robot description (E5).** Today: base, wheels, lift, head and D415 meshes plus `opendubs.xml` are in `sim/`; the OpenArm URDF and its 10 collision meshes are vendored in `software/kiosk-dashboard/frontend/assets/arm/`; the MuJoCo arm model is loaded at run time from an external `openarm_assets` checkout (the arm stack's own repo), outside this one; the 143 MB visual meshes are deliberately absent. **One description folder**, consumed by the sim, the dashboard arm view and `clearance.py`, with upstream licences and commits recorded. Decide collision-only vs a decimated visual set, and whether large meshes need Git LFS. | `sim/scene.py` runs from a fresh clone with no external checkout, and the dashboard and the sim read the same files. |
| 2.2 | **Native MuJoCo window driven by the composer (E1, DEC-14).** The composer is the clock and publishes only *time and play state* over localhost; the viewer evaluates the exported timeline at that time itself, so scrubbing and seeking are free and latency cannot accumulate. | Scrub, play and pause in the composer move the window in step; a 3-minute show stays in sync by eye. |
| 2.3 | ~~Run `sim_preview.py`'s render and clearance paths~~ **DONE, 2026-10-07.** Both ran first time. Clearance needed a fix: it reported the rest-pose floor (hands ~39 mm from the arm frame) as every show's closest approach; it now reports the closest approach the show *creates* — `hello` 63.1 mm, `beat-demo` 66.7 mm, both left arm base ↔ head as the head turns — plus any contact and the floor separately. | — |
| 2.4 | **BUILT, 2026-10-07.** The twin moves the base through DANCE moves (open loop, placeholder speeds, turning about the wheel centroid) and draws the declared floor radius as a disc. Verified by its log: `beat-demo`'s sways ±6 cm, twist ±5°, rock ±7 cm, back at the origin after. | **Open:** judged by eye; the disc itself is unverified by anyone. |
| 2.5 | **The face on the twin's head.** Render the face twin's frames onto the Tab5 panel of the body model, so one window shows the whole robot. | Stretch goal; depends on 3.2. |

## Phase 3 — the face twin (Tab5 desktop build)

*The desktop SDL build (`firmware/tab5_idf/build.sh desktop`) is the real face app compiled for the
host. It has no network input today, by design.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 3.1 | ~~Write the intent (E2–E4, DEC-15)~~ **DONE, 2026-10-07 — agreed: [`design/face-twin.md`](design/face-twin.md)** (decisions FT-1–FT-8). **Write the intent (E2–E4, DEC-15)** as `notes/design/face-twin.md`, before building. Its three roles: **digital twin** (the same app code, the same state machine, emotes, band, themes, settings; and a declared list of what cannot be twinned — the LVGL lock and threading, the frame budget, panel colour, camera, mic, ToF, pan/tilt hardware); **choreography player** (receives `FACE` / `LIGHT` / `SOUND` from the composer, in step); **customisation tool** (author and preview themes, background images, accessories, and export them as configuration the head loads). Must reconcile with the Reserved / Bounded / Free tiers in `face-config.md` and the cost hierarchy in `tab5-platform.md` §6b. | The team agrees the intent. Nothing in 3.2–3.4 starts before. |
| 3.2 | **Desktop command input** — the same line protocol the head accepts on UDP 4242, on localhost, desktop build only. | A command that works on the head works on the twin, unmodified. |
| 3.3 | **Composer → face twin (E3, DEC-10).** `FACE`/`LIGHT` cues delivered as the playhead crosses them; on a seek, the state at that time. | The face twin and the body twin play the same show in step. |
| 3.4 | **Theme and asset intake (E4).** Scope set by 3.1: formats, size and cost bounds, how a theme reaches the head (fetch-once at startup, per PA-4.5). | A theme authored on the twin loads on the head and looks the same; one that would exceed the cost bounds is refused, not shipped. |

## Phase 4 — the tablet as player and audio device

| # | Item | Kill criterion / done when |
|---|---|---|
| 4.1 | **DRAFTED, 2026-10-07 — [`design/tablet-device.md`](design/tablet-device.md), decisions TD-1–TD-4 awaiting the team; experiments TB-1–TB-6 for the hardware session.** **The tablet as a managed robot device (E9, DEC-8).** Compare a custom APK, a kiosk-style browser and dashboard-only changes against: never sleeping or nagging during a run, no system updates or notifications interrupting, behaviour that follows robot state, mic and wake lock (which need a secure context — HTTPS), and audio that keeps playing with the screen off. | A choice with its reasons, and a tablet that runs a 10-minute show without a single interruption. |
| 4.2 | **Audio out through the tablet (E6–E8).** The open question: how does the Thor make the tablet play — pre-load the file and schedule a start at a Thor timestamp, or stream? Plus the clock-offset handshake over the dashboard websocket, output latency and drift (experiments S1–S3). | Music starts within a stated tolerance of the show clock and stays within it for 3 minutes. |
| 4.3 | **Audio in from the tablet (E8):** mic uses — clap or cue to start, live beat, voice. | Each use is an experiment with a kill criterion before it is built (experiments C1–C5 in the teach-and-sync note). |
| 4.4 | **The player surface (E10, DEC-12):** in the dashboard — pick a show, see its validation and `requires:` preflight, choose the audio output, play and stop. No editing. | An operator can run a show from the tablet without a laptop. |
| 4.5 | **Disconnect behaviour (DEC-7):** the tablet dropping stops the music and nothing else. | Tested by killing the tablet's connection mid-show. |

## Phase 5 — the runner (on the Thor)

*From PA-2. The piece that moves the robot; nothing here starts before C1 and 2.2 exist.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 5.1 | **BUILT (core), 2026-10-07** — `software/choreo/runner.py` keeps the show clock (monotonic, its own process); on the Thor in production. | — |
| 5.2 | **BUILT** — preflight from `requires:` plus e-stop clear, deadman held, no fault; any UNKNOWN field refuses with code `UNKNOWN_STATE`. Tested against a fake robot state. | "Unknown" refuses; it never defaults. |
| 5.3 | **PARTLY BUILT** — the runner renews a 250 ms lease every tick and publishes it; the twins treat an expired lease as the show over. The robot-side expiry belongs to the arbiter and the firmware, with live output (5.6). | A killed runner stops the robot — tested by killing the process. |
| 5.4 | **BUILT** — e-stop ends at once (audio cut); a released deadman, CH5 leaving the required mode, a fault, an unknown state or an operator stop ABORTS, with each slot's state argued in `ABORT_STATE` (arms and grippers hold, head stops, lift relays off, dance zero velocity, face handed back, light released, music faded 250 ms); arms travel to the start pose first, timed by distance at the warning-free speed. Verified live against the twins: a deadman release at 12 s aborted, held the twin, sent no further cue. | Every slot's abort state is argued, not assumed. |
| 5.5 | **BUILT** — Spot's status taxonomy plus typed codes (`INVALID_SHOW`, `PARSING_FAILURE`, `UNKNOWN_STATE`, `PRECONDITION_FAILED`, `ESTOP`, `DEADMAN`, `AUTHORITY`, `FAULT`, `OPERATOR_STOP`), one JSON line per status change and per cue. | An abort's cause can be reconstructed from the log — tested. |
| 5.6 | **Live output as an armed binding** from the composer and the player. | Default stays the twin. |

## Phase 6 — on the robot

*The hardware session. Experiments as specified in
[`design/choreo-teach-and-sync.md`](design/choreo-teach-and-sync.md). Confirm a person is at the
robot before any of them; flashing is per `CLAUDE.md`, never re-derived.*

| # | Item | Kill criterion / done when |
|---|---|---|
| 6.1 | **Measure the controller's velocity profile (T4) and source the speed caps (DEC-1).** | 1.1's interim factor replaced by a measured one; every cap has a source. |
| 6.2 | **Gripper calibration per side (DEC-4)**, stored on the robot: `grippers.py template` → measure each side fully open and fully closed from `/joint_states` → `/etc/opendubs/grippers.yaml` → `grippers.py check`. | `open: 0.5` looks half open on both sides; `check` passes on the robot. |
| 6.3 | **Teach by capture from teleop (DEC-3)** — the real joint source replaces the fake one. | A taught pose replays where it was taught, within the controller's settling error. |
| 6.4 | **Music sync S1–S3** on the robot's own network (the bench is not a latency test). | 4.2's tolerance met on the robot. |
| 6.5 | **Audio-role experiments C1–C5** (teach-and-sync note). | Each answered against its kill criterion. |
| 6.6 | **Dance floor test (DEC-2):** first open-loop dance on the robot, floor space marked, CH8 in a human hand. | Net displacement and time limits hold. |

## Phase 7 — capture and growth

| # | Item | Kill criterion / done when |
|---|---|---|
| 7.1 | **Freedrive** as its own feature (`arm-manual-control.md` §1) — per arm, `MANUAL` only, latched, auto-exit, holds where it is. Then a capture source like any other. | Settled semantics met; the operator's hands are stated as the safety system. |
| 7.2 | **Capture-a-performance (DEC-13)**, after 6.3: record a live RC/teleop/cue session, reduce it, validate it, edit it as a timeline. | A recorded performance becomes a valid show with one edit pass. |
| 7.3 | **Example shows as schema tests** (PA-6): Halloween, Christmas, a show written entirely by an agent from the spec, a show that reacts to a failure. | Every primitive a show cannot express is a C1 item. |
| 7.4 | **Format growth** deferred by the spec: variation and animation groups, the capability layer (shows name capabilities, not joint indices), a tempo map, an idle/breathing layer. | Each argued from a show that needs it. |

---

## Stub register

Everything that pretends, so nothing is forgotten at robot time.

| Stub | Where | Replaced by |
|---|---|---|
| Fake joint source for teach | `composer/static/composer.js` `FakeJointSource` | 6.3 |
| ~~MuJoCo render and clearance paths, never run~~ — run 2026-10-07 | `software/choreo/sim_preview.py` | 2.3 |
| `FACE` / `LIGHT` / `SOUND` have no robot receiver | `cue.py` notices | 3.3, 1.5, the head's network verbs |
| Live output — designed, not built | teach-and-sync §5 | 5.6 |
| The runner | prototype `~/opendubs-demo/showcase_demo.py`, outside the repo | C5 |
| Speed caps, unsourced | `cue.py` `SPEED_CAP_DEG_S` | 6.1 |

## Parked

Recorded so they stop recurring as surprises, and not being worked on.

- **P1 — Robot security**, as a larger discussion: LAN and Wi-Fi first, Thor login second, StamPLC
  and Tab5 hardening about where they accept code "at some point", and a lock on the tablet. Not a
  blocker for anything above.
- **P2 — A node graph** for trigger wiring (PA-5.7). Timeline first; a graph only as a view over the
  same text.
- **P3 — Resume-from-middle as recovery.** Never; as an *authoring* start marker in the twin, maybe.
- **P4 — Accessories on the 8Servo bus or the one spare relay** (PA-3.4, 3.5) — the hardware is the
  constraint, not the format.
