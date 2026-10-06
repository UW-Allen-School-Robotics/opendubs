# Brief — the bones of the choreography composer (unattended cloud run)

**Audience:** an AI agent running unattended in a cloud sandbox, with no robot, no hardware, no
SSH, and no human to ask. Emma (the maintainer) is offline and will review the result as a draft PR.

**Branch:** `feat/choreo-composer` (cut from `main`). Work here. Open a **draft** PR against `main`
when done. **Never push to `main`, never merge, never enable auto-merge.**

---

## 1. What Emma asked for, in her words

> "build the bones of a composer — likely with a timeline view — while also thinking about how to
> utilize the resources onboard to enable the composition. Things I want to explore include using
> the arm freedrive concept to 'teach' keyframe-like poses, music synchronization, leverage the
> Tab5 [and] the Android tablet for audio in/out during the performance."

The composer choreographs: the **mecanum base**, the **Z-axis lift**, **two 7-DoF arms + grippers**,
the **head** (pan/tilt + the Tab5 face), and **audio out** (music, earcons).

## 2. Read first — in this order, and do not skip

This is **not greenfield.** A format, a validator and a simulator already exist and are tested.

1. `notes/workplan-choreography.md` — **the plan of record.** Phases 0–6, standing rules, a
   benchmark of Spot Choreographer / QLC+ / Choregraphe / Cozmo / Furhat / BML / Ableton, and eleven
   proposed changes. Everything below is a slice of it. Its **standing rules are binding**.
2. `software/choreo/README.md`, `software/choreo/cue.py`, `software/choreo/tests/test_cue.py`,
   `software/choreo/shows/hello.yaml` — the format and the gate.
3. `docs/control-authority.md` — **the binding safety constraint.** The composer is not special.
4. `notes/design/arm-manual-control.md` §1 — freedrive (the `FREE` mode): settled semantics.
5. `notes/design/behavior-api-scope.md` — slots, priority tiers, "the choreographer" section.
6. `notes/design/face-config.md` — Reserved / Bounded / Free; firmware rejects, never discourages.
7. `notes/design/tab5-platform.md` §3 (audio) and `notes/workplan-tab5-idf.md` 1.3, 1.3b, 1B.
8. `sim/README.md`, `sim/scene.py`, `sim/clearance.py` — the MuJoCo kinematic sim.
9. `software/kiosk-dashboard/` — FastAPI backend + **vanilla-JS, no-build-step** frontend that the
   Android tablet browses. `backend/openarm.py` is the arm-facing surface.
10. `CLAUDE.md` — repo rules. Note the CI gates.

## 3. Robot facts you will not find in the repo

Distilled from the maintainer's session memory. Treat as true; do not try to re-measure.

- **Arms (OpenArm, ROS 2 Jazzy, `openarm_impedance_controller`, in a container on the Thor).**
  Joint limits live in the arm software's `hw_validation/joint_limits.yaml`, **never copied into
  this repo** (cue.py takes `--limits`; tests use a fixture). J1/J2 are mirrored left vs right;
  shows write shoulder angles in each arm's **own outward sense** and the validator applies the
  mirror. **No self-collision checking exists**; the only Cartesian guard is a 1.2 m workspace box.
  Shoulder meets the head at J2 ≈ 182° with the elbow folded; an extended arm caps near 140° on the
  box. The impedance controller settles ~1.5–2° short (gravity comp). **Grippers were zeroed in
  different physical states per side.** Motors hold their last command forever with no timeout —
  which in `FREE` (zero stiffness) means a dead stack drops the arm.
- **Arm activation hazard:** activating the hardware interface steps every joint toward 0 rad at
  production gains. Arms are launched INACTIVE and activated deliberately. A composer must never
  activate an arm.
- **Lift (Z-axis):** two StamPLC relays (DOWN, UP). `MAST CMD` is deliberately the ONLY remote way
  to move it. A pinch guard stops descent near the bottom; **no show may acknowledge it**.
- **RC (SBUS via the StamPLC):** CH5 = mode OFF / MANUAL / AUTONOMOUS, CH6 = deadman, CH8 = master
  speed (scales **rates, not targets** — keep it that way). Freedrive requires CH5 = MANUAL.
- **Base:** mecanum, four drive ESCs. **`DRIVE` is deliberately not a slot** (no trustworthy
  odometry yet). Do not add it. See §6.
- **Head:** pan/tilt servos + the **Tab5** (ESP32-P4, 1280×720 panel, speaker, 2-ch mic ES7210,
  camera). Moving from Arduino to ESP-IDF + LVGL on another branch — **do not touch firmware**.
  **The Tab5 is powered from the e-stop-protected branch: an e-stop turns it OFF.** Under the IDF
  HAL it has **one audio player that refuses while busy**; six earcons exist (`app_face/cues.cpp`,
  48 kHz stereo). Mic is capable (direction from L/R level ratio works) and unused. Speaker and mic
  can run simultaneously. Network: UDP command port 4242.
