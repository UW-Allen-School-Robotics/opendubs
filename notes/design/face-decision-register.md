# Face decisions that lived only in code

Design decisions about the face that existed as **C++ comments in a firmware tree** and nowhere in
`notes/design/`. Lifted 2026-10-09, while both trees are still here to read: if the tablet becomes
the face, these comments are the only place several of these choices exist.

**Docs only. Nothing here changes what runs.** Where records disagree, the conflict is listed for
the maintainers rather than settled — except where the shipping head's choice is plainly the
governing one, which is said explicitly.

Sources: `firmware/tab5_idf/app_face/` (the **shipping** ESP-IDF head) and `firmware/tab5_face/src/`
(the **retired** Arduino head).

---

## 1. Three places the governing design note and the code disagree

`face-design.md` is the clean-sheet spec — *"what to build now that the platform is not the
constraint… grounded in the research rather than in taste"*. It is current and forward-looking, so
it governs new work. **In three places it rules against what every implementation actually does.**

These are raised, not resolved.

### 1a. Pupils

> `face-design.md`: **"No pupils."** Anki added them to Cozmo, found they made him read as too
> human, removed them, and recovered the same legibility through **size and ratio**. Gaze direction
> lives in shape and in inter-eye asymmetry.

**Every implementation has a pupil.** `skins/eyes.js` draws one at `PUPIL_FRAC = 0.34`;
`app_face.cpp` creates a pupil object and parents the glint to it. The retired head is the only one
that followed the rule, and its comment says why that was hard:

> `face.cpp:547`: **"GAZE LIVES IN THE ASYMMETRY BETWEEN THE TWO EYES, not inside either one…**
> These eyes have no pupil, and a pupil is the strongest gaze cue there is… Both eyes come from one
> sprite pushed twice and mirrored, so the pair was ALWAYS perfectly symmetric — and a symmetric
> pair of pupil-less eyes has almost nothing left to say about direction."

So the retired head obeyed "no pupils" and then found it could not express gaze, because its
renderer forced the two eyes to be identical. **That constraint is gone.** Both the web engine and
any tablet renderer can draw the two eyes independently.

**The question this leaves:** the no-pupil rule was adopted on Cozmo's evidence, abandoned in
practice without a recorded decision, and the reason it originally failed here was a renderer
limitation that no longer exists. Either the rule should be withdrawn in the note, or the pupil
should go. It should not keep being both.

### 1b. A highlight that follows the gaze

> `face-design.md`: **"One highlight, fixed.** A highlight that chases the gaze was tried and read
> as confusing."

The shipping head does the opposite — `app_face.cpp:893`, *"CHILD OF THE PUPIL… it travels with the
pupil"* — and on 2026-10-09 the maintainers looked at an anchored highlight in the prototype and
chose the travelling one.

**I reported that conflict as firmware-versus-firmware and missed this third record.** The note
takes the retired head's side. The maintainers' choice stands — they looked at both — but it is an
override of a research-grounded line in the governing note, not merely a pick between two trees,
and the note should be amended to say so rather than left contradicting the thing that ships.

### 1c. Smooth pursuit

> `face-design.md`, under **What NOT to build**: **"No continuous smooth tracking.** It reads as a
> stare and it exposes sensor jitter that discrete glances conceal."

