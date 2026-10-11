# Power: idle power management, a graceful shutdown, and power-on

**Status: design, 2026-10-09, for the team to approve. Nothing here is built.** Tablet & head
interface lane. The Thor's side is to be agreed with the Thor stack lane and the arms' with the Arms
lane (questions sent, §6); the sleeping face's *look* is the Face & HRI lane's; the operator-facing
procedures and troubleshooting entries are the Docs lane's once this settles.

**Facts this rests on**, each checked on 2026-10-08/09:

| fact | source |
|---|---|
| RUN switches the **logic bus** — Thor, StamPLC, router, sensors — and is **not** behind the e-stop; E-STOP drops only the drive contactor; BATTERY connects the pack | `docs/operating.md`, `docs/power-architecture.md` |
| The PRISM LEDs are the **safety** channel and stay lit through an e-stop; the face is a **social** channel (intent before motion) | `notes/design/hri-interaction-design.md` §0 |
| The dashboard already has `POST /api/system/shutdown` → `sudo systemctl poweroff` (sudoers in `deploy/opendubs-sudoers`), and runs arm operations through `sudo openarm-up` | `backend/app.py`, `deploy/opendubs-sudoers` |
| `openarm-up park both` moves the arms to their stow pose; `down` de-energizes and refuses an arm active away from stow | `software/openarm/openarm-up`, `notes/design/arm-authority.md` |
| **A device-owner app cannot power the tablet off.** `DevicePolicyManager` has `reboot()` and `lockNow()`, no shutdown; `PowerManager`'s shutdown is hidden from apps | the Android 36 SDK's `android.jar` |
| **The kiosk locks with no power menu** (`LOCK_TASK_FEATURE_NONE`), and on this tablet holding the side key opens the AI assistant, not the power menu | `DeviceOwner.kt`; the bench tablet's settings |
| Double-tap-to-wake is on | the bench tablet's settings |
| **The kiosk app never retries** if the dashboard is unreachable when it starts — it shows "Webpage not available" for good | `KioskActivity.kt` (no error handling) |
| A graceful bring-down was asked for and never built ("command neutral, disarm, open the contactor, park the Z-axis, then bring down the compute") | `docs/operating.md`, `notes/punch-list.md` |

---

## 1. Idle power management

Today the screen never turns off (`FLAG_KEEP_SCREEN_ON`). The tablet should use what it knows to dim,
sleep the face, and turn the screen off when nothing needs it — and never when something does.

### What counts as active

The tablet is **active** while any of these holds; it is **idle** when none has for the tier's time:

| signal | from | counts while |
|---|---|---|
| a touch | the screen | the last touch was within the window |
| a person present | screen-side camera (face mount), the neck's ToF (tablet head) | seen within the window |
| robot live | the robot state (dashboard websocket; the StamPLC broadcast via the door when the Thor is down) | CH5 is `MANUAL` or `AUTO`, **or** the deadman is held, **or** the drive is armed, **or** the base, lift or arms are moving |
| RC input | StamPLC telemetry | a stick moved within the window |
| a show | the show runner | loaded, waiting or running |
| a state change | the robot state | within the window (a fault, a mode change) |
| the mount | the mount signal | changed within the window (lifted off, put back) |

### Never deeper than "active"

- **A show loaded or running.** The runner's `load` wakes the face before `ready` (`choreo-screen-track.md`).
- **The robot live**, as above. A robot that can move shows its face (intent before motion, HRI §0).
- **A fault or `E_STOP` — for the first 10 minutes.** After that the face may dim and sleep: the PRISM
  LEDs carry "stopped" indefinitely; the face is not the e-stop indicator (HRI §0). This matters because
  the tablet has its own battery and the mount's power may be off (an e-stop drops the drive branch) —
  a face held lit forever would drain the tablet while nobody is there.

### The tiers

