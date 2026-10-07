# The face twin — intent

**Status: agreed by the project team, 2026-10-07** (choreography plan item 3.1). Items 3.2–3.4
build against it. Everything stated as fact here was read from the Tab5 code on
`feat/tab5-idf-subtractive` on 2026-10-07 and is cited by file.

The face twin is the Tab5 face app (`firmware/tab5_idf/app_face/`) compiled for a laptop — the
existing desktop SDL build (`firmware/tab5_idf/build.sh desktop`) — grown into three things at once:

1. **a digital twin of the head** — the same face, driven by the same inputs, judged on a laptop;
2. **a choreography player** — it plays a show's `FACE`, `LIGHT` and `SOUND` cues in step with the
   composer, beside the MuJoCo body twin;
3. **the customisation tool** — where themes, palettes, accessories and background images are
   authored, previewed and validated before the real head loads them.

---

## Decisions — agreed 2026-10-07

| # | Question | Decision |
|---|---|---|
| FT-1 | **Is the twin the real app, or a re-implementation?** | **The real app, always.** The twin is `app_face` compiled for SDL, never a browser or Python copy of the face. A second implementation drifts; the reason to have a twin is that it *is* the face. |
| FT-2 | **How does the twin receive commands?** | **The head's own protocol.** The desktop build gains a real listener for the head's UDP line protocol, bound to localhost, so a command that works on the head works on the twin unchanged. Today the whole network layer is stubbed on desktop (`net.cpp:811-827`). |
| FT-3 | **How does the composer drive it?** | **Through a bridge, not from the composer.** A bridge process follows the composer's twin feed (`/api/twin`, the one the body twin uses) and sends the head's command lines to the twin. The composer keeps no robot-shaped output path, and the bridge is the first draft of the runner — so the twin is also the runner's test target. |
| FT-4 | **New head verbs.** The head accepts `PING`, `STATE`, `TOF`, `I2CSCAN`, `REBOOT`, `OTA` and a pan/tilt rate frame — no emote, theme, sound, band, gaze or config verb (`net.cpp:579-750`). | **All four, on head and twin together:** `EMOTE <name>`, `THEME <name>`, `CUE <earcon>`, `CFG <url>` (pull a configuration, per `face-config-data-model.md`). Band and gaze verbs wait for the Tab5 plan's F1/F2. |
| FT-5 | **Where themes are authored.** | **Text files first,** previewed live in the twin. A theme is a folder under **`firmware/tab5_idf/themes/`**: a JSON file in the schema `face-config-data-model.md` specifies (keyed by state name; reserved states absent) plus its image assets. A validator checks it in CI the way `cue.py` checks a show; the twin reloads on save. An editing UI may come later and writes the same files. |
| FT-6 | **Background images.** | **Pre-rendered offline** to the panel's native format, so the head decodes nothing (its PNG/JPEG decoders are off, and the device build reads Kconfig, not `lv_conf.h` — `tab5-platform.md` §6a0). **Storage is open between two places:** the unused 5.5 MB `storage` partition, and **the Tab5's SD card**, which is not implemented yet but would hold far more and could be swapped. Cost is measured comparatively on the twin before full-screen backgrounds are allowed (see §3). |
| FT-7 | **Simulated inputs.** | **Keyboard first,** in the twin window, with the same inputs settable by command so the bridge or a script can drive them. Each is labelled as simulated on screen. A panel may come later. |
| FT-8 | **Three Tab5 issues found while writing this.** | **Resolved in the Tab5 work plan** (branch `feat/tab5-idf-subtractive` at `0b04dfe`): the theme toggle was **already gated** (`afd3153`) — this note read a stale copy; state checks now test **named properties** (`stateFault()`, `stateCharging()`) instead of numbers, with `fault` kept deliberately separate from `reserved` (one is behaviour, the other styling); and the no-link face was **wrong on the head too**, not only on the desktop — boot now shows NO LINK until the robot is heard from. **For the twin work:** every `kStates` row now needs `fault` and `charging` fields, and the no-link fix lives in shared code, so the twin must not re-fix it. |

---

## 1 · The digital twin

### What is the same

The twin runs the same code, so it has the same face: the seven-state table with its colours,
pinned emotes and reserved flags (`app_face.cpp:293-306`); the five emotes (`:212-222`) and their
random cycling on unpinned states; eased saccades, perception-driven gaze and blinking; the quotes;
the themes and their accessory layer; the settings screen; and the HMI.

### What it needs in order to be useful

The head reacts to the robot. A laptop has no robot, so the twin needs **simulated inputs**
(FT-7), each visibly labelled as simulated:

| input | on the head | in the twin |
|---|---|---|
| robot state | `STATE` from the Thor, StamPLC telemetry on 5005 | `STATE` on the localhost listener, or a key |
| link up / down | the state feed arriving or going stale (5 s) | a key, or stop sending |
| e-stop | StamPLC telemetry → `E_STOP` | a key |
| a person to look at | camera → face detection → gaze | the mouse position as a fake detection |
| ToF distance | the front sensor | a slider or key |
| pan/tilt | servos (slewed at 25 °/s) | not drawn by the face twin; the MuJoCo body twin owns the head's pose |

### What it cannot tell you

