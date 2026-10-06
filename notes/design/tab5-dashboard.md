# 1A — The Tab5 as a dashboard

Desk work, 2026-10-05. No robot, no network. Grounded in the dashboard that actually exists:
`software/kiosk-dashboard/frontend/index.html` (536 lines, 13 views) and
`backend/app.py` (435 lines, 19 POSTs + 5 GETs + a WebSocket), and in the head firmware that
exists: `firmware/tab5_face/src/` (no touch handler anywhere in it today).

Companion notes: [`../../docs/control-authority.md`](../../docs/control-authority.md) — the
binding constraint, not background reading — [`hri-interaction-design.md`](hri-interaction-design.md)
for the audience split, [`pinch-guard-consent.md`](pinch-guard-consent.md) for the one interaction
that is already half-built on the panel, and
[`../../docs/network-architecture.md`](../../docs/network-architecture.md) for the API's
authentication status.

## The finding that reframes the question

I expected this to be a question about *information*: which readouts belong on the robot versus on
the operator's tablet. Reading the backend, it is mostly a question about **authority** — but not
in the way a first pass suggests.

Counted honestly, the dashboard has 19 POSTs and **seven** of them change what the robot is doing
or how it is energised: `/api/teleop/{verb}`, `/api/arms/{op}`, `/api/mast/{cmd}`,
`/api/relay/3/{state}`, `/api/estop`, `/api/estop/clear`, `/api/pdh/{action}`. An eighth,
`/api/system/{action}`, takes the Thor down. The rest are instruments and soft state:
`/api/i2c/scan`, `/api/i2c/info`, `/api/enc/reset`, `/api/led/*`, `/api/vnc/*`, `/api/tracker/*`,
`/api/fault/clear`, `/api/ack`, and `/api/action/{name}` — which documents itself as *"No actuator
or safety effect — purely informational/UI."*

That is still an operator control surface rather than a status display with buttons. But the
seven that matter are each already wrapped in a guard, and reading those guards is what settles
where a control may live:

- **`/api/mast/{cmd}`** is an *intent*, not a relay write. It becomes `MAST CMD` on the StamPLC,
  feeds the same driver the RC sticks feed, and **expires after 250 ms** — a caller that dies
  stops the lift. The pinch guard, the limit detection and the authority/deadman gate all still
  apply.
- **`/api/relay/{ch}/{state}`** refuses channels 1 and 2 outright, with the reason in the
  docstring: they are the lift's legs, and writing them directly walks past the pinch guard. Only
  R3 is free.
- **`/api/estop/clear`** is supervisor-guarded: it refuses while the physical button is still
  pressed, while telemetry is stale, when the contactor reads welded, or when CH5 is not `OFF`.

**The guards are on the robot, not in the browser.** That is the whole reason this question has an
answer: a control's safety does not come from which glass it is rendered on.

## Authority: confirmation and intent, not prohibition

**Local controls are in scope.** The panel is an operator surface as well as an expressive one.
Four arguments, in the order they changed the conclusion.

**1. Physical reachability is not new exposure.** Anyone who can touch the screen can touch the
robot — reach into the scissor lift, push the chassis, pull a cable, press the mushroom head. The
e-stop is *deliberately* reachable by exactly that logic: we put the most consequential control in
the world within reach of any passer-by because the person who needs it most is the person
standing there. A touchscreen at the same height does not create a new population of people with
physical access to the machine; it gives the population that already has it a better instrument.

**2. The API's missing authentication is a defect to fix on the API.** It is real — the dashboard
is served by `uvicorn 0.0.0.0:8080` with no authN, and rosbridge and Foxglove bind `0.0.0.0` the
same way (`docs/network-architecture.md`). Fix it there. It is an argument about *who may reach an
endpoint over the network*, and reusing it as an argument about *where a UI may be drawn* imports
a network defect into a human-factors decision, where it does not belong and cannot be fixed.

