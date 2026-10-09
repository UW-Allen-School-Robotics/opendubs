# Troubleshooting reference and glossary — design

_Docs and BOM lane. Design only, 2026-10-08; nothing built. Requested by the orchestrator, from an
idea of the user's: an error/troubleshooting reference plus a glossary, with the dashboard linking
straight to the relevant entry._

## What exists today

Nothing on the robot speaks in stable names yet, so the registry introduces them rather than
cataloguing them.

- **Dashboard backend.** Every refusal is `JSONResponse({"ok": False, "error": <free text>}, status)`,
  sometimes with `hint` or `blockers`. No code field anywhere. `app.py:168` even chooses between 409
  and 400 by **matching a substring** of the error text ("still running") — exactly what a code
  field removes.
- **Partly-named strings.** `status.py:356-378` faults ("CONTACTOR WELDED", "PDH brownout",
  "OVERTEMP", "lift module not responding"); `supervisor.py:67-75` re-arm blockers ("E-STOP
  BUTTON still pressed", "STALE TELEMETRY (safety state unknown)", "CONTACTOR WELDED", "CH5 not
  OFF"); e-stop sources SOFTWARE / BUTTON / STALE TELEMETRY. Upper case with spaces — readable, not
  stable.
- **The one real code system is the show runner.** `software/choreo/runner.py` returns
  `(code, sentence)` pairs — `PRECONDITION_FAILED`, `INVALID_SHOW`, `PARSING_FAILURE`, aborts
  `ESTOP` / `DEADMAN` / `FAULT` / `AUTHORITY` / `OPERATOR_STOP` — and its result is
  `{id, status, code, cause}`. `choreo.js:74` shows `cause` and drops `code`. **This is the shape
  to copy.**
- **The head** explains why the lift would refuse in plain sentences (`app_face/net.cpp:177-185`,
  `:815`): "clear the e-stop at the dashboard", "set CH5 to MANUAL", "close the contactor",
  "transmitter is off or out of range", "no link to the robot", …
- **The StamPLC** logs `[SAFE]` reasons as sentences; `MAST` has **no refusal at all** — it zeroes
  the lift intent silently when not armed (`main.cpp:2298`).
- **The frontend** has no shared error surface. `setNotifs()` (`operate.js:148`, two call sites),
  `whyNot()` (`operate.js:84-93`), `noteArm()` (`teleop.js:133`), a banner (`app.js:779`). **No
  head-offline indicator exists.**
- **Docs.** `docs/operating.md` §"When something is wrong" is already a nine-row symptom table.
  No glossary; the terms are defined across `control-authority.md`, `e-stop.md`, `operating.md`.
  The head-loses-power-on-e-stop rule is in CLAUDE.md and one row of that table.

## The design

### 1. Registry: YAML, one file per lane

```
docs/reference/registry/
  safety.yaml        e-stop, contactor, RC, CH5, deadman, StamPLC link   (docs lane, until the StamPLC has one)
  power.yaml         PDH, battery, overtemp                               (docs lane, same reason)
  head.yaml          head offline, head refusals                          (Tablet lane)
  arms.yaml          arm refusals and faults                              (Arms lane)
  shows.yaml         show refusals and aborts                             (Choreography lane)
  glossary.yaml      terms                                                (docs lane; others propose)
```

**Why not one CSV like `parts.csv`.** A part is one line of facts. A troubleshooting entry is
prose — what it means, then an ordered list of things to check, with links — and a CSV cell holding
a markdown list is unreadable in a diff and impossible to review. YAML keeps one entry per block.
PyYAML already arrives with MkDocs, so neither CI nor the deploying machine gains a dependency.
(TOML via `tomllib` would need Python 3.11, and the dev Mac's system Python is 3.9.)

**Why one file per lane, not one shared file.** Every lane appending to one file is a merge
conflict per PR and makes the file a shared path forever. Per-lane files make ownership
mechanical: a lane edits only its own file, the owner is the filename (no `owner:` field to drift),
and the generator merges them. The Lanes table in CLAUDE.md gains one path per lane.

**Why under `docs/`.** The docs CI already triggers on it, and MkDocs copies it into the built site,
so the deployed dashboard could later read the registry itself (see *Later*).

**An entry:**

```yaml
- code: HEAD_OFFLINE
  title: The head is offline
  symptom: head                 # which symptom group it is listed under (below)
  seen_on: [dashboard, head]    # dashboard | head | transmitter | log | lights
  means: >
    The dashboard has not heard from the head for several seconds.
  do:
    - "**Check the e-stop first.** The head is powered behind the e-stop contactor, so an open
      contactor turns it off — it is not a network fault. Clear the e-stop and re-arm."
    - If the contactor is closed, …
  see: [e-stop.md, operating.md#re-arming]   # validated by mkdocs --strict
  derived: true                 # no code emits it; the dashboard infers it (exempt from the unused check)
```

**Names.** `UPPER_SNAKE`, globally unique, with a prefix by area: `ESTOP_`, `CONTACTOR_`, `RC_`,
`DRIVE_`, `LINK_`, `POWER_`, `LIFT_`, `HEAD_`, `ARM_`, `SHOW_`. The prefix is how a reader and
the checker tell areas apart; it is not the owner. The show runner's existing codes keep their
names on the wire, and the dashboard prefixes `SHOW_` before linking (`PRECONDITION_FAILED` →
`SHOW_PRECONDITION_FAILED`), so the runner does not change.

### 2. Organised by symptom, keyed by code

Operators see symptoms; code sees codes. The page does both: a short fixed list of **symptoms**
(defined in `safety.yaml`, so they are the docs lane's to change), each listing its entries **in the
order to check them**.

| symptom | first entries |
|---|---|
| The robot won't drive | `ESTOP_LATCHED`, `CONTACTOR_OPEN`, `RC_NO_LINK`, `RC_FAILSAFE`, `CH5_OFF`, `DEADMAN_RELEASED`, `DRIVE_ARMING`, `DRIVE_NOT_READY`, `DRIVE_CHARGER_CONNECTED` |
| Re-arm is refused | `ESTOP_BUTTON_PRESSED`, `LINK_TELEMETRY_STALE`, `CONTACTOR_WELDED`, `CH5_NOT_OFF`, `LINK_STAMPLC_LOST` |
| The head is offline or dark | `HEAD_OFFLINE` (contactor first), `HEAD_NO_ROBOT_LINK` |
| The lift won't move | `LIFT_NOT_ARMED`, `LIFT_RELAY_RESERVED`, `LIFT_NOT_RESPONDING`, `LIFT_PINCH_STOP` |
| The arms won't activate | Arms lane — `ARM_OP_RUNNING`, `ARM_CAN_NOT_READY`, `ARM_MOTORS_SILENT`, `ARM_MOTOR_FAULT`, `ARM_SERVICE_FAILED` (names theirs to set) |
| A show won't start, or stopped | Choreography lane — `SHOW_PRECONDITION_FAILED`, `SHOW_INVALID`, `SHOW_RUNNER_DOWN`, `SHOW_ABORT_*` |
| Power or temperature warning | `POWER_OVERTEMP`, `POWER_PDH_BROWNOUT`, `POWER_PDH_FAULT`, `POWER_BATTERY_LOW` |

Each entry gets a **stable anchor equal to its code, lower-cased** (`#head-offline`) — that anchor
is the contract the dashboard links to, so a title can be reworded without breaking a link. An entry
may appear under two symptoms; it is written once and listed twice.

`operating.md`'s "When something is wrong" table is folded in and replaced by a link, so there is
one symptom list, not two.

### 3. Generator and check: `tools/faults.py`

Same shape as `bom.py`:

- `--inject` regenerates the page between `<!-- FAULTS-AUTO:… -->` markers in
  `docs/reference/troubleshooting.md`, and the glossary between its markers in
  `docs/reference/glossary.md`.
- `--check` fails on:
  1. schema errors — missing field, unknown `symptom`, a bad prefix, a duplicate code across files;
  2. a stale page (as `bom.py --check`);
  3. **a code used in source that the registry lacks.** Scanned by explicit call sites, not by
     guessing at upper-case strings: `refuse("CODE"` in the backend, `helpLink("CODE"` in the
     frontend, and later the firmware's equivalent. A convention the checker can see is the whole
     point — a regex over every capitalised string would drown in false positives.
- It **warns**, not fails, on a registry entry no source uses, unless the entry is `derived: true`.

**CI.** The docs workflow only fires on `docs/**`, and check 3 must also run when someone edits the
dashboard. A small separate workflow — paths `docs/reference/**`, `tools/faults.py`,
`software/kiosk-dashboard/**`, `software/choreo/**`, `software/openarm/**` — runs only
`faults.py --check` (seconds, no MkDocs build). The docs workflow adds `faults.py --check` beside
`bom.py --check` for page staleness.

### 4. The wire contract

A refusal body gains one field, next to the text it already carries:

```json
{"ok": false, "code": "LIFT_RELAY_RESERVED", "error": "relays 1 and 2 are the lift legs — …"}
```

`error` stays, so nothing that reads it today breaks. A backend helper,
`refuse(code, text, status=409, **extra)`, is the only way to build one — which is also what makes
check 3 work. `blockers` becomes a list of `{code, text}`. The show runner already returns `code`;
the proxy passes it through unchanged.

### 5. The "?" on the dashboard — an overlay, never a navigation

**This is the decision that matters most.** The kiosk app's WebView has a plain `WebViewClient`
(`KioskActivity.kt:44`): **every link opens in place of the dashboard.** There is no URL allowlist to
satisfy, but there is something worse: following a docs link unloads the dashboard — its WebSocket,
the show audio, a teleop session, the microphone grant — and the only way back is the Back gesture
(`onBackPressed` → `goBack()`), on a tablet with its system bars hidden.

So `helpLink(code)` (one helper in `app.js`, shared) renders a small "?" that opens a **side sheet
with a same-origin iframe** of `/docs/reference/troubleshooting/#<code>`, and a Close button. The
dashboard never navigates. Two hard rules for the sheet:

- **It never covers the E-STOP button** (`#opEstop`, top bar). It opens below or beside it.
- It is dismissible with one tap, and it does not take focus from any control that is holding.

A same-origin iframe is only possible because the docs come from the robot (next section); an
iframe of GitHub Pages would need the internet and is outside the page's origin.

### 6. Docs served from the Thor

`deploy.sh` already runs on the dev machine and `rsync`s the frontend. It gains one step:
`mkdocs build -d <tmp>` there, then `rsync` the result to `frontend/docs/` on the Thor — with a
protect filter, as `shows/audio` already has, so the frontend's `--delete` does not remove it. The
existing static mount (`app.py:492`, `html=True`) then serves `/docs/` with **no backend change**,
on both 8080 and 8443.

What that buys: links work with no internet; the docs match the deployed code exactly, because they
were built from the same checkout; and they are on the dashboard's own origin, which the kiosk
already trusts. Cost: **35 MB** for the full site (measured today), a one-time copy and small deltas
after. The Thor never needs MkDocs.

Offline gaps to close, all small:

- `theme.font: false` — Material otherwise fetches Roboto from Google Fonts (falls back cleanly,
  but each page stalls on the request first).
- Mermaid diagrams load their library from a CDN and will not render offline. The troubleshooting
  and glossary pages use none; other pages degrade to the diagram source.
- Fusion 3D embeds and external images need the internet. Not on these pages.

### 7. Glossary

A separate page, `docs/reference/glossary.md`, generated from `glossary.yaml`, because an outside
replicator needs it without ever seeing a robot. The troubleshooting page links into it. First
terms: CH5 and its positions (OFF / MANUAL / AUTO), CH6 deadman, contactor, e-stop vs abort vs
stop, latch, lease, arbiter, the head, StamPLC, Thor, drive bus vs logic bus, PDH, BMS, pinch guard,
OTOS, Pinpoint, regen clamp.

Option, not committed: the same file can generate a MkDocs abbreviations snippet, giving every page
of the site a hover definition of each term. Worth trying once the glossary exists.

### 8. Who builds what

| piece | who | path status |
|---|---|---|
| schema, `tools/faults.py`, the two pages, CI workflow, `glossary.yaml` | Docs lane | own |
| `safety.yaml`, `power.yaml` seed entries | Docs lane, until the StamPLC has a lane | own |
| `head.yaml`; the head-offline indicator (none exists); whether the head protocol carries codes | Tablet lane | own |
| `arms.yaml`; `refuse()` at the arm endpoints; "?" in `teleop.js` / `armview.js` | Arms lane | own |
| `shows.yaml`; the `SHOW_` prefixing and "?" in `choreo.js` | Choreography lane | own |
| `refuse()` helper + converting `app.py`'s own safety/lift refusals and `supervisor.py` blockers | Docs lane, scoped | **shared** (`backend/app.py`) |
| `helpLink()` + the side sheet in `app.js` / `style.css` | Docs lane, scoped, Tablet lane reviews the look | **shared** |
| the docs step in `deploy.sh` | Docs lane, scoped | **shared** (`kiosk-dashboard/deploy/`) |

Order: the generator, pages and seed entries first (useful on GitHub Pages on their own); then
`refuse()` and `helpLink()`; then each lane converts its own refusals at its own pace. Nothing
requires a flag day — an endpoint with no `code` simply shows no "?".

## Found while designing — for other lanes

- **The lift refuses silently.** `MAST` zeroes its intent when not armed and reports nothing
  (`main.cpp:2298`), which breaks the control-authority rule that a monitor that changes behaviour
  must say so. StamPLC firmware — no lane; to the orchestrator.
- **No head-offline indicator** on the dashboard. Tablet lane.
- **`choreo.js` drops the runner's `code`.** Choreography lane; it is also the first "?" to wire.
- **`app.py:168` picks a status code by substring match.** Goes away with `refuse()`.

## Decisions for the user

1. **Serve the docs from the Thor** (35 MB per deploy target, built on the dev machine) — yes/no.
   Without it, "?" can only open GitHub Pages, which needs internet and navigates away from the
   dashboard.
2. **If the docs build fails during a deploy:** warn and deploy the dashboard anyway (recommended —
   a docs problem must never hold back a safety fix), or fail the deploy.
3. **The head entries are written for "the head", not the Tab5,** given the head may become a
   tablet. Confirm, or defer `head.yaml` until that is settled.
4. **Public.** The page is published on the public site with the rest of `docs/`. Entries are
   written for any replicator — no addresses, hostnames or sudoers detail. Confirm the
   troubleshooting page belongs on the public site rather than robot-only.

## Later

- The dashboard reads `registry.json` (generated beside the page) to show the one-line `title`
  inline, so the "?" is only needed for the full entry.
- Firmware codes: the head and the StamPLC report codes rather than sentences. A head protocol
  change, so agreed with the Tablet lane first.
