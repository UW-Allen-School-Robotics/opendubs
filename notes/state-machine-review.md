# Robot State Machine — Review & Gap Analysis

Status: review, June 2026. Scope: the robot-level FSM spec (`docs/state-machines.md`)
vs. the implemented derivation (`kiosk-dashboard/backend/robot_state.py` +
`kiosk-dashboard/backend/status.py`) vs. the safety model
(`docs/power-architecture.md`, `docs/control-authority.md`,
`notes/history/control-architecture.md`, since archived). This document records findings and
recommendations only — no code or spec changes are made here.

---

## 1. Executive summary

The implementation is a **stateless, priority-ordered derivation** (`derive()` in
`robot_state.py:83`), while the spec describes an **event-driven FSM with guarded
transitions** (`state-machines.md:21-30`). That single architectural difference
explains most of the gaps below: a pure function of the current telemetry frame
has **no memory**, so nothing can actually *latch*, and every "transition guard"
in the spec silently degenerates into "whatever the inputs say right now."

The five most consequential findings:

1. **`E_STOP_LATCH` does not latch.** Releasing the e-stop button (or POSTing
   `/api/estop/clear`) drops the robot straight back into whatever CH5 selects —
   including directly into `AUTONOMOUS_CONTROL` — with no pass through
   `SAFE_IDLE` and no re-arm checks (`robot_state.py:93-111`, `app.py:74-80`).
2. **The contactor-weld fault can never surface.** `welded` is only true *while*
   e-stopped (`status.py:179`), and e-stop outranks faults in the priority chain
   (`robot_state.py:94-102`), so `"CONTACTOR WELDED"` never produces `DEGRADED`
   — and the moment the e-stop sources clear, `welded` evaluates false again.
   A welded contactor (motion power that *cannot be cut*) is arguably the single
   most dangerous fault in the power architecture, and today it is invisible to
   the FSM.
3. **Stale StampPLC telemetry is not fail-safe.** `stamplc.reader.snapshot()`
   returns the *last-seen* DI/relay values forever (`stamplc.py:147-153`), and
   `_safety()`/`_robot_state()` ignore the `fresh` flag. If the StampPLC USB
   link dies, the dashboard keeps reporting the e-stop button, contactor, weld
   sense, and limit switches as they were — the robot can sit in
   `MANUAL_CONTROL` with its entire safety-sensing channel dead.
4. **`DEGRADED` has an entry but no defined exit.** The spec requires
   `DEGRADED → SAFE_IDLE` on "fault cleared + re-entry checks pass"
   (`state-machines.md:30`); the implementation exits `DEGRADED` the instant the
   fault strings disappear, directly into `MANUAL`/`AUTONOMOUS` if CH5 says so.
5. **INIT is a 4-second wall-clock timer, not a readiness check**
   (`status.py:23`, `status.py:238`). "Boot complete" per the spec means
   telemetry present + no hard faults; the implementation can declare boot
   complete with neither MAVLink nor StampPLC connected.

None of these are bugs in the sense of incorrect code — `robot_state.py` is
honest about being "an INDICATOR only" (`robot_state.py:7`). But the docstring
also says it is "the live, implemented FSM" (`state-machines.md:98-99`) and the
intended source of truth for the PRISM LED scheme, i.e. the thing a bystander
will read to judge whether the robot is safe to approach. The indicator should
therefore implement the latching/guard semantics of the spec, even though
*enforcement* rightly stays in hardware + StampPLC.

---

## 2. Divergence list (spec vs. code)