**3. The person standing at the robot is the one who most needs local control.** Jogging the mast
while watching the clearance is better done at the robot than from a tablet across the room —
the operator's eyes are on the gap, and the tablet forces them to be somewhere else.
`docs/control-authority.md` already reaches this conclusion from the other direction for
annunciation: the active mode *"belongs on the **robot** — the face and the PRISM indicator — not
on the transmitter: the operator's eyes are on the machine."* The same sentence argues for the
control next to the readout.

The sharpest case is the pinch guard. Consent to descend through the shear zone is a human
decision, every time — no show may script it (`software/choreo/README.md`), and the prompt that
explains the gesture **already renders on the head** (`firmware/tab5_face/src/prompt.cpp`). The
person who can actually see whether a hand is in the gap is the person at the robot. A consent
affordance that is reachable only from a tablet across the room asks the wrong human.
`pinch-guard-consent.md` leaves *"Should `MAST ACK` be exposed to the dashboard at all?"* open;
the panel is a better answer than the dashboard, for the same reason the prompt is already there.

**4. The StamPLC button precedent does not transfer.** That rule —

> Safety rule: buttons may only DE-energize (BtnC hold = local e-stop); re-arm stays
> host/dashboard-guarded.

— is about **three unlabelled physical buttons on a 135×240 panel**, where the only thing the
device can know is that a button went down. It cannot show what it is about to do, cannot ask, and
cannot distinguish a deliberate press from a sleeve. The asymmetry is the right rule *for a blind
button*. A 1280×720 touchscreen is a different instrument: it can name the action, state the
precondition, require a sustained gesture at a specific place, and show the progress of that
gesture. Note that even `uiButtons()` already reaches for this — its local e-stop is
*hold*-to-confirm, not a press. The precedent's own implementation is evidence that confirmation,
not prohibition, is the mechanism.

### The grades

So the question is not *may the panel command* but *what does a given command have to cost*. Four
grades, with the test for assigning one.

| grade | gesture | the test | examples |
|---|---|---|---|
| **1 — single tap** | one touch, immediate, undoable by its own inverse | nothing moves, nothing is energised, and a mis-tap is corrected by tapping again | page/screen change, `MUTE`, `THEME`, backlight, acknowledge a prompt, `/api/fault/clear`, `/api/led/*`, start/stop an instrument |
| **2 — hold to act** | sustained contact; motion continues only while held; releasing stops it | the control is a **live intent with an expiry**, so an abandoned touch is a stop — exactly what `MAST CMD`'s 250 ms already enforces | mast up / mast down, a jog |
| **3 — hold to confirm** | a named action, a stated precondition, and a sustained gesture (≈1 s) with visible progress | the action is discrete and consequential, and the panel can state what it will do before it does it | de-energize (see below), pinch-guard consent, R3 on/off, `PDH_SWITCH` |
| **4 — deliberate mode** | a non-obvious entry gesture, a visible mode banner, an idle timeout back to the face | the control is for the one person who approached with a job, and a bystander finding it would be a surprise | arm activate/stow, teleop bridge, `/api/estop/clear`, `/api/system/{action}`, `/api/enc/reset`, anything per-robot |

Grade 2 is the one that earns its place. A hold-to-move jog is **safer than a tap-to-move
button**, because the stop condition is the absence of input rather than the presence of a second
input. That property is already in the protocol: `MAST CMD` expires in 250 ms and the firmware
drops the lift when the heartbeat stops. A panel jog is the same source re-published at the same
rate, so an operator whose finger slips, whose hand is pulled away, or who simply walks off has
already stopped the lift.

### What actually bounds a panel control

Not a list of forbidden endpoints. The arbiter that already exists:

- **The panel is a source claiming a lease**, at a priority, with an expiry — the same primitive
  as a stick, a planner, or a tablet-triggered routine. `docs/control-authority.md` says a
  tablet-triggered routine *"goes through the same arbiter and the same monitors as everything
  else, not around them"*; a panel-triggered one is not special either.
- **CH5 is still the authority and it is still a physical switch.** A panel control is subject to
  it exactly as the dashboard is. With CH5 in `OFF` a panel jog does nothing, and the panel must
  say *why* rather than appearing broken.
