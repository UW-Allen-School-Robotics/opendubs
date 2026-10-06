# Work plan — the choreography composer

The plan of record for making a show into something a person or an agent can *author* rather than
hand-code. Written after benchmarking against the real prior art — Boston Dynamics' Choreographer,
QLC+, Choregraphe, Furhat, Cozmo's animation pipeline, BML, and the DAW conventions all of them
borrow from — because this project has twice invented a mechanism that a thirty-year-old tool
already names.

**This is a design note. It commissions no firmware and touches no hardware.**

Companion notes: [`workplan-tab5-idf.md`](workplan-tab5-idf.md) (this is its step 1.3a, grown up),
[`design/face-config.md`](design/face-config.md) (the authorable/not-authorable boundary, already
settled), [`design/behavior-api-scope.md`](design/behavior-api-scope.md) (the slot and merge-policy
proposal), [`design/face-design.md`](design/face-design.md),
[`design/tab5-platform.md`](design/tab5-platform.md) §6b (the frame budget a show may not blow), and
[`../docs/control-authority.md`](../docs/control-authority.md) — **the binding constraint, not
background reading**.

---

## This is not greenfield. Read this table first.

The brief for this plan assumed a composer had to be designed from scratch. It does not. A
substantial part of it is built, merged to `main`, and tested.

| Thing | State | Where |
|---|---|---|
| Show-as-data, YAML, absolute `t` timeline | **built** | `software/choreo/shows/hello.yaml` |
| Validator: limits, speed caps, per-side grippers, shoulder/head clearance, one-owner-per-slot | **built, 19 tests** | `software/choreo/cue.py`, `tests/test_cue.py` |
| `requires:` preflight declaration (`arms`, `tab5`, `rc_authority`) | **built** | `hello.yaml` |
| Slot vocabulary | **built**: `ARM_L ARM_R GRIP_L GRIP_R HEAD LIFT SOUND FACE LIGHT` | `cue.py` |
| Kinematic simulator that answers "does this pose collide / does the sequence read" | **built** | `sim/scene.py`, `sim/clearance.py`, `sim/view.sh` |
| The authorable/reserved boundary for face configuration | **designed** | `design/face-config.md` |
| Slot merge policies and priority tiers | **proposed** | `design/behavior-api-scope.md` |
| **The runner** | **does not exist** | prototype is `~/opendubs-demo/showcase_demo.py`, outside the repo |
| `FACE` and `LIGHT` validators | **missing** — the slots exist and are unchecked | `cue.py` has no `_check_face`/`_check_light` |
| Music, accessories, themes, emotes, band motion, earcons as show data | **do not exist** | — |

So the work is: **fix what is wrong, add the runner, add the missing channels, and only then build an
editor.** Not "design a composer".

---

## Standing rules

1. **The authority model is not negotiable and the composer is not special.** A routine is a
   lease-holding motion source that goes through the same arbiter and the same monitors as a stick
   or a planner. `docs/control-authority.md` is the specification; this plan may not amend it.
2. **Preemption aborts. It never pauses.** Already decided, for a stated reason: once a human has
   intervened the robot is no longer where the timeline assumed it would be. A "resume" button is
   out of scope, and a "pause" that holds a pose is a *different feature* that must be argued
   separately — see 2.4, where the lighting world's distinction between *pause* and *release* turns
   out to matter a great deal.
3. **Anything safety-bearing is not authorable, and the firmware must REJECT rather than
   discourage.** An editor is a convenience; it can be bypassed, hand-edited, or out of date.
   `design/face-config.md` already states this and it extends unchanged to the whole composer.
