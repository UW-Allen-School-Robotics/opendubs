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

---

## Features: what the research says about a mouth and eyebrows

Asked directly, because an earlier decision against a mouth was made when the graphics were
believed to be the constraint, and that premise is gone.

### Eyebrows: keep them, and make them do more

**You already have them.** `drawBrow()` exists in `face.cpp` and is called from two places, and the
expression tuple is `{h, w, tilt, bow, squint, lookY, brow}` — a brow parameter has been there all
along. If they read as absent, they are too subtle, which is a tuning problem rather than a missing
feature.

The evidence supports investing in them:

- They are named as the **emotion intensifier** in minimal-face work — "dominant and strongly
  pulled-down eyebrows for anger"
  ([geometric simplification study](https://openaccess.cms-conferences.org/publications/book/979-8-950676-13-0/article/979-8-950676-13-0_86)).
- More surprisingly, **eyebrow-raising measurably improves speech intelligibility.** A study of
  animated head gestures found that **head-nods and eyebrow-raising significantly increased speech
  intelligibility** relative to the same gestures being absent *or added randomly*
  ([audiovisual speech work](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10686424/)).

That second finding is worth more to us than the first, for two reasons. It means brows have a
**functional** payoff once the robot speaks, not merely an expressive one. And **we also have a
pan/tilt neck**, so head-nods — the other half of that result — are available for free.

**The condition attached to it matters: the gestures must be correlated with the speech.** Randomly
added gestures did *not* help. So brow-raises and nods belong on prosodic events — emphasis,
phrase boundaries, question intonation — not on a timer.

### A mouth: the evidence does not support adding one

Two independent findings, pointing the same way:

- **Eyes on a large display, with the mouth omitted, was best for conveying emotion** — especially
  happiness and anger — in
  [*It's in Your Eyes: Which Facial Design is Best Suited to Let a Robot Express Emotions?*](https://dl.gi.de/items/77a25433-cb05-4265-8125-b76ef074d124/full).
  That is close to a description of our hardware.
- The audiovisual-benefit literature is clear that seeing a talker helps, and helps **more as the
  room gets noisier** — which would be the argument for a mouth at a demo. But the one study of a
  **robot head** specifically found intelligibility was **not** increased by its visual speech cues
  against noisy audio alone, and concluded that this "underlines the importance of carefully
  designing and controlling the facial co-speech movements."

So the honest statement is **not** "a mouth cannot work" — it is that the audiovisual benefit is
established for *human* faces and **unproven for robot faces**, while the cost is high: lip-sync
accurate enough to help is exactly the thing that study found hard, and an inaccurate one buys
nothing. Meanwhile brow-raises and nods delivered a measured intelligibility gain on the same
problem for far less work, using parts we already have.

**Recommendation: no mouth. Invest the same effort in brows and nods, which have the measured
result.** Revisit only if speech intelligibility proves to be a real problem in a real demo — and
then measure it rather than assume a mouth fixes it.

---

## The perimeter band: a second channel, and a good idea

Modelled on the band that races around the iOS screen edge during a Siri invocation. **Adopt it**,
for a reason stronger than aesthetics: it separates two messages that currently share one carrier.

Today the robot's **state** is encoded in eye **colour and shape** — so the eyes carry both *where I
am looking* and *what mode I am in*. Those are different messages to different audiences, and
making one carrier do both is why neither reads cleanly.

| Channel | Carries | Audience | Readable |
|---|---|---|---|
| **Eyes** | attention — where I am looking, am I engaged with you | whoever is in front | needs to be roughly face-on |
| **Perimeter band** | state — mode, busy, listening, thinking, fault | anyone, including bystanders | from any angle, at distance, peripherally |

Three things follow:

1. **The band is the right home for the conversational states.** Listening, thinking, speaking — the
   exact problem the local/remote latency split creates, where the robot must visibly be *doing
   something* while the Thor works. Siri's band is literally that affordance.
2. **It frees the eyes.** If state lives on the perimeter, the eyes can hold a consistent colour and
   spend their whole vocabulary on gaze and expression, which is what the pupil-less-gaze problem
   needs.
3. **It is peripherally legible**, which no eye detail is. A band at the screen edge is visible to
   someone beside the robot or glancing past it — closer to the PRISM LEDs in function, and a
   natural partner to them.

Implementation is unremarkable in LVGL: an arc or a set of edge segments with an animated gradient,
driven by the same `AnimateValue` machinery. Motion style carries meaning — a slow pulse for idle,
a travelling sweep for thinking, a fast flash for a fault — so the band should have a small,
deliberate vocabulary of *motions*, not just colours, for the same reason the earcons are
rhythm-distinct.

**One hard limit:** the band is on the Tab5, so it dies with the e-stop and with the link. It is a
social and operational channel, not a safety one. PRISM remains the safety annunciator.

---

## The funny quotes: keep them, and keep them in their place

They stay. Two design constraints on where:

- **Text is the least accessible channel we have.** It needs literacy, proximity, attention and a
  shared language — so it must never carry anything that matters. Jokes are the perfect payload for
  exactly that reason.
- **They are an idle-state artefact and should remain one.** They appear when the robot has nothing
  else to say, which is precisely the right scope. They must not compete with the band or the eyes,
  so they keep their own region — the bottom strip — and vanish the moment there is real state to
  communicate or a person to attend to.

Now that speech is on the table, the temptation is to have the robot *say* them. **Resist it for
now:** a spoken joke invites a conversational turn, which lands straight in the
expectation-calibration risk, and a visual gag that someone has to walk up and read is self-limiting
in a way that is actually desirable.

---

## States and emotes are different things

A state is **mostly a colour**, plus a policy. An emote is an **expression**. Mapping one state to
one expression conflates them, and it throws away the thing that makes a face worth having: in the
calm states the face should be free to be *emotive*, not locked to a posture.

| | carries | when it changes |
|---|---|---|
| **State** | colour (and later the band), plus whether emotes are free | the robot's mode changes |
| **Emote** | the seven-parameter expression, plus a point in valence/arousal | what the robot notices or is doing |

**The test is whether the robot is ATTENDING TO THE WORLD or REPORTING A CONDITION.** Attending
states — `SAFE_IDLE`, `MANUAL`, `AUTONOMOUS` — are free to be emotive. Reporting states — `INIT`,
`DEGRADED`, `E_STOP`, `CHARGING` — pin one appropriate emote, because cycling through moods while
reporting a condition is incoherent: `SLEEPY` during an e-stop says nothing true about anything.

**Pinning the emote does not freeze the face.** Saccades and blinks continue in every state, because
a robot whose eyes have stopped moving reads as broken rather than as stopped.

**Emotes are locked in the states where the posture is the message.** `E_STOP` and `DEGRADED` are
communicating a condition, and a robot that looks cheerful while degraded is lying. `INIT` and
`CHARGING` are likewise fixed. `SAFE_IDLE`, `MANUAL` and `AUTONOMOUS` are free.

### Named *and* dimensional, because the research says both

The categorical-versus-dimensional question has an answer: **together beats either alone.**
Predicting discrete categories and continuous valence/arousal simultaneously improves both
([CAGE](https://cs.paperswithcode.com/paper/cage-circumplex-affect-guided-expression),
[Handrich et al.](https://repo.bibliothek.uni-halle.de/bitstream/1981185920/82354/1/Handrich%20et%20al._Simultaneous_2021.pdf)).

So emotes keep **names** — which is what makes them designable, discussable and reviewable — and
each also carries a **valence** (−1..+1) and an **arousal** (0..1).

**Arousal is not a label: it sets the saccade rate.** Per *Eyes Alive*, an activated face looks
about often and holds briefly while a calm one holds for much longer — so `ALERT` and `SLEEPY`
differ in *timing* as well as shape, which is most of what makes them read differently. That is the
dimensional axis doing real work.

The free vocabulary is `NEUTRAL`, `ALERT`, `SQUINT`, `SOFT`, `SLEEPY`, `HAPPY`. Worth noting that
happiness is the most reliably recognised expression across the minimal-face literature, which is
an argument for `HAPPY` carrying more of the load than its share.

### Colour must be eased, never sprung

Transitions were flashing through other states' colours. The cause: R, G and B were three
independent **springs**, and a spring overshoots — an overshoot in RGB space lands on a genuinely
different hue, so a green-to-amber transition could pass through something that reads as a third
state. Easing cannot overshoot. **Any value whose intermediate positions carry meaning must not be
sprung**, which covers colour and would also cover anything mapped to a discrete annunciation.

## State machine: the port

The existing table in `ui_shared.cpp` is good and carries over directly — seven states (`INIT`,
`SAFE_IDLE`, `MANUAL`, `AUTONOMOUS`, `DEGRADED`, `E_STOP`, `CHARGING`), each with a colour and an
`Expr` of `{h, w, tilt, bow, squint, lookY, brow}`.

What changes is that each parameter becomes its own `AnimateValue`, so a state transition is seven
independent springs rather than one lerp toward a tuple — and transitions can differ per parameter,
which is how a state change reads as a change of posture rather than a crossfade.

What the band adds is a **second axis**: state no longer has to be inferred from eye colour, so the
table grows a band behaviour per state (colour **and** motion) while the `Expr` can be re-tuned
purely for how the face should *hold itself* in that state.

---

## Eye sizing and spacing: the numbers, and they do not say what we assumed

The eyes read as small for the screen. Measured against the panel's real physical dimensions and
against a human face, **size turns out not to be the main problem.**

The Tab5 is a **5-inch** panel: 1280×720 over **110.7 × 62.3 mm**, so **0.0865 mm per pixel**. That
is worth sitting with — the whole display is physically about the size of a human **eye region**,
not a whole face. It is a mask over the eyes, which is why proportions borrowed from whole-face
designs mislead.

| | OpenDubs today | Human | |
|---|---|---|---|
| eye width | 284 px = **24.6 mm** | 30 mm (visible aperture) | 82% — close to right |
| eye height | 376 px = **32.5 mm** | ~11 mm | **3.0×** |
| **interpupillary distance** | 380 px = **32.9 mm** | 63 mm | **52%** |

**The dominant error is spacing, not size.** The eyes sit at **half human interpupillary distance**,
and are three times as tall as a human eye's aperture. Tall, narrow and close together is the
geometry of a small animal's face, or of a narrow mask — not of a person-scale robot standing at
eye level. That explains "it does not read right" better than size does, and it is also why making
the eyes *smaller* earlier in this project made things worse rather than merely different.

**Human-scale proportions fit the panel**, with room to spare:

```
IPD 63 mm   = 729 px  ->  slots at x=276 and x=1004
eye 30 mm   = 347 px  ->  eyes span 102..449 and 831..1178
                          side margin 102 px, inner gap 382 px
```

An inner gap of roughly one eye width is also the human proportion (63 mm centres minus a 30 mm
eye leaves 33 mm between them), so that falls out for free.

### What the research adds on top of human scale

The **baby schema** (Lorenz's *Kindchenschema*) is the relevant body of work, and it has been tested
on robot faces specifically: **large eyes, round face, and medium vertical and horizontal eye
positions produce the highest perceived trustworthiness and cuteness**
([facial features and robot trustworthiness](https://arxiv.org/pdf/2509.13948),
[Hong Kong PolyU study](https://research.polyu.edu.hk/en/publications/the-effect-of-facial-features-on-facial-anthropomorphic-trustwort/)).
Eye **shape and size** are called out as having significant effect. So the research-supported
direction is **larger than human**, not merely human-scale — with one recorded caveat: *excessively*
high baby schema can become counterproductive for some robot types, so this is a dial, not a
maximum.

**Roundness matters as much as scale.** A human eye aperture is strongly landscape (~2.7:1); ours is
portrait (0.76:1). Baby schema asks for round. Moving from tall-and-narrow toward round is a change
in the *same* direction as both the human-proportion argument and the baby-schema argument, which is
unusual and worth taking.

### Why this matters more for this robot than for a desk toy

The robot **stands at human eye level with the Z-axis up, and a little below it when down.** A
machine at eye height is socially salient in a way a tabletop robot is not — it is read as a
counterpart rather than an object, and it can read as confrontational. Baby-schema features are the
established mitigation. And with Z down the robot looks slightly *up* at people, which is
intrinsically a non-threatening posture and pairs naturally with those same features.

### The gaze cue is already strong — do not spend effort there

Pupil travel is now **±80 px = ±6.9 mm**, where a human eye rotating 15° displaces its pupil about
**3.1 mm**. Our gaze displacement is therefore **2.2× a 15-degree human saccade**. Gaze geometry is
not the weak link, and no further travel is needed; the earlier ±11 px problem is solved by the
pupil being its own layer.

### Proposed geometry

1. **Widen the IPD to roughly human scale first** (380 → ~729 px). Largest single improvement, costs
   nothing, and the panel has room.
2. **Move the aspect ratio toward round.** Both the human-proportion and baby-schema arguments push
   the same way.
3. **Then scale up past human** as taste allows, which is where baby schema points — bounded by
   leaving perimeter space for the state band.
4. Keep the pupil at roughly a third of body width; the travel that produces is already ample.

All four are now single constants in `app_face.cpp`, iterable on the desktop build in seconds.

---

## Themes

Worth building as a first-class concept rather than a seasonal hack, because the face already has
the right shape for it: a state table with a colour per state, plus layered objects. A theme is
then three things — a **palette**, an **accessory layer**, and a **band motion style** — applied over
an unchanged state machine.

`m5stack-avatar` already treats this as a concept (it ships an `Accessory` drawable alongside
`Eyeblow`, `Mouth` and `Balloon`), which is some evidence it is the natural decomposition rather
than ours alone. In LVGL an accessory is just another layered object, so a hat, a cobweb in the
screen corner or a prop over one eye costs almost nothing.

### The one rule a theme must not break

**State must stay legible through any theme.** The state channel is partly carrying safety and
operational meaning — `DEGRADED` is amber, `E_STOP` is red — and a theme that recolours everything
orange has quietly destroyed the difference between "it is Halloween" and "something is wrong."

So the constraint is: a theme may restyle **everything except the state distinction**. In practice
that means the perimeter band keeps a reserved, theme-proof vocabulary for fault and e-stop states,
and themes get the rest. This is the same separation-of-channels argument that justified the band
in the first place — and it is exactly the kind of thing that is easy to get right now and
expensive to retrofit after a theme ships.

### Halloween as the first theme

A good first theme to build, because it is a motivated test case: it exercises the **accessory
layer**, the **band's motion vocabulary** and the **animation system** — all of which are 1.2 work
regardless — and it has an obvious success test, which is usually when this kind of work goes
fastest. Things worth trying: a prop over or beside an eye, something in a screen corner, a theme
palette, and a band motion that is seasonal rather than operational.
