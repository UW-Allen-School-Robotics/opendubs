# Interaction design for OpenDubs — what the HRI literature says, and what we can actually build

A survey mapped onto **measured** capability, not hoped-for capability. Every "we can" below is
backed by a bench or robot measurement recorded in this repo; every "we cannot" is a constraint we
hit, not a guess.

The companion notes are [`tab5-face-expressiveness.md`](tab5-face-expressiveness.md) for the
rendering and audio substrate, and [`../../docs/control-authority.md`](../../docs/control-authority.md)
for who is allowed to move the robot. This note is about what a **person standing in front of it**
can tell, and it closes that document's open item on annunciating the active mode.

---

## 0. Who each channel is for

The robot has **two** annunciation channels and they are not interchangeable.

**The PRISM LEDs are the first-line state communicator**, and they stay lit through an e-stop —
they are on their own fused branch (position 6), not the drive branch. `docs/operating.md` already
relies on this: amber strobe means waiting to arm. This is the channel for anything that must be
true regardless of robot state.

**The Tab5 face is a social channel, not a safety channel.** It is powered from the
e-stop-protected drive branch, so it goes dark when the contactor opens. That is fine, because of
the audience split below — but it does mean the face must never be the *only* carrier of anything
that matters when the robot is stopped.

### The audiences want different things

This distinction does more design work than any single behaviour:

| | Bystanders | Operators |
|---|---|---|
| What they need | *Is it about to move, and where?* | *What state is it in, and how do I recover it?* |
| Have training | no | yes |
| Have a transmitter | no | yes |
| Care that it is e-stopped | **no** — an e-stopped robot is not a hazard | **yes** — they have to recover it |

**An e-stop message is an operator concern, not a bystander one.** A stopped robot has already
communicated the only thing a bystander needs by not moving. The people who need detail are the
ones who must diagnose and restart it, and they are the ones with PRISM in view and a dashboard in
hand.

So the face's job is **intent before motion**, which is a bystander concern and is only relevant
while the robot is live and powered — exactly the condition in which the face is up. The
e-stop-dark property stops being a problem the moment the channels are assigned to the right
audiences.

**Corollary for the e-stop eye state.** `ui_shared.cpp` defines an `E_STOP` expression with the
widest eye of any state (`w=1.06, h=1.08`). It can only ever be seen when the *software* believes
e-stop without the contactor having opened — a real condition, but a narrow one. It is not the
e-stop indicator; PRISM is.

## 1. Legibility is not predictability — and it is the frame to use