- **Android tablet:** the operator UI; browses the dashboard on the Thor (`:8080`). Speaker, mic and
  camera are unused (`notes/punch-list.md`: "decide which device is the robot's voice"). **The
  robot's LAN may have no internet** — the composer must not load anything from a CDN at runtime.
- **Thor (Jetson AGX Thor):** the brain; runs ROS 2, the dashboard, the arm container. The show
  clock must live here (workplan 2.1) because it survives the e-stop.
- **Security debt:** the dashboard has no authN yet. Do not add new unauthenticated endpoints that
  can move anything. (This job adds none that move anything at all.)

## 4. Hard constraints for this run

1. **Nothing in this PR can command the robot.** No live output path, no relay, no UDP to 4242, no
   ROS publisher, no `MAST CMD`. Preview goes to the browser and/or `sim/`. A live-output binding is
   a *separately armed* future feature (workplan 5.4) — design its interface, do not implement it.
2. **No firmware edits** (`firmware/` is off limits — the Tab5 is mid-port on another branch).
3. **`cue.py` stays the gate** and stays stdlib + PyYAML. Every existing test passes;
   `shows/hello.yaml` validates **unchanged** (backward compatible). New rules get tests.
   Every new bound is traceable to a measurement or labelled a sanity check (workplan carry-over:
   "a validation rule outlives the claim it was built on").
4. **Text, diffable, stable key order, no layout/x-y data in the show file** (standing rule 7).
5. **No copyrighted audio in the repo.** Generate test audio (click track, synthesized loop) with
   Python's `wave` module or similar, and say so.
6. CI must stay green: `python3 tools/bom.py --check` and `mkdocs build --strict`. Run both. If a
   tool is missing in the sandbox, install it (`pip install mkdocs-material pyyaml` etc.).
7. Public-docs voice applies to anything under `docs/` (write for an outside replicator). Notes go
   in `notes/`. Prefer `notes/` for this run; do not add pages to `docs/` or `mkdocs.yml` nav.
8. Plain-Unicode math in prose (ω, ½, ≈), never LaTeX.

## 5. Deliverables, in priority order

Stop at a clean, committed state after each. If time runs out, an honest half is better than a
broken whole.

### D1 — Format spec v1: `notes/design/choreo-format-v1.md`

Workplan proposal 11: the spec as a document, not as `cue.py`'s source. Decide, with reasons drawn
from the workplan's benchmarks, at minimum:

- **Time base (1.2/1.3):** seconds *and* a musical grid without lying about either. Suggested shape
  (yours to improve): an optional `music:` block (`file`, `bpm`, `offset_s`, `beats_per_bar`) and
  cues that may say `t: 6.0` **or** `beat: 13` / `bar: "4.1"`, resolved to seconds by the validator.
  One tempo per show, recorded as a deliberate limit; tempo map deferred.
- **Durations (0.4, 1.5):** resolve `hold_s` — name it in the spec or delete it. Anything cue-able
  carries a duration.
- **Clips (1.5, 5.3, proposal 6):** a reusable named fragment in **clip-local time**, placed on the
  arrangement, optionally parametric (`strength`, `duration` scale). Repeats.
- **Keyframes and interpolation** for arm tracks: what happens between two waypoints, and how that
  maps onto `JointTrajectoryPoint.time_from_start`.
- **Markers emit named cues, never device addresses (5.6).**
- **Capability layer (1.6):** note the gap (shows name raw joint indices); propose, don't build.
- What is explicitly **out**: `DRIVE` (see §6), resume-from-middle as recovery, node graphs.

### D2 — `cue.py` v1 + tests

Implement the spec's validator side: beat/bar → seconds resolution, `music:` block validation,
clip expansion (validate the *expanded* show), duration semantics. Add `FACE`/`LIGHT` validators
or remove them from `SLOTS` (0.3) — Emma's call is open, so prefer a **minimal** validator that
accepts only a closed vocabulary (e.g. `FACE: {do: emote, name: <known set>}`) and say what the
vocabulary is sourced from. Add `cue.py export` → a **resolved JSON timeline** (absolute seconds,
clips expanded, per-slot sorted, stable order) — this is the one artifact the composer, the sim and
the future runner all consume, so its schema goes in D1. Write a second show,
`shows/beat-demo.yaml`, against generated audio, that exercises the grid and a clip (workplan 6.1:
every primitive it lacks is a finding — list them).

### D3 — The composer, preview-only: `software/choreo/composer/`

The bones of a timeline editor. Vanilla JS + a tiny Python server, matching the dashboard's
no-build-step style, **with every asset vendored or inline** (no CDN). Shape:

- **One lane per slot** (tracks are scopes, not free lanes — 5.2), per-lane mute, a playhead,
  zoom/scroll, cues as blocks with duration, keyframes as diamonds on arm lanes.
- **Music lane:** waveform of the show's audio file, beat/bar grid from `bpm`/`offset_s`,
  **snap-to-beat** (graded snap is a stretch goal), tap-tempo to set BPM, play/pause through
  **Web Audio** with the playhead driven from `AudioContext.currentTime` (not `setInterval`).
