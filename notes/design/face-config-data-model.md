# The face configuration data model

Where the face's authorable data lives on flash, how it is loaded, validated, selected and
updated. Desk work, 2026-10-05 — read against the code, not against the notes.

This is the concrete layer under two notes that already exist and are not re-decided here:
[`face-config.md`](face-config.md) settles the **policy** (Reserved / Bounded / Free, firmware
rejects rather than discourages, selectable is primary and authoring is the option), and
[`tab5-face-architecture.md`](tab5-face-architecture.md) §5–6 sketches the **mechanism** (a `cfg`
partition, `cfg_load` / `cfg_validate` producing a POD `FaceConfig`). This note supplies the
schema, the on-flash format, the failure table and the delivery path, and it corrects the
architecture note in one place where the real partition table does not match its proposal.

Companion: [`../workplan-choreography.md`](../workplan-choreography.md) Phase 4 (this is its data
model), [`stamplc-demo-audit.md`](stamplc-demo-audit.md) §3 (the vendor reference),
[`tab5-platform.md`](tab5-platform.md) §6b (the cost hierarchy the bounds come from),
[`../../docs/control-authority.md`](../../docs/control-authority.md).

---

## 0. Ground truth, read from the code

The workplan's rule — *you cannot extract a table into data while the written description of it is
wrong in four places* — applies to this note first. So: the five tables as
`firmware/tab5_idf/app_face/` has them today.

| table | where | shape, exactly |
|---|---|---|
| **emotes** | `app_face.cpp` `kEmotes[]` | **5** entries — `NEUTRAL ALERT SQUINT SOFT SLEEPY`. `struct Emote { const char* name; float wMul, hMul, radiusFrac, lid, browRaise, browAngle; }` — **six** floats. No `HAPPY`. |
| **states** | `app_face.cpp` `kStates[]` | **7** entries. `struct St { const char* name; const char* label; uint8_t r,g,b; int locked; bool reserved; }`. `locked` is an index into `kEmotes` or `-1` for "may emote freely". `reserved` is `true` for `DEGRADED` and `E_STOP` only. `CHARGING` pins nothing (`locked = -1`). |
| **themes** | `app_face.cpp` `namespace theme` | `enum Id { NONE, HALLOWEEN, COUNT }`. Palette is a `switch (state)` inside `stateColour()`; accessories are built once on activation; `kMaxAcc = 24`. |
| **band** | `app_face.cpp` `namespace band` | **four edge strips**, `kThick = 16`, each a fixed linear gradient whose only per-frame write is opacity. `enum Activity { IDLE, LISTEN, THINK, SPEAK, FAULT, GAUGE }`. |
| **earcons** | `cues.cpp` / `cues.h` | **6** cues — `Heard Attend Refuse Fault Mode Boot`. Each is a `Note[]` (`{hz, ms}`) plus inter-note gaps, plus `kPrio[]` and `kGain[]`. Rendered to PCM **once** in `cuesBegin()`. `kCuesEnabled = false` today. |

Two corrections to the notes that cite these, both in our favour:

- **`face-config.md` and workplan 1.2d still say 48 band segments.** They are wrong; it is four
  strips, and the comment in the code says why (48 uniform opacity writes per frame during
  breathing was the freeze). **Segment count is not an authorable parameter, because there is no
  segment count** — the authorable band parameters are per-activity phase, period and depth.
- **Phase 0.2 is done.** `theme::reserved()` is no longer `state == 4 || state == 5`; it is
  `kStates[state].reserved`, with an out-of-range index treated as reserved so a bad index refuses
  to theme rather than theming a fault colour. That is the right shape and this schema depends on
  it.

**But the other half of 0.2 is still open, and it is the same bug.** `theme::stateColour()` is
`switch (state) { case 0: ... case 1: ... }` — the Halloween palette is indexed **by the state's
position in `kStates`**. Reserved is protected by name; the palette it protects is addressed by
ordinal. Insert a state and every themed colour shifts one row. This must be keyed by name before
a theme becomes a file, and it is the single cheapest prerequisite in this note.

---

## 1. The format: JSON, keyed by name, and never by ordinal