- **Monitors constrain; they never command.** The pinch guard, the envelope, the e-stop chain all
  still bound a panel-sourced intent, because they bound the driver and not the caller.
- **Preemption aborts.** A hand on the stick in NORMAL quiets the robot, panel intent included.

Under those four, "which endpoint" stops being the interesting question. The interesting question
is which grade, and the grade table above is the answer.

### What genuinely should not be on the panel

Three things, each for a stated reason rather than by category.

**A control that must survive an e-stop.** The Tab5 is powered from the **e-stop-protected drive
branch**: with the contactor open it has no power at all — it is *off*, not offline. Anything whose
job includes working after the e-stop has fired cannot live here.

**A thing that looks like the e-stop and is not.** This is where the original version of this note
was most wrong, and in the direction it thought was safest. It allowed exactly one control — "a
single large de-energize affordance" — on the grounds that removing energy is always safe. The
*action* is safe; the **affordance** is the hazard. A big red STOP on glass next to a real
mushroom-head e-stop teaches a bystander that the glass is the emergency control, and the glass is
a software path through an application, a touch controller and a network stack, on a panel that is
dark whenever the contactor is already open. A genuine e-stop is hardwired, latching and
fail-safe; a touch target is none of those.

So: the panel **may** carry a de-energize control — it is grade 3, hold-to-confirm, and it is
useful to the technician who is already looking at the panel — but it must be labelled as what it
is (a *software* stop), must not be styled as the e-stop, and must point at the physical button.
The robot's emergency control is a mushroom head you hit without reading, and nothing on a screen
may compete with it for that role.

**A control whose precondition the panel cannot establish.** `/api/estop/clear` is the clean case.
Re-arming is guarded on the *robot's* state — button released, telemetry fresh, contactor not
welded, CH5 `OFF` — and that guard must stay where it is. The panel may *request* a re-arm (grade
4) and must render the blockers the supervisor returns; it may never hold the decision. The
guard-on-the-robot rule is the real invariant, and it is the one the original note was reaching for
when it wrote a list of forbidden endpoints.

### Still open

- **The technician-mode entry gesture.** Deliberate and non-obvious. Not a corner tap, which
  bystanders find. A specific unlabelled region plus a hold is the cheapest candidate; whether it
  is discoverable enough for a student who was shown it once is a question for the first person who
  has to use it in the field, and the evidence that would settle it is watching someone try.
- **Whether the panel annunciates `pmode`.** `docs/control-authority.md` lists this as open and
  names the face as the place for it. It is a grade-0 readout and almost free; it is listed here
  because it is the only open item in that note that this surface can close.
- **Touch does not exist in the head firmware yet.** `firmware/tab5_face/src/` has no touch
  handler; `hmi.cpp` renders and nothing reads the digitiser. So every grade above is a design, not
  a change to something that works, and the first implementation question is the LVGL input device
  rather than the policy.

## Route decision: native LVGL, reading the existing JSON

