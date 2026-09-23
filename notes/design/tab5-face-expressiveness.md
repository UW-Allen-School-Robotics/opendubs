# How much more can we get out of the Tab5 face?

Draft, 2026-09-22. Internal.

## What is already there

More than I expected. The face is not a set of static images — it is a small parametric
expression model, and most of the hard part is done.

**The expression vector.** `struct Expr { float h, w, tilt, bow, squint, lookY, brow; }` — seven
parameters per robot state (height, width, tilt, bow, squint, vertical gaze, brow). The renderer
interpolates from the current expression to the target (`lp()`), so state changes are a smooth
morph rather than a cut.

**Life between states.** Blink on a timer with a real blink phase, a breathing oscillation, gaze
drift with random targets, a radar sweep, turn-signal response, and a pun ticker.

**The renderer is fast and was hard-won.** The eye sprite lives in internal SRAM (448 px tall)
with the glow rasterised straight into it each frame; an earlier version kept a glow template in
PSRAM and blitted per frame, and that traffic is what held it to ~15 fps. Dirty-rect repaints:
a state change remakes the glow and label without a `fillScreen`, so it never flashes.

**And now:** audio on the same device (2–4 kHz is what the speaker actually carries), a stereo
mic that nothing reads yet, and network flashing that takes ~15 s.

## What it does not have

- **Expression is bound to robot state.** Seven parameters exist, but only `STATES[]` ever sets
  them. Nothing can say "look pleased" independently of the FSM.
- **No gaze target from outside.** `gTX/gTY` drift randomly; the head's pan/tilt and the eyes are
  unrelated. The eyes do not look where the head points, and neither looks at a person.
- **No transitions with intent.** Every state change is the same morph at the same rate. Surprise
  and settling should not look alike.
- **No sound-expression pairing.** The speaker and the face do not know about each other.
- **Nothing reads the mic**, so the robot cannot look toward a voice — the single most legible
  social behaviour there is.

## Where the headroom actually is

Ordered by expressiveness per unit of work.

### 1. Separate expression from state (small, high payoff)

Let the expression vector be commanded: `EXPR h w tilt bow squint lookY brow` plus a transition
time, over the same UDP text channel as `STATE`. State becomes the *fallback* expression when
nothing else claims the face — which is exactly the `FACE` slot in the behaviour API scope.

That alone unlocks a vocabulary — curious, attentive, pleased, confused — with no renderer
changes, because the morph machinery already exists.

### 2. Gaze that means something (medium)

Couple `gTX/gTY` to a commanded target, and couple that to the head's pan/tilt. Looking at a
point should move the eyes, then the head, with the eyes leading — that lead is most of what
makes gaze read as intent rather than mechanism.

We have the pieces: head angles are commanded from the Thor, eyes are local. What is missing is
a shared notion of "where", and the head's absolute-angle API only works in AI tracking mode.

### 3. Sound married to expression (small)

A blink with a tick, a state change with a tone, the pinch-guard hold with its prompt and a voice
on the screen. Sounds must sit in 2–4 kHz to be heard, which constrains the palette: we get
"chirp", "tick", "trill", not warmth. Worth knowing before designing a personality around it.

### 4. Listening (larger, and the biggest step)

The ES7210 gives two channels. That is not Sprout's four-mic array with source localisation, but
left/right energy difference is enough for "turn toward the voice", which is the behaviour people
actually notice. A wake word ("hey robot", as they use) is a separate, bigger piece of work and
probably belongs on the Thor, not the P4 — the head has the mic, the Thor has the compute.

### 5. Render headroom (unknown, needs measuring)

The panel is 720×1280 and the eye sprite is 448 px in internal SRAM. Unknown: what frame rate a
*two*-eye layout costs, whether a second sprite fits in internal SRAM, and whether PSRAM can be
used for anything off the hot path. Before designing richer faces, measure — the previous PSRAM
lesson cost a 5× frame rate.

## Constraints to design within

- **Internal SRAM is the budget**, not PSRAM. The hot path must not touch PSRAM.
- **The panel is the operator's controller too** (`UiMode::{FACE,CONTROLLER}`): lift buttons,
  e-stop, tracking toggle. Expression cannot own the screen unconditionally.
- **The head can be held in reset by a cable** — fixed in #28, worth remembering.
- **Audio only carries 2–4 kHz** on this speaker, measured by ear on the robot.
- **AI tracking mode is a physical tap.** Any behaviour that aims the head remotely depends on a
  mode nobody can set over the network. That should change.

## A concrete next step

The pinch-guard prompt (see `pinch-guard-consent.md`) is the best first behaviour: it is genuinely
useful, it needs the face and the speaker together, it exercises the SAFETY priority tier, and it
turns a real operator complaint into a demonstration of why expression is worth building.