| tier | face position (tablet is the head) | controller (in hand, or the rear cradle) |
|---|---|---|
| **0 · active** | full face, brightness follows the room's light, 90 Hz | the dashboard, brightness follows the room |
| **1 · calm** — after **2 min** idle | brightness halved, 30 fps, slower blinks | brightness halved |
| **2 · asleep** — after **10 min** | the **sleeping face**: eyes closed, a slow breath, minimum brightness, 10 fps; presence checked by cheap motion detection | — (skipped) |
| **3 · screen off** — after **30 min** (face) / **10 min** (controller) | screen off, the app still running and its door listening | screen off |

Placeholders, for the team to set. Ambient light scales brightness within every tier (a dark room
needs less). **Any wake trigger returns to tier 0 at once.**

### Wake triggers

| trigger | wakes from | how |
|---|---|---|
| a touch | 0–2 directly; **3 by double-tap** (double-tap-to-wake is on) or the side key | the screen |
| a person approaching | 1–2 | camera motion detection (tier 2 only), the neck's ToF |
| a robot state change, a fault, CH5 leaving OFF, the deadman | 1–3 | the robot state; in tier 3 the app is woken by its door or websocket and turns the screen on (`wake()`) |
| RC input | 1–3 | StamPLC telemetry |
| a show loading | 1–3 | the runner, via show media |
| the mount changing, or the charger connecting | 1–3 | the mount signal; `ACTION_POWER_CONNECTED` |

### The camera's cost

The face-tracking worker costs ~50 ms of CPU per detection — about three quarters of a core at 15 Hz —
plus the camera sensor itself. **Not worth it just to wake.** So:

- **tier 0:** full tracking (Face Landmarker, ~15 Hz), because the face is in use;
- **tier 1–2:** **motion detection only**, a few frames a second on a small image (frame differencing,
  no model) — enough to notice someone approaching, then tier 0 starts tracking;
- **tier 3:** camera **off**. Waking relies on touch, the side key and robot events.

How much each tier actually draws is measured before any number is chosen (experiment PW-1).

### Who does what

The **page** (`main.js`, this lane) computes the tier from the signals and drives brightness, frame rate,
the camera and screen-off through the kiosk bridge (`keepAwake`, plus a new `setBrightness`). The
**sleeping face** — what tier 2 looks like — is the **Face & HRI lane's**: the page tells the face model
the tier, and their `face.js` draws it.

---

## 2. Graceful shutdown — one action from the tablet

### The sequence — in two phases, because parking moves the arms

**DEC-21** (the arms plan): with CH5 in `OFF`, the arms may hold, report and de-energize *from stow* —
but **not park**, since parking moves them. A shutdown belongs in `OFF` (`docs/operating.md`, *Powering
off*). So the arms are made safe first, in `MANUAL`, only when they need it:

**Phase A — make the arms safe** (only if an arm is energized away from its stow pose)

| # | step | done when |
|---|---|---|
| A1 | the tablet says *the arms need parking: CH5 to MANUAL, then hold to park* | CH5 is `MANUAL` (read fail-closed, as the teleop bridge reads it) |
| A2 | **stop teleop and wait for it to be gone** — `systemctl stop opendubs-teleop` returns before the bridge exits, so poll the arm container for the bridge process, up to ~10 s (arms plan F9) | no bridge process: one writer per arm |
| A3 | **`openarm-up park both`** after the operator's hold-to-confirm, as the TELEOP page's PARK BOTH does — ~25 s | exit status 0 |
| A4 | **`openarm-up down`** — ~15 s | exit status 0, **and** `openarm-motors summary` reads `enabled=0`: the ground truth that no arm can drop, independent of the arm stack |
| A5 | the tablet says *now CH5 to OFF* | CH5 is `OFF` |

An arm with **no motor energized** (never activated, or after an e-stop) is already limp: `down` passes it
without a park, and Phase A is skipped.

**Phase B — shut down** (CH5 `OFF`)

