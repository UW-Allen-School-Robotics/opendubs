# Interaction design for OpenDubs — what the HRI literature says, and what we can actually build

A survey mapped onto **measured** capability, not hoped-for capability. Every "we can" below is
backed by a bench or robot measurement recorded in this repo; every "we cannot" is a constraint we
hit, not a guess.

The companion notes are [`tab5-face-expressiveness.md`](tab5-face-expressiveness.md) for the
rendering and audio substrate, and [`../../docs/control-authority.md`](../../docs/control-authority.md)
for who is allowed to move the robot. This note is about what a **person standing in front of it**
can tell, and it closes that document's open item on annunciating the active mode.

---

## 0. The finding that outranks the rest: the face dies in an e-stop

**The Tab5 is powered from the e-stop-protected drive branch.** Open the contactor and the head has
no power — it is off, not offline. That is recorded in `CLAUDE.md` as a flashing gotcha, but its
interaction consequence has not been written down anywhere, and it is severe:

> **The single channel we would most want during an emergency is the one guaranteed to be dark.**

Every expressive capability below — eye state, colour, gaze, earcons — is unavailable in exactly
the situation where a bystander most needs to know what is happening. A robot that goes blank is
read as *broken*, or worse as *still about to move*, not as *safely stopped*.

So e-stop annunciation **cannot live on the face**, and no amount of face work substitutes for it.
It needs a channel on the always-on branch: the PRISM indicator, a separate beacon, or a mechanical
flag. Until that exists, the honest statement is that OpenDubs has no bystander-legible stopped
state. This is a prerequisite for public demos, not a nice-to-have, and it is cheap compared to
anything else here.

**Corollary for the e-stop eye state.** `ui_shared.cpp` defines an `E_STOP` expression with the
widest eye of any state (`w=1.06, h=1.08`). It can only ever be seen when the *software* believes
e-stop without the contactor having opened. Worth keeping, but it is not the e-stop indicator and
should not be mistaken for one.

---

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

**Our speaker carries roughly 2–4 kHz**, measured by ear on the robot. That removes most of the
palette: no usable timbre contrast, no low-frequency weight, nothing speech-like. What survives is
**rhythm, repetition, and pitch contour inside a narrow band**.

This is a real design constraint, and it has a clear implication: our earcons must be
distinguishable by **rhythm and contour, never by tone colour**. Two cues at the same tempo in that
band will be confusable no matter how different they look in a spectrogram. A small vocabulary,
rhythmically distinct, is the only thing that will work:

| meaning | why it is needed | shape |
|---|---|---|
| *heard you* | acknowledgement inside ~1 s, even when nothing can be done yet | one short blip |
| *attending to you* | pairs with the glance | two quick rising blips |
| *cannot do that* | refusal must be distinct from failure | two slow falling blips |
| *something is wrong* | fault, needs a human | repeating triple, does not stop |
| *mode changed* | authority transitions must be audible, not just visible | single long tone |

Deliberately **five**, and deliberately not an emotional palette — see §5.

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
| Audio out | 2–4 kHz only | measured by ear, on-robot |
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

2. **Gaze leads motion.** Before the base drives, the lift moves, or an arm starts, aim the eyes
   (and head, where pan-tilt is live) at the destination first. Pure legibility, no planner change.
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
