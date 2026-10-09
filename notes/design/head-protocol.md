# The head protocol

**Status: version 2, 2026-10-08** — version 1 agreed with the Tab5 head lane; version 2 adds `SKIN` (tablet face only), agreed with the Choreography lane. Tablet plan items 2.1 and 2.2
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
| `THEME <name>` | `OK theme <name>` / `ERR unknown theme` | shipping | Built in: `plain` (alias `NONE`) and `halloween`; plus any authored theme installed by `CFG`. No theme can recolour `DEGRADED` or `E_STOP`. |
| `CUE <earcon>` | `OK cue <earcon>`, or `OK cue <earcon> (AUDIO OFF -- …)` / `ERR unknown cue` | shipping | `heard`, `attend`, `refuse`, `fault`, `mode`, `boot`. Audio is a per-robot setting that defaults **off**. The reply says so, because an acked cue that nobody hears is otherwise unexplainable. |
| `CFG <url>` | `OK …` / `ERR …` (a verdict) | shipping | Fetch an authored configuration (today: themes as palettes, per `face-config-data-model.md`). On the tablet head, "fetch this library item now". |
| `SKIN <name>` | `OK skin <name>` / `ERR unknown skin <name> (showing default)` / `ERR skin <name>: <why> (showing default)` | **version 2, agreed** (with the Choreography lane, 2026-10-08), tablet face only | Which skin draws the eyes: a code skin — `default` (the standard eyes, an ordinary argument) or `ring` — or a library skin's id. Persistent, like `THEME`. **Never a blank face:** an unknown name, or a library skin whose file will not load, shows `default` and says so; `PING` then reports `skin=default skin_error=…`. A reserved state draws `default` whatever is set. A library skin may not use a code skin's name. The Tab5 answers `ERR unknown`. |
| `VOICE PRESS` / `RELEASE` / `STATUS` / `THOR <host> <port>` | `OK …` / a status line / `ERR VOICE {…}` | Tab5 only | Push-to-talk. Compiled into the twin, **out of the shipping image**, and **paused** (decided 2026-10-08). A tablet head answers `ERR unknown` until a voice decision says otherwise. |
| `SIM LINK\|ESTOP\|PERSON\|TOF …` | `OK sim …` / `ERR SIM …` | twins only | Simulated inputs for the face twin. A real head refuses them rather than pretending. |

## 4. Neck messages

### Precedence

Highest first. A source **owns** the head while its lease is live and nothing above it holds one:

    touch  >  RC  >  GAZE (a show)  >  TRACK thor  >  TRACK local

A **park** (§4, `PARK`) sets aside everything below RC. This order is `head-ownership.md` §2
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
| `NECK` | `OK neck pan=<deg> tilt=<deg> target=<pan>,<tilt> moving=<0\|1> owner=<o> parked=<0\|1> tilt_band=<min>,<max>` / `ERR neck no pan/tilt fitted` | **agreed**, not built | **Read-only** status, for the dashboard, the conformance suite and anything that only needs to look. `owner` is one of `touch`, `rc`, `gaze`, `thor`, `local`, `park`, `none`. |
| `TOUCH HOLD [lease_ms]` / `TOUCH RATE <pan_dps> <tilt_dps> [lease_ms]` / `TOUCH OFF` | `OK touch hold` / `OK touch rate <pan_dps> <tilt_dps>` / `OK touch off` / `ERR touch HOLD [lease_ms] \| RATE <pan_dps> <tilt_dps> [lease_ms] \| OFF` | **agreed**, tablet head only | **A person at the face.** `HOLD` keeps the head still so the face can be touched (tablet plan DT-12). `RATE` is the on-face aiming pad, integrated like RC and capped at 45 °/s. Lease default 600, at most 1000, renewed by the face while the finger is down or the dashboard is open. Highest precedence. Sent **only** by the face's own touch surface, never by a remote browser. **On expiry:** the next live source down owns the head; with none it stays where it is — a released hold does not move the head by itself. The Tab5's touch is on the same board, so the Tab5 answers `ERR unknown`. |
| `PARK [lease_ms]` / `PARK OFF` | `OK park` / `OK park off` / `ERR park [lease_ms] \| OFF` | **agreed**, tablet head only | **The face is absent** (the tablet is in someone's hand: tablet plan DT-11, CONTROLLER mode). The neck slews to centre and holds; `GAZE` and `TRACK` are still accepted but do not move it; `TRACK`'s reply and `NECK` show `owner=park`. RC and touch, which are people, still move it. Lease default 2000, at most 5000, renewed by the door while the face is away. **On expiry:** the neck returns to normal ownership and stays at centre until a live source moves it. So a tablet that vanishes (battery, range, a crash) frees the head within one lease, and the head does not jump. The Tab5's face is never absent, so the Tab5 answers `ERR unknown`. |
| `TOF` | a report line | shipping | The front distance sensor, read-only. |
| `I2CSCAN` | a report line | shipping | The neck's I2C bus, read-only. |

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
| `neck` | `fw=<sha> state=<VALID\|…> plc=<ip>/<how>\|none owner=<o> pan=<deg> tilt=<deg>` |

## 7. What a head sends and hears besides replies

| direction | what | from / to |
|---|---|---|
| hears | the StamPLC's telemetry broadcast, a JSON line about three times a second, carrying `rc.mode` and `io.estop` | UDP 5005 broadcast |
| sends | `MAST CMD <-1..1>`, re-sent every 50 ms while a lift button is held (the StamPLC's own 250 ms heartbeat stops the lift if they stop) | StamPLC, UDP 5005 |
| sends | `ESTOP`, re-sent until the robot reports `E_STOP` | StamPLC, UDP 5005 |

The tablet head's door does the same for its Thor-down panel (tablet plan DT-17).

## 8. Retired — decided 2026-10-08

Answered `ERR unknown` by the ESP-IDF head. **Not carried over.** Senders are to stop sending them.

| retired | what it was | sent today by | replaced by |
|---|---|---|---|
| `0x104` absolute pan/tilt frame | the Arduino head's tracker input | `yolo_tracker.py`, `ros/tab5_bridge_node.py` | `TRACK thor` |
| `0x108` tracking-mode frame | AI/MANUAL tracking switch | `state_out.head_set_tracking` | the precedence itself: a tracker that sends owns the head when nothing above it does |
| `TURN`, `LIFT`, `PMODE` | turn-signal chevrons, lift height, CH7 mode on the Arduino face | `state_out.py` | nothing yet; a face message, specified here first, if wanted again |
| `PLCIP` | "the StamPLC may command you" | `state_out._introduce_peers` | the head resolves the StamPLC by name (`opendubs-stamplc.local`) and hears its broadcast |

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