Stated so a twin result is never read as a robot result (the Tab5 plan's standing rule 1):

| the twin cannot reproduce | why |
|---|---|
| **LVGL lock and threading bugs** — the class behind the longest freeze | on desktop LVGL runs single-threaded (`AUDIT.md:217-221`) |
| **the frame budget, CPU load, layer-allocation failures** | a laptop has far more of everything |
| **absolute rendering cost** | only *comparative* dirty area transfers between machines (`face-config.md`) |
| **panel colour and brightness** | it is a laptop screen |
| **fonts and image support** | the device ignores `lv_conf.h` and builds from Kconfig, so the two configurations differ (`tab5-platform.md` §6a0) |
| **NVS persistence, network, OTA, ToF, pan/tilt, camera, face detection** | stubbed on desktop |

**The rule:** the twin is evidence about appearance and behaviour logic. It is never evidence about
timing, cost or anything device-only, and its cost readings are comparisons, not budgets.

---

## 2 · The choreography player

The show format already has the slots: `FACE` (`do: emote, name: …` from the five firmware emotes),
`LIGHT` (`do: color`, the PRISM base colour) and `SOUND` (`do: play, sound: …`, the Tab5's six
earcons or a sound file). Today all three are *valid but not executable* — nothing receives them.
The twin becomes their first receiver.

**How a show reaches it (FT-3):**

```
composer ──/api/twin (time, play state, timeline)──▶ bridge ──head command lines──▶ face twin
                                                        └─ the same lines, later, from the runner, to the real head
```

The bridge evaluates the timeline at the playhead like the body twin does. As the playhead crosses
a cue it sends that cue's command; on a seek it sends the state at that time (the current emote,
light and theme), so jumping around a show leaves the face correct. Sounds are sent only while
playing, never on a seek.

**What each slot does in the twin:**

| slot | in the twin | needs |
|---|---|---|
| `FACE` | sets the emote | `EMOTE` verb (FT-4) |
| `SOUND` | plays the earcon through the laptop's audio — if the desktop HAL's audio works, which is unverified | `CUE` verb (FT-4) |
| `LIGHT` | the PRISM light strip is not part of the face; the twin shows it as a labelled swatch so a show's lighting can be judged | a twin-only indicator |

**Showing it with the body.** The plan's 2.5 stretch goal — the face drawn onto the head of the
MuJoCo model — follows naturally: the twin can write frames that the body twin maps onto the
panel. Not part of this first step.

---

## 3 · The customisation tool

### What can be customised, by tier

`face-config.md` settled the tiers; this applies them.

| tier | what | rule |
|---|---|---|
| **Reserved** | `DEGRADED` and `E_STOP` colours, the fault band, which states attend and which report | not authorable. The firmware **rejects** a theme that sets them, whatever the editor allowed |
| **Bounded** | colours of non-reserved states, the accessory layer, background images, emote selection (and later two scaling dials — `face-config.md`'s "curated vocabulary"), earcon set, band style once the band returns | validated and clamped against the cost hierarchy |
| **Free** | quotes, names, which theme is active | anything well-formed |

### A theme, concretely (FT-5)

Themes live in `firmware/tab5_idf/themes/`, beside the code they configure:

```
firmware/tab5_idf/themes/halloween/
  theme.json        palette keyed by state name, accessories, background, quotes
  background.bin    pre-rendered for the panel (FT-6), with the source image beside it
```

The JSON follows `face-config-data-model.md` (keyed by name; reserved states absent; per-field,
per-table and per-set validation; A/B storage slots with a generation counter on the head). A
theme validator — the face's equivalent of `cue.py` — runs in CI. The twin watches the folder and
reloads on save. Deploying to the head is `CFG <url>`: a pull at startup or on command, never a
stream over the StamPLC's UDP link.

### Background images

New to the face, and worth doing deliberately:

- **Pre-rendered** to the panel's pixel format offline (FT-6), so the head decodes nothing.
- **Stored** either in the head's unused 5.5 MB `storage` partition (`partitions.csv:28`), as the
  data model proposes for configuration, or on **the Tab5's SD card** (not implemented yet). The
  partition is simpler and always present; the card holds far more and can be swapped. Decide with
  step 3.4b, once one background's size and cost are known.
- **Cost is the open question.** The eyes animate over the background, so every frame redraws the
  background under them. `tab5-platform.md` §6b found "static is free" only for objects that do
  not overlap animation. A background image may therefore cost real frame time on the head, and
  the twin can only *compare* it. Measure with the cost gate on the twin first, then confirm on the
  head before allowing full-screen backgrounds.

---

## Proposed sequence

Each step states its question and kill criterion, mapped onto the plan's 3.2–3.4.

| step | question | done when / kill criterion |
|---|---|---|
| ~~**0**~~ | ~~FT-8's three issues~~ **DONE in the Tab5 work plan, `0b04dfe`** | — |
| **3.2a** | Can the desktop build listen on localhost and obey the head's existing verbs? | `STATE` and `PING` work on the twin, unchanged |
| **3.2b** | Twin input panel (FT-7) | every input in §1's table can be simulated, and is labelled as such |
| **3.2c** | New verbs `EMOTE`, `THEME`, `CUE` on head and twin together (FT-4) | the same line changes both, verified on the twin, then on the head |
| **3.3** | Bridge: composer → twin | `FACE`, `SOUND` and the `LIGHT` swatch play in step with the body twin; a seek leaves the face correct |
| **3.4a** | Theme files + validator + live reload | Halloween re-expressed as files, rendering identically to today's compiled version |
| **3.4b** | Background images, and where they are stored (partition or SD card) | one background on the twin, with its comparative cost recorded; kill if it more than doubles the face's dirty area |
| **3.4c** | `CFG` delivery to the head | a theme authored on the twin loads on the head and looks the same; a theme touching a reserved state is rejected *by the head* |

## Not decided here

- **What an emote means** and **what the band means** — the Tab5 plan's F1 and F2. The twin will
  play whatever vocabulary those settle.
- **Whether the face appears on the MuJoCo head** (plan 2.5).
- **An editing UI for themes.** Text first; a UI is a later convenience that writes the same files.