`stamplc-demo-audit.md` points at M5Stack's `trigger_relay` as the reference shape, and it is the
right reference — a small declarative vocabulary, serialized to the filesystem, selectable and
editable with no reflash, with the interpreter separated from the storage. Read for shape, it is
exactly what we want.

Read for **specifics it is a warning**, and the four defects are worth naming because three of them
are mistakes we are positioned to make:

| what the vendor does | why not to copy it |
|---|---|
| Serializes `Condition_t` / `Action_t` as **integer enum ordinals** (`"condition": 7`) | This is `state == 4 \|\| state == 5` in the persistence layer. Insert one condition mid-enum and every saved file silently means something else — and unlike the code, the files are not in the repo to be fixed. **Every key and every value in our schema is a name.** |
| A missing field yields ArduinoJson's default, usually `0` | `0` is benign there by luck (`Condition::False`, `Action::None`). For us `0` is `kEmotes[0]`, `kStates[0]`, `Activity::IDLE` — all plausible-looking wrong answers. **Absent must mean "fall back to the compiled default for this field", never "zero".** This is the gap GDTF's exhaustive-coverage invariant exists to close. |
| A parse failure returns an **empty** config | Failing safe by accident. Ours must fall back to the **compiled defaults** and say which table fell back and why. |
| `fprintf` then `fclose`, **in place, no temp file** — and configs are identified by a zero-padded index (`trigger_config_007.json`, shown to the user as `Config: 007`) | Non-atomic on a board whose power is cut by the contactor. And an index is not an identity: nobody can tell what `007` is. **Configs are named sets.** |

So: **JSON, via cJSON** (an IDF core component — no new dependency), **one file per table**, every
reference by name, parsed at boot into a POD `FaceConfig` and freed before the UI starts, per the
architecture note's rule 5.

### Schema

```
states.json     { "<STATE_NAME>": { "colour": "#RRGGBB" } }          non-reserved states only
emotes.json     { "<EMOTE_NAME>": { "wMul":…, "hMul":…, "radiusFrac":…,
                                    "lid":…, "browRaise":…, "browAngle":… } }
band.json       { "<ACTIVITY_NAME>": { "period_ms":…, "depth":…, "phase_deg":… } }
earcons.json    { "<CUE_NAME>": { "notes": [ {"hz":…,"ms":…}, … ],
                                  "gaps_ms": [ … ], "gain":… } }
themes/<name>.json
                { "palette": { "<STATE_NAME>": "#RRGGBB", … },
                  "accessories": [ "<VOCAB_NAME>", … ],
                  "band_flavour": "<name>" }
quotes.txt      one line each
manifest.json   { "schema_version": 1, "name": "…", "generation": N, "crc32": "…",
                  "files": { "<path>": "<crc32>" }, "cost": { … } }
```

Names, not indices, everywhere — `"DEGRADED"`, `"SQUINT"`, `"LISTEN"`, `"Fault"`. A name the
firmware does not know is a **warning that names the key**, and the entry is ignored; it is not a
parse failure, because a config authored against a newer vocabulary must still load the parts this
firmware understands. A name the firmware knows but the file omits keeps its compiled default.

**Emote parameters are authorable but not the primary path.** `face-config.md` decided selection is
primary; workplan 4.6 raises a third option from Furhat and Spot — **parametric clips**, where one
definition is scaled in amplitude and time rather than re-authored. That belongs in `emotes.json`
as a second, narrower form:

```
emotes.json     { "SQUINT": { "from": "SQUINT", "strength": 0.6 } }   parametric
```

`strength` scales the six floats toward `NEUTRAL`, which is a well-defined operation on this
particular table because `NEUTRAL` is the identity row (`1.00, 1.00, 0.205, 0, 0, 0`). It is
strictly safer than six free floats — it cannot leave the vocabulary's own envelope — so it should
be the documented way to vary an emote, with the raw six-float form available and unpolished. I am
not certain the interpolation reads well at intermediate strengths; the desktop build is where that
gets looked at, and it costs nothing to try.

### What is deliberately NOT in the schema

Per `face-config.md`'s Reserved tier, and implemented as **absence of a key** rather than as a
rejection, per the architecture note:

- `DEGRADED` and `E_STOP` have **no entry in `states.json`'s accepted key set** and no entry in a
  theme's `palette`. The loader iterates the compiled `kStates` and reads a colour only where
  `reserved == false`. A file naming `"E_STOP": {"colour": "#FF8800"}` is not "rejected" — the key
  is **never looked for**, so no future schema version can accidentally expose it, and
  `applyState()` reads the compiled table for reserved states unconditionally.
- `locked` — the attending-versus-reporting policy — has no key at all. Whether a state may emote
  freely is not a style choice.
- The band's **strip count** has no key, because the geometry is four edges.
- `Fault`'s gain has no key. `kGain[Cue::Fault] = 1.00` and the mute path cannot reach it; the
  exempt list is compiled, not authored. A muted robot shows a mute glyph so that muted is
  visible.

The distinction between "rejected" and "unparsed" is worth keeping sharp: a rejection is a code
path that can have a bug, and an absent key is not a code path at all.

---

## 2. On-flash: two slots, a generation counter and a CRC

### Correcting the architecture note

`tab5-face-architecture.md` §5.2 proposes `cfg, data, littlefs, 0xCB0000, 0x50000` and argues for
littlefs because *"a config write is write temp → rename, and littlefs makes the rename atomic
across power loss. SPIFFS does not promise that."* The reasoning is right. But the **table in the
tree is not that table**:

```
# firmware/tab5_idf/partitions.csv, as committed
nvs,               data, nvs,     0x9000,   0x5000,
otadata,           data, ota,     0xe000,   0x2000,
app0,              app,  ota_0,   0x10000,  0x500000,
app1,              app,  ota_1,   0x510000, 0x500000,
human_face_det,    data, spiffs,  0xa10000, 0x80000,
storage,           data, spiffs,  0xa90000, 0x560000,
```

One 5.5 MB **SPIFFS** `storage`, no `cfg`, no littlefs. And **the partition table cannot be changed
by OTA** — the architecture note says so itself. Moving to the proposed layout is a one-time USB
bench flash of a head that lives inside the robot on the e-stop-protected branch.

So the design must not make config correctness **depend on a filesystem migration**. It would be a
new instance of a mistake this project has already paid for: a correctness property that lives in
an environment rather than in the repo.

### The layout

Atomicity in the **application**, not delegated to the filesystem:

```
/storage/cfg/A/           one complete set: the JSONs above + manifest.json
/storage/cfg/B/           the other slot
/storage/examples/        shipped with the image: curated, cost-measured sets, read-only
NVS "face" namespace:     active_set (name), slot (A|B), theme, mute, vol
```

A write never touches the live slot. It fills the **inactive** slot, `fsync`s, writes that slot's
`manifest.json` last with a `generation` one higher than the live slot's and a CRC32 over every
file, `fsync`s again, and only then does NVS's `slot` key move. Boot picks the slot with the
**higher valid generation**, where "valid" means the manifest parses, every file listed is present,
and every CRC matches.

Why this shape:

- **It is filesystem-independent.** It is correct on today's SPIFFS and correct on the proposed
  littlefs, so adopting littlefs becomes an improvement rather than a prerequisite. littlefs's
  atomic rename then makes the manifest write itself atomic — a belt over the braces, not the
  braces.
- **The commit point is a single NVS key write.** NVS is log-structured with a CRC per entry and is
  already trusted with the tilt limits and IMU offsets, which are the one piece of genuinely
  unrecoverable per-robot state on this board. It is the most power-fail-safe thing on the device.
- **A torn write is a stale slot, never a mixed set.** The failure mode of losing power mid-update
  is "the robot came back with the previous configuration", which is the right answer and needs no
  recovery logic.
- **Two slots make a rollback free.** `CFG ROLLBACK` is one NVS write, which matters because the
  head's panel is the only place a bad theme is visible and the robot may be in front of an
  audience.

A full set is a few KB of JSON; two slots plus the examples fit the existing 5.5 MB with three
orders of magnitude to spare, so nothing here needs the 320 KB figure the architecture note
reserved. **Accessory artwork and audio clips are the thing that would actually consume that
partition**, and they are not in scope here — `face-config.md` leaves "may a theme add accessories,
or only select from pre-built ones" open, and this schema takes the narrow answer: `accessories` is
a list of names from a **compiled vocabulary**, so a theme file carries no geometry and no bitmaps.
Widening that is a separate decision with the cost gate attached.