4. **Validation is a gate, not hygiene.** Validate the entire show before sending any of it — the
   rule `showcase_demo.py` earned the day it caught a 43 °/s opening move. A partially-sent show is
   a robot in a pose nobody designed. **This validator is the part of the system that is ahead of
   the field, not behind it** (see [§ the field mostly does not
   validate](#safety-in-these-tools-there-essentially-is-none)), so it is the thing most worth
   protecting when editor work starts creating pressure to relax it.
5. **Every experiment states its question and its kill criterion before it starts.** Carried over
   from the Tab5 plan, for the same reason.
6. **A show must never be able to crash the robot by being expensive.** The face has frozen twice,
   both times from a cost term, and `tab5-platform.md` §6b sets out the cost hierarchy. A
   configuration format that accepts any number invites a theme that freezes the panel. **Bound
   against the hierarchy, not against the absolute Mpx/s ceiling** — see 4.3; that number is
   currently being retracted by the Tab5 thread.
7. **Text, in the repo, diffable, with layout segregated.** Settled in Phase 1.1 below on measured
   evidence, not taste; stated here because every later phase depends on it.
8. **No new control path.** The composer emits the commands the robot already accepts. It does not
   get its own channel to the relays, and `MAST CMD` stays "deliberately the ONLY remote way to move
   the lift".

---

## Phase 0 — corrections to what exists

*Cheap, bounded, no new design, and they block nothing. Do these first because every one of them is
a thing the next phase would otherwise build on top of.*

| # | Question | Kill criterion |
|---|---|---|
| **0.1** | **`cue.py` enforces a measurement that was withdrawn.** `SOUND_BAND_HZ = (1800, 4500)`, commented "what the head's speaker actually carries, judged on the robot". `tab5-platform.md` §3 retracts exactly that claim: *"A claim that it 'carries only 2–4 kHz' was a by-ear judgement of `M5.Speaker.tone()` in a noisy room, and is withdrawn."* A test (`"a beep the speaker cannot carry is caught"`) enshrines it. What is the real band, and who measures it? | Either a measurement replaces the constant, or the check is removed. A validator that refuses a legal show on a retracted basis is worse than no check — it teaches authors the validator is wrong. |
| **0.2** | **`theme::reserved()` is two integer literals.** `return state == 4 \|\| state == 5;` — indices into `kStates` for `DEGRADED` and `E_STOP`. Insert a state and the reservation silently protects the wrong two. This is the safety-bearing rule from `face-config.md` expressed as magic numbers, in the file that is about to become data-driven. | A state's reservation is a property of the state, by name, not by position. Until that is true, nothing in Phase 4 should ship. |
| **0.3** | **`FACE` and `LIGHT` are unvalidated slots.** They are in `SLOTS` and have no `_check_*`. A show can currently say anything about the face and the lights and pass. | A slot either has a validator or is not in `SLOTS`. |
| **0.4** | **`hold_s` is read by the one-owner check and documented nowhere**, and no show uses it. Is it the intended duration primitive or a vestige? | Named in the format spec or deleted. A field that only the validator knows about is a trap — and note QLC+'s finding that **duration is what makes an object schedulable at all** (1.5). |
| **0.5** | **`kPrismServoPin = 0` is declared and never used.** 8Servo pin 0 is now `RL`'s drive ESC (`kDriveCh[4] = {1,3,0,2}`); PRISM moved to I²C. Not a live bug — a loaded gun. | The constant is gone, or carries a comment saying what it would do if used. |

---

## Phase 1 — the data model

*Design, not implementation. The deliverable is a format specification and an extension of
`cue.py`'s vocabulary, with the research below as the justification for each choice.*

| # | Question | Kill criterion |
|---|---|---|
| **1.1** | **Text or binary; declarative or imperative.** Decided: **declarative text, in the repo, with no x/y coordinates in the document.** Reasoning and measured evidence in [§ Programmatic execution](#programmatic-execution-by-an-agent-is-the-reason-to-stay-declarative-and-text). | Any proposal for a GUI-only or binary-native format must first answer the 8:1 editing-viscosity measurement and the Blueprint merge problem below. |
| **1.2** | **What is the time base: seconds, or a quantised musical grid?** Today `t` is float seconds. Spot quantises to *slices* (¼ beat) with one `slices_per_minute` and integer durations; **Cozmo does the opposite** — every keyframe is `trigger_time_ms` + `duration_ms`, absolute milliseconds, with tracks as independent lists rather than a rigid grid. Dancing wants the grid; a lift travelling for 6 s wants seconds. | A show must express both "on the beat" and "for 6 seconds" without lying about either. The honest reading of the two precedents is that **a pure grid is right with music and wrong with speech**, so a format that can only do one has picked a side it will regret. |
| **1.3** | **Does a show get one tempo, or a tempo map?** Spot has exactly one and says the slice duration "must remain constant throughout the entire script" — a documented structural limitation. | One tempo is acceptable **only if recorded as a deliberate limit**, not discovered later. |
| **1.4** | **Absolute times only, or relative sync constraints too?** Today absolute, as Spot's `start_slice` is — which makes inserting a bar an O(n) rewrite. BML's answer is different and better for anything whose duration is not known in advance: every behaviour exposes **phase sync points** (`start, ready, strokeStart, stroke, strokeEnd, relax, end`) and authors write *bidirectional constraints between* behaviours rather than timestamps. That is how you say "the band pulse peaks on the word 'hello'" without knowing how long the TTS will take. | If the robot ever speaks during a show — and `face-design.md` says it will — absolute times alone cannot express the thing an author most wants. Note the ROS [`bml_realizer`](https://rosbmlrealizer.sourceforge.net/bml_realizer/html/index.html) is event-driven on sync points **explicitly because control over motors is not as consistent as control over an animation**: voltage sag, obstructed joints and controller skew make fixed-clock scheduling wrong on real hardware. That argument applies to us directly. |
| **1.5** | **Are there groups, repeats and reusable fragments, and does everything schedulable carry a duration?** Spot has no nesting and no loops — a flat list. QLC+ does, via a single `Function` interface, and its Cue List carries the sharpest constraint in this whole survey: *"only Chasers can be assigned to a Cue List, for the simple reason that the Chaser is the only function with a duration."* | A seasonal show that cannot say "do that again" is evidence the model is too flat. And **anything cue-able must expose a duration, possibly infinite** — otherwise no scheduler can show progress or auto-follow. See 0.4. |
| **1.6** | **The capability layer: does a show name joints, or capabilities?** QLC+, Vixen and GDTF all put an abstraction between the show and the wire. `hello.yaml` names `joints: {2: 20, 4: 55}` — raw joint indices. | A show that must be rewritten for robot 2 has failed the portability test the lighting world solved decades ago. Per-robot calibration must not leak into a committed show (`per-robot-values-not-in-repo`) — and note that **GDTF/MVR split device-type from this-rig's-instances for exactly that reason**, which is the same cut we already make. |
| **1.7** | **Per-slot merge policy, as data — and what a global scalar may touch.** `behavior-api-scope.md` proposes one policy per slot; lighting desks have precisely this as HTP vs LTP, resolved from the fixture definition's channel group. Choregraphe ships a third axis we have not considered: a per-box **resource acquisition mode** of *Passive / Waiting / Aggressive*, which is an explicit answer to "two things want this actuator". | A format that cannot say *how* two sources combine has deferred the arbitration bug, not fixed it. **Every joint angle is LTP** — `max(a,b)` of two target poses is a pose nobody asked for. And the sharpest safety lesson in the lighting survey: QLC+'s Grand Master affects intensity channels only *by default and deliberately*, so that pulling it does not drag the movers toward zero. **Our analogue: a global speed scalar must scale rates, not targets.** CH8 already does this correctly; nothing in a composer may change that. |
| **1.8** | **Is variation in the format, or absent?** Cozmo put it in: `AnimHeadAngle` carries `variability_deg`, `AnimLiftHeight` carries `variability_mm`, `AnimRobotAudio` carries `probabilities`, and above the clips sit **Animation Groups** — named sets with the same purpose, so a behaviour requests an *intent* and the runtime picks a variant. | A show played three times that is byte-identical three times reads as a vending machine. This is cheap, it is in the format or it is nowhere, and it is much harder to retrofit. |

### Programmatic execution by an agent is the reason to stay declarative and text

The requirement is that Claude — or any agent — can run, compose and modify a routine. That is not
a tie-breaker between formats; it is nearly decisive, and the evidence against the alternative is
measured rather than argued.

**The editing cost of a graph has a number on it.** Green & Petre timed the same change — adding air
resistance to a rocket-trajectory program — across three notations: LabVIEW 508.3 s, Prograph
193.6 s, Basic 63.3 s. *"An astonishing ratio of 8:1 between extremes… if the programs were larger
we would expect them to increase proportionately."* The mechanism was mechanical: "all the boxes had
to be jiggled about and many of the wires had to be rebuilt"
([Blackwell & Green, *Cognitive Dimensions tutorial*](https://www.cl.cam.ac.uk/~afb21/CognitiveDimensions/CDtutorial.pdf),
summarising [JVLC 7(2):131–174](https://www.sciencedirect.com/science/article/pii/S1045926X96900099)).
For a tool whose dominant activity is iterative tweaking, an 8:1 penalty on *modification* is the
whole ballgame.

**And graphs resist version control.** Unreal Blueprints are the canonical case: `.uasset` is
binary, so GitHub renders a change as `Bin 41231 -> 41902 bytes`, and
[the practitioner account](https://www.strayspark.studio/blog/how-to-diff-unreal-blueprints-in-pull-requests)
is blunt that of the three available approaches **"None of the three ways merges concurrent Blueprint
edits,"** and that "the reliable team answer in 2026 is still **file locking** — one person edits a
Blueprint at a time." This is not a tooling gap that will close: diffing graphs reduces to graph edit
distance, which is computationally infeasible in general — the problem
[NodeGit](https://history.siggraph.org/?p=214587) exists to approximate.

A format an agent cannot diff is a format an agent cannot review, revert, or safely edit alongside a
human. Against that, the tools that chose text say so deliberately. A Spot `.csq` is **protobuf
text**; the client ships `load_choreography_sequence_from_txt_file` and
`save_choreography_sequence_to_file`; Boston Dynamics state outright that animations "can be written
manually using protobuf in any application"
([Choreography service](https://dev.bostondynamics.com/docs/concepts/choreography/choreography_service)).
Cozmo's FlatBuffers `.bin` has a **lossless JSON equivalent**. Furhat's Kotlin gesture DSL
round-trips to JSON, and the repo ships `.json` gestures beside Kotlin ones — the lesson being that
**the GUI, the DSL and the file must be three views of one structure, with none of them the master.**
QLC+ made it a release note: XML tags "are now always saved with the same order, thus allowing files
to be scriptable or easily compared"
([QLC+ 4.10.2](https://mail.qlcplus.org/news/please-double-check-your-workspace-updated)).

**So: YAML stays, and `cue.py` stays the gate.** The important consequence is that an agent's loop
is already closed without a robot — write YAML, run `cue.py validate`, read sentences back. That is
a better agent affordance than any API, and it exists today. **Stable serialisation and no layout
data in the document** are requirements, not niceties: if a round trip through an editor reorders
keys or records node positions, every agent edit becomes an unreadable diff.

---

## Phase 2 — the runner, and the authority boundary

*The piece that does not exist. `software/choreo/README.md` says so: "Not here yet. Execution."
This is the phase where getting it wrong moves a robot.*

| # | Question | Kill criterion |
|---|---|---|
| **2.1** | **Where does the show clock live?** Not the Tab5: **it is powered from the e-stop-protected drive branch**, so the contactor opening takes the panel with it. A clock that dies with the e-stop cannot be the thing that notices the e-stop. | The clock must survive every condition in which the show must abort. The Thor is the only candidate. |
| **2.2** | **Preflight from `requires:`, refusing on `unknown`.** The `[AUTH]` telemetry line already carries `ch5`, `deadman`, `contactor`, `rc_live`, `rc_ok`, `drive_safe` — and `ch5` is reported `unknown` whenever the RC link is not trustworthy, rather than the last value seen. | `unknown` must refuse, not default. A preflight that treats "I don't know" as "probably fine" is not a preflight. **"This cue commands the base while the contactor state is unknown" is exactly the error class a preflight exists to catch, and nothing in the prior art will catch it for us.** |
| **2.3** | **The lease, with a heartbeat.** One lease for the routine, priority `TASK`, released at the end; republished while the runner lives; expiry means release. Every existing remote command on this robot already dies in 250 ms without a heartbeat. Spot's lease model is the same shape — hierarchical resources, `RetainLease` keep-alives, and a lease that goes *stale* rather than being held forever. | A runner that dies must stop the robot, not leave it running. Test by killing the process, not by asking it to stop. |
| **2.4** | **Abort is total — and "stop", "pause" and "release" are three different things.** Releasing the deadman, a stick deflection in NORMAL, a monitor trip, a fault, or watcher loss all abort. Two precedents sharpen what abort must *mean*. QLC+'s Pause "freezes current playback state **without shutting down lights**" — hold output, do not release — while **Blackout masks the output while the engine keeps running**, so releasing blackout snaps to whatever the engine now thinks. That second behaviour would be actively dangerous copied to actuators. And Spot's warning applies to us directly: *"Interrupting a dance in the middle of a dynamic movement (such as a jump), or starting a different dance before the previous dance finishes, may cause falls."* | Abort must leave every slot in a **defined** state, and "defined" must be argued **per slot**, not assumed to be "stop". The lift's abort state is not obviously the arms'. A mask that hides output while a timeline keeps advancing underneath must not exist at all. |
| **2.5** | **Normalise the start pose.** Carried from `showcase_demo.py`: plans assume a starting pose, and the runner must travel there first, timed by the distance actually covered, "or the caps silently lie". | A show that begins by slamming to its first keyframe has no speed cap, whatever the validator said. |
| **2.6** | **Consent gestures cannot be scripted, and must be waitable.** A show may not acknowledge the pinch guard — already enforced. But it must be able to *ask* and wait. BML has the primitive for this: a `<wait>` no-op that exists purely to anchor a timing gap. | A show that fakes consent, or that deadlocks waiting for it with no timeout and no annunciation, both fail. |
| **2.7** | **What does the runner log?** Spot buffers a `ChoreographyStateLog` from receipt of the execute command until 3 s after completion, with a first-class `FALLEN` status and a `double current_slice` so phase is observable sub-slice. We have `[TAG] key=value` telemetry and the habit of grepping it. | An abort whose cause cannot be reconstructed from the log is an abort that will happen again. |
| **2.8** | **A status surface, with typed feedback.** Spot's `ChoreographyStatus` enumerates `DANCING / PREPPING / WAITING_FOR_START_TIME / VALIDATING / INTERRUPTED / FALLEN / COMPLETED_SEQUENCE`. BML goes further and separates three channels: optional **prediction** feedback (the scheduler's resolved solution, including predicted sync times), **mandatory progress** feedback per sync point, and typed **warnings** — `IMPOSSIBLE_TO_SCHEDULE`, `BEHAVIOR_TYPE_NOT_SUPPORTED`, `NO_SUCH_TARGET`, `PARSING_FAILURE`. | "Is the show running?" must be answerable without reading a log. And note Spot's counter-example: `UploadChoreographyResponse.warnings` is `repeated string` — **untyped**, which is hard to drive a UI from. BML already named the error classes; we should not invent worse ones. |

**The one piece of Spot's model we should not copy: resume by slice.** `ExecuteChoreography` takes a
`choreography_starting_slice` and the UI has a Start Marker for launching mid-sequence. That is
genuinely useful *for authoring* and flatly incompatible with rule 2 *for recovery* — the robot is
not where the timeline assumes. If it is built, it is an **authoring** feature, gated behind the
simulator and never offered as a response to an abort.

---

## Phase 3 — output channels

*Four were asked for. Two exist, one is thin, one has almost no hardware behind it. Note that
Cozmo's shipped keyframe set — head angle, lift height, body motion, backpack lights, face
animation, procedural face, audio, and a generic named `AnimEvent`
([pycozmo.anim_encoder](https://pycozmo.readthedocs.io/en/latest/generated/pycozmo.anim_encoder.html))
— is startlingly close to ours, which is evidence the decomposition is natural rather than ours
alone. `AnimEvent` is also exactly the hook an accessory output wants.*

| # | Question | Kill criterion |
|---|---|---|
| **3.1** | **Base motion — "dancing" — is currently refused, for a stated reason.** `DRIVE` is deliberately absent from `SLOTS`: opendubs2 reports `y_mm` as NaN with a climbing failure count, so *"keep X and Y clear" is advice the robot cannot honour*. `punch-list.md` has the same blocker: no `/odom` publisher, no TF. **The brief asks for dancing; the repo refuses it.** | Either odometry is earned, or dancing is open-loop and bounded by something other than position — time-limited, speed-capped, floor-space declared, with the operator holding a physical throttle. Spot's own requirement calibrates the risk: **3 m clearance in all directions** and bystanders kept back. Either way it must be *chosen*, with limits written down the way the `DRIVE` refusal already is — not slipped into `SLOTS`. Worth knowing before committing: the systematic review of animation in HRI found **25 of 27 studies animated configuration only, with almost nothing animating locomotion** ([arXiv:1812.06784](https://arxiv.org/pdf/1812.06784)), so expressive base motion is genuinely under-explored. That is an opportunity and a warning — there is no prior art to lean on. |
| **3.2** | **Audio and music.** `SOUND` is `beep\|chime` — no track playback, no tempo. The Tab5 has 8 virtual speaker channels with independent volume, `playRaw`, `playWav`, and 3.5 MB of unused SPIFFS ≈ 3 minutes of 16-bit 11 kHz mono. *"A cue system is channel assignment, not a synth."* | A music track on the Tab5 dies with the e-stop and is bounded at ~3 minutes mono. **If music is the timebase it must live where the clock lives** (2.1). Spot's precedent is the clean one: the *laptop* plays the audio, the robot does not, and the robot's only sound source is a buzzer track. |
| **3.3** | **The face.** Today `STATE <name>` text over UDP and USB CDC. A show needs emote, theme, band activity and gaze — none of which have a command. | Phase 4 owns the data; this owns the verbs. A `FACE` slot that can only set state is not an output channel, it is an indicator. Note Furhat's precedent that one frame can carry **motor values, audio, a face texture and an LED pixel together** — our face, band and earcons are the same shape. |
| **3.4** | **Accessories — and the hardware is the constraint, not the format.** There is exactly **one** spare relay: R0 is the e-stop contactor, R1 the lift's DOWN leg, R2 its UP leg, R3 spare. The dashboard already enforces this (`"only relay 3 is free"`, and 1 and 2 are refused outright so the pinch guard is not bypassed). A disco ball **and** a musical instrument is two devices and one relay. | 8Servo channels 5–7 are nominally free PWM, **but that is the bus that commands the four drive ESCs and the board with the documented wedge.** Putting a decoration on the bus that drives the wheels is not a trade worth making. So: an expander, a separate accessory board, or one accessory. Decide before designing an `ACCESSORY` slot that implies N of them. |
| **3.5** | **Does an accessory survive the e-stop, and should it?** Unverified: whether R3's switched load sits on the e-stop-protected branch or an always-on rail. | A disco ball still spinning after an e-stop is a legibility failure — the machine must *look* stopped. Check the wiring; do not reason about it. |
| **3.6** | **An idle layer, which is not an output channel but belongs here.** The systematic review found **Secondary Action is the most-used animation principle in HRI by a wide margin (8 of 27 studies)** — and explicitly calls out the gap we sit in: *"It would be interesting if this could also be applied to the non-human, non-animal robot. For example, a part on the non-humanoid, non-animal robot could be animated to have an analog of a blink."* That is the perimeter band. Two studies give the payoff: adding breathing to a robot arm as secondary action [improved most measures of perception with minimal impact on task performance](https://peopleandrobots.wisc.edu/publications/Designing-Social-Cues-for-Collaborative-Robots-the-Role-of-Gaze-and-Breathing-in-Human-Robot-collaboration/) (HRI 2020), and idle "adaptor" movements made a robot [perceived as more human and more friendly, proportional to intensity, without disrupting the task](https://infoscience.epfl.ch/record/231097) (RO-MAN 2017). | This is the highest value per unit of effort in the whole plan and it needs no new hardware — the band already breathes. The kill criterion is the arbitration, not the animation: an idle layer must sit at priority `IDLE` and yield instantly, or it becomes a competing writer. |

---

## Phase 4 — the Tab5's configuration becomes show data

*This is `workplan-tab5-idf.md` step 1.3a. **Its data model is already settled** in
`design/face-config.md` — three tiers, Reserved / Bounded / Free, with the firmware rejecting rather
than discouraging. This phase does not re-decide that. It does the migration, and it starts by
correcting the notes, which have drifted from the code.*

| # | Question | Kill criterion |
|---|---|---|
| **4.1** | **Reconcile the notes with `app_face.cpp` before extracting anything.** Four drifts found: `struct Emote` is **six** floats (`wMul, hMul, radiusFrac, lid, browRaise, browAngle`), not the seven-parameter `{h,w,tilt,bow,squint,lookY,brow}` in `face-design.md`; **`HAPPY` is in the documented vocabulary and not in `kEmotes[]`**; states are **colour only** (`{name, label, r, g, b, locked}`) by explicit decision — *"Two previous attempts also gave each state an expression, which made the face worse both times"*; `CHARGING` no longer pins an emote; and the band is **four edge strips, not 48 segments**, which `face-config.md` and workplan 1.2d both still say. | You cannot extract a table into data while the written description of it is wrong in four places. Fix the notes first; it costs an hour and saves a schema. |
| **4.2** | **Reserved, by name.** Phase 0.2 is the prerequisite. Reserved content per `face-config.md`: `DEGRADED` amber, `E_STOP` red, the fault band motion, and the attending-vs-reporting policy per state. | A theme that recolours `E_STOP` must be **rejected by the firmware**. Test it by hand-editing a config to do exactly that — the editor is not the thing under test. |
| **4.3** | **Bounded, with ranges that come from the measured budget — and the budget is in flux.** `tab5-platform.md` §6b is the source, and its *relative* findings are solid: the eye body alone dominates the frame, a resize dirties old area plus new, complex gradients are cached per geometry, 120 band segments froze the device and 48 ran. **Its absolute ceiling is not.** The Tab5 thread is, as of this writing, retracting the ~16 Mpx/s figure on the grounds that it was measured on a primitive-rasterisation workload and then applied as a fill/blit ceiling. `kMaxAcc = 24` already exists. | A configuration that can freeze the panel has not been bounded. **But a bound must not be derived from the absolute ceiling until that number is re-established** — the cost *hierarchy* is what to bound against, not the Mpx/s figure. The firmware clamps and reports; it does not trust. Borrow GDTF's **exhaustive-coverage invariant** while you are here — its value ranges must tile a channel with no gaps and no overlaps, because gaps are where undefined behaviour lives. |
| **4.4** | **Degradation.** Absent, unreadable or rejected configuration falls back to the compiled-in defaults **and says so**. The compiled tables stay in the firmware permanently. | A robot with no configuration must be fully functional — it is the right default for a machine students will rebuild. |
| **4.5** | **Transport.** The StamPLC link is line-based UDP carrying short commands and must not be stretched to this. The face needs the configuration at **startup**, not continuously — a fetch-once problem, not a streaming one. 3.5 MB of SPIFFS is already partitioned for a cached copy. | Confusing this with the audio path, which `workplan-tab5-idf.md` §1B owns, is the failure mode. |
| **4.6** | **Open from `face-config.md`, unresolved and worth deciding deliberately:** whether emote *parameters* are authorable at all, or only *selectable* from a curated set. Two attempts at inventing emote geometry in this project produced worse faces than the originals. | The vocabulary may be a design artefact and the selection the configuration. There is a third option neither note considered, and it is what Furhat and Spot both do: **parametric clips** — `defineGesture(name, strength, duration, defaultPriority)` scales amplitude *and* time base of one definition, and Spot narrows parameter ranges in Beginner mode relative to Expert. See proposed change 6. |

**Why the choreography system is the right home**, restated from 1.3a because it is the load-bearing
claim: a routine is a timeline of commanded states, and a theme is a lookup from state to
appearance. Both are declarative descriptions the robot interprets. Cozmo agrees structurally —
`AnimProceduralFace` and `AnimBackpackLights` are *keyframe types in the animation format*, not a
separate configuration system. So does Furhat, whose gesture frames carry LED and texture alongside
motor values.

---

## Phase 5 — authoring surfaces

*Last, deliberately. Every tool benchmarked here has a data model that outlived several editors.*

| # | Question | Kill criterion |
|---|---|---|
| **5.1** | **A timeline is the primary surface.** Not in question — `behavior-api-scope.md` already found that "an absolute timeline beats sequential steps", and it is what let the lift rise *during* the arm sequence. Corroborating from an unexpected direction: `trajectory_msgs/JointTrajectoryPoint` carries positions, velocities, accelerations and a "time from the trajectory start to arrive at this trajectory point" — **the robot's own interface is already timeline-shaped**, and a keyframed timeline maps onto it near-losslessly where a message graph does not. | A timeline that cannot show two slots moving at once has lost the only thing the prototype proved. |
| **5.2** | **Tracks as scopes, not lanes.** QLC+'s Show Manager binds each track to a Scene: *"All the sequences created on this track will act only on the associated Scene, not affecting any of the other tracks"* ([Show Manager](https://docs.qlcplus.org/v4/show-manager)), and "an item cannot be moved to another track, since it is bound to the track where it has originally been created." Our slots already are scopes. Choregraphe does the same and adds a **per-layer mute** (an eye icon), exactly as a DAW does. | A track that is a free lane reintroduces two-writers-per-slot as a UI affordance. |
| **5.3** | **Clip time versus arrangement time.** Two independent traditions converged here: Ableton's clip envelopes are attached to the clip and move with it, and can be **unlinked** to lock "to the song position rather than to the clips" with "its own local loop/region settings" ([Clip Envelopes](https://www.ableton.com/en/live-manual/12/clip-envelopes/)); TouchDesigner gives "every component … its own Timeline" nested under root time. | A reusable gesture must carry its internal shape in *clip-local* time or it cannot be relocated. Global things — speed cap, band brightness, lift height — belong on *arrangement-time* automation lanes. Getting this split wrong is the most expensive modelling error available in a timeline design. |
| **5.4** | **Blind editing, and the default output sink.** This is where the lighting world's *practice* is safer than its open-source *tools*: theatrical desks have Blind — edits "will not directly affect the output of the console" ([MA dot2](https://help.malighting.com/dot2/en/help/dot2_quick_start_guide/key_gsg_19_blindpreviewanddmxtest.html)) — and QLC+, xLights and Vixen have only preview or blackout. **We need blind far more than a lighting desk does**, because the cost of accidentally commanding live motion is not a wrong colour. | The editor's default output must be the **simulator**, with live output a separately armed binding. `sim/` exists precisely to answer "does this pose collide, can the arm reach there, does the sequence read" without the robot — and *"waiting for robot access is the slowest part of making one."* Distinguish the two deliberately: **preview** = render to the simulator instead of the rig; **blind** = the rig is live running show state while you edit a *different* part. No open-source prior art will give us the second. |
| **5.5** | **Two schedulers over one set of objects.** A demo where a human talks over the robot wants an operator-advanced cue stack with indefinite dwell; an audio-locked routine wants an absolute timeline. **Ableton is the twenty-year precedent**: Arrangement is where "everything happens along a fixed song timeline", Session is for launching clips when "the order of pieces, the length of each piece and the order of parts within each piece is generally not known in advance" — **and the same clips serve both** ([Session View](https://www.ableton.com/en/live-manual/12/session-view/)). QLC+ reaches the same place from theatre. | Building only the timeline is defensible; building only the timeline *without noticing* that demos want GO semantics is not. Ableton also hands us the arbitration rule for free: *"The Session clips and the Arrangement clips in one track are mutually exclusive: Only one can play at a time."* That is one-owner-per-slot, arrived at independently. |
| **5.6** | **Markers emit named cues, never device addresses.** Unity Timeline's Signals decouple a marker from its consequence: a Signal Asset is referenced by a marker, and a Signal Receiver holds the table from signal to object and method, which "decouples Timeline events from their consequences" ([Unity Signals](https://docs.unity3d.com/Packages/com.unity.timeline@1.8/manual/wf-signals.html)). | A marker that says `RELAY 3` has put per-unit wiring into a committed show. It must say `disco_ball_on`. |
| **5.7** | **Should there be a node graph as well, and for what?** Evaluated below, not assumed. | A node graph that duplicates what the timeline does is cost without payoff. |

### The node graph: where it earns its place, and where a timeline is simply better

**Timed, ordered content must not go through a dataflow graph.** Node-RED is the honest test case,
and its own maintainers give the answer: *"We have always said no assumptions should be made about
ordering once a flow branches"* ([going async](https://nodered.org/blog/2019/08/16/going-async)).
Since 1.0, `node.send()` queues delivery "to be called in a later pass of the Event Loop", and when
a flow branches "the branches will be evaluated in 'parallel'". The
[messages guide](https://nodered.org/docs/user-guide/messages) states no ordering guarantee at all.
For "the left arm waves while the lift rises and the band sweeps", that is disqualifying. The
timeline is not merely more convenient; it is the structure that **can state the constraint**.

**The long-lived counter-examples prove the same point.** Max/MSP and Pure Data are the oldest
node-graph tools in a domain with hard timing, and both had to bolt an ordering primitive on, because
topology was not enough. Max's documented rule is "right-to-left, or bottom-to-top for objects that
are vertically aligned" — *order determined by screen position* — and the official advice is to stop
relying on it: "Using the [trigger] object makes the message order explicit; that is, it forces the
messages to follow a specific path regardless of spatial orientation"
([Max 8 Tutorial 5](https://docs.cycling74.com/max8/tutorials/basicchapter05)). Pd is worse: order at
a fan-out depends on the order the connections were *made*, which the patch does not display. That
is Green & Petre's "hidden dependencies" reappearing inside the notation that was supposed to
abolish them — and hidden dependencies are precisely what we cannot tolerate in a robot sequence.

**The industry verdict is a natural experiment.** Godot removed VisualScript in 4.0 while explicitly
keeping visual shaders: only "0.5% of the user base has used VisualScript as their main engine
language", "Visual shaders are working well and appreciated by many users, so they're not going
anywhere", and "the approach we took to visual scripting was simply not the right one"
([Godot 4.0 will discontinue VisualScript](https://godotengine.org/article/godot-4-will-discontinue-visual-scripting/)).
The pattern across engines: **graphs survive where the problem is genuinely a pure function of
inputs — shaders, VFX, parameter mapping — and get abandoned where the problem is imperative
sequencing.**

**But the hybrid tools are strikingly consistent about where a graph does belong**, and the pattern
is the same across six of them: **the graph says what a thing is and how it reacts; the timeline says
when it happens.** Unreal states it almost verbatim — you define "*when* events trigger in Sequencer
while defining *what* those events do in the Director Blueprint". Choregraphe is the closest analogue
to what we are building and makes it literal, nesting the two in *both* directions: a Timeline box
holds a **motion layer** of joint keyframes with six interpolation modes (Constant, Linear, Automatic
Bezier, Bezier, Smooth, Symmetrical) plus zero or more **behavior layers** whose keyframes each hold
a *flow diagram*, loaded on entering its frame range and unloaded on leaving — with "two behavior
keyframes within the same behavior layer will never be active at the same time", so parallelism comes
from adding layers
([Timeline box](https://www.cs.cmu.edu/~cga/nao/doc/reference-documentation/software/choregraphe/objects/timeline_box.html),
[timeline panel](https://fileadmin.cs.lth.se/robot/nao/doc/software/choregraphe/panels/timeline_panel.html)).
Unity splits Timeline from Animator the same way; so do Blender, After Effects and TouchDesigner.

**Recommendation: both, with a hard division, and the timeline first.** The timeline owns *when*.
A graph, if built, owns *what fires a show and what a show may react to* — the trigger wiring the
brief describes — and it must not be able to express a timed sequence, because it cannot do so
honestly. **It must not be the source of truth**: 1.1's argument applies in full, so a graph is a
*view* over the same text `cue.py` validates, never a `.uasset`.

Two caveats worth recording now rather than discovering. **Determinism in a graph is achievable but
costs a type system** — Lingua Franca gets it from logical time plus declared dependencies
([lf-lang](https://www.lf-lang.org/docs/writing-reactors/a-first-reactor)), which makes it a language
rather than a sketching UI. And **the scale ceiling is real**: practitioners report Blueprint graphs
of 350–500 nodes where simple modifications take weeks, with no cross-graph reference search. A
trigger graph past a screenful has stopped being the discoverable thing it was adopted for.

**Reactivity belongs below the composer, in neither surface.** Conditional-on-the-world behaviour —
e-stop cleared, deadman released, RC link lost — is a state machine's job, and ours already lives on
the StamPLC. Behaviour trees are the modern form and are driven by an external tick rather than by
data arrival, which is what makes preemption and fallback cheap
([BehaviorTree.CPP](https://www.behaviortree.dev/docs/learn-the-basics/BT_basics)). **The composer
should be something the state machine can refuse to run.** Worth being honest that BT-over-FSM is
partly taste: a study of ROS-ecosystem projects found "similar usage patterns at model structure and
at code reuse in the behavior-tree and state-machine models"
([arXiv:2208.04211](https://arxiv.org/pdf/2208.04211)); only the trend line favours BTs.

---

## Phase 6 — example shows, including seasonal ones

*These are the test of whether the data model is expressive enough. They are not decoration, and
they are the cheapest possible check: a show that cannot be written is a schema finding.*

| # | Question | Kill criterion |
|---|---|---|
| **6.1** | `hello` exists. What does the **second** show need that it doesn't have? | Every missing primitive is a Phase 1 item, discovered for free. |
| **6.2** | **Halloween.** It is the right first seasonal show for the same reason it was the right first theme: it exercises the accessory layer, the band's motion vocabulary and the animation system, and it has an obvious success test. It also already cost something real — **the palette had to give up orange**, because orange sits close enough to `DEGRADED`'s amber to be confusable, so purple and toxic green carry it instead. | A seasonal show must survive the reservation rule. If Halloween needs a reserved colour, the answer is to move the theming to a different channel, not to compromise the reserved one. |
| **6.3** | **Christmas.** Red and green against `E_STOP` red and `SAFE_IDLE` green is the same collision, worse. | Same test. Two independent seasons hitting the reserved palette is evidence about the *reservation*, not about the seasons — and worth hearing. |
| **6.4** | **A show written entirely by an agent**, from the format spec, validated and simulated with no human edit. | This is the real test of 1.1, and it needs no robot. If an agent cannot write a valid show from the spec, the spec is the problem. |
| **6.5** | **A show whose point is a reaction, including to a failure.** The Takayama result (below) is that reacting to an outcome improved perceived competence **regardless of whether the task succeeded**, and that reacting to a failure partially mitigated the failure. | A format that can only express success has thrown away the cheapest legibility win available. |

---

## Benchmarks — what the prior art actually does

Condensed; per-item citations are in the phases above.

### Spot's Choreography SDK — the closest prior art

[Choreography service](https://dev.bostondynamics.com/docs/concepts/choreography/choreography_service).
Its three best ideas:

1. **Quantise to a sub-beat unit, store it once, make durations integers in it.** "A slice is ¼
   beat… a dance with a BPM of 100 beats/minute has 400 slices per minute", "Moves always run for an
   integer number of slices". The grid has a *physical* valid range, not just a musical one:
   250–450 slices/min is the documented reliable band.
2. **Serve the capability catalogue from the device.** `ListAllMoves` returns a `MoveInfo` per move
   with min/default/max durations and `controls_arm/legs/body/gripper/lights/...` booleans, so an
   editor never hard-codes the robot's capabilities and **conflict detection falls out of the
   flags**. An older editor still works against newer firmware.
3. **Two-tier validation plus a pose-transition state machine**, surfaced in the editor. Blocking
   *failures* versus auto-clamped *warnings* (gated by `non_strict_parsing`); a
   `STAND/KNEEL/SIT/SPRAWL` entry/exit automaton on the legs track whose violations are "rejected by
   the API" and **highlighted in red in Choreographer**; a 3D/2D Dance Preview before hardware;
   `Run Move` to test one block alone; and **narrower parameter ranges in Beginner mode than Expert
   mode**, explicitly to produce more reliable results.

Its model is 9 concurrent tracks where **a same-track overlap is forbidden, not blended**: *"Moves
that control any of the same tracks cannot run simultaneously."* That is exactly `cue.py`'s
one-owner rule, independently arrived at. Its documented limits are worth knowing because we would
otherwise rediscover them: a closed move vocabulary, a flat absolute timeline with no nesting or
loops, one global tempo, and untyped validation strings. Also: blocks are "holistically optimized
requests to the controller, rather than pre-scripted motion", and when asked for the impossible the
controller "will find the possible motion most similar to what was requested and do that instead"
([In Step with Spot](https://bostondynamics.com/blog/in-step-with-spot/)) — a *good* property, and
one our open-loop command path does not have.

**Two findings that cut against the obvious assumption.** First, **the music is played by the
laptop, not the robot** — the `music` track is "(Choreographer only)" — and sync is entirely
open-loop: one future timestamp in robot time plus an authorially matched BPM, no beat tracking, no
drift correction, and a documented case where the robot starts *late* while the music does not wait.
Second, and most useful: Boston Dynamics ship, in their own documentation, *"The choreography
framework is less robust than other Spot behaviors… Some combinations of moves will be incompatible
and can result in the robot falling… **The robot will fall down.** Trial-and-error might be needed…
**Not recommended for use with payloads.**"* **Bounds checking did not buy them stability.** Ours
should be read the same way: `cue.py` passing means a show is *allowed*, not that it is *safe*.

### QLC+ — the best conceptual benchmark among the lighting tools

[Function Manager](https://docs.qlcplus.org/v4/function-manager). The only one of four with both
scheduling paradigms *and* an arbitration model. The transferable parts:

- **A function typology behind one interface** — Scene (static state), Chaser (ordered steps),
  Collection (parallel bundle), EFX/RGB Matrix (parametric generator), Script (escape hatch), Show
  (timeline) — and **a Show is itself a Function**, so it can be bound to a button, nested, or fired
  from a cue. A pose, a move, a phrase and a routine all being the same kind of thing is exactly
  what a robot composer wants.
- **HTP vs LTP**, resolved from the fixture definition's channel group, overridable per channel
  ([concepts](https://www.qlcplus.org/docs/html_en_EN/concept.html)). The reasoning transfers
  cleanly: `max(a,b)` is meaningful for brightness and meaningless for a target pose. The Grand
  Master is the sharper lesson — intensity-only *by default, deliberately*, so pulling it does not
  drag the movers toward zero.
- **Visible, individually releasable override.** Simple Desk takes a channel, **turns it red**, and a
  reset returns it to normal arbitration. Compare our rule that "a monitor that changes behaviour
  must say so".
- **Phase-coded progress** — green during fade-in, blue during hold. For a robot, "still moving"
  versus "holding position" is a safety-relevant distinction and deserves the same treatment.
- **Manual crossfade**, where the cue list's side fader drives a transition by hand and **overrides
  the programmed fade speeds**. Worth stealing outright: a human taking over the interpolation of an
  automated move is a better intervention primitive than abort, where abort is not required.
- **Fixture definitions (QXF)** decouple a show from hardware via a semantic channel `Group` and
  `Preset`, value-range `Capability`, and a `Mode`. **GDTF**
  ([DIN SPEC 15800](https://gdtf-share.com/help/developers/gdtf_1_2/file-format-definition/index.html))
  is the rigorous version: FeatureGroup → Feature → Attribute, where an Attribute is "a singular
  mutually exclusive control function", plus **ActivationGroups** for "attributes which need to be
  activated together to gain control over one logical function" — which is a coordinated multi-joint
  pose, something neither QXF nor Vixen models. GDTF's geometry collect is typed `Axis`/`Beam` nodes
  with 4×4 transforms, i.e. structurally a URDF with a channel map attached. The synthesis for us:
  **URDF for geometry, a GDTF-like attribute vocabulary for capabilities, a mode-like mapping layer
  per robot** — and GDTF-vs-MVR (device type vs this rig's instances) is the same cut as
  `per-robot-values-not-in-repo`.

Also worth knowing: **[OLA](https://github.com/OpenLightingProject/ola) is a transport, not a
composer** — "the distribution layer that abstracts away the various protocols used". All three
composers still carry their *own* output-mapping layer, so the real shape is **three layers, not two:
logical elements → patch/mapping → transport**, and a simulator is then just another consumer of the
same frame stream. xLights contributes a per-element layer stack with explicit blend modes
(compositor semantics rather than priority semantics), **song-structure regions** that can be
exported as self-contained sequences, and a clean rig / output-map / sequence file split. Vixen
contributes [filters in the patch path](https://www.vixenlights.com/docs/concepts/filters-patching/)
— intent in, calibrated output out — which maps precisely onto per-unit calibration staying on the
robot, and **mark collections whose weight sets how strongly effects snap**, a graded magnet rather
than a binary one.

### Timeline conventions

[Ableton's Session versus Arrangement](https://www.ableton.com/en/live-manual/12/session-view/) is
the one to copy, and it gives us three things: the dual-scheduler model over one clip library (5.5),
mutual exclusion per track, and — the highest-leverage idea in this whole survey — **Arrangement
Record, which logs a live Session performance including clip launches and automation into the
editable timeline.** From the rest of the DAW/NLE vocabulary: tracks, duration-bearing clips,
keyframes with named interpolation modes, automation lanes as breakpoint envelopes, markers, a tempo
map, graded snapping, non-destructive editing. One xLights subtlety to decide deliberately: its
timing marks are a *placement grid, not a binding* — "once the effects are dropped they have no
relation to the original timing marks" — so changing the tempo does **not** re-time the content.

### Expressive-robot authoring

- **[Choregraphe](https://ii.tudelft.nl/naodoc/site_en/greendoc/choregraphe/intro_boxes.html)** is
  the closest analogue and settles the graph/timeline question by example (see 5.7). It also ships
  the arbitration primitive we have not considered: a per-box **resource acquisition mode** of
  *Passive / Waiting / Aggressive*, which is an explicit, authored answer to "two things want this
  actuator" — our LED band driven by the idle loop versus by a cue, exactly.
- **Cozmo** is the closest analogue in *decomposition*: FlatBuffers with a lossless JSON twin, and
  a keyframe type per channel — head angle, lift height, body motion, backpack lights, named face
  animation, procedural face, audio, and a generic `AnimEvent`. Every keyframe is
  `trigger_time_ms` + `duration_ms` with independent per-track lists, and **variation is in the
  format** (`variability_deg`, `variability_mm`, audio `probabilities`) with **Animation Groups**
  above it so a behaviour requests an intent and the runtime picks a variant. Authored in Maya, with
  the stated difficulty that real physics meant "no cheating"
  ([GDC 2017](https://gdcvault.com/play/1024221/Cozmo-Animation-Pipeline-for-a); talk paywalled, so
  treat its content as secondary).
- **Furhat** gives the minimum viable expressive primitive, read from
  [the example skills source](https://github.com/FurhatRobotics/example-skills):
  `defineGesture(name, strength, duration, defaultPriority)` with `frame(t) { PARAM to value }`,
  `persist = true` to change the baseline rather than return to it, and `reset(t)`. Gestures play
  **concurrently and are resolved by priority, not by stopping one to start another** — every mature
  system blends and arbitrates; none of them switch. And a frame can carry audio, a face texture and
  an LED pixel alongside motor values.
- **BML / SAIBA** ([BML 1.0](https://projects.cs.ru.is/projects/behavior-markup-language/wiki)) is
  not a format to adopt — it is XML for conversational humanoids and most of it does not map to a
  mecanum base — but three of its ideas are worth taking: **phase sync points with bidirectional
  constraints** (1.4), the **transient versus shift** distinction (`face` vs `faceShift`, which is
  Furhat's `persist` arrived at independently), and **typed warnings with a prediction pass that
  returns resolved timings** — which *is* pre-flight validation, already named.
- **[Takayama, Dooley & Ju, *Expressing Thought*](https://www.wendyju.com/publications/151r-takayama.pdf)**
  (HRI 2011, N=273), and the real numbers matter because the paper is usually mis-cited.
  **Forethought did *not* improve literal readability** — keyword match was not significantly
  predicted by it, *F*(1,1083)=0.82, *p*=.37 — but it did improve **confidence** in one's reading
  (5.69 vs 5.20, *p*<.0001), **appeal** (4.83 vs 4.27) and **approachability** (5.05 vs 4.54).
  **Reaction improved perceived competence regardless of outcome** (4.72 vs 3.86, *p*<.001), and
  reacting to a failure *partially mitigated the failure*. Their mechanisms map onto our hardware
  better than onto theirs: engagement was a small base translate toward the person (we have a mecanum
  base, which does this better than a PR2 torso), and timing was the expressive move firing an
  instant *before* the functional one.
- **The cautionary tale is in that same paper, and it is the best justification for `cue.py` in the
  literature.** Their animations were made in Maya and, they write, *"not all of the kinematic
  constraints were maintained, particularly the speed of the torso lift"*, and the setup was *"as yet
  somewhat risky to study participants because it does not yet incorporate collision avoidance."*
  That is a published HRI paper from Willow Garage saying out loud that the animation authoring path
  produced motion the robot could not safely execute. The
  [systematic review](https://arxiv.org/pdf/1812.06784) makes the same point structurally: screen
  animation is free of physical limits, and servos are not.
- **[ELEGNT](https://arxiv.org/html/2501.12493v1)** (Apple, DIS 2025) is the caution against
  over-applying all of this. A lamp-like arm optimising functional plus weighted expressive utility
  was strongly preferred overall (M=56.16 vs 28.77, *p*<0.0001) **but the benefit is task-dependent**
  — large for social tasks, near zero for function-oriented ones. And: **older participants preferred
  expressive movement less, and character-design professionals rated it lower than the general
  population.** Two lessons: do not make everything expressive, and our own taste is not the
  audience's.

### Safety in these tools: there essentially is none

Stated plainly because it changes how rule 4 should be read. Choregraphe, Furhat, Misty, Jibo,
Reachy and Blossom all ship the author's data straight to the robot — no joint-limit check, no
velocity envelope, no clearance model. Choregraphe's resource acquisition mode is a *correctness*
mechanism, not a safety one.

The exceptions are instructive and few. **Spot** is the strongest industry example (its three-part
validation story above), and even it ships "the robot will fall down" with procedural guardrails —
3 m clearance, 3 m personnel distance, an E-Stop in the UI — as the remaining guarantee. In research,
[Porfirio et al. (UIST 2018)](https://peopleandrobots.wisc.edu/publications/authoring-and-verifying-human-robot-interactions/)
applied **design-time formal verification** to authored interactions, and an N=9 evaluation found it
increased designers' ability to identify and contextualise violations — note it verifies *social*
properties, but the architecture (author → model → check → annotate the editor) is what we would want
for physical envelope checks. [Polaris (HRI 2024)](https://arxiv.org/abs/2403.13988) adds the
generalisable rule, from a 32-participant study where its Plan Visualizer significantly improved
program quality: **anything the tool infers must be shown resolved before it runs.**

**So `cue.py` is not us catching up with the field. It is the part of this system that is ahead of
it**, and the part most worth protecting.

---

## Proposed changes to this plan

Offered for acceptance or rejection rather than assumed.

1. **Reframe the effort from "build a composer" to "finish the composer".** The brief assumed
   greenfield. The format, the validator, the slot model, the preflight declaration and the
   simulator exist and are merged. Treating this as new work would duplicate `cue.py` and lose its
   19 tests. **The highest-leverage item in this plan is Phase 0** — five small corrections to
   things already built.
2. **Do Phase 0.1 and 0.2 before anything else, and 0.2 before any of Phase 4.** `theme::reserved()`
   as two integer literals is the safety-bearing rule of `face-config.md` expressed as magic
   numbers, in the exact file about to become authorable data. The withdrawn frequency band is a
   validator actively lying to authors.
3. **Settle the base-motion question explicitly rather than inheriting the refusal.** The brief asks
   for dancing; `cue.py` refuses `DRIVE` and `punch-list.md` says why. Recommendation: a **bounded
   open-loop dance move** — time-limited, speed-capped, declared floor space, CH8 throttle in a
   human's hand — written down as a deliberate limitation with its reasoning, the way the `DRIVE`
   refusal already is. What must not happen is `DRIVE` quietly appearing in `SLOTS`.
4. **Make the simulator the editor's default output sink**, with live output separately armed. This
   is the lighting world's Blind, it is the one place their practice is clearly safer than their
   tools, `sim/` already exists for it, and *"waiting for robot access is the slowest part of making
   one."* Same argument and the same expected payoff as "use the desktop SDL build for face design"
   in the Tab5 plan.
5. **Build the timeline; defer the node graph to a trigger-wiring view over the same text.** The
   division is "graph says what and why, timeline says when", which Choregraphe, Unity, Unreal,
   Blender, After Effects and TouchDesigner all converged on. Node-RED's documented lack of ordering
   guarantees and Godot's removal of VisualScript both rule a graph out for timed content.
6. **Make clips parametric, and put variation in the format.** Furhat's four knobs —
   `strength`, `duration`, `defaultPriority`, `persist` — plus Cozmo's `variability_*` and animation
   groups. This is the cheapest available defence against a show that reads mechanically by its third
   playback, and it also offers a third answer to the open question in 4.6: not "parameters
   authorable" versus "selection only", but **a curated vocabulary with two scaling dials**.
7. **Map Spot's Beginner/Expert parameter ranges onto NORMAL/RESEARCH.** Spot narrows authorable
   ranges in Beginner mode "explicitly to produce more reliable results". We already have exactly
   this distinction as a *mode* in `docs/control-authority.md`, and it is currently about overrides
   rather than authoring. Extending it to authored parameter ranges is nearly free and fits the
   existing model.
8. **Adopt Spot's status taxonomy and BML's warning codes rather than inventing worse ones.**
   `VALIDATING / PREPPING / WAITING_FOR_START / DANCING / INTERRUPTED / COMPLETED / FAULTED`, plus
   typed warnings (`IMPOSSIBLE_TO_SCHEDULE`, `BEHAVIOR_TYPE_NOT_SUPPORTED`, `NO_SUCH_TARGET`). Note
   Spot's own untyped `repeated string warnings` as the counter-example to avoid.
9. **Build the capture path early, not late.** Ableton's Arrangement Record — drive the robot live
   by RC or by firing cues, record the performance, then edit it as a timeline — collapses the
   author-versus-perform dilemma instead of resolving it, and it is how a show gets written by
   someone who cannot read YAML. It also needs the runner and the logging from Phase 2 and almost
   nothing else. Flagged as a proposal rather than a phase because it is the item I am least certain
   anyone wants.
10. **Add an idle/breathing layer (3.6).** Best value per unit of effort in the plan, measured
    support in two studies, needs no new hardware, and the band already breathes.
11. **Write the format specification as a document, not as `cue.py`'s source.** An agent can read
    `cue.py`, but Phase 6.4 — an agent writing a valid show from the spec with no human edit — is the
    test of whether the spec exists at all.

---

## What is unproven, and what could not be verified

Stated so none of it is read as settled.

**About our hardware**

- **Whether R3's switched load survives the e-stop.** Not checked. Affects 3.5 directly.
- **8Servo channels 5–7 being usable for an accessory.** `kDriveCh` accounts for 0–3 and
  `g_mirrorPin` defaults to 4, so 5–7 look free — but this is read from source, not measured, and the
  bus has a documented wedge and a red-fault history. **Do not design against it without a test.**
- **The real speaker bandwidth.** The narrow-band claim is withdrawn and nothing replaced it;
  `cue.py`'s constant is currently unsupported in both directions.
- **Whether the Tab5 can usefully play a music track.** 3.5 MB of SPIFFS and `playWav` from a flash
  array are facts; "a show can have music on the head" is not. Speaker bring-up is also nested inside
  the Wi-Fi-up branch of `commsTask`, so no network means no sound.
- **Any latency number for a show.** Nothing here has been timed; the bench-network caveat from
  `workplan-tab5-idf.md` §1B applies unchanged.
- **The face's absolute frame-cost ceiling.** The ~16 Mpx/s figure this note originally bounded
  against is being retracted in the Tab5 thread as a primitive-rasterisation measurement misapplied
  as a fill/blit ceiling. The cost *hierarchy* stands; the number does not. Phase 4.3 must not
  derive a validated range from it until it is re-measured.
- **`sim/` is six degrees optimistic on clearance** — contact felt by hand at J2 182°, the model
  reaches zero at 188.3°. The hand-measured constant stays in charge; a simulator-validated show is
  not a safe show. And **the sim's dynamics are placeholders**: it answers collision, reach and "does
  the sequence read", not force, torque, sag, tipping, or **how long a move takes** — which matters
  here, because a timeline is made of durations.
- **Whether anyone wants a node graph.** The argument above is about where a graph *can* be correct,
  not evidence of demand. Cheap to leave unbuilt.

**About the prior art**

- **There is no controlled study comparing timeline against flow-graph authoring for robot
  behaviour.** The main HRI survey of end-user robot programming
  ([arXiv:2105.01757](https://arxiv.org/pdf/2105.01757)) does not list timelines as a representation
  category at all, and flags our exact problem as open — such systems "should also support concepts
  such as parallel programming and synchronization, which are typically difficult even for advanced
  programmers." **Our argument is by transfer from media tools plus one strong piece of robot prior
  art (Choregraphe), not by experiment.** This is the thinnest part of the evidence base.
- The **"graph = what, timeline = when" pattern is inferred** from six tools converging plus one
  explicit statement from Epic. A strong empirical regularity, not a proven principle.
- **Spot**: no explicit API error for same-track temporal overlap as distinct from the leg-state
  check; what happens to an in-progress sequence on mid-dance lease revocation; the difference between
  `.chr` and `.csq`.
- **Lighting tools, secondary sources only**: that xLights' `.xsq` is XML; Vixen's `.tim` format;
  first-party MTC/LTC timecode support in xLights; the USITT ASCII standard text; the internal element
  names of a QLC+ `.qxw` (the schema 404'd); GDTF's `dmx-mode-collect` page (404). **No QLC+ numeric
  priority level per playback was found** — arbitration appears to be HTP/LTP plus frame-scoped
  submasters plus a latched override. **No true blind mode exists in QLC+, xLights or Vixen**; Blind
  is documented only on commercial desks.
- **No OLA disclaimer exists** saying it is not a show composer. The separation is documented
  positively, not as a disclaimer.
- **Expressive-robot tools**: Pure Data's connection-order dependence (expired TLS, snippet only);
  Max/MSP's scheduler specifics; the GDC 2017 Cozmo talk's content and speakers (paywalled); Jibo's
  animation editor and `.bt` compiler (primary docs offline); Misty having any timeline authoring
  surface (not found — it appears to be script-only); Choregraphe's on-disk project format (not
  confirmed, so no claim made); and Unreal's own Blueprint-merge documentation, which was reachable
  only as a table of contents, leaving the "viewer not merger" detail vendor-sourced.
- **Claims deliberately dropped rather than guessed**: a Catie Cuan journal article on *Time to
  Compile* and any link between her and a Boston Dynamics authoring tool; a paper titled
  "Choreographic interfaces"; and behaviour formats for Keepon, Tapia and OPSORO, none of which were
  documented in anything reachable.

---

## Carry-over from the Tab5 session, 2026-10-05

Captured while the Tab5 work was fresh, because these are things the choreography effort will hit
and they were learned expensively.

### A validation rule outlives the claim it was built on

`cue.py` carried `SOUND_BAND_HZ = (1800, 4500)`, commented *"what the head's speaker actually
carries, judged on the robot"*. That was a by-ear impression of `M5.Speaker.tone()` in a noisy room.
It was **withdrawn** in `design/tab5-platform.md` §3 once the hardware was looked at — and it stayed
in the validator for days afterwards, **rejecting legitimate shows as physically impossible**, with
a unit test asserting it.

Now `SOUND_SANITY_HZ = (150, 12000)`, with the name carrying the semantics because the old one did
not. The lesson is about **propagation**: by the time the claim was retracted it had become a rule
*and* a test. **Withdrawing a claim means finding what was built on it** — and a validator is
exactly the kind of thing that quietly turns a guess into a constraint on other people's work.

This matters disproportionately here, because a choreography validator's whole job is encoding
assumptions about the robot. Every bound in `cue.py` should be traceable to a measurement or
labelled as a sanity check, and the ones already there deserve that audit.

### The reservation rule is currently magic numbers

`theme::reserved()` in `app_face.cpp` is `state == 4 || state == 5`. That is the **safety-bearing**
rule — which states' colours may never be themed — expressed as indices into `kStates`, in the file
that is about to become authorable data. Reorder the table and the rule silently protects the wrong
states.

**Prerequisite for choreography owning face configuration:** the reservation must be *derived from
the table* (a flag on the state) rather than hardcoded beside it. Cheap now, and a silent
safety-rule inversion later.

### The face configuration boundary is settled, not open

`design/face-config.md` has it: **Reserved / Bounded / Free**, with the firmware **rejecting**
rather than discouraging an invalid configuration, because an editor is a convenience and not a
safety boundary. Treat that as a decision to build against.

Also decided after the plan was drafted: **selectable is the primary path and authoring is the
option** — the vocabulary is a design artefact, the selection is the configuration — with bad
options caught by rule *and* by simulation evaluation.

### The cost gate is comparative only, and why

`firmware/tab5_idf/app_face/cost_gate.cpp` measures dirty area per frame on the desktop. The idea is
sound: **dirty area per frame is geometric and transfers between machines; pixels per second does
not**, because it depends on how fast the machine renders.

But **its absolute verdict is retracted.** It compared against ~16 Mpx/s, which came from a bench
that was rasterising fourteen `drawEllipse` calls per frame — a *primitive rasterisation* workload
applied as a *fill and blit* ceiling. The device then ran happily while the gate claimed 532% over.
`LV_USE_PERF_MONITOR` is now on so the device reports its own FPS and CPU, which is what the gate
should be re-anchored to.

**So: a benchmark is only a budget for the thing it benchmarked.** Use the gate to compare before
and after a change. Do not quote its absolute number at anyone.

### The simulator has three jobs and one structural blind spot

Relevant because this plan leans on `sim/`:

| the sim is good for | the sim cannot see |
|---|---|
| geometry, proportion, colour, motion feel | **anything involving the LVGL lock** — it is single-threaded there |
| full-rate iteration with no flashing | the real frame budget (far more CPU) |
| **ASan / UBSan**, which caught nothing but would have caught the pool sizing | layer allocation failures (GBs of heap) |
| **comparative cost evaluation** | panel-specific rendering |

The blind spot is not a gap to close: on the desktop `lv_timer_handler()` runs on the main thread,
so the unsynchronised `lv_label_set_text()` that caused the longest freeze **cannot be reproduced
there**. Do not expect the sim to validate a runner's threading.

### Invariants any face-driving runner inherits

`firmware/tab5_idf/AUDIT.md` lists ten classes, each of which has already caused a failure. The four
a choreography runner is most likely to reintroduce:

1. **Every LVGL call holds the lock.** The longest freeze was one call site of five that did not.
2. **No unbounded loops** normalising an accumulator — `fmodf`, not `while`. One slow frame made an
   O(value) loop iterate millions of times inside the render path.
3. **Wrap-safe deadlines.** `now > deadline` inverts at ~49 days of uptime.
4. **Guard every per-frame write**, and remember a guard reduces how *many* writes happen, not what
   each costs.

### And a process note, since this plan will span sessions

**Notes drift from code, and a revert is exactly when it happens.** Four claims in the face notes
described a face that did not exist — an emote documented but never implemented, a pin that had been
removed, a band segment count two refactors stale. The subagent found them by reading the code
against the prose, which nobody had done.

Worth doing at every phase boundary here, because a workplan that describes a system inaccurately
is worse than no workplan: it is confidently wrong in a form people trust.
