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

**The renderer is fast and was hard-won.** The eye sprite is *meant* to live in internal SRAM (448 px tall)
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

The ES7210 gives two channels, and **M5Unified does configure it on this board** —
`_microphone_enabled_cb_tab5` in `M5Unified.inl` is a complete ESP32-P4-guarded init at I2C 0x40.
(Do not conclude otherwise from the S3-only `es7210_write_reg` helper: the P4 path writes the same
chip through `In_I2C.writeRegister8`/`in_i2c_bulk_write` instead, and reasoning from that one
helper's `#if` guard produced a confidently wrong "the mic is never configured" claim on
2026-10-04.) **Spatial separation has been observed working** during voice testing on the bench.

Two details of that init matter for the behaviours below:

- `MIC12_PDN=0x00`, `MIC34_PDN=0xFF` — **two of the four channels are enabled**, which is the
  left/right pair these behaviours need.
- **No gain register is written.** The Tab5 path leaves the PGA at the chip default, where the
  sibling `_microphone_enabled_cb_corep4x` path writes `0x44`/`0x45`/`0x46 = 0x1B`. If levels ever
  read low, registers `0x43`/`0x44` (MIC1/MIC2 gain) are the first knob, not the driver.

**Testing this needs a stimulus the firmware itself produces.** A mic test whose stimulus is
"someone makes a noise" measures the room's noise floor whenever nobody is at the bench, and a
quiet room reads as low, correlated levels on both channels — indistinguishable from a dead chip.
Any mic bench here must drive the device's own speaker and report level-during-tone against
level-during-silence, and must report *inconclusive* when no stimulus is detected rather than
blaming the microphone. Usefully, the speaker and mic **can hold the I2S port at the same time**
(measured), so a self-stimulating test is possible — and so is listening while speaking. That is not Sprout's four-mic array with source localisation, but
left/right energy difference is enough for "turn toward the voice", which is the behaviour people
actually notice. A wake word ("hey robot", as they use) is a separate, bigger piece of work and
probably belongs on the Thor, not the P4 — the head has the mic, the Thor has the compute.

### 5. Render headroom — MEASURED 2026-10-04

On a bench Tab5 with `firmware/tab5_face/bench/`, at the face's own 324×448 geometry:

Measured at both widths in both memories, because narrowing changes two things at once — fewer
pixels *and* internal SRAM instead of PSRAM — and only a matrix attributes the gain:

| eye | memory | needs | 1 push | 2 pushes |
|---|---|---|---|---|
| **324×448** (what ships) | internal | 283 K | **would not allocate** | — |
| **324×448** (what ships) | PSRAM | 283 K | 55.1 | **27.6** |
| **276×448** | internal | 241 K | **82.4** | **39.5** |
| 276×448 | PSRAM | 241 K | 66.7 | 33.4 |

Largest contiguous internal block at start: **247 KB**.

**Narrowing the eye to 276 takes the two-eye frame from 27.6 to 39.5 fps, +43%**, and the two
causes separate cleanly: +21% from the 15% pixel reduction (27.6 → 33.4, both in PSRAM) and +18%
from reaching internal SRAM (33.4 → 39.5, both at 276 px). 1.21 × 1.18 = 1.43, so they multiply as
they should rather than one explaining the whole effect.

For context, `face.cpp` aims at "smooth ~30fps" in its own comment and is in fact delivering
**27.6** — slightly under its target, not over it.

**Take 276, not the measured maximum.** A 4-px probe finds **280×448** is the widest that fits, but
that needs 245 K against a 247 K block — **2 KB of margin**, and when the block comes up short the
code does not fail loudly, it silently moves to PSRAM. 276 leaves 6 KB, which is still thin. So the
more valuable half of this change is not the width at all: **make the fallback annunciate.**
`g_eyePsram` currently reports only over serial, which is why a PSRAM-resident face went unnoticed
in the field. It belongs on the panel or in telemetry.

**Three of this note's own claims were wrong, and the first matters most.**

**The eye does NOT live in internal SRAM.** `face.cpp` says "sized to fit INTERNAL SRAM (~290KB)",
but it needs 283 KB *contiguous* and the largest block is 247 KB, so `createSprite` fails and the
code silently takes its own PSRAM fallback. `g_eyePsram` records this — to **serial only**, which
on the robot nobody reads. The shipping face is almost certainly running from PSRAM in the field
and has been all along.

**The two-eye question was already answered by the shipping firmware.** `face.cpp` allocates *one*
sprite and pushes it twice at `slots[2]`, mirrored by `i == 0 ? -1 : +1`. There is no second
sprite and never was.

**PSRAM is not the disaster recorded here.** 55 fps for a single push, not 1.3. The old 15 fps
figure was a specific access pattern — blitting a glow *template* out of PSRAM every frame — not
PSRAM sprites as such. Worth not repeating as a general rule.

**The actionable number is 276×448**: 15% narrower and the eye fits internal SRAM. And since one
push to two is 55.1 → 27.6 fps, almost exactly half, this is draw-bound rather than
overhead-bound — so a faster sprite should translate nearly 1:1.

## Constraints to design within

- **Internal SRAM is the budget**, not PSRAM. The hot path must not touch PSRAM.
- **The panel is the operator's controller too** (`UiMode::{FACE,CONTROLLER}`): lift buttons,
  e-stop, tracking toggle. Expression cannot own the screen unconditionally.
- **The head can be held in reset by a cable** — fixed in #28, worth remembering.
- **Audio only carries 2–4 kHz** on this speaker, measured by ear on the robot.
- **`Serial.setTxTimeoutMs(0)` makes writes silently lossy.** It is mandatory (without it the
  first print blocks forever and the firmware hangs in `setup()`), but non-blocking means a bulk
  `Serial.write()` emits only what fits the ~256-byte CDC FIFO and **discards the rest with no
  error**. That truncated the bench report mid-word and looked exactly like a failed measurement.
  Emit line by line with a few ms between lines.
- **Serial on this board is the USB-serial-JTAG.** Touching DTR/RTS drops it into ROM download
  mode, so a reader must attach without handshaking — `cat /dev/cu.*` works, pyserial's defaults do
  not. And `Serial.setTxTimeoutMs(0)` is mandatory: without it the first print blocks forever when
  no host is draining the port, and the firmware hangs in `setup()`. Anything worth reading should
  be **re-emitted on a timer**, because attaching resets the chip and so destroys the boot output
  you attached to read.
- **AI tracking mode is a physical tap.** Any behaviour that aims the head remotely depends on a
  mode nobody can set over the network. That should change.

## A concrete next step

The pinch-guard prompt (see `pinch-guard-consent.md`) is the best first behaviour: it is genuinely
useful, it needs the face and the speaker together, it exercises the SAFETY priority tier, and it
turns a real operator complaint into a demonstration of why expression is worth building.