The plan listed three routes — native LVGL pages, streamed HTML (headless Chromium on the Thor →
JPEG tiles → the P4's hardware decoder), or both. **Native wins, and not narrowly.**

The streamed route's whole appeal was reusing the real UI so nothing is duplicated. But the thing
worth reusing is not the UI — it is the **data**, and that is already available in exactly the form
a native client wants: `/api/snapshot` plus a push `WebSocket /ws`. The backend does not need to
change at all.

| | native LVGL + `/ws` JSON | streamed HTML as JPEG |
|---|---|---|
| **what it reuses** | the data and the backend, unchanged | the pixel-level layout |
| **what it duplicates** | layout of the panels we choose | nothing |
| **bandwidth** | a JSON snapshot, kilobytes | a 1280×720 JPEG per refresh — two orders of magnitude more, continuously |
| **new machinery on the Thor** | none | a headless Chromium instance on the Jetson, kept alive and supervised |
| **behaviour when the link drops** | **shows last-known-good with an explicit age** | **shows a frozen image that looks live** |
| **touch** | native widgets, each gradeable | needs input forwarded back, and the grade of a forwarded tap is whatever the remote page decides |

**The link-loss row is the one that decides it, and it is a safety argument rather than an
engineering preference.** A streamed JPEG that stops arriving is pixel-identical to a live panel
displaying steady values. There is no way for someone standing at the robot to tell "the battery is
at 71%" from "the battery was at 71% when the link died four minutes ago." A native renderer holds
the last values *and* the timestamp, so it can grey them out and show the age. For a surface whose
whole job is to tell a human what the robot is doing, a failure mode that silently impersonates
success is disqualifying.

The touch row now reads differently from the way it did when the panel was meant to be read-only,
and it still points the same way. A forwarded tap arrives at the Thor as a coordinate; the grade
table above is a property of the *widget*, and a pixel stream has no widgets. Native keeps the
grade with the control.

The bandwidth and Chromium rows are secondary but push the same way, and the comparison is not even
close enough to need a measurement.

### What the streaming route was actually for, and why it still isn't needed

Being fair to it: three things in the dashboard are genuinely hard to redraw in LVGL, and they are
the real reason the idea came up — `lidarFront` / `lidarRear` (scan plots), `armCanvas` (arm pose),
and `usSpark` (a sparkline). Everything else in those 536 lines is text, SVG icons and buttons,
which LVGL does natively and well.

But those three are **operator-analysis views**. Nobody standing in front of the robot needs a polar
lidar plot; they need to know whether it is about to move. They belong on the tablet, where they
already are.

**Decision: build native. Do not build the Chromium pipeline.** If a canvas view ever genuinely
needs to be on the panel, revisit then, for that one view, as a JPEG the Thor already renders — the
P4's hardware decoder makes that cheap and the option does not expire.

## What goes on the panel

Not a port of 13 views. The right unit is **one screen the face yields to**, sized for someone
standing at arm's length and reading in a second or two — closer to a car's instrument cluster than
to a dashboard.

Four states for the panel, in priority order:

1. **The face** — the default, and what the panel shows almost always. Already built.
2. **A fault screen** — automatic on a fault, saying *what* failed and *what clears it*. This is the
   one genuinely novel thing the panel can do that the tablet cannot, because the person who needs
   it is the person who walked over to the robot.
3. **A prompt layer** — the pinch-guard banner and its consent affordance, drawn over whatever is
   below it. Already half-built (`prompt.cpp` renders it; nothing yet reads a touch).
4. **A technician screen** — what someone kneeling at the robot needs to diagnose it *and act*
   without a tablet. From the existing snapshot: mode and state, the e-stop chain and contactor,
   battery SOC and pack voltage (already reported over RS485), the RC link, the Thor link and its
   age, fault flags, and the IP. Plus the grade-2 and grade-3 controls that belong next to those
   readouts — a mast jog above all. Reached by a deliberate gesture, not a stray touch; times out
   back to the face.

Note the asymmetry with the face's own audience split (`hri-interaction-design.md`): the face serves
bystanders and needs no detail; the technician screen serves the one person who approached. Both
live on the same glass, and the face is the default because bystanders outnumber technicians.

### Open, and deliberately not decided here

- **Does the technician screen need the Thor at all?** The strongest version reads what the *StamPLC*
  already knows over the existing link, so it keeps working when the Thor is down — which is
  precisely when someone kneels at the robot. The head already receives a `LIFT` frame and pan/tilt
  rates directly from the StamPLC, so some of this is in reach without the Thor; whether the whole
  set is depends on 1B and should be decided there.
- **Charging.** Flagged earlier as not purely an idle state: the robot can charge *and* move, so
  charging is both an idle and an attending condition. It has been litigated before and is
  explicitly not reopened here; the panel should take SOC as data and not infer a state from it.

## Why this did not need hardware

Everything above came from reading 971 lines of the dashboard and the head firmware's source.
Worth noting because the instinct was to go build a page and look at it — and because the
correction that mattered most (that each actuating endpoint is already guarded on the robot, and
that the panel has no touch handler at all) is not something a prototype on the bench would have
surfaced.
