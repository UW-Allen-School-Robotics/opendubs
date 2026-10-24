# The head protocol

**Status: version 9, 2026-10-22** — version 1 agreed with the Tab5 head lane; version 2 adds `SKIN` (tablet face only), agreed with the Choreography lane; version 3 adds `SPIN` (tablet face only), agreed with Face & HRI and Choreography; version 4 adds `MOVE` (tablet face only), proposed by Face & HRI for Choreography's shows; version 5 adds `VOLUME`, `BRIGHT` and `ARMED` (tablet face only), agreed with Choreography. Version 6 adds `BATT` (tablet face only), agreed with Face & HRI. Version 7 adds `CHEST` (the neck's chest light), agreed with Face & HRI. Version 8 adds `CAPTION` (tablet face only), agreed with Face & HRI. Version 9 adds `HOLD` and `BADGE` (the neck Dial's hold and status screen, the user's design of 2026-10-22; `head-ownership.md`), and the Dial now answers `TOUCH`. Tablet plan items 2.1 and 2.2
([`workplan-tablet.md`](../workplan-tablet.md)). The messages marked **shipping** are what the
ESP-IDF Tab5 head answers today, read from its source on `main` (`d6d2e4c`). The four marked
**new** — `TRACK`, `NECK`, `TOUCH`, `PARK` — and `kind=` in `PING` were **agreed with the Tab5 head
lane on 2026-10-08**, after one correction to the expiry rule (§4) and four definitions pinned
(§4, *What a neck reports*). They are not built yet. The messages marked **retired** were decided
2026-10-08.

**Who owns this document.** The Tablet and head interface lane owns the protocol, meaning the
messages the Thor, the dashboard and the StamPLC send the head. The Tab5 head lane implements it in
firmware. A new message is agreed between them before either side ships it.

**Why it exists.** The robot's head may be the **Tab5** or, under the tablet plan's Part 2, a
**tablet face with a separate neck controller**. The dashboard, the show runner, the YOLO tracker
and the StamPLC must not care which is fitted. They agree on this protocol, and each head
implements it.

---

## 1. The head's two halves

| half | what it does | the Tab5 head | the tablet head |
|---|---|---|---|
| **face** | what shows: state, emotes, themes, earcons | the Tab5 | the tablet: its kiosk app's network listener (*the door*) passes lines to the face engine |
| **neck** | what moves: pan/tilt, and the front ToF | the same Tab5 | the neck controller (an M5 board in the head) |

Each half has its own address, set per robot in `/etc/opendubs/robot.env`:

| setting | default |
|---|---|
| `OPENDUBS_HEAD_FACE_HOST` | `OPENDUBS_TAB5_HOST` if set (the old name), else `opendubs-tab5.local` |
| `OPENDUBS_HEAD_NECK_HOST` | the face's host |

So with the Tab5 fitted both halves are one address, and nothing has to be configured. **Every
sender resolves the two in this order** (`software/kiosk-dashboard/backend/head.py`).

## 2. Transport

- **UDP, port 4242**, on each half.
- **One message per datagram**: ASCII, case-sensitive verbs, at most 255 bytes. A trailing `\n`
  or `\r\n` is stripped, so senders may add one.
- **Every text message gets exactly one reply**, sent to the datagram's source address and port,
  at most 383 bytes. A reply starts `OK`, `ERR`, `PONG`, or (for `TOF`, `I2CSCAN` and
  `VOICE STATUS`) a free-text report.
- **The one binary message** (§5, `0x107`) gets no reply.
- **Unknown or retired messages** are answered `ERR unknown`. That is how a head refuses a message
  it does not implement, and how a sender learns which head it is talking to.
- **Leases.** Anything that moves the head carries a lease in milliseconds and is renewed by its
  sender, typically every 200 ms. When a lease runs out, that source stops counting. A sender
  that stops sending (a crash, a lost network, a lifted-off tablet) therefore stops commanding
  within one lease, with no message needed.
- **No authentication.** The robot's network is the first protection (security is parked as P1
  in the choreography plan).

## 3. Face messages