### NVS is for selections only

`theme`, `mute`, `vol`, `active_set`, `slot`. A few bytes, changed by a tap, and they must survive a
configuration reset — a robot whose config was wiped should still come back muted if it was muted.
Bulk data stays on the filesystem; the architecture note's characterisation of NVS as "a few
re-derivable integers" stays true, with `slot` as the one exception, which is why it is a single key
and not a structure.

---

## 3. Validation, and what an invalid configuration does

**Validation is a gate, not hygiene.** The whole set is validated before any of it is applied;
there is no partial apply, for the same reason `cue.py` validates a whole show before sending any
of it — a partially-applied configuration is a face nobody designed.

`cfg_validate()` runs in exactly two places and is the **same function** in both: the boot path,
and the update path before a slot is committed. It takes a staging `FaceConfig` and returns a
`FaceConfig` plus a **verdict list** — one short string per thing it changed or refused.

### Fallback is per field, then per table, then per set

Three levels, finest first, so one bad number costs one number:

1. **Per field.** A value out of range is **clamped** to the bound and a verdict records it. A field
   absent or unparseable keeps the compiled default.
2. **Per table.** A file that does not parse at all, or whose CRC fails, falls back to that
   table's compiled defaults; the other tables still load.
3. **Per set.** A manifest that does not parse, or a `schema_version` this firmware does not know,
   drops the whole slot and boot tries the other slot, then the examples, then the compiled tables.

**The compiled tables stay in the firmware permanently.** A robot with no configuration at all is
fully functional, which is the right default for a machine students will rebuild, and it is also
the thing that makes "never bricks" true by construction rather than by care: the fallback path is
the path that exists when there is no filesystem.

### The failure table

| what is wrong | what happens | what the operator sees |
|---|---|---|
| no `/storage/cfg` at all | compiled defaults | `STATUS` lists every table `default` |
| both slots' manifests invalid | examples, then compiled defaults | `config: examples (cfg/A: manifest crc, cfg/B: manifest parse)` |
| one file's CRC fails | that table only falls back | `config: default emotes (emotes.json: crc)` |
| JSON syntax error | that table only falls back | `config: default band (band.json: parse at line 12)` |
| `schema_version` newer than the firmware | whole slot dropped, other slot tried | `config: slot B (A: schema_version 3 > 1)` |
| a key the firmware does not know | entry ignored, rest of the table loads | `warn: emotes.json: unknown emote "SMUG"` |
| a reserved key present (`E_STOP.colour`) | **never read** — not a code path | `warn: states.json: 1 key outside the schema` |
| a value out of range | clamped, rest applies | `clamp: emotes.SQUINT.radiusFrac 0.9 → 0.75 (max)` |
| more accessories than `kMaxAcc` | list truncated at 24 | `clamp: themes/halloween accessories 31 → 24` |
| a duration below the floor | raised to the floor | `clamp: band.IDLE.period_ms 40 → 120 (min)` |
| power lost mid-update | previous slot stays live | nothing; the update simply did not happen |

Every verdict is published in `STATUS` and rendered on the technician screen
([`tab5-dashboard.md`](tab5-dashboard.md)), which answers `face-config.md`'s open question about
surfacing failures: **the author sees the rule they broke, named, on the robot**, and the editor is
a convenience on top of that rather than the only place the information exists.

### Where the bounds come from — and the one that must not be quoted

The bounds are compile-time constants sitting **next to the compiled defaults**, so a default and
its range cannot drift apart. `tab5-platform.md` §6b is the source, and the distinction the
workplan insists on is load-bearing here:

- **The cost *hierarchy* is solid and is what the bounds are derived from**: the eye body dominates
  the frame; a resize dirties old area plus new; complex gradients are cached per geometry; a
  uniform per-frame write across many objects is worse than a travelling one across few (which is
  the 48-segments-to-4-strips finding); a static object is never invalidated and therefore free.
- **The absolute ~16 Mpx/s ceiling is retracted** — it was measured on a primitive-rasterisation
  workload and applied as a fill/blit ceiling, and the device then ran happily while the gate
  claimed 532% over. `LV_USE_PERF_MONITOR` is on now, so the device reports its own FPS.