| # | step | where | done when |
|---|---|---|---|
| B1 | **Preflight** — refuse unless safe (below) | Thor | all checks pass, or the first refusal is shown with its code |
| B2 | **Confirm**: *hold to shut down* (2 s), with what will happen listed above it | tablet | held |
| B3 | **Snapshot the battery** — pack SOC, voltage, charging or not (BMS → StamPLC → dashboard) — kept on the tablet | Thor → tablet | the tablet holds it |
| B4 | **Stop the show runner** (idle) and teleop, if not already | Thor | both stopped |
| B5 | **`openarm-up down`** if anything is still energized — at stow only; otherwise refuse (`ARM_DOWN_WOULD_DROP`) | Thor | `openarm-motors summary` reads `enabled=0` |
| B6 | **Open the contactor** — a software e-stop (`ESTOP`), so nothing can drive while the compute goes down | Thor → StamPLC | the StamPLC reports it open |
| B7 | **The Thor powers off** — the existing `systemctl poweroff`, after the 1 s that lets the reply out (it syncs and unmounts itself; no extra `sync`) | Thor | the dashboard stops answering |
| B8 | **"Thor down", then a countdown to *safe to switch RUN off*, then the battery advice** (below) | tablet | — |
| B9 | **The tablet goes to sleep** (tier 3), or to *storage* (below) | tablet | — |

B3–B7 run **on the Thor, as one request** (`POST /api/system/safe-shutdown`), so a tablet that drops off
mid-way cannot leave the robot half-done; the tablet follows progress (`GET /api/system/safe-shutdown`)
until the Thor stops answering. **The tablet's page keeps running after the Thor is gone** — B8's screens
are already loaded.

### Knowing when RUN can be cut

The dashboard dies **before** the Thor has halted, so "the dashboard stopped answering" is not the signal.
A clean Ubuntu power-off is typically 10–30 s; Docker gives each container 10 s; a unit that hangs on stop
can hold it for systemd's 90 s (Thor stack lane; **to be measured** on the spare Thor and on opendubs2).

- **The real signal belongs to the StamPLC**, which outlives the Thor and already senses the logic rail's
  current: *rail current at a halted Thor's floor for N s* → its own screen says **SAFE TO SWITCH OFF**.
  Simpler fallback: the Thor's heartbeat to the StamPLC stops, plus a measured margin. **Either is a StamPLC
  firmware change** — that board has no lane yet, so it goes through the orchestrator — and the threshold
  is measured first.
- **The tablet** shows a countdown sized from the measured halt time (until measured: 90 s). Once the
  kiosk app's door exists (tablet plan 5.2) it **hears** the StamPLC's broadcast and can show the same
  SAFE TO SWITCH OFF, and then RUN going off when the broadcast stops; until then the operator confirms.

### If the arms cannot be made safe

The shutdown **refuses rather than drop the arms**, and the refusal says how to reach a clean state. But a
refusal does not stop someone cutting RUN, which drops unparked arms (they are on the drive bus; RUN off
opens the contactor). **Decided 2026-10-09: a second path, *shut down anyway — the arms will drop*,**
offered **only** when parking fails or is refused: an explicit warning (*the arms will fall; clear the area
around them*), then its **own** hold-to-confirm, separate from the first. It runs Phase B with `down
--drop`, and is logged with the refusal that led to it (`POWER_SHUTDOWN_ARMS_WILL_DROP`).

**The lift does not move by itself during a shutdown.** `docs/operating.md` says to lower it or leave it
at a height from which it cannot fall; moving it is a person's decision (and needs `MANUAL`). The
preflight shows its height, and the confirm screen says "lower it first if it should be lowered".

### Refusals — each with a stable `refuse()` code

Arm refusals reuse the **arms registry's** codes, so the help link lands on the arm entry; one shutdown
code wraps them:

| refused when | code |
|---|---|
| CH5 is not `OFF` at Phase B | `POWER_SHUTDOWN_NOT_STANDBY` |
| a show is loaded or running | `POWER_SHUTDOWN_SHOW_RUNNING` |
| the base, the lift or an arm is moving | `POWER_SHUTDOWN_MOVING` |
| a software update is running (dpkg's lock is held) — a cut then breaks packages | `POWER_SHUTDOWN_UPDATE_RUNNING` |
| the arms cannot be made safe — wraps the arm code below | `POWER_SHUTDOWN_ARMS_NOT_SAFE` |
| — no stow pose configured | `ARM_NO_STOW_POSE` (arms registry) |
| — `down` would drop an arm (energized, away from stow, CH5 `OFF`) | `ARM_DOWN_WOULD_DROP` (arms registry) |
| — an arm operation is already running | `ARM_OP_RUNNING` (arms registry) |
| — the arm stack is dead | `ARM_STACK_DEAD` (arms registry) |
| — the arm command was refused by sudo | `ARM_SUDO_DENIED` (arms registry) |
| no link to the StamPLC (the contactor and battery cannot be read) | `LINK_STAMPLC_LOST` (exists) |
| *not a refusal* — the operator chose *shut down anyway*, and the arms were dropped | `POWER_SHUTDOWN_ARMS_WILL_DROP` (logged, and shown on the warning) |

New `POWER_SHUTDOWN_*` codes to be agreed with the Docs lane, which owns the registry
(`docs/reference/registry/power.yaml`).

### The battery advice — after the Thor is gone

From the snapshot, with the steps quoted from the docs, so the tablet needs no network:

- **SOC and voltage, and whether it was charging.**
- **Charge or not: no recommendation yet.** The screen shows the pack's charge level and voltage only.
  The threshold comes from the pack's datasheet (the team is checking it), is recorded in
  `docs/operating.md` *Charging* by the Docs lane, and only then does the screen advise.
- **How:** `docs/operating.md` *Charging*, carried in the tablet app at build time: **BATTERY on**
  (never charge with BATTERY off and RUN on); charger at **48 V**, **8 A**; plug into the charge port;
  unplug before driving.

### Powering the tablet off

**The app cannot power the tablet off itself** (a device owner has `reboot()` and `lockNow()`, no shutdown),
**but the Thor can, over adb** — probed and recommended in **§7**:

- **Daily use: the tablet sleeps** (tier 3) and wakes by itself at the next power-on.
- **Storage: *Power the tablet off too*** on the shutdown screen — the Thor leaves a delayed power-off on
  the tablet after B8's screens, then halts. At the next power-on a person holds the side key (a
  switched-off tablet does not boot on charger power).
- **By hand, always:** hold the side key → Power off (§7, once `power_button_long_press` is set at setup).

---

## 3. Power-on

| # | step | who | notes |
|---|---|---|---|
| 1 | **CH5 to OFF**, **BATTERY on**, **RUN on** | a person | `docs/operating.md`, *Powering on* |
| 2 | **The Thor boots** | itself — **unverified**; the Thor stack lane is measuring it on the spare Thor | if it does **not** boot on power, power-on means pressing the Thor's own button inside the bay — a usability problem this procedure must then state |
| 3 | **The tablet comes up**: if it slept, the mount's power wakes it; if it was powered off, a person **holds the side key** | itself / a person | **verified on the bench: a powered-off tablet does not boot on charger power** (PW-5) |
| 4 | **The kiosk app waits for the Thor, and says so** — *Waiting for the robot's computer…*, retrying, then the dashboard | the app — **new** | today it shows "Webpage not available" for good: a page bundled in the app that retries, and (with the door) shows the StamPLC heard before the Thor |
| 5 | **The arms stay inactive** until someone activates them | as today | arms start inactive (`openarm-up`) |
| 6 | **Clear the e-stop latch** if the shutdown's software e-stop (B6) left one | a person, at the dashboard | depends on the StamPLC's behaviour at power-on — `docs/operating.md` notes a contactor auto-arm behaviour not yet in the repo |

---

## 4. Experiments, before anything is built

