# Face configuration: what may be authored, and what may not

**1.3a.** The face has accumulated several things that are *configuration* rather than code —
themes, the emote vocabulary, the state→colour table, the band's motion vocabulary, the earcon set
— and all of them are compiled-in C tables today. This settles the **data model** and, more
importantly, the **boundary of what may be authored**. It does not build an editor or a transport;
those belong to the choreography work.

Companion notes: [`face-design.md`](face-design.md) for what the parameters mean,
[`tab5-platform.md`](tab5-platform.md) for what they cost,
[`../../docs/control-authority.md`](../../docs/control-authority.md) for the authority model this
must not undermine.

---

## Why these should be data

- **They are authored, not programmed.** A theme is a palette plus an accessory set; an emote is
  six numbers. Neither needs a compiler, and putting them behind one means **every tweak costs a
  flash** — which this project has now demonstrated at length.
- **They are per-robot and per-occasion.** A Halloween theme, a demo-day palette, a calmer emote
  set for a classroom. Per-robot values must not live in committed source; that rule already exists
  for drive calibration and applies here for the same reason.
- **They are tables.** States carry a colour and a policy, emotes carry parameters and a
  valence/arousal point, themes carry a palette and an accessory list. Tables want to be data.

## The three tiers

The important structure is not the file format. It is that **these things are not equally safe to
change**, and a single "face config" blob would flatten that distinction away.

| tier | contains | who may change it | enforcement |
|---|---|---|---|
| **Reserved** | `DEGRADED` amber, `E_STOP` red, the fault band motion, the "attending vs reporting" policy per state | **nobody, at runtime** | compiled in; the firmware **rejects** any configuration that touches these |
| **Bounded** | emote parameters, state colours for non-fault states, band motion per activity, earcon rhythms, theme palettes and accessories | authored freely, **within validated ranges** | firmware validates and clamps; out-of-range is rejected with the default kept |
| **Free** | the quote list, accessory artwork, theme names, which theme is active | authored freely | no validation beyond not crashing |

### Reserved exists because an editor is not a safety boundary

A theme that recolours `E_STOP` must be **rejected by the firmware**, not merely discouraged by the
editor. An editor is a convenience: it can be bypassed, an older version may not know the rule, a
file can be hand-edited, and a well-meaning author will eventually try exactly this because orange
is the obvious Halloween colour and amber is right next to it.

This is the first real test of the theme reservation rule against a system built specifically to let
people change things — and the rule only means anything if the thing *being configured* enforces it.

**Also reserved: the attending-versus-reporting policy.** Whether a state may emote freely is not a
style choice. `E_STOP` and `DEGRADED` pin a posture because in those states the posture *is* the
message, and a configuration that let them cycle moods would make the face lie about the robot's
condition.

### Bounded needs ranges, because the budget is real

Several face parameters have costs rather than merely appearances, and the platform note quantifies
them. A configuration format that accepts any number invites a theme that freezes the robot:

| parameter | bound | why |
|---|---|---|
| eye body size | ≤ panel minus band and margins | a resize dirties old area plus new; the body already spends most of the frame budget |
| accessory count | a hard cap | each one is an object, and objects over an animating region are recomposited every frame |
| band segment count | fixed, not authorable | 120 segments froze the device; 48 ran |
| animation durations | a floor | a very short duration means more frames of change, which means more invalidations |
| anything sprung | spring, not for colour | an overshoot in RGB lands on a different hue, which is how a state change flashed through other states |

**So validation is not hygiene here, it is the thing that stops configuration from becoming a way
to crash the robot.** The firmware clamps and reports rather than trusting.

## Degradation

**If the configuration is absent, unreadable, or rejected, the face falls back to the compiled-in
defaults and says so** — it must never fall back to nothing. The compiled tables stay in the
firmware permanently for exactly this reason; the configuration overrides them rather than
replacing them.

That also means a robot with no configuration at all is fully functional, which is the right
default for a machine students will rebuild.

## What this implies for the transport, without deciding it

- The existing StamPLC link is **line-based UDP** carrying short commands (`STATE <NAME>`,
  `LIFT h m f`). It was not built to carry a payload like this and should not be stretched to.
- The face needs the configuration at **startup**, not continuously, so this is a fetch-or-receive-
  once problem rather than a streaming one — which is a much easier problem and should not be
  confused with the audio path.
- There is **3.5 MB of unused SPIFFS** already partitioned, which is an obvious place for a cached
  configuration so the face is not dependent on the link being up at boot.
- Per-robot values must not be committed, so the authored configuration lives **on the robot**, with
  the repo holding only the defaults and the examples.

## Selectable first, authorable as an option

**Decided.** The primary path is **selecting** from a curated set of working options; authoring new
ones is an **option**, not the main road. But authoring stays available, because this is a research
robot and the goal is *working examples plus the ability to adapt* — not a locked appliance.

That ordering matters for where effort goes:

| | gets | does not get |
|---|---|---|
| **Selectable** (primary) | a curated vocabulary that works, example themes and shows, a way to pick | — |
| **Authorable** (option) | the same validated ranges, plus a simulation gate | polish, a visual editor, hand-holding |

The argument for this ordering is empirical rather than philosophical: two attempts at inventing
emote geometry in this project produced faces **worse than the originals**, and the eye geometry
that works was arrived at by measurement against human proportion and the baby-schema literature.
The vocabulary is a **design artefact**. The selection is the **configuration**. Someone adapting
the robot for research can still author, and should expect to do design work when they do.

### Bad options are caught by rule AND by simulation

Two gates, and they catch different things.

**Rules** catch what can be stated: a reserved colour touched, a value out of range, too many
accessories, a duration below the floor. Cheap, exact, and the firmware enforces them regardless of
what any editor did.

**Simulation evaluation** catches what can only be observed — and this is a third job for the
desktop build, alongside design iteration and the sanitizers.

The non-obvious part: **dirty area per frame is a geometric property, not a CPU property.** The
desktop has far more CPU than the Tab5 and therefore cannot tell you whether something *runs* there
— but it can count the pixels a configuration would invalidate per frame, and that number is the
same on both. So the sim can measure an authored configuration's cost and compare it against the
**device's measured ~16 Mpx/s budget**, catching a theme that would freeze the robot without ever
putting it on the robot.

That is worth building because the alternative is what this session did: flash, watch, freeze,
guess. A configuration gate that reports "this would need 1.4 screens per frame" is the thing that
would have caught the 120-segment band and the oversized eye before either reached hardware.

It also means the examples can be **checked in with their measured cost**, so a shipped theme comes
with evidence rather than a hope.

## Open

- **Whether a theme may add accessories, or only select from pre-built ones.** Arbitrary authored
  geometry reintroduces every cost question the platform note just answered — though the simulation
  gate above is most of the answer, if it measures accessory cost too.
- How validation failures are **surfaced**. The face can say "default configuration" on the panel,
  but the author needs to know *which* rule they broke, and that is the editor's job rather than the
  firmware's.
- **What the curated set actually is.** The current six emotes and two themes are a starting point,
  not a considered vocabulary, and choosing the shipped set is design work in its own right.