So the cost gate's role in this design is **comparative**: a candidate set is measured against the
currently-shipping set and the examples, and `manifest.json`'s `cost` field records the figure so a
shipped example carries evidence rather than a hope. It must not emit a pass/fail against an
absolute number until that number is re-established on a fill/blit workload. A gate that says
"this set invalidates 2.3× what the shipping set does" is useful and honest; one that says "532%
over budget" was neither.

The concrete bounds, each with its reason:

| parameter | bound | why |
|---|---|---|
| `wMul`, `hMul` | eye body ≤ panel − band (16 px/edge) − margins | a resize dirties old plus new, and the body already spends most of the frame |
| `radiusFrac` | ≤ 0.5 of `min(w,h)` | above that it is a circle and the corner geometry degenerates |
| `browAngle` | a cap well short of a rotation that induces a layer | a rotated object renders through a layer, which allocates per draw; the rotated brow asked for 73 KB once |
| `lid`, `browRaise` | the table's own closed range | they are normalised already |
| accessory count | ≤ `kMaxAcc` (24) | existing constant; objects over an animating region are recomposited |
| `band.*.period_ms` | a floor | a short period means more frames of change, so more invalidations |
| `band.*.depth` | 0..1 | opacity |
| colours | never sprung, no overshoot | an RGB overshoot lands on a different hue, which is how a state change once flashed through other states |
| earcon total duration | a cap, and ≤ the PCM budget | each cue is rendered to PCM at startup and held |

---

## 4. Selection and update

### Selection is cheap, live, and by name

`THEME <name>`, `MUTE`, `VOL n`, `SET <name>` arrive as UDP line commands — the protocol that
already exists — are applied by the UI at its next tick, and are persisted to NVS by `main`.
Live-safe because a selection changes only colours, opacities and hidden flags on objects that
already exist. **No allocation**, which is AUDIT.md invariant 6 and the reason selection is the
primary path rather than merely the recommended one.

`SET <name>` selects among the sets already on flash — the two slots and the shipped examples. This
is the vendor's load-by-index, fixed: named sets, listed by `STATUS`, so a person can tell
`classroom` from `demo-day` without loading it to find out.

### Authored data is pulled, not pushed

**`CFG <url>`.** The head fetches a tarball or a single JSON over HTTP from the Thor, writes it into
the inactive slot, runs the same `cfg_validate()`, and commits only on a clean verdict. This
mirrors `ota_pull` and `ASSETS <url>`, and it is a pull for three reasons that are specific to this
board rather than general:

1. **The line-based UDP command link must not be stretched to carry a payload.** It carries
   `STATE <NAME>`, `LIFT h m f` and pan/tilt rates at 20 Hz, and the pan/tilt path is the one that
   must not stall while an operator is aiming. `face-config.md` already says this and it is right.
2. **The head loses power with the contactor.** A push has to be resumed by the pusher; a pull is
   retried by the only party that knows whether it has a valid set — the head. An interrupted pull
   leaves a half-written inactive slot, which the generation-and-CRC rule already discards.
3. **One mechanism, not two.** `ota_pull` exists and works; `cfg_pull` is the same socket, the same
   staging-then-commit shape, and the same `net` module.

**The dashboard is the editor; the head is the owner.** The repo holds the defaults and the examples
under `firmware/tab5_idf/config/` (which does not exist yet — it is created by this work), packed
into the image by `build.sh` with each example's measured cost checked in beside it. The robot's own
authored set lives on the robot and is never committed, per `per-robot-values-not-in-repo`.

### What applies live and what waits for a boot

This is the part most likely to be got wrong, because it is tempting to make everything live.

| change | when | why |
|---|---|---|
| theme selection, mute, volume | **live** | restyles existing objects; no allocation |
| non-reserved state colours | **live** | a colour write on an existing object |
| band period / depth / phase | **live** | they are parameters of the existing four strips |
| emote parameters | **live** | the six floats are read per emote application; geometry is recomputed, objects are not created |
| accessory **selection** within a theme | **live** | the vocabulary's objects are built on theme activation, which is already a thing that happens at runtime |
| anything changing **object count** | **next boot** | "nothing allocates in the steady state" (AUDIT 6). A set that adds an accessory kind rebuilds the scene, and the scene is built once |
| **earcons** | **next boot** | `cuesBegin()` renders each cue to PCM and holds it. Re-rendering live is an allocation in the steady state, and the PCM is the largest thing the audio path owns |
| `schema_version` change | **next boot** | the loader's shape changed |