| message | reply | status | notes |
|---|---|---|---|
| `PING` | `PONG …` (§6) | shipping | Read-only. Answered by **each** half. |
| `STATE <name>` | `OK state <name>` | shipping | The robot's state, from the dashboard: `INIT`, `SAFE_IDLE`, `MANUAL`, `AUTONOMOUS`, `DEGRADED`, `E_STOP`, `CHARGING`. The face also hears the StamPLC's own telemetry broadcast (§7), which is the authority for e-stop and mode. |
| `EMOTE <name>` / `EMOTE AUTO` | `OK emote <name>` / `ERR unknown emote` | shipping | `NEUTRAL`, `ALERT`, `SQUINT`, `SOFT`, `SLEEPY`, case-insensitive. `AUTO` hands the face back to perception and its own cycling. A reserved state (`DEGRADED`, `E_STOP`) pins its own emote whatever is asked. |
| `THEME <name>` | `OK theme <name>` / `ERR unknown theme` | shipping | Built in: `plain` (alias `NONE`) and `halloween`; plus any theme in the robot's content folder (`face/themes/<id>.json`, *Discovering the looks* below) and any authored theme installed by `CFG`. No theme can recolour `DEGRADED` or `E_STOP`. **`THEME IDLE`** (version 5, tablet face only, agreed 2026-10-13 with Choreography) → `OK theme idle <name>`: back to the robot's idle theme (`plain` without one), sent at every show end with `SKIN IDLE`. No theme may be called `idle`. The Tab5 answers `ERR unknown theme`, which the runner expects. |
| `CUE <earcon>` | `OK cue <earcon>`, or `OK cue <earcon> (AUDIO OFF -- …)` / `ERR unknown cue` | shipping | `heard`, `attend`, `refuse`, `fault`, `mode`, `boot`. Audio is a per-robot setting that defaults **off**. The reply says so, because an acked cue that nobody hears is otherwise unexplainable. |
| `CFG <url>` | `OK …` / `ERR …` (a verdict) | shipping | Fetch an authored configuration (today: themes as palettes, per `face-config-data-model.md`). On the tablet head, "fetch this library item now". |
| `SKIN <name>` | `OK skin <name>` / `ERR unknown skin <name> (showing default)` / `ERR skin <name>: <why> (showing default)` | **version 2, agreed** (with the Choreography lane, 2026-10-08), tablet face only | Which skin draws the eyes: a code skin — `default` (the standard eyes, an ordinary argument), `ring`, the other built-ins, or one in the robot's content folder (`face/skins/<id>.js`, *Discovering the looks* below) — or a library skin's id. Persistent, like `THEME`. **Never a blank face:** an unknown name, or a library skin whose file will not load, shows `default` and says so; `PING` then reports `skin=default skin_error=…`. A reserved state draws `default` whatever is set. A library skin may not use a code skin's name. The Tab5 answers `ERR unknown`. **An unknown name gets a second look first**: the face re-reads its library (at most every 2 s, for at most 250 ms) and shows the skin if it has just arrived, so a skin added after the face loaded is not reported unknown. **`SKIN IDLE`** (version 5, agreed 2026-10-13 with Choreography) → `OK skin idle <name>`: back to the robot's idle skin (the Head & face page; `default` without one). The runner sends it at **every show end** — complete, stop, cut, abort — with `THEME IDLE`, `VOLUME IDLE`, `BRIGHT IDLE` and `EMOTE AUTO`, whether or not the show changed it. `IDLE` is therefore a reserved name: no library skin may be called `idle`. |
| `SPIN COUNTER [hold_s]` / `SPIN TOGETHER` / `SPIN AUTO` | `OK spin <counter\|together\|auto>` / `ERR spin COUNTER [hold_s] \| TOGETHER \| AUTO` | **version 3, agreed 2026-10-11** (with Face & HRI and Choreography), tablet face only | A spinning skin's **counter-rotation on a beat**. `COUNTER` breaks the eyes into opposite spins (held `hold_s`, or until `TOGETHER`, `AUTO` or the show's end); `TOGETHER` returns them; `AUTO` hands the schedule back to the library entry's `counter` block. **While a show holds it, the skin's random counter-spins are suppressed.** The **ease belongs to the skin** (`ease_s`, default 0.9 s) and cannot be set from the wire, so a show cannot make the reversal snap. **The cue's time is the zero crossing** — both eyes slow to a standstill together and spin up the other way, and that pause is what lands on the beat; the eyes reach it `ease_s / 2` after the line arrives (450 ms by default), so the runner sends it that much early. A skin that does not spin answers `OK` and does nothing. The Tab5 answers `ERR unknown`. |
| `MOVE <name>` | `OK move <name>` / `ERR move <name>` | **version 4, agreed 2026-10-11** (proposed by Face & HRI, for Choreography's shows), tablet face only | A **one-shot gesture**: it plays, then the face returns to whatever emote is held. It does not touch `EMOTE`'s pin, so `EMOTE SOFT` then `MOVE wink_l` is a wink while soft, and `AUTO` is unaffected. Names, case-insensitive, answered lowercase: `wink_l`, `wink_r`, `blink2`, `look_l`, `look_r`, `roll`, `bob`, `sparkle`, `side_eye_l`, `side_eye_r`, `to_camera`, `double_take`, `startle`. Most moves shift the eyes from wherever they were; `to_camera` instead goes to a fixed place, straight out at the viewer, and then returns. Moves act on the eyes only, never the neck: `to_camera` overrides where the eyes look, the face's own tracking included, for its duration, then hands it back, and no move changes who owns the head. **No parameters:** the face owns each move's timing, as a skin owns `SPIN`'s ease, so a show cannot make a gesture snap. **The cue's time is the move's peak** (a wink's lid fully shut, the bottom of a bob): the face publishes each move's offset from receipt to peak, **measured**, in the table below before it ships the move, and the runner sends the line that much early. A new `MOVE` **replaces** the one playing; it does not queue, so a run of beats stays predictable. An **unknown** name is `ERR move <name>`, so a typo in a show is loud; a **known** name a skin cannot draw (today `sparkle` on an asset skin) is `OK` and does nothing, so a show never fails because a different skin was chosen. Not a `CUE`: a cue says something happened and the face chooses how to react; a move says do this now. The Tab5 answers `ERR unknown`. |
| `VOLUME <0..1> [fade_s]` / `VOLUME IDLE [fade_s]` | `OK volume <applied>` / `OK volume idle <applied>` / `ERR volume <0..1> [fade_s] \| IDLE [fade_s]` | **version 5, agreed 2026-10-13** (with Choreography), tablet face only | The face device's **media volume** (it scales show music, ambient and earcons together; show music itself stays at full gain). Faded **on the device** over `fade_s` (0 = a step, at most 30 s), so a cue is one line at its time. **Clamped on the device** to the robot's `vol_max`; the reply carries the **applied** value. `IDLE` returns to the robot's idle setting (the Head & face page). The runner sends `VOLUME IDLE` at every show end, however it ends; the face also returns to idle by itself when its door connection drops. The Tab5 answers `ERR unknown`. |
| `BRIGHT <0..1> [fade_s]` / `BRIGHT IDLE [fade_s]` | `OK bright <applied>` / `OK bright idle <applied>` / `ERR bright <0..1> [fade_s] \| IDLE [fade_s]` | **version 5, agreed 2026-10-13** (with Choreography), tablet face only | The face's **screen brightness**, as `VOLUME`: faded on the device, `IDLE` at every show end and on a lost door. **Clamped up to the robot's `bright_min`** (default 0.15), so a show can never black the face out by mistake; the reply carries the applied value, and Choreography's validator warns below 0.15. |
| `ARMED WAIT` / `ARMED <seconds>` / `ARMED OFF` | `OK armed wait` / `OK armed <n>` / `OK armed off` / `ERR armed WAIT \| <1..60 seconds> \| OFF` | **version 5, agreed 2026-10-13** (with Choreography; the look is Face & HRI's), tablet face only | **A live show is armed**, so people near the robot can see it. `WAIT`: armed, waiting for the operator's deadman; **leased 3 s**, re-sent about every second, so a runner that dies cannot leave the face saying "armed". `<seconds>`: sent at the deadman's rising edge, a countdown to the start (default 5 s) on the face's own clock, which ends by itself. `OFF`: started, aborted, timed out or disarmed. Drawn over **any** skin, like a reserved state, and never mistakable for E_STOP or DEGRADED. Sent only on live runs (`LiveFace`). |
| `BATT <soc> <charging>` | `OK batt <soc\|-> <0\|1>` / `ERR batt <0..100\|-> <0\|1>` | **version 6, agreed 2026-10-15** (Tablet lane with Face & HRI, who draw the gauge on the edge ring), tablet face only | **The pack's charge.** `soc`: the BMS state of charge, a whole percent 0–100, or `-` with no reading. **Out of range is refused (`ERR`), never clamped**: a 150 % reading is a bug upstream, and the face is where someone might notice it; the dashboard sends `-` for one and logs it. `charging`: `1` while current flows **into** the pack, else `0`. The BMS cannot tell a full pack on the charger from one unplugged, so a full pack still on the charger reads `0`. **The STATE decides whether the gauge shows, BATT what it says:** the gauge shows only in `STATE CHARGING`; `charging` picks the word ("Charging 97 %" vs "On charge 100 %", never "Charged"). Sent by the dashboard on a change (a whole percent, or `charging`), **every 30 s as a guarantee** (the face drops the number, keeping the gauge, from a reading older than 90 s), and to a face page as it connects. **Related:** a charging robot that would otherwise show `SAFE_IDLE` is sent `STATE CHARGING` (E_STOP, DEGRADED and INIT win), presentation only (`state_out.present`); the PRISM breathes the same purple, (109, 48, 224). The Tab5 answers `ERR unknown`. |
| `CAPTION <text>` / `CAPTION OFF` | `OK caption` / `OK caption off` / `ERR caption <text, at most 60 characters> \| OFF` | **version 8, agreed 2026-10-16** (Tablet lane with Face & HRI, who draw it: `skins/caption.js`, white words on a panel, never the state's colour), tablet face only | **The robot's state in plain words**, for people who don't know the colour code. `text` is UTF-8, the rest of the line, at most 60 characters: longer is refused, never cut. The face shows what it is sent and never decides when; how it looks and where it sits is the face's. The **dashboard** owns the words (`state_words.py`): INIT "Starting up", SAFE_IDLE "Ready", MANUAL "Manual control", AUTONOMOUS "Running a show", CHARGING "Charging 95%" (the % from the BMS), E_STOP "Emergency stop", and DEGRADED "Problem: <what>", the real reason in a bystander's words ("RC transmitter not connected", "lift not responding", "running too hot"). It owns when, too: the Head & face setting `state_words` is `always`, `hide_auto_manual` (the default: no words while the robot is driven or running by itself; a fault and an e-stop are still said) or `off` (`CAPTION OFF`). It applies live. Sent with every `STATE` line (on a change and the 3 s refresh), at once when the words change, and to a face page as it connects. **The face drops a caption not renewed within 10 s**, so a stale "Ready" never outlives the dashboard. Shown in the reserved states too: that is where it matters. The Tab5 answers `ERR unknown`. |
| `VOICE PRESS` / `RELEASE` / `STATUS` / `THOR <host> <port>` | `OK …` / a status line / `ERR VOICE {…}` | Tab5 only | Push-to-talk. Compiled into the twin, **out of the shipping image**, and **paused** (decided 2026-10-08). A tablet head answers `ERR unknown` until a voice decision says otherwise. |
| `SIM LINK\|ESTOP\|PERSON\|TOF …` | `OK sim …` / `ERR SIM …` | twins only | Simulated inputs for the face twin. A real head refuses them rather than pretending. |


**`MOVE` peak offsets** (receipt → peak, seconds; measured on the face engine, as `SPIN`'s 0.450 was). Measured by Face & HRI (PR #196). The engine keeps them as `MOVES[name].peak` in `face.js`; its `test_moves.mjs` re-measures every run and fails if a move's curve and its number drift apart by more than a frame, and `test_head_protocol.py` fails if this table and `face.js` differ.

| move | peak | what peaks |
|---|---|---|
| `wink_l`, `wink_r` | 0.139 | that eye's lid fully shut |
| `blink2` | 0.333 | the second blink's lids shut |
| `look_l`, `look_r` | 0.231 | the gaze at its furthest |
| `roll` | 0.148 | the eyes at the top of the roll |
| `bob` | 0.138 | the bottom of the bob |
| `sparkle` | 0.124 | the sparkle at its brightest (code skins only) |
| `side_eye_l`, `side_eye_r` | 0.198 | the sideways glance arrived, lids part-lowered |
| `to_camera` | 0.164 | the eyes arrived straight out at the viewer |
| `double_take` | 0.632 | the snap back, eyes wide |
| `startle` | 0.084 | the eyes at their widest |


### Discovering the looks — 2026-10-24

The themes and skins a robot has are **discovered**, not listed here: seasonal and show looks live in a
separate content repository (`notes/hygiene-content-split.md`). The messages do not change; `THEME <id>`
and `SKIN <id>` take any id the robot has. Agreed with Face & HRI (the face's loading) and Choreography
(the show validator).

- **One implementation**, `software/head-protocol/looks.py`: the core's built-ins (`THEMES_BUILT_IN`,
  `CODE_SKINS` in `head_protocol.py`) plus the content folder's `face/themes/<id>.json` (a state's
  colour by state name; `DEGRADED` and `E_STOP` refused, and `CHARGING`, purple everywhere) and
  `face/skins/<id>.js` (a code skin module, importing core helpers only from the face engine's one
  stable entry point, `skin-api.js`, never an engine file by path: Face & HRI's).
  A built-in name always wins. The content folder is the show runner's: `$OPENDUBS_SHOWS_DIR`, else
  `$OPENDUBS_LOCAL_SHOWS`, else `/var/lib/opendubs/shows` if it exists, else `~/opendubs-shows`.
- **The dashboard** answers `GET /api/head/looks` (that list, plus the library's image and video skins)
  and serves the content files to the face at `/face/content/{themes,skins}/<file>`.
- **The show validator** reads the same ids (`looks.theme_ids()`, `looks.code_skin_ids()`).
- Until the content moves, the built-ins still include `halloween` and the `monster_*` skins.
- **A content skin is code the face runs**, with everything the page has (its door included). It is
  served from the dashboard's own origin (which is also what lets the face `import()` it) out of the
  robot's own content folder, filled from the private content repository: the same trust as the page.
- **Content code is never uploaded** (the user's rule, 2026-10-24). Content DATA (images, sprites,
  parameter skins, audio, video, show files) may come in through the dashboard; content CODE (code
  skins) arrives ONLY from the content repository by the deploy/sync path. The dashboard serves
  `/face/content/` read-only and has no endpoint that writes to the content folder; a test pins both.

## 4. Neck messages

### Precedence

Highest first. A source **owns** the head while its lease is live and nothing above it holds one:

    HOLD  >  touch  >  RC  >  GAZE (a show)  >  TRACK thor  >  TRACK local

**`HOLD`** (v9) is a latch, not a lease: a held head stays where it is until released, whoever else
sends. A **park** (§4, `PARK`) sets aside everything below RC. This order is `head-ownership.md` §2
with `GAZE` placed as the choreography plan's 5.6b decided.

**On a lease's expiry the head never jumps.** Either nothing else holds a live source, and it
**stays where it is** (`owner=none`); or **the next live source down takes it over**, and moves it
at the slew rate. A head that jumps when a show ends reads as a fault; a head that is handed back
to its own tracker and turns there smoothly does not. Each leased message's row below states its
expiry behaviour, and the conformance suite checks it by **watching the angle and the owner**, not
by reading replies (§9).

**On the Tab5 the local tracker is always a live source** while tracking is on, and its resting
behaviour returns the head to centre at the slew rate (`app_face.cpp`: "the slew and the return to
centre are continuous"). So on the Tab5 an expired `GAZE` is handed to `local` and the head drifts
back to centre: a handover, by design, not a spring. *Corrected 2026-10-08 at the Tab5 head lane's
review: the first draft said the head "never springs back to centre", which a correct Tab5 fails.*
There is no message that turns local tracking off; it is the touch panel's MODE button only.

### What a neck reports — pinned 2026-10-08

- **The angles are commanded, not measured.** The servos report nothing back, so `NECK`'s
  `pan=`/`tilt=` and `TRACK`'s `at=` are the control law's position. A check on them verifies the
  control law; a person watching verifies the mechanism.
- **One coherent snapshot.** All of a `NECK` or `TRACK` reply's values are read together (on the
  Tab5, through a seqlock), never torn between two ticks. A tracker turns `at=` into an absolute
  target, so a torn read would be a wrong answer, not an untidy one.
- **`moving=1`** means the commanded angle has not reached its target: `|target − commanded|`
  is larger than the neck's write deadband (1.5° on the Tab5). `moving=0` means the control law
  has arrived; the servo may still be settling. A tracker ignoring frames taken mid-turn uses
  `moving=`, plus its own settling allowance if its camera needs one.
- **`owner=none`** means no source holds a live lease and the head **holds where it is**. On the
  Tab5 this includes the state after the head was aimed by touch, which clears local tracking until
  the MODE button turns it back on.
- **`PING`'s pan/tilt field is free text for people** (on the Tab5 the owner appears as a prefix in
  some branches and a suffix in others). Nothing parses it once `NECK` exists; the conformance suite
  reads it only from a head that has no `NECK` yet.

### Messages

| message | reply | status | notes |
|---|---|---|---|
| `0x107` rate frame (binary) | none | shipping | The **RC stick**, from the StamPLC, 20 Hz. Packed little-endian `{u32 id = 0x107; u8 dlc = 4; i16 pan_dps; i16 tilt_dps; u8 pad[4]}`, 13 bytes. Full stick = 60 °/s. **A rate, not an angle**: the head owns the position. RC's lease is 600 ms. **On expiry:** the rate stops; the head stays where it is, or passes to the next live source. The StamPLC learns the neck's address from the Thor (`TAB5IP <ip>`, the StamPLC's own word, unchanged). |
| `GAZE <pan> <tilt> [lease_ms]` / `GAZE OFF` | `OK gaze <pan> <tilt>` (**the clamped angles**, rounded) / `OK gaze off` / `ERR gaze <pan_deg> <tilt_deg> [lease_ms] \| OFF` / `ERR gaze no pan/tilt fitted` | shipping | A **show** aims the head. Absolute degrees, **90 = centred** on both axes. Lease default 600, at most 1000. Clamped to the mechanism's limits (pan 0–180; tilt to this robot's commissioned band), not to the autonomous envelope. Slewed at the head's rate (70 °/s). The reply shows what will actually run, so a sender can see a limit bite. **On expiry:** the next live source down takes over at the slew rate (on the Tab5, its local tracker, back toward centre); with none, it stays where it is. |
| `TRACK <src> <pan> <tilt> [lease_ms]` / `TRACK <src> OFF` | `OK track <src> <pan> <tilt> owner=<o> at=<pan>,<tilt> moving=<0\|1>` / `OK track <src> off` / `ERR track <thor\|local> <pan_deg> <tilt_deg> [lease_ms] \| <thor\|local> OFF` / `ERR track no pan/tilt fitted` / `ERR track local is on this head` | **agreed**, not built | A **tracker** aims the head. `src` is `thor` (the Thor's YOLO tracker) or `local` (a tracker beside the face: the tablet's screen-side camera). Absolute degrees and leases exactly as `GAZE`, but clamped to the **autonomous envelope**, and ranked below it. The reply always carries **who owns the head** and **where it is now and whether it is moving**. A tracker whose camera turns with the head needs both, to turn an image offset into an absolute target and to ignore frames taken mid-turn. The Tab5 used its own gyroscope for this, and the tablet has none. A head whose local tracker runs on the neck itself (the Tab5) refuses `TRACK local`. **On expiry:** that source stops counting: the next live source down takes over at the slew rate, and with none the head stays where it is. A tracker that loses its target simply stops renewing. **Replaces `0x104`.** |
| `NECK` | `OK neck pan=<deg> tilt=<deg> target=<pan>,<tilt> moving=<0\|1> owner=<o> parked=<0\|1> tilt_band=<min>,<max>` / `ERR neck no pan/tilt fitted` | **agreed**; the Dial answers it | **Read-only** status, for the dashboard, the conformance suite and anything that only needs to look. `owner` is one of `hold`, `touch`, `rc`, `gaze`, `thor`, `local`, `park`, `none`. The Dial adds `commissioned=`, `tilt_pending=`, `chest=`, and from v9 `hold=<off\|on\|releasing> hold_by=<knob\|remote\|->`. |
| `TOUCH HOLD [lease_ms]` / `TOUCH RATE <pan_dps> <tilt_dps> [lease_ms]` / `TOUCH OFF` | `OK touch hold` / `OK touch rate <pan_dps> <tilt_dps>` / `OK touch off` / `ERR touch HOLD [lease_ms] \| RATE <pan_dps> <tilt_dps> [lease_ms] \| OFF` | **agreed**, tablet head only | **A person at the face.** `HOLD` keeps the head still so the face can be touched (tablet plan DT-12). `RATE` is the on-face aiming pad, integrated like RC and capped at 45 °/s. Lease default 600, at most 1000, renewed by the face while the finger is down or the dashboard is open. Highest precedence. Sent **only** for the tablet's own touch, never by a remote browser: the dashboard sends `TOUCH HOLD 1000` when the tablet reports a touch (`POST /api/head/touch`) and renews it every 0.5 s until 4 s after the last one, so the pause ends shortly after (2026-10-22). **The Dial answers it from v9**; an older neck answers `ERR unknown` and the dashboard sends `GAZE OFF` instead. **On expiry:** the next live source down owns the head; with none it stays where it is — a released hold does not move the head by itself. The Tab5's touch is on the same board, so the Tab5 answers `ERR unknown`. |
| `PARK [lease_ms]` / `PARK OFF` | `OK park` / `OK park off` / `ERR park [lease_ms] \| OFF` | **agreed**, tablet head only | **The face is absent** (the tablet is in someone's hand: tablet plan DT-11, CONTROLLER mode). The neck slews to centre and holds; `GAZE` and `TRACK` are still accepted but do not move it; `TRACK`'s reply and `NECK` show `owner=park`. RC and touch, which are people, still move it. Lease default 2000, at most 5000, renewed by the door while the face is away. **On expiry:** the neck returns to normal ownership and stays at centre until a live source moves it. So a tablet that vanishes (battery, range, a crash) frees the head within one lease, and the head does not jump. The Tab5's face is never absent, so the Tab5 answers `ERR unknown`. |
| `HOLD` / `HOLD HERE` / `HOLD <pan> <tilt>` / `HOLD OFF` | `OK hold <pan> <tilt>` (**the clamped angles**, rounded) / `OK hold off` / `ERR hold [HERE \| <pan> <tilt> \| OFF]` / `ERR hold no pan/tilt fitted` | **version 9, 2026-10-22**, neck only | **Hold the head where it is**: tracking, shows, touch and RC do not move it. The same hold as the neck Dial's knob (turned one way, three detents within a second; the other way releases). `HOLD` and `HOLD HERE` hold at the commanded angles now, and move nothing. `HOLD <pan> <tilt>` is for software that needs the head somewhere (aiming the head-top camera): it slews there at the head's rate, clamped like `GAZE` to pan 0–180 and this robot's tilt band, and holds. A **latch, no lease and no timeout**. `HOLD OFF` (or the knob) **announces** the release: `NECK` says `hold=releasing` for 3 s, the neck's screen says "releasing hold", the dashboard shows a notice and the face's `CAPTION` says "The head can move again" ("The head is held still" while held; `state_out.hold_words`), and only then is the head free. `HOLD` during that time cancels the release. **Kept in RAM only**: a rebooted or power-cycled neck is always released. The dashboard's way in is `POST /api/head/hold`. A Tab5 or a tablet face answers `ERR unknown`. |
| `BADGE <STATE> <driver> <rrggbb>` / `BADGE OFF` | `OK badge <STATE> <driver> <rrggbb>` / `OK badge off` / `ERR badge <STATE> <driver> <rrggbb> \| OFF` | **version 9, 2026-10-22**, neck only | **The neck Dial's status screen**: a ring in `rrggbb`, the `STATE`'s label (one of §3's `STATES`; underscores shown as spaces, `E_STOP` as E-STOP), and `driver`, who drives the head: `show`, `tracking`, `rc`, `touch`, `hold` or `nobody`. The colour is the state's own on the PRISM (`state_out._MAP`, never a show's light over it), sent so that one map decides it. Sent like `CHEST`: on a change, when the neck (re)appears, and every 5 s. **No lease**; the neck greys the ring after 20 s without one. What the neck knows itself it shows instead of `driver`: HOLD, "releasing hold", RC, TOUCH, and "press to confirm tilt …" while a tilt change waits. **Blank from boot until told.** `OFF` blanks the ring and label. No neck fitted, or a Tab5: nothing is sent. |
| `TOF` | a report line | shipping | The front distance sensor, read-only. |
| `I2CSCAN` | a report line | shipping | The neck's I2C bus, read-only. |
| `CHEST <us>` / `CHEST OFF` | `OK chest <us>` / `OK chest off` / `ERR chest <us 500-2500> \| OFF` / `ERR chest not fitted` | **version 7, agreed 2026-10-16** (Tablet lane with Face & HRI, who drive the light from the neck, on 8Servo channel 2), neck only | The **chest light**: a goBILDA RGB Indicator Light (3118-0808-0002) behind the chest plate, on the neck's 8Servo. `us` is the servo pulse it gets, 500–2500, which sets its **hue** only: it has no brightness, so nothing on it fades or breathes. goBILDA's published chart (Product Insight #4): below 1100 off; 1100 red, 1200 orange, 1300 yellow, 1400 sage, 1500 green, 1600 azure, 1700 blue, 1800 indigo, 1900 violet, a smooth gradient between; above 1900 solid white. `OFF` darkens it. **It shows what the PRISM shows.** The dashboard takes the PRISM's winning colour (the state's, charging purple, or a show's light lease over it, with the reserved states always their own), finds its hue on that chart (`state_out.chest_line`), and sends it on a change, when the neck (re)appears, and every 5 s. **A coloured pulse is kept within 1130–1880**, so red keeps a margin from off (an E_STOP must never read as a dark, powered-down chest) and violet from white. A colour too pale to have a hue is white (2500); a black or near-zero one is `OFF`. Charging is solid purple, since the light cannot breathe. **No lease:** the neck holds the last value, and is `OFF` from boot until told. The reply is what is applied, in the form sent: `CHEST 500` and `CHEST OFF` apply the same dark pulse and answer `OK chest 500` and `OK chest off`. `ERR chest not fitted` means the neck's 8Servo does not answer (a bench Dial without one); the neck cannot tell whether a light is plugged into the channel. No neck fitted, or a Tab5 (both halves, no chest light): nothing is sent. The Tab5 answers `ERR unknown`. |

## 5. Device messages

Answered by each half for itself.

| message | reply | status | notes |
|---|---|---|---|
| `REBOOT` | `OK reboot` (then the device restarts) | shipping | Not for the conformance suite on a robot. |
| `OTA <url>` | `OK ota` | shipping | Pull and install an image. **What** it installs is per device: the Tab5's image (`firmware/tab5_idf/ota-from-thor.sh`), the neck's (its own script, before it is fitted), and the tablet app's: **decided 2026-10-08, the kiosk app updates itself over the network**, silently, as device owner. There is no automatic rollback, so how a bad update is recovered is the tablet plan's to settle before that path ships. The app's version is in its `PING` (`app=`), so drift is visible. |

## 6. `PING`

`PONG` followed by fields. **The fields are `key=value` tokens, separated by spaces and optionally
by `|`. A reader ignores keys it does not know, and never depends on order.**

**Today's Tab5** (shipping):

    PONG slot=<ota_0|ota_1> state=<VALID|PENDING_VERIFY|…> sha=<16 hex> | plc=<ip>/<name|heard>|none | audio=<on|OFF> | tp … st=<STATE> | <touch-panel diag> | <pan/tilt status>

**From version 1, every head adds `kind=`** as its first field, so a sender knows which head it
reached. A reply without `kind=` is today's Tab5.

| `kind=` | fields it adds |
|---|---|
| `tab5` | both halves in one; the fields above |
| `tablet-face` | `app=<version> engine=<version> library=<hash> mount=<in_mount\|held\|unknown> mode=<face\|engaged\|controller> st=<STATE> audio=<on\|OFF> skin=<shown>` and, if what was asked is not what shows, `skin_error=<why>` |
| `neck` | `fw=<sha> state=<VALID\|…> plc=<ip>/<how>\|none owner=<o> pan=<deg> tilt=<deg>`, and the signed-update policy (`firmware/neck_dial/README.md`, *Signed updates*): `otasig=<enforce\|report> otaverify=<ok\|BROKEN> otakeys=<id>[,<id>]\|none` |

## 7. What a head sends and hears besides replies

| direction | what | from / to |
|---|---|---|
| hears | the StamPLC's telemetry broadcast, a JSON line about three times a second, carrying `rc.mode` and `io.estop` | UDP 5005 broadcast |
| sends | `MAST CMD <-1..1>`, re-sent every 50 ms while a lift button is held (the StamPLC's own 250 ms heartbeat stops the lift if they stop) | StamPLC, UDP 5005 |
| sends | `ESTOP`, re-sent until the robot reports `E_STOP` | StamPLC, UDP 5005 |

The tablet head's door does the same for its Thor-down panel (tablet plan DT-17).

## 8. Retired — decided 2026-10-08

Answered `ERR unknown` by the ESP-IDF head. **Not carried over.** Senders are to stop sending them; the dashboard's tests fail if one does (`tests/test_no_retired_head_messages.py`). The dashboard's UDP 5006 listener (the Arduino head's lift buttons and status) is gone with them.

| retired | what it was | sent today by | replaced by |
|---|---|---|---|
| `0x104` absolute pan/tilt frame | the Arduino head's tracker input | `yolo_tracker.py`, until the Tab5 parses `TRACK thor` (`ros/tab5_bridge_node.py` deleted 2026-10-11) | `TRACK thor` |
| `0x108` tracking-mode frame | AI/MANUAL tracking switch | nothing (removed from the dashboard 2026-10-11) | the precedence itself: a tracker that sends owns the head when nothing above it does |
| `TURN`, `LIFT`, `PMODE` | turn-signal chevrons, lift height, CH7 mode on the Arduino face | nothing (removed 2026-10-11) | nothing yet; a face message, specified here first, if wanted again |
| `PLCIP` | "the StamPLC may command you" | nothing (removed 2026-10-11) | the head resolves the StamPLC by name (`opendubs-stamplc.local`) and hears its broadcast |

## 9. Conformance

`software/head-protocol/conformance.py <host>` sends each message and checks the reply against
this document. It reports each check as **PASS**, **FAIL**, **PENDING** (specified, and this head
answers `ERR unknown`: not built yet, or not this head's) or **SKIP**.

- **Read-only by default**, and safe on a robot: `PING`, `NECK`, `TOF`, `I2CSCAN`, every retired
  message, and malformed or unknown-name forms of the rest. None of these changes what the face
  shows or moves the head.
- `--active` also sends the messages that change the face (`STATE`, `EMOTE`, `THEME`, `CUE`) and
  `TOUCH HOLD`, which takes the head without moving it.
  Allowed against `127.0.0.1` (a twin); against anything else only with `--at-the-robot`.
- `--motion` also sends `GAZE`, `TRACK`, `TOUCH RATE` and `PARK`, short leases at small angles
  around centre, and then runs the **sampled checks**. **Moves the head.** It needs
  `--at-the-robot` on anything but a twin, and a person watching.

**The sampled checks** exist because a reply-only check passed a `GAZE` that crawled at about
3 °/s while every reply said `OK` (2026-10-08; the twin has no servos, so nothing there could
notice). For each leased message the head implements (`GAZE`, `TRACK thor`, `PARK`), the suite
renews it every 200 ms and reads the angle every 100 ms, from `NECK`, or from `PING`'s pan/tilt
field on today's Tab5. It judges three things:

| check | passes when |
|---|---|
| **holds ownership** | while the lease is renewed, every `NECK` sample names that source as `owner=`. A lease that flaps — the `GAZE` underflow — fails here even when the angle gets there. |
| **reaches** | within 2° of the target within 1.5 × distance ÷ 70 °/s + 0.5 s |
| **slews** | the fastest 0.3 s of the move is at least half the head's 70 °/s. A crawl fails; a slow servo does not. A move under 15° is too short to judge, and says so. |
| **no jump** | from the last renewal through the wait after expiry, no step between samples exceeds what 70 °/s allows in that interval (with 3° for sampling and the deadband) |
| **after expiry** | `owner=none`: the angle holds within 1° of where the lease left it, over the whole wait. Any other owner: ownership has passed from the expired source to it. |

A head without `NECK` (today's Tab5) cannot report its owner, so it is judged on the angle only, and
the ownership checks are skipped with that reason.

It leaves the head where it found it. **This is the check that signs off a leased message on the
robot**: the Tab5 lane runs it with `--motion --at-the-robot`, with a person watching the head
move as the numbers say.

`software/head-protocol/fake_head.py` is a reference implementation of this version on localhost.
The suite's own tests run against it, and it stands in for the tablet's door until the door exists
(tablet plan 4.3). The tests also check this document's vocabulary and message list against the
Tab5's source, so the spec and the firmware cannot drift apart unnoticed.

## Open, and agreed elsewhere

- **Agreed with the Tab5 head lane, 2026-10-08:** `TRACK thor`, `TRACK local` refused, `NECK`
  (`ERR neck no pan/tilt fitted` on the twin), and `kind=tab5` as `PONG`'s first field. The Tab5
  keeps answering `TOUCH` and `PARK` `ERR unknown`. Built by the Tab5 head lane; signed off on the
  robot by `--motion --at-the-robot`.
- Not assumed here: whether the D415 stays on the head. It only changes where `TRACK thor`'s
  camera is, not the message.
