# The face, designed from a clean sheet

What to build now that the platform is not the constraint: LVGL with hardware compositing, a
camera, dual microphones, an IMU, a front rangefinder, and a pan/tilt neck. Grounded in the
research rather than in taste, and explicit about which capabilities are real today.

Platform facts are in [`tab5-platform.md`](tab5-platform.md); the interaction model and the
audience split are in [`hri-interaction-design.md`](hri-interaction-design.md).

---

## The thesis: the face is an attention system whose output happens to be eyes

**What makes a face read as alive is contingency, not artwork.** An observer infers a mind from
gaze that demonstrably responds to the world — and infers a screen from gaze that does not. Every
hour spent on eye rendering is worth less than the first hour spent making the gaze *about
something*.

This reframes the whole problem. The face is the final output stage of a perception → attention →
motor pipeline, and it should be built in that order. Our previous attempts failed not because the
eye geometry was wrong but because the eyes were looking at nothing: a random saccade generator and
a 12-pixel travel budget.

It also means the right literature is **non-conversational character gaze** — saliency-driven gaze
for characters that do not speak. That is exactly what OpenDubs is, and it is a mistake to borrow
from conversational-agent work that assumes dialogue.

---

## Layer 1 — perception: what can aim the gaze

| Source | Gives | Status |
|---|---|---|
| Camera + `HumanFaceDetect` | **bearing, apparent size (≈ distance), presence** | **scaffolded but COMMENTED OUT** in the demo; needs `esp-dl` added |
| Front ToF (Port A, `0x29`) | distance to whatever is ahead | works today |
| Dual mic (ES7210) | sound onset, coarse left/right/centre | works today, measured |
| IMU (BMI270) | own angular rate, tilt, impact | works today, HAL-exposed |
| Pan/tilt (8Servo `0x25`) | physical head aim | works today |
| Robot state, lift request, drive intent | **own intent** | works today |

**Vision is the keystone and it is not free.** Everything below is designed to work without it and
to get better with it.

---

## Layer 2 — attention: one target, with a priority and a confidence

A single arbiter resolves all sources into **one** target. Highest priority first:

1. **Own motion intent** — where the robot is about to move.
2. **A vision-confirmed person's face** — mutual gaze.
3. **A sound onset** — unconfirmed, an orienting glance only.
4. **A task object** — what an arm is reaching for.
5. **Ambient** — idle looking about.

**Intent outranks the person deliberately.** If the robot is about to move, telling people where it
is going matters more than being sociable — that is the legibility argument from
[Dragan & Srinivasa](https://personalrobotics.cs.washington.edu/publications/dragan2013legible.pdf),
and it is also the safer ordering. A robot that holds eye contact while starting to drive is
charming and wrong.

---

## Layer 3 — the five behaviours worth building, in value order

### 1. Mutual gaze with an aversion rhythm

Look at the detected face, hold **1–2 s**, break away briefly, return. Not a continuous lock.

Sustained eye contact is aversive in humans, and the gaze literature is consistent that **multiple
short fixations read more accurately than one long stare**. The rhythm *is* the behaviour: it is
what separates comfortable attention from being stared at by a machine. This is the foundational
social signal and everything else is secondary.

### 2. Vestibulo-ocular reflex — the highest-value non-obvious behaviour

**When the body moves, counter-rotate the gaze to hold the target.** Integrate the BMI270's gyro
and subtract it from the eye/head aim.

This is the single thing that most distinguishes a face that is *looking* from a screen that is
*displaying*. A gaze rigid to its chassis reads as a monitor bolted to a cart: the robot turns and
the eyes turn with it, so they are evidently not attending to anything. A gaze that stays locked on
a person while the base rotates underneath reads, immediately and without explanation, as a creature
looking at you. It is biologically canonical, it is cheap, and we have the sensor.

It also pays off on a mobile base in a way it would not on a desk toy — which is precisely our case
and not the case of most robot-face prior art.

### 3. Audio orients, vision confirms

Audio is **fast and wide but coarse**; vision is **slow and narrow but accurate**. Biological
orienting uses exactly this division: an auditory saccade, then a visual fixation.

So a sound triggers a **glance**; if a face is found there it promotes to mutual gaze; if not, the
glance decays and the robot returns to what it was doing. This directly fixes the crowd failure
named in `hri-interaction-design.md` — audio alone chases the loudest bystander, while
vision-gating means only an actual person captures attention.

Without vision enabled, this degrades to exactly today's behaviour: a glance toward a noise. That
is the graceful-degradation path.

### 4. Eyes lead, the head follows

With a pan/tilt neck: the **eye saccade happens first** — fast, cheap, on-screen — and then the head
rotates to centre the target while the eyes re-centre.

This is the human pattern, and it is what makes a 2-DOF neck look intentional rather than servo-
driven. Moving the head alone, with eyes fixed in the screen, is the uncanny version.

### 5. Gaze precedes motion

Already implemented (`faceLookAt`, the `GAZE` command, lift gaze on `Lift.request()`). With the neck
available it should aim the head too, and the lead time still belongs to the caller.

---

## Proxemics: distance should change behaviour

Distance comes free from the ToF and from apparent face size. Behaviour should not be
distance-invariant:

| Zone | Gaze |
|---|---|
| Far | larger amplitudes, bigger head moves — legible at a distance |
| Social | the normal mutual-gaze rhythm |
| Near | smaller amplitude, more blinks, softer — a crowding response |
| Near **and** about to move | annunciation dominates; sociability yields |

A person merely passing through should not capture attention at all — presence plus dwell, not
presence alone.

---

## Rendering, now that it is not the bottleneck

- **Compositor, not rasteriser.** Layers blitted by PPA into a DSI framebuffer. The moving part is
  its own layer, so gaze travel is unbounded and the ±11 px budget that defeated every previous
  attempt does not exist.
- **Saccadic motion with main-sequence timing**, already written and carried over unchanged.
- **No pupils.** Anki added them to Cozmo, found they made him read as too human, removed them and
  recovered the same legibility through **size and ratio**. Gaze direction lives in shape and in
  inter-eye asymmetry.
- **One highlight, fixed.** A highlight that chases the gaze was tried and read as confusing.

---

## What NOT to build, and why

- **No pupils** — the Cozmo lesson above.
- **No emotion palette.** Express *state*, which is true and bystander-legible, not feelings, which
  are not.
- **No speech-shaped audio.** Five rhythm-distinct earcons. Anything voice-like invites a
  conversation this robot cannot hold, and spends its credibility immediately.
- **No continuous smooth tracking.** It reads as a stare and it exposes sensor jitter that discrete
  glances conceal.
- **Nothing implying recognition.** The robot must not appear to know who you are.

---

## Build order

1. **Enable vision** — add `esp-dl`, uncomment `HumanFaceDetect`, and measure what it costs in image
   size and PSRAM. Everything of value depends on this, and it is currently the only unproven item.
2. **Mutual gaze with aversion rhythm**, vision-gated.
3. **VOR from the IMU** — cheapest large win, and independent of vision.
4. **Audio-orients-vision-confirms arbitration.**
5. **Eyes-lead-head-follows** on the pan/tilt.
6. **Proxemic modulation** from ToF and face size.