The core distinction, from [Dragan & Srinivasa](https://personalrobotics.cs.washington.edu/publications/dragan2013legible.pdf)
(UW Personal Robotics — i.e. our own collaborators' framework):

- **Predictable** motion matches what an observer expects, given a known goal. Goal → action.
- **Legible** motion lets an observer *infer* the goal from the motion in progress. Action → goal.

They are different and often contradictory: legible motion deliberately exaggerates away from the
efficient path to make intent inferable, bounded so it does not become merely surprising. Legible
motion produces more fluent collaboration than predictable motion.

**Why this matters for us before any arm planning work:** the cheapest legibility we can buy is not
in the trajectory at all. It is **looking where we are about to go, before going there.** The gaze
precedes the action, the observer infers the goal, and the motion itself is unchanged. No planner,
no new sensing, no risk.

That single behaviour — *gaze leads motion* — is the highest-value item on this list, and it works
for the base, the lift, and the arms equally.

---

## 2. Gaze is the primary social channel, and coarse is fine

From the gaze literature ([attention-control model](https://arxiv.org/pdf/2007.06531),
[planning-based gaze architecture](https://arxiv.org/pdf/2210.02866)):

- Gaze signals attention and coordinates **joint attention** — mine, yours, and the shared object.
- Turning the head toward a speaker signals engagement; humans turn first toward the person they
  intend to address, and robots using the same convention are read correctly.
- Gaze at or away from an interlocutor signals **holding or yielding a turn**.
- A four-phase structure recurs: attract attention → make eye contact → avert → shift to the object.
- **Multiple short fixations beat one long stare** — people read brief, repeated glances more
  accurately than sustained gaze.

That last point is a gift given what we measured. Our microphone resolves **left / right / centre**
and little more, and a level-ratio bearing is noisy. Continuous smooth tracking would expose that
noise as jitter; **discrete glances hide it**, because a glance is allowed to be approximate and
then end. The literature's preferred behaviour is also the one our sensing can actually support.

**So: do not build a head that continuously tracks a voice. Build one that glances toward it.**

---

## 3. Sound: a much narrower palette than the literature assumes

Nonverbal robot sound maps state onto **pitch, rhythm, timbre, loudness, repetition and location**
([survey](https://link.springer.com/article/10.1007/s12369-021-00788-4),
[functional-state expressions](https://arxiv.org/pdf/2404.19253)). Cues must be detectable without
becoming fatiguing or masking speech and environmental warnings, and listeners decode them through
prior media and cultural convention.

**The audio path is not the constraint.** An earlier draft of this note claimed the speaker
"carries only 2–4 kHz" and used that to rule timbre out of the palette. That was not a measurement
— it was `M5.Speaker.tone()` judged by ear in a noisy room — and it is withdrawn. The Tab5 pairs an
**ES8388** codec with an **NS4150B** amp driving a **1 W 8 Ω** speaker, documented by M5 for
*"Hi-Fi recording/playback and accurate voice recognition"*, with a 3.5 mm jack as an escape hatch.
Treat the audio path as capable and spend the effort on what is played through it.

A small vocabulary separated by **rhythm** is still the right starting point, not because the
speaker is poor but because rhythm survives a noisy room and a listener who is not paying
attention:

| meaning | why it is needed | shape |
|---|---|---|
| *heard you* | acknowledgement inside ~1 s, even when nothing can be done yet | one short blip |
| *attending to you* | pairs with the glance | two quick rising blips |
| *cannot do that* | refusal must be distinct from failure | two slow falling blips |
| *something is wrong* | fault, needs a human | repeating triple, does not stop |
| *mode changed* | authority transitions must be audible, not just visible | single long tone |

Deliberately **five**, and deliberately not an emotional palette — see §5.

### How these get played — mostly by configuring M5Unified, not by writing a synth

`M5.Speaker` already provides the machinery, and the vendored examples
(`Basic/Speaker`, `Advanced/Speaker_SD_wav_file`, `Advanced/MP3_with_ESP8266Audio`) show the
patterns. Worth reading before writing anything:

| Already provided | Consequence for us |
|---|---|
| **8 virtual channels (0–7)**, independent volume, `isPlaying(ch)`, `stop(ch)` | A mixer exists. Assign channels by purpose and a cue can never cut off a more important one. |
| `tone(freq, ms, ch, stop_current, raw_data, len)` | **Timbre is a parameter** — pass a short wavetable. No synth to write. |
| `playRaw(pcm, len, rate, …)`, int8 or uint8 | The route for an envelope-shaped cue, exactly as `Basic/Speaker` does with its click sample. |
| `playWav(array, size, …)` from a flash byte array | Short cues need no filesystem at all. |
| `setBufferReleaseCallback()` | Streaming generated audio, if it is ever wanted. |
| `spk_cfg.sample_rate`, default 64 kHz, up to 192 kHz | Quality is a config line, not an engineering effort. |

**So the thin layer we actually write** is a table of cues — frequency, rhythm, channel — and a
small non-blocking sequencer to step a cue's blips. Everything underneath it exists.

**Channel assignment is the design decision**, and it is what keeps cues from trampling each other:
give state and mode changes their own channel, attention cues another, and character audio a third,
with `stop_current_sound = false` so they layer rather than interrupt. Getting this right is worth
more than any amount of sound authoring, and costs nothing.

**Where files still earn their place:** character audio, in the 3.5 MB SPIFFS partition that
`default_16MB.csv` already defines and nothing uses (~3 min at 16-bit/11 kHz mono). Functional cues
stay synthesised, because they carry state and a missing file is silent failure — indistinguishable
from the robot not noticing.

---

## 4. What the measured capability actually supports

| Capability | Status | Source |
|---|---|---|
| Direction of a voice, left / right / centre | **works at default gain** | `bench_mic/`, interactive, 2026-10-04 |
| Finer bearing than three answers | not built; needs sample-level cross-correlation | — |
| Listening while speaking | **works** — speaker and mic hold I2S together | bench, 2026-10-04 |
| Eye rendering, two eyes | **works**, ~27.6 fps | `bench/`, 2026-10-04 |
| Gaze direction on screen | exists (`gx`/`gy` in `face.cpp`) | — |
| Physical head aim | pan-tilt exists as a payload mode (CH7) | `docs/control-authority.md` |
| Per-state eye expression and colour | exists, 7 states | `ui_shared.cpp` |
| Audio out | ES8388 + NS4150B, 1 W 8 Ω, "Hi-Fi"; 3.5 mm jack | M5 Tab5 documentation |
| Asset storage for audio | **3.5 MB SPIFFS, already partitioned, unused** | `default_16MB.csv` |
| **Any annunciation during e-stop** | **none — the face loses power** | `CLAUDE.md`, §0 above |

Listening while speaking deserves a note: it means the robot can be interrupted. Barge-in is
possible, so a cue does not have to finish before the robot notices it is being addressed again.
That is a genuine affordance and most cheap speakers would not have given it to us.

---

## 5. The main risk: inviting conversation we cannot hold

Listeners interpret robot cues through prior media convention — and the convention for an
expressive face with two large eyes that tracks you is *this thing can talk*. OpenDubs cannot. It
has three directions of hearing, no speech recognition, and a 2 kHz band to answer in.

A face expressive enough to invite speech, on a robot that cannot converse, spends its credibility
immediately and gets read as broken rather than as limited. The mitigation is not a less appealing
face; it is **keeping the expressive vocabulary honestly non-linguistic** — glances, blinks, state
colour and rhythmic earcons, none of which promise a conversation. The five-cue table in §3 is
small on purpose.

Concretely: no idle chatter, no speech-shaped vocalisations, and no behaviour that implies the
robot understood *words* rather than *that a sound came from somewhere*.

---

## 6. Build order

Priced by value over effort, using only measured capability.

**First, and not on the face at all**

1. **A stopped-state indicator on the always-on branch.** §0. Without it there is no bystander-legible
   e-stop, and everything below is decoration on an unsolved safety-communication problem.

**Then, cheap and high-value**

2. **Gaze leads motion.** *Mechanism built — see `faceLookAt()` in `face.h` and the `GAZE x y [ms]`
   protocol command.* Three things were learned building it:

    - **The face is downstream of every mover and cannot delay the robot.** True gaze-leading needs
      the mover to wait, so the lead time belongs to the *caller*: send `GAZE`, wait, then command
      motion. Discrete actions — a park, a lift move, an arm reach, a routine — can afford that
      wait. An analog stick cannot, and does not need to (below).
    - **Lift gaze reads `Lift.request()`, not `state().motion`.** The request is set on the Tab5;
      the reported motion comes back only after the relay closes. Gazing on intent therefore leads
      visible travel by a full round trip, where gazing on the report could only ever accompany it.
      Any future axis should look for the same intent/actuality pair.
    - **Even a reactive gaze beats visible motion**, because the drivetrain ramps (`kEscSlewUs`,
      `kEscRevDwellTicks`). So RC driving gets legibility for free, without the stick having to be
      predicted.

    Still to do: the robot does not *send* `GAZE` yet — no drive vector exists in the Tab5 protocol
    at all, so the dashboard and StamPLC callers are the next step. And **a lead delay must never be
    inserted ahead of a stop** — only ahead of a start. Delaying a halt to look pretty is the one
    way this idea could hurt someone.
3. **Acknowledge inside one second.** A glance plus one short blip whenever the robot notices it is
   being addressed, whether or not it can act. Silence reads as ignoring or as broken.
4. **Glance toward a voice.** Discrete, brief, L/R/centre only. Explicitly *not* continuous tracking.
5. **Mode annunciation for bystanders.** Who is driving — human, autonomous, or nobody — in eye
   colour and shape, legible without training and without a transmitter. Closes the open item in
   `docs/control-authority.md`.

**Then, needs design**

6. **The five-earcon vocabulary**, rhythm-distinct inside 2–4 kHz.
7. **Turn-taking by gaze** — avert while busy, return when ready for input. Uses the measured
   listen-while-speaking capability.

**Probably never**

8. **Finer bearing than three answers.** A head that turns toward whoever is talking does not need
   degrees. Revisit only if three directions provably fail.

## Open

- **Where the stopped-state indicator lives**, electrically and physically. The only hard
  requirement is that it is not on the drive branch.
- **Whether the pan-tilt is the right head aim** for gaze, or whether on-screen gaze alone is
  enough indoors at demo distances. Untested either way.
- **Crowd robustness.** Every microphone reading so far is one deliberate speaker in a quiet room.
  A demo is many people and noise, and the failure that matters is glancing at the loudest
  bystander rather than at the person addressing the robot.