| # | Spec (`docs/state-machines.md`) | Implementation | Severity |
|---|---|---|---|
| D1 | Event-driven FSM with per-transition guards (`:21-30`) | Memoryless priority chain: estop → boot → faults → link/authority (`robot_state.py:93-114`). No transition history, no guard evaluation against a *previous* state. | High (architectural root cause of D2–D6) |
| D2 | `E_STOP_LATCH` is latched; exit implies guarded re-arm (the name, plus `ANY → E_STOP_LATCH` with no return edge defined → re-arm must be deliberate) | State clears the instant `estop_sources` empties; next frame lands wherever CH5 points. `/api/estop/clear` re-energizes relay 0 with **no guard** beyond a UI confirm — it does not check DI0 released, weld sense, or CH5 position (`app.py:74-80`). | High |
| D3 | `DEGRADED → SAFE_IDLE` only, guard "re-entry checks pass" (`:30`) | Exit is implicit and unguarded; can exit directly to `MANUAL_CONTROL`/`AUTONOMOUS_CONTROL` (`robot_state.py:100-111`). | High |
| D4 | `ANY → DEGRADED` guard: "fault is recoverable" (`:28`); unrecoverable faults → `E_STOP_LATCH` (`:29`) | All faults in the list → `DEGRADED` unconditionally; there is no recoverable/fatal classification, so no fault can ever escalate to `E_STOP_LATCH` (`robot_state.py:100-102`). | Medium |
| D5 | `SAFE_IDLE → AUTONOMOUS_CONTROL` guard: "autonomy ready + safety clear" (`:25`); `MANUAL ↔ AUTONOMOUS` guarded by "policy allows transfer" / "override accepted" (`:26-27`) | CH5 high + RC link is the *only* condition (`robot_state.py:109-111`). No autonomy-ready handshake exists (Thor's autonomy stack has no readiness signal), no transfer policy, no override-accept. | Medium (high once autonomy is real) |
| D6 | INIT exit guard: "boot complete, no hard faults" (`:23`) | `booting = uptime < INIT_GRACE_S (4.0 s)` (`status.py:22-23,238`). No check that MAVLink/StampPLC telemetry actually arrived. Conversely, e-stop *does* outrank INIT (good), but faults are masked during the grace window (`robot_state.py:97-102` ordering). | Medium |
| D7 | Local events map to "local containment **or** global transition" via supervisor policy (`:88-89`) | Mapping is implicit and inconsistent: mast limit inconsistency (DI1∧DI2) → subsystem `FAULT` only, robot mode unaffected (`status.py:236`, passed as `mast_fault`, never appended to `faults`); RC frame loss → **global** `SAFE_IDLE` even from `AUTONOMOUS_CONTROL` (`robot_state.py:103-105`), though the spec calls it a *local base-domain* event (`:84`); `base_fault` is plumbed but never set anywhere (`robot_state.py:84`, no producer in `status.py`); arm controller faults have no source at all (`status.py:204-208`). | Medium |
| D8 | Enablement matrix (`:68-74`) — rows `SAFE_IDLE`…`E_STOP_LATCH` | `ENABLE` (`robot_state.py:33-41`) matches the doc row-for-row and **adds an `INIT` row** (all off) that the doc lacks. Semantics match: enabled→"on", optional/conditional→"cond", disabled→"off", forced safe→"off". Minor: doc should gain the INIT row. | Low |
| D9 | `E_STOP_LATCH` forces subsystems to "DISABLED **or local safe state**" (`:44`) | `_subsys()` returns `DISABLED` before checking `has_fault` (`robot_state.py:71-74`), so a faulted subsystem's `FAULT` status is masked during e-stop. Acceptable display choice, but fault info is lost exactly when an operator is diagnosing. | Low |
| D10 | Subsystems are `ACTIVE` when actually driven (`:43`) | `"cond"` permission always maps to `READY`, never `ACTIVE` (`robot_state.py:78-79`) — the mast can never show ACTIVE in MANUAL, arms never in AUTONOMOUS. Base `ACTIVE` is inferred from mode+deadman, not motion feedback (`robot_state.py:120-121`, acknowledged "best-effort"). | Low |
| D11 | Global event "system watchdog timeout" (`:80`) | Not implemented anywhere. No Thor↔StampPLC or Thor↔Pixhawk watchdog feeds the FSM. (StampPLC staleness — the natural proxy — is ignored; see Finding 3 / E1.) | High |
| D12 | DI0 e-stop is active-low NC, "wire break reads as pushed" (`power-architecture.md:142-143`) | `ESTOP_BTN_ACTIVE_HIGH = True` bench convention (`status.py:42-46`); fail-safe inverted until the flip. Documented and intentional, but it is a one-line global that nothing will force anyone to remember — see R10. Same applies to `WELD_AUX_ACTIVE_HIGH` (`status.py:36-41`). | Medium (tracked) |
| D13 | `control-authority.md` defines a six-mode taxonomy (`manual_drive`, `assisted_drive`, `autonomous_nav`, `autonomous_override`, `service`, `fault_recovery`) | The FSM has no `service` or `fault_recovery` analog, and `autonomous_override` (RC reclaiming during autonomy, audited entry, timeout policy) has no state or guard. The two documents have never been reconciled. | Medium |
| D14 | Software e-stop is "logged/recoverable" (`power-architecture.md:46`) | `_software_estop` is a module global, `False` on import (`status.py:48`). A backend restart silently clears the SOFTWARE latch while relay 0 may still be open — producing the inconsistent display "not e-stopped, contactor de-energized, motion_enabled False" with robot state `MANUAL_CONTROL`. Not logged. | Medium |

---

## 3. Updated transition table (proposed)

This supersedes `state-machines.md:21-30`. New states: **`E_STOP_RECOVERY`** is
folded into a guard rather than a state (re-arm goes through `SAFE_IDLE`);
**`MANUAL_STANDBY`** is shown as a sub-state of `MANUAL_CONTROL` (see §5/R4);
**`TELEOP_CONTROL`** is new (see §5/R5). "Latched" means the condition is held
by the supervisor until explicitly cleared, not re-derived per frame.

| From | To | Trigger / Event | Guard |
|---|---|---|---|
| INIT | SAFE_IDLE | ready-checks pass | StampPLC fresh ∧ MAVLink fresh ∧ no e-stop source ∧ no latched fault ∧ DI/relay map plausible (DI0 readable, relay 0 state known) |
| INIT | DEGRADED | ready-check timeout (e.g. 30 s) | telemetry still missing/stale — *do not* fall through to SAFE_IDLE on a timer |
| SAFE_IDLE | MANUAL_CONTROL (STANDBY sub-state) | CH5 → RC_MANUAL | RC link fresh ∧ safety clear (contactor energized, no latched fault) |
| MANUAL_CONTROL: STANDBY → ENGAGED | CH6 deadman armed | deadman > center | RC link fresh (outputs enabled only here) |
| MANUAL_CONTROL: ENGAGED → STANDBY | deadman released **or** RC frame loss | — | force-neutral outputs (StampPLC gate) |
| SAFE_IDLE | AUTONOMOUS_CONTROL | CH5 → AUTONOMOUS ∧ autonomy-start | autonomy stack reports READY (heartbeat) ∧ safety clear ∧ supervisor ack |
| SAFE_IDLE | TELEOP_CONTROL | teleop session request (Quest connect) | operator-confirmed handover ∧ teleop link fresh ∧ teleop deadman armed ∧ safety clear |
| MANUAL_CONTROL | AUTONOMOUS_CONTROL | CH5 → AUTONOMOUS | transfer policy: base neutral ∧ autonomy READY; otherwise → SAFE_IDLE, not silent engage |
| AUTONOMOUS_CONTROL | MANUAL_CONTROL | CH5 → RC_MANUAL (operator reclaim) | always accepted (RC reclaim is the override path); autonomy gets a preempt notification |
| AUTONOMOUS_CONTROL | SAFE_IDLE | autonomy stack stops / heartbeat lost / supervisor link loss | force-neutral; latch reason for display |
| TELEOP_CONTROL | SAFE_IDLE | teleop link stale (> threshold) ∨ teleop deadman released ∨ operator ends session ∨ CH5 leaves OFF? (see §5/R5 authority note) | force-neutral |
| ANY | DEGRADED | recoverable fault asserted (latched) | fault classified recoverable (fault-class table) |
| ANY | E_STOP_LATCH | e-stop source asserts (DI0, SOFTWARE) ∨ fatal fault (incl. **latched CONTACTOR WELD**, StampPLC telemetry stale > threshold ⇒ treat as watchdog) | none (unconditional) |
| DEGRADED | SAFE_IDLE | operator clear action | underlying fault condition absent ∧ re-entry checks pass (same checks as INIT exit) — **never** auto-exit on the fault string vanishing |
| E_STOP_LATCH | SAFE_IDLE | operator re-arm action (UI confirm / physical sequence) | ALL sources released (DI0 read released, SOFTWARE cleared) ∧ weld sense shows contactor opened while it was commanded open ∧ CH5 = OFF (force the operator through neutral) ∧ relay 0 re-energize succeeds |
| E_STOP_LATCH | DEGRADED | re-arm attempted, weld detected | DI6 aux closed while contactor commanded open ⇒ latch `CONTACTOR_WELDED`; robot must not return to any control mode until physically serviced |

Notes:
- `E_STOP_LATCH` and `DEGRADED` exits are the only transitions requiring an
  explicit *operator action* — everything else is telemetry-driven. That is the
  core latching semantic the current code lacks.
- The `CH5 = OFF` guard on re-arm prevents the D2 failure (release e-stop with
  the switch still in AUTO → instant autonomy).

---

## 4. Event taxonomy → telemetry sources

Spec classification per `state-machines.md:76-89`; sources as implemented today.

| Event | Spec class | Telemetry source | Implemented? | Where |
|---|---|---|---|---|
| E-stop button | Global | StampPLC **DI0** (other pole of dual-pole NC buttons; hardware pole trips the coil directly) | Yes — but active-HIGH bench polarity (`ESTOP_BTN_ACTIVE_HIGH`, `status.py:46,161`) | `_safety()` |
| Software e-stop | Global | Module flag + `ESTOP` serial cmd → StampPLC drops **relay 0** | Yes (`app.py:65-71`, `status.py:48-57`) — non-persistent across restart (D14) | `_safety()` |
| Contactor state (effect, not cause) | — | **Relay 0** readback (coil drive) | Yes (`status.py:159,171`) — correctly excluded from `estopped` | `_safety()` |
| Contactor weld | Global (should be **fatal**) | **DI6** = GX11CAB SPDT aux contact (`power-architecture.md:145-147`) | Partially — computed only while estopped, never latched, never visible (Finding 2) | `status.py:174-179,226` |
| System watchdog timeout | Global | *Should be:* StampPLC snapshot `fresh`/`age` (`stamplc.py:147-153`) and MAVLink heartbeat age | **No** (D11) | — |
| Major power fault | Global | PDH `brn`/`hw` flags over StampPLC proprietary CAN | Yes → `DEGRADED` (`status.py:227-231`); per-channel breaker faults deliberately excluded (bench) | `_robot_state()` |
| RC frame loss | **Local** (base domain) | `RC_CHANNELS` age ≥ 2.0 s via MAVLink `/dev/ttyACM0` (Pixhawk) | Implemented as **global** → `SAFE_IDLE` (`status.py:220`, `robot_state.py:103`) — divergence D7 | `_robot_state()` |
| Authority select | Mode event | **CH5** (chan5_raw, index 4): <c−q OFF / mid RC_MANUAL / >c+q AUTONOMOUS, q = 25 % of range (`robot_state.py:47-58`) | Yes | `interpret_authority()` |
| Deadman | Mode guard (base domain per `:51`) | **CH6** (chan6_raw, index 5) > center (`robot_state.py:61-65`) | Read, but does **not** gate the MANUAL state — only annotates the reason string and the base ACTIVE flag (§5/R4) | `derive()` |
| Mast limit inconsistency | Local (mast domain) | **DI1 ∧ DI2** both asserted (`status.py:236`) | Yes — local containment only (mast → FAULT, robot unaffected); policy implicit (D7) | `_robot_state()` |
| Arm controller fault | Local (arm domain) | None — arm CAN not wired (`status.py:204-208`) | No (placeholder reports not-connected) | — |
| Boot complete | Lifecycle | 4 s monotonic timer (`status.py:22-23,238`) | Timer only, no readiness (D6) | `_robot_state()` |
| Shore / battery presence | Info only | DI3 / DI4 / DI5 | Yes — correctly *not* an FSM input | `_battery()`, `collect_fast()` |
| Autonomy ready / heartbeat | Mode guard (spec `:25`) | None — no autonomy stack signal exists | No (D5) | — |
| Teleop session / link / deadman | (proposed) | None — TELEOP tab is a placeholder (`frontend/index.html:263-265`, `capability-backlog.md:81-88`) | No | — |

---

## 5. Specific analyses

### 5.1 DEGRADED entry and exit

Entry is fine in shape (faults list → DEGRADED) but unlatched and unclassified
(D4). Exit is the gap: because `derive()` is memoryless, `DEGRADED` lasts
exactly as long as the fault *string* does. For a transient (PDH brownout flag
that clears), the robot bounces `MANUAL → DEGRADED → MANUAL` frame-to-frame —
no operator ever has to acknowledge that a brownout happened, and the
spec-required `DEGRADED → SAFE_IDLE`-only exit is violated.

**Propose:** supervisor-held fault latches. Each fault gets `(class: recoverable
| fatal, latched_at, cleared_by)`. `DEGRADED` exits only to `SAFE_IDLE`, only on
an explicit clear action (dashboard button → API, mirroring `/api/estop/clear`),
and only if the underlying condition currently reads absent **and** the same
ready-checks as INIT-exit pass (`state-machines.md:30` "re-entry checks pass" —
currently undefined; define it as the INIT ready-check list so there is one
checklist, used twice). Fatal-class faults route to `E_STOP_LATCH` instead,
restoring the spec's `ANY → E_STOP_LATCH` on "fatal fault" edge that is
currently unreachable.

### 5.2 Deadman × authority

`MANUAL_CONTROL` is entered on CH5=RC_MANUAL regardless of CH6
(`robot_state.py:106-108`); the unarmed case only changes the reason string and
keeps the base at `READY` instead of `ACTIVE` (`robot_state.py:120-121`). The
*hardware* is safe — the StampPLC gate force-neutrals without deadman — but the
display claims "MANUAL" (white/solid LED) when no human is actually holding the
robot. For a bystander-facing LED scheme that distinction matters: armed-manual
and parked-in-manual-mode are different risk levels.

**Propose:** keep one robot-level state (avoid state explosion) but make the
deadman a formal **sub-state**: `MANUAL_CONTROL/STANDBY` vs
`MANUAL_CONTROL/ENGAGED`, with distinct presentation (e.g. white *breathe* vs
white *solid*) in `STATE_STYLE`. Implementation: a `substate` field in
`derive()`'s return, keyed off `deadman`; ENABLE stays per the parent state but
base permission becomes `"cond"` in STANDBY. This also gives the spec's Mobile
Base FSM rule ("gate drive outputs on deadman and link health",
`state-machines.md:51`) a visible robot-level reflection.

### 5.3 TELEOP mode

The TELEOP dashboard tab exists (`frontend/index.html:263-265`) and the backlog
explicitly poses the question "MANUAL_CONTROL vs a dedicated TELEOP mode?"
(`capability-backlog.md:86-88`). Recommendation: **a dedicated
`TELEOP_CONTROL` state**, not a MANUAL sub-mode, because every FSM-relevant
property differs from RC manual:

- **Command path:** Quest → WiFi/WebRTC → Thor → MAVLink → Pixhawk, vs RC →
  SBUS → Pixhawk. Different link, different failure modes, different latency.
- **Deadman:** must be a teleop-native primitive (controller grip/trigger held,
  plus session heartbeat), since the RC transmitter may be off entirely. The
  backlog requirement "deadman + e-stop must apply to teleop the same as RC"
  (`capability-backlog.md:87-88`) is satisfied structurally: the StampPLC gate
  doesn't care *who* fails to assert enable — same Option-1 gating
  (`notes/history/control-architecture.md:58-71`, archived — line numbers are as of that revision).
- **Authority:** per the hierarchy (safety > supervisor > autonomy > RC,
  `control-authority.md:9-16`), teleop is a *supervisor-mediated* source —
  Thor injects setpoints exactly like autonomy does. So `TELEOP_CONTROL` slots
  in at autonomy's precedence, and the physical RC remains the recovery
  override: **CH5→RC_MANUAL must preempt teleop instantly**, same as it
  preempts autonomy. Entry should require CH5=OFF or =AUTONOMOUS? Recommend:
  CH5=OFF + explicit dashboard/operator handover, so the RC switch positions
  keep their meaning (OFF = "no RC claim", and teleop is a species of
  supervisor control claimed from OFF). Document the choice either way.
- **Enablement row:** `TELEOP_CONTROL: base on, mast cond, arms cond, cond` —
  teleop's whole point is the arms (Case 2 manipulation), unlike MANUAL where
  arms are off.
- **Exit/fail-safe:** teleop link stale beyond threshold, deadman release, or
  session end → `SAFE_IDLE` with force-neutral. Same pattern as RC frame loss
  but on the teleop link.

Concretely: add `TELEOP_CONTROL` to `ROBOT_STATES`, `STATE_STYLE` (suggest
magenta/solid, sev between MANUAL and AUTONOMOUS), `ENABLE`, and a
`teleop` input dict (`session`, `link_fresh`, `deadman`) to `derive()`; add the
state + row to `state-machines.md`.

### 5.4 E_STOP_LATCH re-arm and the welded contactor

Covered as Findings 1–2 and D2. The re-arm sequence should be, in order:

1. Operator clears the SOFTWARE source (if set) and releases/twists all
   physical buttons — DI0 must read released (with correct NC polarity).
2. Supervisor verifies the contactor actually **opened** during the latch:
   DI6 aux must have read open while relay 0 was de-energized. If aux stayed
   closed ⇒ latch `CONTACTOR_WELDED` (fatal class) → robot is mechanically
   unsafe; land in `DEGRADED` with a non-clearable-from-UI fault (requires the
   flag to be cleared by a service procedure, not a button). This is the
   correct answer to "where does a welded contactor land": **not plain
   DEGRADED-by-string, but a latched fatal fault that blocks re-arm** —
   functionally a dedicated `CONTACTOR_FAULT` condition even if it reuses the
   `DEGRADED` state for display.
3. CH5 must be OFF (forces re-entry through `SAFE_IDLE`).
4. Only then re-energize relay 0 (`RELAY 0 1`) and verify DI6 follows.

Because `welded` is currently computed instantaneously and only-while-estopped
(`status.py:179`), step 2 requires the weld check to be **edge-triggered and
latched at the supervisor**: sample DI6 a debounce interval after commanding
the contactor open, latch the result. That belongs in `status.py` (or a small
stateful supervisor module — see R1) rather than in the pure `derive()`.

### 5.5 INIT / boot grace

Replace the 4 s timer with real ready-checks (D6, transition table above).
Minimum set: StampPLC `fresh`, MAVLink `connected` + RC_CHANNELS seen at least
once OR explicitly waived (RC may legitimately be off), relay/DI vectors of
expected length, software-estop state reconciled with relay-0 readback (fixes
D14's inconsistent restart state: if relay 0 reads de-energized at boot and no
source explains it, latch SOFTWARE or a `STATE_MISMATCH` fault rather than
showing healthy). If checks don't pass within a timeout, go to `DEGRADED`
("telemetry not ready"), not `SAFE_IDLE`.

### 5.6 Subsystem rollup vs parent-child rule

The rollup honors the matrix and the E_STOP forced-safe rule. Gaps: D9 (FAULT
masked under e-stop — suggest returning `FAULT` even in `E_STOP_LATCH`, since
"forced safe" is about *outputs*, not display), D10 (`cond` can never be
ACTIVE — once mast/arm activity feedback exists, plumb an `active` flag per
subsystem the way `base_active` already works), and D7 (`base_fault` has no
producer — RC frame loss in MANUAL is the natural one per
`state-machines.md:84`, distinct from the global SAFE_IDLE fallback). Also the
parent-child rule "DEGRADED allows only explicit subset"
(`state-machines.md:45`) is implemented as blanket `cond`×4; eventually the
fault domain should pick the subset (mast fault ⇒ mast off, base still on).

---

## 6. Edge cases / failure modes worth explicit transitions

| # | Scenario | Current behavior | Proposed |
|---|---|---|---|
| E1 | **StampPLC telemetry stale** (USB unplugged, firmware hang) | Last DI/relay values reused indefinitely; FSM unchanged (Finding 3) | Treat `not stamplc.fresh` (beyond a short grace) as the spec's *system watchdog timeout* → `E_STOP_LATCH`-equivalent display (safety state unknown = assume unsafe). At minimum `DEGRADED` + all safety fields rendered "STALE". |
| E2 | **RC link loss mid-AUTONOMOUS** | → `SAFE_IDLE` ("no RC link") — autonomy display dies with the RC link even though autonomy doesn't use RC | Decide and document: either (a) RC link is a deliberate supervisory deadman for autonomy (then say so in `state-machines.md` and keep the behavior, but as `AUTONOMOUS → SAFE_IDLE [RC supervisory link lost]`), or (b) authority is held at last-known and only the *base* domain reacts. (a) is safer and nearly free — recommend (a). |
| E3 | **E-stop during AUTONOMOUS, then release with CH5 still AUTO** | Straight back to `AUTONOMOUS_CONTROL` next frame | Blocked by the re-arm guard `CH5 = OFF` (§5.4 / transition table). |
| E4 | **Fault during MANUAL while moving** | → `DEGRADED`; base → `FAULT`/`READY`; physical gating by StampPLC | OK, but add the latch (§5.1) so a flickering fault can't bounce states; force-neutral on entry is already the StampPLC's job — state should record `entered_from: MANUAL_CONTROL` for the operator. |
| E5 | **MAVLink RC frame older than 2 s but StampPLC fine** | `authority=None` → `SAFE_IDLE` — correct fail-safe | Keep; the 2.0 s threshold appears twice (`status.py:118,220`) — hoist to one constant. |
| E6 | **Backend restart while software-e-stopped** | SOFTWARE source lost; contactor still open; state shows healthy-but-no-motion (D14) | Persist the software latch (file under `/run` or reconcile-from-relay at boot, §5.5). |
| E7 | **Welded contactor discovered at e-stop** | Invisible (Finding 2) | Latched fatal fault blocking re-arm (§5.4). |
| E8 | **CH5 flicker / switch bounce across the quarter-range thresholds** | Instant state changes, MANUAL↔AUTONOMOUS with no debounce or transfer policy | Add hysteresis/debounce (e.g. require N consistent frames) in `interpret_authority()` callers, plus the transfer-policy guard (D5). |

---

## 7. Prioritized recommendations

> **Implemented 2026-06-11:** R1 (supervisor), R2 (stale fail-safe), R3/R4
> (weld-aware guarded e-stop re-arm + latched DEGRADED with guarded clear), R5 (INIT
> ready-checks). `kiosk-dashboard/backend/supervisor.py` holds the latch/fault/ready
> state and feeds `derive()` latched inputs; `derive()` stays pure. New endpoints:
> `/api/estop/clear` is now guarded (refuses on button-pressed / stale / welded /
> CH5≠OFF → HTTP 409 + blockers) and `/api/fault/clear` clears non-fatal latched
> faults once their condition is absent. **Refinement beyond the table:** `STALE
> TELEMETRY` is a *live, self-recovering* fail-safe source (forces E-stop display
> while the link is dead, auto-clears on recovery) — only `BUTTON`/`SOFTWARE` latch,
> since latching a transient USB blip / reboot would force a needless manual re-arm.
> Still open: R6 (deadman sub-state), R7 (TELEOP state — next), R8 (full fault-class
> table / fatal→E_STOP routing; today fatal faults are non-clearable DEGRADED), R9–R12.

Safety-relevant first. "Supervisor" below = a small stateful layer; see R1.

1. **R1 — Add a minimal stateful supervisor wrapper** (new ~80-line module, e.g.
   `kiosk-dashboard/backend/supervisor.py`, called from `status._robot_state()`
   between input-gathering and `robot_state.derive()`). It owns: previous
   state, fault latches, weld latch, e-stop re-arm gating, debounce. Keep
   `derive()` pure — the supervisor feeds it *latched* inputs instead of raw
   ones. This single change is the enabler for R2–R6.
2. **R2 — Fail-safe on stale StampPLC telemetry** (E1/D11): in
   `status._safety()` and `_robot_state()`, when `snapshot()['fresh']` is
   false beyond ~2 s, report safety fields as unknown and drive the FSM to the
   watchdog-timeout state. Highest-value safety fix; ~10 lines.
3. **R3 — Latch the weld fault and gate re-arm on it** (Finding 2/§5.4): weld
   check becomes edge-triggered at contactor-open command in the supervisor;
   `/api/estop/clear` (`app.py:74-80`) refuses while `CONTACTOR_WELDED` or
   while DI0 still reads pressed or CH5 ≠ OFF. Update
   `power-architecture.md`'s dashboard-mapping note accordingly.
4. **R4 — Real `E_STOP_LATCH`/`DEGRADED` exit semantics** (Findings 1, 4): both
   exit only to `SAFE_IDLE`, only on operator clear, with the shared
   ready-check list (also used by R5). Spec change: replace
   `state-machines.md:30` with the §3 table rows; code change: supervisor.
5. **R5 — Replace INIT timer with ready-checks** (D6/§5.5), including the
   relay-0-vs-software-flag reconciliation that fixes D14.
6. **R6 — Deadman sub-state** (§5.2): `MANUAL_CONTROL` STANDBY/ENGAGED in
   `derive()` + `STATE_STYLE`, so the LED scheme distinguishes armed manual.
7. **R7 — Add `TELEOP_CONTROL`** (§5.3): state + ENABLE row + STYLE in
   `robot_state.py`; spec section + matrix row in `state-machines.md`;
   authority/handover rules (CH5 preemption) in `control-authority.md` — which
   also closes its open decision on override-mode guards for the teleop case.
8. **R8 — Classify faults (recoverable vs fatal)** (D4): small table in
   `robot_state.py` or the supervisor; fatal → `E_STOP_LATCH`, restoring the
   spec edge. Initial classification: CONTACTOR WELDED = fatal; PDH hw = fatal;
   PDH brownout = recoverable; mast limit inconsistency = local (mast only).
9. **R9 — Decide & document RC-loss-during-autonomy policy** (E2) and the
   `MANUAL↔AUTONOMOUS` transfer guard + CH5 debounce (D5/E8). One paragraph in
   `state-machines.md`, ~15 lines in supervisor.
10. **R10 — Polarity-flip tripwire** (D12): when the NC button/weld aux are
    wired, `ESTOP_BTN_ACTIVE_HIGH`/`WELD_AUX_ACTIVE_HIGH` must flip. Add a
    loud "BENCH POLARITY" banner on the dashboard SAFETY card while the
    active-high bench convention is in effect, so the unsafe polarity is
    impossible to forget. (`status.py:36-46`.)
11. **R11 — Spec hygiene** (D8/D13): add the INIT row to the enablement matrix;
    reconcile the `control-authority.md` mode taxonomy with the FSM states
    (map `manual_drive`→MANUAL, `autonomous_nav`→AUTONOMOUS,
    `fault_recovery`→DEGRADED, `service`→future, `autonomous_override`→guarded
    sub-mode of AUTONOMOUS or fold into R7's preemption rules); regenerate the
    Mermaid diagrams (`tools/generate_state_machine_mermaid.py`) once the
    transition table is updated.
12. **R12 — Subsystem rollup polish** (D9/D10/D7): surface `FAULT` under
    e-stop, plumb per-subsystem activity, give `base_fault` its RC-frame-loss
    producer.

---

## 8. What is working well (keep)

- The **cause/effect separation** in `_safety()` (sources vs contactor) is
  exactly right and well-commented (`status.py:147-158`).
- `derive()` as a **pure, testable function** is the right shape — R1
  deliberately preserves it.
- The **priority ordering** (safety > boot > fault > mode) matches the
  authority hierarchy; only the latching around it is missing.
- The implemented ENABLE matrix faithfully matches the spec's matrix (D8 is
  the doc lagging the code, not a code bug).
- Deliberate exclusion of bench-expected PDH per-channel faults from DEGRADED
  (`status.py:232-234`) shows the right instinct: fault *policy*, not raw
  rollup. R8 just makes that policy a table.