- **The validator is the gate in the loop:** the server shells to (or imports) `cue.py`; editor
  saves round-trip the YAML **without reordering keys or adding layout data**, and violations are
  shown on the offending cue (Spot's "highlighted in red", Polaris' "show inferred values resolved").
- **Preview:** at minimum a 2D per-slot state readout at the playhead. Stretch: drive `sim/` from the
  exported JSON (a script that steps `sim/scene.py` through the resolved timeline and writes frames
  or a video) — that is the "default output sink is the simulator" proposal.
- **Mountable later** under the dashboard (one router include), but **standalone in this PR** —
  do not modify `software/kiosk-dashboard/` beyond, at most, a commented integration note.
- Must be usable on a ~10″ Android tablet (touch targets, landscape) as well as a laptop.
- `software/choreo/composer/README.md`: how to run it, what works, what is stubbed.

### D4 — Design note: `notes/design/choreo-teach-and-sync.md`

The three explorations Emma named, each as **experiments with a stated question and kill criterion
before it starts** (standing rule 5), sequenced for the first hardware session. Ground each in the
workplan's prior art and the facts in §3. Include, at minimum:

**(a) Teach-by-freedrive keyframes.** The capture flow: arm in `FREE` (per-arm, CH5 MANUAL,
latched, auto-exit) → operator poses it by hand → "capture" reads **measured** joint positions →
a keyframe lands on that arm's lane at the playhead, **converted into the outward-sense convention**
(the left arm's mirrored joints must be un-mirrored on capture, or every taught left pose is wrong).
Then: the validator re-checks speed caps between taught poses (a taught pose has no timing — who
sets it?), the 1.5–2° gravity sag, gripper per-side zeros, and the fact that teaching happens in
MANUAL but playback needs the show's `requires:`. Specify the **capture interface** the composer
needs from the arm backend (read-only joint state, no command path) and stub it in D3 with a fake
source. Also cover the Ableton-style **capture-a-performance** variant (proposal 9) — record a live
RC/freedrive session as an editable timeline — as a design, not code.

**(b) Music synchronisation.** The hard problem is **clocks**: music plays on the tablet (Spot's
precedent: the laptop plays the music, the robot doesn't), the show clock lives on the Thor, and
each has its own latency. Design the handshake: tablet ↔ Thor clock-offset estimation over the
dashboard websocket (NTP-style ping), `AudioContext.outputLatency`/`baseLatency`, a scheduled start
at a future Thor timestamp, drift bound over a 3-minute show, and what the robot's motion latency
(arm trajectory, lift relay, Tab5 over Wi-Fi) does to "on the beat". Offline beat extraction
(BPM + first-beat offset from a file) vs live beat tracking from a mic. What happens to the music
on **abort** and on **e-stop** (the Tab5 goes dark; the tablet does not) — flag as a decision.

**(c) Audio in/out roles: Tab5 vs tablet.** A table of who does what — music, earcons, speech/TTS,
mic (clap-to-start, live beat, direction-of-voice to aim the head) — with the constraints from §3:
Tab5 dies with the e-stop, has one non-mixing player, ~3.5 MB of storage; tablet is the operator's
device, may move away from the robot, and its browser needs a user gesture to start audio (and
possibly HTTPS for `getUserMedia` — this interacts with the dashboard authN/HTTPS work). Recommend
a split and the experiments that would falsify it.

### D5 — Hand-off: `notes/choreo-cloud-run-report.md` + draft PR

**Lead with the decisions Emma must make**, numbered, one or two sentences each. Then what was
built, what is stubbed, what was deliberately not done and why, and anything in the existing notes
you found to be **wrong against the code** (the workplan warns notes drift — check). Keep it short;
detail lives in D1/D4. Update the status table at the top of `notes/workplan-choreography.md` to
reflect what now exists. The PR body summarises the report and ends with the attribution line.

## 6. Things that look like scope but are not

- **Base motion / "dancing".** Wanted eventually; refused today for a stated reason. In D1/D4, write
  the *bounded open-loop dance move* proposal (workplan proposal 3: time-limited, speed-capped,
  declared floor space, CH8 throttle in a human hand) as a design and an open decision. **Do not add
  `DRIVE` to `SLOTS`.**
- **The runner** (workplan Phase 2) — leases, preflight, abort. Design hooks only; no execution.
- **Tab5 face configuration as show data** (Phase 4) — blocked on firmware prerequisites (0.2).
- **A node graph** — explicitly deferred by the workplan.
- **Phase 0.2 and 0.5** are firmware fixes; list them as outstanding, don't touch them.

## 7. Working style

- Commit in small, well-described steps (`feat(choreo): …`, `docs(choreo): …`). Each commit message
  ends with the `Co-Authored-By` line the harness gives you.
- Run `python3 software/choreo/tests/test_cue.py`, `python3 tools/bom.py --check` and
  `mkdocs build --strict` before every push.
- When a choice is genuinely Emma's, make the reversible default, record it in the report's
  decision list, and keep going. Do not stop to ask; nobody is there.
- Do not invent robot measurements. Where a number is needed and unknown, name the experiment that
  would produce it.
