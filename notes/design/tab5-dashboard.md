# 1A — The Tab5 as a dashboard

Desk work, 2026-10-05. No robot, no network. Grounded in the dashboard that actually exists:
`software/kiosk-dashboard/frontend/index.html` (536 lines, 13 views) and
`backend/app.py` (435 lines, 24 endpoints + a WebSocket).

## The finding that reframes the question

I expected this to be a question about *information*: which readouts belong on the robot versus on
the operator's tablet. Reading the backend, it is mostly a question about **authority**.

Of the 24 endpoints, **17 are POSTs that actuate the robot**: `/api/estop`, `/api/estop/clear`,
`/api/relay/{ch}/{state}`, `/api/mast/{cmd}`, `/api/teleop/{verb}`, `/api/arms/{op}`,
`/api/pdh/{action}`, `/api/system/{action}`, `/api/action/{name}`. The dashboard is an **operator
control surface** that happens to display status, not a status display that happens to have
buttons.

That matters because of where the Tab5 is: a **1280×720 touchscreen on the front of the robot, at
roughly human eye height when the Z-axis is up.** Anyone standing near the robot can reach it. The
operator's tablet is in an operator's hands. And per `notes/design/deployment-network.md`, the
dashboard API has **no authentication yet** — so "put the dashboard on the Tab5" in its naive form
means putting unauthenticated drive, mast, arm and e-stop-clear controls within reach of any
passer-by, on a research robot that will be around students.

So the first answer is not a layout. It is a rule.

### The rule: the head displays; it does not command

With one exception, and the exception already has a precedent in this codebase. `rc_serial_probe`'s
own on-board UI settled the identical question for the StamPLC's buttons:

> Safety rule: buttons may only DE-energize (BtnC hold = local e-stop); re-arm stays
> host/dashboard-guarded.

**Apply the same asymmetry to the Tab5.** A local control that *removes* energy is safe for anyone
to reach — that is what an e-stop button is. A control that *grants* energy or commands motion is
not. So:

- **Allowed on the panel:** everything read-only, plus a single large de-energize affordance.
- **Not allowed:** `estop/clear`, `relay/{ch}/on`, `mast`, `teleop`, `arms`, `pdh`, `system`,
  `action` — anything that arms, moves, or re-enables.
- **Later, if wanted:** a technician mode behind a deliberate gate. That is a separate decision with
  its own threat model, and it should not be smuggled in as a layout choice. Note it, do not build
  it.

This also disposes of the "both" route cleanly: a streamed dashboard page behind a touch gesture
would hand the panel the *whole* control surface, including every endpoint above, in one step. That
is not a page; it is a privilege escalation.

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
| **touch** | native widgets, trivially gated | needs input forwarded back, which re-opens the authority problem |

**The link-loss row is the one that decides it, and it is a safety argument rather than an
engineering preference.** A streamed JPEG that stops arriving is pixel-identical to a live panel
displaying steady values. There is no way for someone standing at the robot to tell "the battery is
at 71%" from "the battery was at 71% when the link died four minutes ago." A native renderer holds
the last values *and* the timestamp, so it can grey them out and show the age. For a surface whose
whole job is to tell a human what the robot is doing, a failure mode that silently impersonates
success is disqualifying.

The bandwidth and Chromium rows are secondary but push the same way, and the comparison is not even
close enough to need a measurement.

### What the streaming route was actually for, and why it still isn't needed

Being fair to it: three things in the dashboard are genuinely hard to redraw in LVGL, and they are
the real reason the idea came up — `lidarFront` / `lidarRear` (scan plots), `armCanvas` (arm pose),
and `usSpark` (a sparkline). Everything else in those 536 lines is text, SVG icons and buttons,
which LVGL does natively and well.

But those three are **operator-analysis views**. Nobody standing in front of the robot needs a polar
lidar plot; they need to know whether it is about to move. They belong on the tablet, where they
already are. So the only content that justified streaming is the content that should not be on the
panel anyway — and the hybrid collapses.

**Decision: build native. Do not build the Chromium pipeline.** If a canvas view ever genuinely
needs to be on the panel, revisit then, for that one view, as a JPEG the Thor already renders — the
P4's hardware decoder makes that cheap and the option does not expire.

## What goes on the panel

Not a port of 13 views. The right unit is **one screen the face yields to**, sized for someone
standing at arm's length and reading in a second or two — closer to a car's instrument cluster than
to a dashboard.

Three states for the panel, in priority order:

1. **The face** — the default, and what the panel shows almost always. Already built.
2. **A technician card** — what someone kneeling at the robot needs to diagnose it without a
   tablet. From the existing snapshot: mode and state, the e-stop chain and contactor, battery SOC
   and pack voltage (already reported over RS485), the RC link, the Thor link and its age, fault
   flags, and the IP. Read-only. Reached by a deliberate gesture, not a stray touch.
3. **A fault screen** — automatic on a fault, saying *what* failed and *what clears it*. This is the
   one genuinely novel thing the panel can do that the tablet cannot, because the person who needs
   it is the person who walked over to the robot.

Note the asymmetry with the face's own audience split (`hri-interaction-design.md`): the face serves
bystanders and needs no detail; the technician card serves the one person who approached. Both live
on the same glass, and the face is the default because bystanders outnumber technicians.

### Open, and deliberately not decided here

- **Does the technician card need the Thor at all?** The strongest version reads what the *StamPLC*
  already knows over the existing link, so it keeps working when the Thor is down — which is
  precisely when someone kneels at the robot. That depends on 1B and should be decided there, not
  pre-empted.
- **The gesture.** A deliberate, non-obvious one. Not a corner tap, which bystanders find.
- **Charging.** Flagged earlier as not purely an idle state: the robot can charge *and* move, so
  charging is both an idle and an attending condition. It has been litigated before and is
  explicitly not reopened here; the panel should take SOC as data and not infer a state from it.

## Why this did not need hardware

Everything above came from reading 971 lines of the dashboard we already have, and the single most
important input — that 17 of 24 endpoints actuate — is not something a prototype on the bench would
have surfaced. Worth noting because the instinct was to go build a page and look at it.
