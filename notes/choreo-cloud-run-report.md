# Choreography composer — cloud run hand-off, 2026-10-06

Branch `feat/choreo-composer`, draft PR against `main`. Brief: `notes/choreo-composer-brief.md`.
All five deliverables are done. Nothing in this branch can command the robot, nothing under
`firmware/` changed, `DRIVE` is not in `SLOTS`, and `shows/hello.yaml` validates unchanged.

## Decisions for Emma

Each has a reversible default already in place. The default is what the branch does today.

1. **Arm speed caps and the controller's interpolation.** The validator checks *average* speed per
   segment. If `openarm_impedance_controller` eases (cubic or quintic), the peak is 1.5–1.875× that.
   Measure it (experiment T4), or check `1.875 × average` until then? *Default: average, unchanged,
   so `hello.yaml` still passes.*
2. **The speed caps have no recorded provenance** (`SPEED_CAP_DEG_S`, carried from
   `showcase_demo.py`). Teaching makes them the binding limit on every taught sequence. Source them
   or measure them? *Default: unchanged, flagged in `cue.py`.*
3. **Dancing (workplan proposal 3).** Accept the bounded open-loop `dance:` design
   (`choreo-format-v1.md` §12: named moves, time-limited, speed-capped, declared floor space, CH8 in
   a human hand, net displacement ≈ 0), or keep the base out until odometry exists? *Default: out.*
4. **Which device plays the show music.** The tablet (works today, Spot's precedent, needs the
   clock handshake), or **the Thor with a USB speaker on the robot**: same clock as the show,
   survives the e-stop, sound comes from the robot, but needs new hardware. *Default: tablet for
   v1, then decide on the Thor after experiments S1–S3.*
5. **The music on abort and e-stop.** *Default proposed:* fade out over ~250 ms on any abort. The
   music also stops by itself if the runner's heartbeat goes missing for ~1 s. Optionally, play a
   "stopped" sound from the tablet after an e-stop, because once the Tab5 goes dark the tablet is
   the only device that can still make a sound.
6. **Does the tablet disconnecting abort a show?** *Default proposed: no.* Losing the tablet stops
   the music, and the RC deadman stays the watcher.
7. **Separate teaching from freedrive.** Capture reads measured joints, so it works no matter how
   the arm got there, including teleop, which exists today. *Default: yes.* Freedrive (T0) is then
   off the composer's critical path. The dashboard has no freedrive operation today.
8. **Who sets a taught pose's time.** The playhead does, and the validator refuses a pose the arm
   can't reach in time. Proposed but not built: an "earliest legal time" helper. The browser check
   this run showed why it's needed: a pose taught at the playhead at t = 0 was refused at once with
   "has no time to move".
9. **Grippers are taught symbolically** (`open` / `close`). A captured `pos` is refused on the
   other side, because the two sides were zeroed in different physical states. *Default proposed,
   not built.*
10. **`requires:` vocabulary.** `cue.py` doesn't validate `requires:`, so `rc_authority: AUOT`
    passes. The CH5 names also disagree: `MANUAL` in `arm-manual-control.md`, `RC` in
    `docs/control-authority.md`. Pick the names, then let the validator enforce them. *Default:
    unchanged.*
11. **`FACE` / `LIGHT` (0.3).** They now have minimal closed-vocabulary validators instead of
    being removed from `SLOTS`. `FACE` takes the five names in `kEmotes[]`. That depends on F1 in
    `workplan-tab5-idf.md` ("what should an emote MEAN"). Keep them, or remove the slots until
    there's a receiver? *Default: kept, with a "valid but not executable" notice.*
12. **`SOUND` loses its receiver when the ESP-IDF face lands.** `beep` / `chime` exist only on the
    Arduino face. The IDF face has six earcons and no network verb to trigger them. Swap `SOUND`'s
    vocabulary for the earcons once a verb exists? *Default: unchanged, with a notice.*
13. **Mounting the composer under the dashboard.** `router.py` is one `include_router` away, but
    that adds an unauthenticated surface that writes files to the operator UI. Mount it now, or
    after authN? *Default: not mounted.*
14. **HTTPS moves up the queue if the tablet is to use audio seriously.** Both `getUserMedia` (the
    mic) and Screen Wake Lock need a secure context. Without wake lock, a locked screen may suspend
    the music mid-show (C1). *Default: no change; recorded as a dependency.*