The `CFG` reply states which category the change fell into, per field. A command that silently
deferred half of what it was asked to do is how an author concludes the panel is broken.

---

## 5. Routines: the slot, not the format

`tab5-face-architecture.md` reserves `/cfg/routines/` and a `ui_choreo` timer. Keep the reservation
and keep the format undecided, because the format belongs to
[`../workplan-choreography.md`](../workplan-choreography.md) and deciding it here would fork it.
Three things this note can settle, because they are properties of the head rather than of the
format:

**The clock is not here.** Workplan 2.1: the Tab5 is on the e-stop-protected branch, so a clock on
the head dies with the contactor, and *a clock that dies with the e-stop cannot be the thing that
notices the e-stop*. The Thor holds the show clock. A face routine is therefore either
(a) wholly local and self-contained — a looping idle flourish that needs no coordination — or
(b) driven by `FACE` cues arriving from the Thor's runner at their scheduled times. Not a third
thing that keeps its own timeline and hopes the two agree.

**A routine may request, never lease.** `docs/control-authority.md` is the specification and this
note may not amend it. A face routine may set expression, gaze and band activity, which move
nothing. If it wants the mast it goes through the same intent path as a touch — `MAST CMD`, 250 ms
expiry, pinch guard, the arbiter — with the same preemption-aborts rule. The face does not get its
own channel to anything that moves.

**The slot vocabulary already exists and is already richer than the verbs.** `software/choreo/cue.py`
has `FACE`, `SOUND` and `LIGHT` in `SLOTS` with **no validators** (workplan 0.3), and the head's
command surface today is `STATE <NAME>` and `TRACK …` — no emote, no theme, no band activity, no
gaze. So the honest ordering is: this schema first (it names the vocabulary a validator would check
against), then the verbs (workplan 3.3), then `_check_face` in `cue.py`. A `FACE` slot that can only
set state is an indicator, not an output channel — and a validator cannot be written before the
thing it validates has names.

---

## 6. What is uncertain, and what would settle it

- **Does parametric `strength` interpolation read well?** Scaling the six floats toward `NEUTRAL`
  is well-defined arithmetic; whether `SQUINT` at 0.6 looks like a weaker squint or like a
  different, worse expression is a judgement. **Settled by:** the desktop build, side by side, in
  an afternoon. This project has twice invented emote geometry that was worse than the original, so
  the prior is against anything that generates expressions.
- **Is one file per table right, or one file per set?** One file per table gives per-table fallback,
  which the failure table above depends on. One file per set is easier to hand-edit and to diff.
  The manifest-plus-CRC design works either way, and I have assumed per-table because the
  granularity of fallback is the thing that matters on a robot. **Settled by:** whoever authors the
  second theme — if they find themselves editing four files to make one change, it is wrong.
- **Whether the examples belong in `storage` or in their own read-only partition.** Putting them in
  `storage` means a filesystem corruption can take the examples *and* both slots, leaving only the
  compiled defaults. That is still a working robot, so it is survivable, but it is less good than
  the architecture note's split. **Settled by:** whether the `cfg`/`assets` split happens at the
  USB bench flash. If it does, examples go in `assets` and nothing else changes.
- **The cost gate's absolute budget.** Retracted, and nothing in this design should re-derive one.
  **Settled by:** a fill-and-blit benchmark on the device with `LV_USE_PERF_MONITOR` reporting,
  which is a Tab5-thread task and not this one's.
- **Whether `kCuesEnabled = false` means the earcon schema is speculative.** It is compiled out
  today. The note list is still the right data model for it, but nothing in this note has been
  exercised against a cue that actually sounds. **Settled by:** enabling the cues, which is a
  separate decision with its own gain tuning.

### The prerequisite, restated

**`theme::stateColour()`'s `switch (state)` must be keyed by state name before any of this
ships.** Reserved is protected by name; the palette is still addressed by ordinal. Everything above
assumes names all the way down, and one `switch` on an index is enough to make a theme file mean
something different after someone inserts a state — which is precisely what an editor does.