| # | question | done when |
|---|---|---|
| PW-1 | **What does each tier draw?** Tablet current (Android's battery service reports `current_avg`) in tiers 0–3, camera on and off, on battery. | A table per tier; the tier timings chosen from it; overnight standby drain known. |
| PW-2 | **Does motion detection notice a person approaching** at a few frames a second, without the model? | It wakes for a person walking up, not for the robot's own idle motion. |
| PW-3 | **Does a screen-off tablet still hear its door and websocket**, and wake promptly on a robot event? | A STATE change wakes the screen within a second, after 30 min screen-off. |
| PW-4 | ~~Which action opens the power menu?~~ **Answered 2026-10-09 (§7):** a long press, once `power_button_long_press` is `1` (setup, over adb); the kiosk must allow the power menu in lock task. | — |
| PW-5 | ~~A powered-off tablet on charger power~~ **Answered 2026-10-09:** it shows the charging screen and goes dark — **it does not boot**; a person holds the side key. | — |
| PW-6 | **The shutdown on the robot**, end to end, with a person at the robot. | Arms parked and limp, contactor open, Thor halted, battery advice shown, RUN off confirmed. |
| PW-7 | **The Thor's halt time and power-on behaviour** (Thor stack lane): auto-boot on power; after `poweroff` with RUN on; seconds from `poweroff` to halted. | Numbers for the countdown and for power-on step 2. |

---

## 5. What changes where — when built

| part | file(s) | lane |
|---|---|---|
| the tier logic, wake triggers, camera modes | `software/face-web/engine/main.js`, `track.js` | this lane |
| the sleeping face | `software/face-web/engine/face.js` | Face & HRI |
| `setBrightness`, screen-off, leaving lock task for storage, the waiting page, wake on charger | `software/tablet-kiosk/` | this lane |
| `POST`/`GET /api/system/safe-shutdown` | `software/kiosk-dashboard/backend/app.py` — **shared**, a scoped edit, flagged in the PR | this lane, agreed with the Thor stack lane |
| sudoers for any new command | `software/kiosk-dashboard/deploy/opendubs-sudoers` — **shared** | this lane, agreed with the Thor stack lane |
| the refuse codes; the operator procedures; the charging threshold | `docs/reference/registry/`, `docs/operating.md` | Docs |

## 6. Answers and what is still open — 2026-10-09

**Arms lane (answered):** park-then-down is right; the arms are on the **drive bus** (RUN off and an e-stop
both leave them limp — unparked, they fall); stop teleop and **wait for the bridge to exit**; park ~25 s,
down ~15 s; success is the exit status, confirmed by `openarm-motors summary` reading `enabled=0`; an arm
with nothing energized needs no park; **parking needs CH5 `MANUAL` and a confirming tap (DEC-21)** — hence
the two phases; reuse the arms registry's codes.

**Thor stack lane (answered, mostly unverified):** auto-boot on power and the behaviour after `poweroff`
are **unverified** and being measured (PW-7); halt time ~30 s typical, 90 s worst case until measured; the
halt is best seen by the StamPLC (rail current, or heartbeat plus margin); **refuse while dpkg holds its
lock**; Docker's storage is the part most hurt by a hard cut (on opendubs2 the arm image is the only copy
until the arms lane's #107 lands); no extra `sync` — `poweroff` does it.

**Decided 2026-10-09 (the team):** *shut down anyway* — yes, behind a second confirm (above); no charge
recommendation until the datasheet threshold is recorded; the tablet's power-off to be probed much harder
before *sleep* is accepted as the answer (§7).

**Still open:**
- **The team:** the tier timings (after PW-1).
- **The orchestrator:** the StamPLC change for SAFE TO SWITCH OFF — that board has no lane.
- **Thor stack:** PW-7's numbers.

---

## 7. Powering the tablet off — probed on the bench SM-X133, 2026-10-09

The team asked for a harder look before accepting "sleep only". Four routes, tried or read:

| route | what was found | verdict |
|---|---|---|
| **(a) The Thor powers it off over adb** | `adb shell svc power shutdown` ("runtime shutdown and power off") **powered the bench tablet off** from a USB host, off adb in under 8 s. A delayed command left on the tablet (`nohup sh -c 'sleep N; …' &`) **ran on time with the host's adb gone** — so the Thor can schedule the power-off and then halt. A device owner **can keep USB debugging on**: `ADB_ENABLED` and `ADB_WIFI_ENABLED` are in AOSP's `setGlobalSetting` allowlist. `adb_enabled` survived a power cycle. | **works over USB** — but the team has since ruled out a USB link (below), so it now depends on **wireless** adb, **not yet probed** |
| **(b) Samsung Knox SDK** | `SystemManager.powerOff()` exists (Knox API 20), and `setForceAutoStartUpState(ON)` would even boot the tablet on charger power — but both need a **KPE Premium** key: free, yet since 2025 issued only to approved enterprise customers through the Knox Partner Program, and activated **online** against Samsung's licence server. Tab A11 support likely (an Enterprise Edition of its LTE sibling exists), not confirmed. | **possible later**, if the team applies for a key; not a path today |
| **(c) An accessibility service** | Samsung's power menu, once open, has stable, language-independent controls (`com.android.systemui:id/sec_global_actions_icon`, "Power off,"), so a service *could* press it — but it needs the power menu reachable in lock task, the service enabled at setup (not settable by a device owner), and two presses through Samsung UI that One UI updates can move. Not tried end to end. | **too fragile** — not recommended |
| **(d) Manual, made workable** | With AOSP's `power_button_long_press` = `1`, a long press opens Samsung's **power-off menu** (Power off, Restart) — confirmed on the bench (it is `103`, the assistant, by default; Samsung's own `function_key_config_longpress_type` did not change it). A device owner **cannot** set it (not in the allowlist), so it is **one setup step over adb**. The kiosk must also allow the power menu in lock task (`LOCK_TASK_FEATURE_GLOBAL_ACTIONS`). | **works — the fallback**, and the operator's way at any time |

**Also measured: a switched-off tablet does not boot on charger power.** With USB still connected it
showed Samsung's charging screen briefly, then went dark; a person had to hold the side key. So a powered
**off** tablet costs a button press at the next power-on — unless Knox's auto-start is ever licensed.

### Recommendation — revised 2026-10-11: no USB data link

**The team decided against a USB data link from the Thor to the mount** (the Thor's USB bus is already
contested): the mount gets **one dumb charge connector** (§8). Route (a) therefore has to run **over Wi-Fi**
— Android's *wireless debugging*, with the Thor as the adb host — or not at all.

- **Daily use — the tablet sleeps** (screen off, app alive): it **wakes by itself** when the mount's power
  returns — which, with the mount on the drive bus (§8), is when the contactor closes after RUN on.
- **Storage — *Power the tablet off too*, by the Thor over wireless adb, IF the probe below shows it is
  robust.** The mechanism is the one proven over USB: the Thor leaves `sleep 120; svc power shutdown` on the
  tablet and halts. Otherwise storage power-off is **manual** (d).
- **The manual way always works** (d).

**What wireless adb needs, and what is still to be shown on the bench** (the bench tablet was not on a Wi-Fi
network when this was written, so none of this has been tried yet):

| question | expectation (unverified) | how the bench answers it |
|---|---|---|
| can the device owner keep it on? | `ADB_WIFI_ENABLED` is in AOSP's device-owner allowlist (checked in the source) | set it, read it back |
| does it survive a reboot, a Wi-Fi drop and a long sleep? | Android tends to switch wireless debugging **off** at boot and when the network changes, unless the network is "always allowed" | reboot; Wi-Fi off and on; an hour asleep — then read it, and reconnect |
| pairing | a **one-time pairing code** per host; the Thor's key then stays authorised | pair once, reconnect after a reboot without pairing again |
| finding it | its **port changes** each time it is enabled; the Thor finds it by mDNS (`_adb-tls-connect._tcp`) | `adb mdns services` after each change |
| reachable while asleep and in lock task? | the adb daemon is a system service, so lock task should not matter; Wi-Fi power saving might | connect to a screen-off tablet after 30 min |
| time to power-off | as over USB (under 8 s); the scheduled form outlives the Thor | the same two tests, over Wi-Fi |

**Exposure, stated rather than blocked on:** only a host whose key was **paired** can connect (TLS, key-based);
the pairing service is open only while someone has the pairing screen up on the tablet. Anyone on the robot's
LAN can see the port; only the Thor can use it. Consistent with the LAN-first stance.

**If wireless adb is not robust** — if it switches itself off at reboot and the device owner cannot keep it on
— route (a) is dropped and storage power-off is manual (d); the guidance below covers when sleeping is fine.

### Operator instructions — powering the tablet off by hand (route d)

1. **Hold the side key** until the power menu appears (about 1 s).
2. Tap **Power off**, then **Power off** again to confirm.
3. To turn it on: **hold the side key** until the Samsung logo shows.

Setup, once per tablet (added to the kiosk README when built):
`adb shell settings put global power_button_long_press 1`.

### When sleeping is fine, and when to power off

Sleep is fine **overnight and over a weekend** if the standby drain is as low as Android's screen-off
behaviour suggests — but **that is not measured yet**: the bench tablet was on USB throughout, which
charges it. **PW-1 measures it** (an hour unplugged, screen off, the app running, Android's charge counter
before and after), and the guidance becomes a number: *sleep if the robot will be used again within N
days; otherwise power off.* Until then: **power the tablet off if the robot will sit unused for more than
a few days.**

---

## 8. The mount's power — wiring (decided 2026-10-11)

**The tablet charges from one dumb USB-C charge connector at the mount, fed from the DRIVE bus**
(e-stop-switched) — the same bus as the neck controller (an M5 AtomS3R, also decided). So on an e-stop or at
RUN off the mount's power drops and the neck goes dark, as the Tab5 did; the tablet runs on its own battery and
shows E_STOP (DT-8); PRISM stays the safety channel (HRI §0).

**Consequence for the mount signal:** losing the mount's power means *the contactor opened*, not *lifted
off* — exactly the case DT-1 (as amended) handles: power counts only while the StamPLC says the contactor is
closed; the **magnet** (TS-7) is the presence signal that holds through an e-stop.

**What the tablet wants:** the SM-X133 charges at **15 W over USB-C**; its FCC filing names Samsung's 15 W
adapter (EP-T1510, a USB-C PD charger). Battery about **5,000 mAh** (4,980 rated; retailers say 5,100). Android's
own estimate on the bench reads 2.7 Ah, so its charge counter is not to be trusted as an absolute.

**A short parts recommendation — nothing bought:**

| part | what to look for | why |
|---|---|---|
| **a 24 V → USB-C PD converter** | input covering the drive bus (24 V nominal; check its range against the bus); **USB PD 3.0, 20 W or more** (offers 9 V at ≥2.2 A); a module meant for vehicles or panels, with its own input protection | 15 W into the tablet with margin; PD so the tablet negotiates 9 V rather than crawling at 5 V |
| **its own fused branch on the drive bus** | a spare channel of the drive bus's distribution (the REV PDH), fused for the converter's input current at 24 V (about 1–2 A) | a fault in the head's charger cannot take down the drive |
| **a magnetic breakaway USB-C connector at the mount** | rated for **PD at 9 V and 20 W or more** (some magnetic adapters pass 5 V only — check); the tip stays in the tablet | the tablet lifts off cleanly with no strain on its port, and drops back on without aiming a plug |

**What the CAD needs from this:** the connector's position must meet the tablet's USB-C port (on its short
edge) when the tablet seats; a short lead-in so the magnetic tip finds its socket; cable strain relief on the
robot side; and room for the converter in the head or on the lift, near the neck controller's own supply.
