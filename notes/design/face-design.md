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

This holds whether or not the robot speaks — and **it can speak**, so the gaze work is the
foundation under a conversational robot rather than a substitute for one. See
[§ Conversation](#conversation-it-is-a-latency-budget-not-a-capability-question).

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
- **No speech-shaped NON-SPEECH audio.** Earcons stay clearly abstract so they are not mistaken
  for words. This is about keeping two channels distinct, not about avoiding speech — the robot
  speaks, and when it does it should use actual speech.
- **No continuous smooth tracking.** It reads as a stare and it exposes sensor jitter that discrete
  glances conceal.
- **Nothing implying recognition.** The robot must not appear to know who you are.
- **No LLM authority over motion.** See the safety boundary below. This is the one hard line.

---

## Conversation: it is a latency budget, not a capability question

An earlier draft of this note asserted that OpenDubs "cannot converse" and designed around that.
That was wrong, and it was asserted rather than checked. The parts are all present:

| Need | What we have |
|---|---|
| Audio in | dual ES7210 mics, working and measured |
| Audio out | ES8388 + NS4150B, documented "Hi-Fi", plus a 3.5 mm jack |
| Interruption | speaker and mic hold I2S **simultaneously** — measured, so barge-in is available |
| Speech-to-text | **Jetson AGX Thor, 128 GB** — Whisper-class STT is an unremarkable load |
| Reasoning | a local model on the Thor, or a hosted API over the existing uplink |
| Text-to-speech | neural TTS on the Thor |
| Transport | Wi-Fi 6 via the C6, on the robot's own LAN |

So the question is not *whether* but *how fast*, and what happens while the robot is thinking.

### Reflexes local, cognition remote

The split that makes this work:

- **The Tab5 owns everything that must be fast**: gaze, VOR, blink, the acknowledgement earcon,
  turn-taking cues, lip movement. Tens of milliseconds, no network in the path.
- **The Thor owns everything that must be smart**: VAD, STT, reasoning, TTS. Hundreds of
  milliseconds to seconds, over the network.

This is not an implementation detail, it is the thing that makes conversation feel alive. Humans
acknowledge being addressed in roughly a quarter of a second, long before they have a reply. A
robot that stands inert until a full pipeline returns reads as broken even when the eventual answer
is excellent.

**So the face must never wait for the pipeline.** The sequence is:

1. **Sound onset → glance** (local, < 100 ms). Contingency established immediately.
2. **Vision confirms a person → mutual gaze** (local).
3. **Speech detected → a listening posture**, held while audio streams to the Thor (local).
4. **Thinking** — a visible, honest state while STT and the model run (local animation, remote work).
5. **Audio returns → speak**, with the mouth driven by the audio envelope.
6. **Barge-in**: the user interrupts, the robot stops talking and returns to listening.

Steps 1–4 and 6 are all local and all available today. Only step 5 needs the Thor.

### Acoustic echo cancellation now matters

`M5Unified`'s Tab5 init sets `MIC34_PDN = 0xFF`, disabling ES7210 channels 3 and 4 — **which are
the AEC reference inputs**. That was a curiosity when the robot only chirped. For conversation it is
a requirement: without it the robot transcribes its own speech and talks to itself. Enabling those
channels is a concrete, bounded task with a clear payoff.

### Degradation, which is a design requirement not a footnote

The link will drop. When it does the robot must stay **attentive but mute** — gaze, VOR, glances,
earcons and expression all keep working because they are local, and only speech is lost. That is a
robot that looks alive and cannot talk right now, which reads as a temporary condition rather than
as a corpse.

### The one hard line: no LLM authority over motion

Conversation is an **input/output channel, never an authority**. A language model must not be able
to move the base, the lift or an arm — not directly, and not by asking a planner nicely. Authority
stays where `docs/control-authority.md` puts it: a physical switch on the transmitter, with a
deadman. The robot may *say* "I could move over there"; it may not *move* because a model emitted
tokens. Prompt injection via anything the robot hears or sees is otherwise a motion-command channel
for any bystander who can talk.

### Expectation calibration, inverted

The earlier draft warned against an expressive face inviting conversation. That warning was
downstream of the wrong premise. With speech real, expressiveness is **honest** rather than
over-promising, and the risks move:

- **Latency breaking the illusion** — mitigated by the local/remote split above.
- **Implying comprehension the model lacks** — a "thinking" state and a visible failure state are
  better than a confident wrong answer delivered smoothly.
- **Implying recognition** — still off limits.

## Build order

0. **Prove the audio round trip** — Tab5 mic → Thor → Tab5 speaker, measured end to end, before any
   STT or model is involved. That single number decides how conversational this robot can feel, and
   it is cheap to measure.
1. **Enable vision** — add `esp-dl`, uncomment `HumanFaceDetect`, and measure what it costs in image
   size and PSRAM. Everything of value depends on this, and it is currently the only unproven item.
2. **Mutual gaze with aversion rhythm**, vision-gated.
3. **VOR from the IMU** — cheapest large win, and independent of vision.
4. **Audio-orients-vision-confirms arbitration.**
5. **Eyes-lead-head-follows** on the pan/tilt.
6. **Proxemic modulation** from ToF and face size.
7. **Listening, thinking and speaking states** on the face, with lip movement from the audio
   envelope — the local half of conversation, useful before the remote half exists.
8. **ES7210 AEC channels** enabled, without which the robot hears itself.
9. **STT, model and TTS on the Thor**, last, because by then everything it plugs into exists.