**`face-eyes-and-gaze.md` (merged, #134) recommends smooth pursuit, and the `?eyes=full` prototype
implements it.** I proposed it without noticing this line. The two notes now contradict each other
in the same directory.

The arguments are not actually symmetrical, which may be how it resolves:

- the prohibition is about **tracking a person continuously** — holding them, which is the stare
  problem that the aversion timer in the same prototype separately addresses;
- what the prototype added is **pursuit instead of a stutter of saccades while already following a
  target**, which is a smoothness fix rather than a decision to follow for longer.

But that is a reading, not a ruling, and it is exactly the kind of reconciliation that should be
made deliberately rather than assumed by whoever writes the next line of code.

---

## 2. Decisions lifted, which were only in code

### Eye spacing is a fraction of eye width, not a distance

> `app_face.cpp:65`: **"SPACING IS SET AS A FRACTION OF EYE WIDTH, not in millimetres, because that
> is the dial that controls how friendly the face reads.** Infants have larger eyes AND
> proportionally less gap between them, so 'bigger and closer together' is a single coherent move
> toward the baby schema rather than two opposing ones."
>
> adult human (63 mm IPD, 30 mm eye) → gap ≈ 1.0 eye-widths · the original OpenDubs face → 0.34

This is the unit `face-design.md`'s measurement should be read in, and it reframes that
measurement: the note's "spacing is the dominant error" and this comment's "bigger and closer is
one coherent move" are **the same dial pulled in opposite directions**, and which way to pull it is
the open geometry question in `face-eyes-and-gaze.md`.

### Confidence sets how far the eyes commit

> `app_face.cpp:191`: **"HOW FAR THE EYES COMMIT IS THE ARBITER'S CONFIDENCE**, scaled from this
> floor to full travel. A low-confidence target therefore gets a GLANCE in its direction rather than
> a fixation, and a confident one gets the full turn… The floor is not zero: a target worth
> publishing is worth looking at a little."

The web engine implements this (`GAZE_AMP_FLOOR`), and it is the face-side counterpart to
`pantilt.h`'s rule that confidence gates the head rather than only its amplitude.

### Colour is state; motion is activity; neither is a safety channel

> `app_face.cpp:368`: **"COLOUR IS STATE, MOTION IS ACTIVITY.** Two independent dimensions on one
> channel, so neither is lost when the other is absent."
>
> `app_face.cpp:372`: **"NOT A SAFETY CHANNEL.** It is on the Tab5, so it dies with the e-stop and
> with the link. PRISM is the safety annunciator and survives both. **Nothing may depend on this
> band alone.**"

The second half matters beyond the band: it is the general rule that **anything on the face is
decorative with respect to safety**, because the face loses power with the e-stop by design.

### A theme may restyle anything except the state distinction

> `app_face.cpp:384`: **"ACCESSORIES ARE STATIC…** a decoration that animates is competing with the
> face for the frame budget. **THE ONE RULE:** a theme may restyle anything EXCEPT the state
> distinction. DEGRADED's amber and E_STOP's red are reserved and pass through untouched."
>
> `app_face.cpp:638`: **"TRUE OUT OF RANGE, deliberately.** An unknown state must not be colourable
> by a file — the safe direction for 'I do not recognise this' is to treat it as reserved."

The second is a small rule with a large consequence: **unknown means reserved**, so a future state
a theme file has never heard of cannot be recoloured by it.

### The band was suppressed, and why

> `app_face.cpp:919`: **"THE BAND IS SUPPRESSED. Decided 2026-10-06:** it spun continuously and
> meant nothing, so it carried no information and **trained people to ignore the one channel that
> should carry urgency.** Not deleted — motion could earn its place once there is a real source for
> it (mic gate, inference running, audio playing)."

A dated decision that appears in no note. It is also the clearest statement of a principle worth
keeping as the tablet's edge ring is designed: **a channel that moves without meaning spends the
attention of the channel that needs it.**

### Four strips, not forty-eight segments

> `app_face.cpp:661`: **"FOUR STRIPS, NOT FORTY-EIGHT SEGMENTS."** The segment version froze, and the
> failure was located precisely: idle breathing is **uniform**, so all 48 segments change every
> frame, where a sweep touches only the few the bump passes over.

Kept because the reasoning generalises past the Tab5: a uniform animation over many elements costs
every element every frame, while a travelling one costs only the moving part.

---

## 3. Deliberately not lifted

Implementation craft, which belongs next to the code it constrains and would only dilute a design
note: the write-only-what-changed discipline, the LVGL lock rules, 8-px resize quantisation,
why mounting is kicked off the boot path, the PSRAM/internal budget for the Arduino sprite, and the
`lv_line` coordinate trap. All of it is sound and all of it is about *how*, not *what*.

---

## How the three were decided — 2026-10-09

All raised above are now answered, except one that is deliberately left open:

1. **Pupils — KEPT, and the rule is amended.** `face-design.md`'s "no pupils" is struck through
   with the override and its two reasons, and **the Cozmo evidence is kept** rather than deleted:
   it is still a fair warning that a pupil pushes the face toward the human end, which is a thing
   to watch while tuning rather than a rule that was wrong.
2. **The travelling highlight — CONFIRMED.** Recorded in `face-design.md` as an override of "one
   highlight, fixed", with the reason and the three-way record, beside the struck-through
   anchoring proposal in `face-eyes-and-gaze.md`.
3. **Smooth pursuit — STILL OPEN, to be judged by eye** in `?eyes=full`. Both notes now carry a
   cross-reference saying so, so neither can be read as settled. Whichever loses gets corrected in
   both.

And a fourth, from §2, which turns out to be the same question as the prototype's geometry
comparison: **the spacing tension is decided by looking.** Baby schema wants the gap *smaller* in
eye-widths; the human-proportion measurement wants it *larger*; the two bodies of evidence
genuinely disagree about this face, so no amount of reading settles it.