15. **Capture-a-performance (proposal 9)** is designed in `choreo-teach-and-sync.md`
    (record → reduce with RDP → validate → new show) and not built. Do you want it?

## What was built

| deliverable | where |
|---|---|
| D1 Format spec v1: `t` / `beat` / `bar`, one-tempo `music:`, spans and `hold_s`, linear keyframes mapped to `JointTrajectoryPoint`, parametric clips, markers as named signals, serialisation rules, resolved-timeline schema, capability layer proposed | `notes/design/choreo-format-v1.md` |
| D2 `cue.py` v1: resolution, clip expansion before validation, span-based one-owner, `FACE` / `LIGHT`, markers, unknown keys refused, `export`; `beat-demo.yaml` with 8 findings; generated click track | `software/choreo/` |
| D2 CI: the validator and composer tests actually run in CI now | `.github/workflows/choreo.yml` |
| D3 Composer, preview only: lanes, waveform, grid, snap, tap tempo, Web Audio playhead, validator in the loop, minimal-diff saves, teach button on a fake source; `sim_preview.py` | `software/choreo/composer/`, `software/choreo/sim_preview.py` |
| D4 Teach, sync, audio roles: 18 experiments with questions and kill criteria, sequenced into one hardware session | `notes/design/choreo-teach-and-sync.md` |
| D5 This report; the status table at the top of the workplan | `notes/workplan-choreography.md` |

## Stubbed

- **Joint source for teaching:** `FakeJointSource` in `composer.js`. The real one would subscribe
  to `/joint_states` over rosbridge and nothing else. The interface is specified in D4.
- **Stillness and staleness checks on capture:** specified, not built.
- **`sim_preview.py`:** the sampling is tested. The MuJoCo render and clearance paths were never
  run, because the environment had no `mujoco` and no OpenArm assets.
- **Live output:** the interface is designed (D4 §5), not built. The runner still doesn't exist.

## Deliberately not done

- No `DRIVE` slot, no runner, no node graph, no tempo map, no variation, no resume-from-middle.
  All of these are in the spec's §12 "out" table, each with its reason.
- No firmware edits. Phase 0.2 (`theme::reserved()` magic numbers) and 0.5 (`kPrismServoPin`)
  are still outstanding.
- No edits to `software/kiosk-dashboard/`. No new pages in `docs/`.
- No copyrighted audio. The `.wav` is git-ignored, and `make_test_audio.py` is the source.
- Proposed composer features from D4, not built: the two-point tempo fit (tap tempo alone drifts
  about 0.7 s over a 3-minute show), the earliest-legal-time helper, and graded snap.

## Notes found wrong against the code

- **Workplan status table:** it said "19 tests" and "FACE/LIGHT missing", and listed `requires:`
  as "built" though nothing validates it. The table is updated.
- **Workplan carry-over** still says `SOUND_SANITY_HZ = (150, 12000)`. This branch lowered it to
  8000, the clamp in the only `BEEP` receiver. Left unedited in the prose (see the merge note
  below).
- **Workplan 3.2 and `tab5-platform.md` §3** describe `M5.Speaker`'s 8 channels. That's the
  Arduino face. Under the IDF HAL there's **one player that refuses while busy**
  (`workplan-tab5-idf.md` 1.3).
- **`software/choreo/README.md`** said the validator ran in CI. It didn't until this branch added
  `choreo.yml`.
- **The emote vocabulary:** `kEmotes[]` has five names. The face notes describe six or seven.
- **`arm-manual-control.md` §1:** freedrive "may already exist". In the controller, unverified.
  In the dashboard, it doesn't: `openarm.py` has `park` / `activate` / `down` / `up` / `hold`.
- **CH5 naming disagrees** across notes (decision 10).

**Merge note:** `workplan-choreography.md` was byte-identical to `feat/tab5-idf-subtractive` so
the merge would be clean. This branch edits only its status table, so a conflict can only come
from there.

## Checks

Run before each push: `python3 software/choreo/tests/test_cue.py` (69 ok),
`software/choreo/tests/test_composer.py` (44 ok), `python3 tools/bom.py --check` (OK, 21
pre-existing warnings), `mkdocs build --strict` (passes). All of these ran under
`uv run --python 3.12 --with pyyaml --with mkdocs-material`. The composer was also loaded in a
browser this run: no console errors, and Teach on the fake source added a keyframe that the
validator flagged right away.
