# Choreography: direction

Intent from the project team's composer discussion, 2026-10-16. **Notes, not work orders**: nothing
here is scheduled or started. What is built is in [`choreo-format-v1.md`](choreo-format-v1.md) and
[`../workplan-choreography.md`](../workplan-choreography.md).

## Direction

The composer is the robot's **behaviour layer**: deterministic, validated and timed to music, written
by humans **and** agents. It has timed notes that an agent edits from, and the face and the MuJoCo
render to see a show before the robot does. It is not a light-show editor.

The goal is not full robot-agnosticism. It is to **survive module swaps on OpenDubs**: other arms, a
lead-screw Z axis in place of the scissor lift, grippers and hands.

## Decided

1. **Shows bind to capabilities; modules declare them.** A module declares its geometry, the slots it
   provides, its limits and its driver. This extends the tool registry (`effectors.py`). The
   validator re-checks a show against whatever is fitted, so a swapped module either still satisfies
   the show or the show is refused with the reason.
2. **Corrections at authoring time first.** A note becomes an agent's edit. A lesson that recurs is
   kept as a reusable **show skill** (a rule or a clip), which is read before every agent edit.
   *Open:* scope tags (`build` / `style` / `general`), so that a module swap invalidates only the
   build-specific skills.

## Intended, not scheduled

- **Music analysis, built in**: tempo, beats, the downbeat, sections and vocal-phrase timings into
  `music:` and markers. Lyrics are never stored.
- **Outward output plug-ins** over well-defined pathways (DMX as the worked example). Each plug-in
  declares its vocabulary, so the validator and the composer understand it, and has a twin stand-in.
- **Three levels of control:**
  - **Scripted**: today. Every cue is fixed and checked before the run.
  - **Adaptive**: an intent plus a tolerance, resolved at run time. For example, a cue saying "the
    head follows the person for N bars", built on the head's existing `neck_follow`; or speed and
    separation adapted near people.
  - **Delegated**: an external planning agent leases a slot for a beat window, or until it reports
    done, within bounds the runner enforces (a workspace, speeds, a timeout, a watchdog; a deadman
    release or an abort ends it). Control comes back cleanly from the **measured** state: the runner
    moves the slot from wherever the robot is to the next cue, as the start-pose move does today. The
    validator checks that next approach from anywhere inside the bounds, since it cannot see inside
    the window. The twin runs a stand-in agent.
- **Spoken corrections at run time**, later, and only through adaptive or delegated slots: never by
  rewriting a scripted cue mid-run.

## Backlog, ranked

1. **Robot / module profile.** The model file, the slots and the joints behind them, limits and
   speed caps, tool sites, mounts, the footprint and the clearance pairs, declared as data. The
   scene, the IK, the clearance sweep and the validator read it. Today these are wired to the
   OpenDubs/OpenArm model.
2. **Output plug-ins.** One driver interface: open, apply a value at a time, renew a lease, an abort
   state, close. The lift and the light are ported to it as the first two plug-ins.
3. **Delegated slots**, as above.
4. **Music analysis**, as above.
5. **Packaging.** A Windows CI leg, one install command, and a starter that writes a profile from an
   MJCF/URDF.
