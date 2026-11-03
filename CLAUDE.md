# OpenDubs — repo guide for AI coding agents

This is the open-source build for **OpenDubs**, a low-cost UW research robot. Most
collaborative work right now is on the **assembly guide** (`docs/build/`) and its **BOM
system**. This file orients an agent (Claude Code or similar) fast — read it, then follow the
recipe that matches your task.

## Golden rules
- **Never hand-edit a generated BOM table.** Any table between `<!-- BOM-AUTO:… -->` markers is
  generated from `hardware/parts.csv`. Edit the CSV, then run `python3 tools/bom.py --inject`.
- **One source of truth:** `hardware/parts.csv`. The per-module Build BOMs *and* the global
  Purchase BOM are both views of it, so they can't drift.
- **Verify before you commit:** `python3 tools/bom.py --check` and `mkdocs build --strict` must
  both pass — CI runs them and fails the build otherwise.
- **Write for an outside replicator:** define jargon, state prerequisites, don't assume
  UW-specific context. Match the existing voice.
- **A test must earn its place.** Add a test or check only if it would catch a plausible failure
  that would otherwise reach the robot or a person, more cheaply than asking the human one
  question. If a person can simply look ("what colour is it?", "did the head turn?"), ask them;
  don't build a rig, a photo procedure or a simulation for it. Keep the gates that prevent damage
  or unsafe motion (voltage and pin order before plugging in, arms idle before a dashboard restart,
  the deploy guard), and keep regression tests for bugs that actually happened. Don't add tests
  that only restate the code, proof matrices wider than what the change could break, or at-robot
  checks of things that already worked. Every check slows CI and deploys; that cost is real.
- **Ask the human** before pushing, before anything destructive, or before big restructuring.
  Secrets never go in the repo (`.env` / keys belong in the git-ignored `ops/`).
- **Never push to `main`. Branch, then open a PR — every time.** `main` requires a pull request,
  but a maintainer's own push *succeeds anyway* and merely prints
  `remote: Bypassed rule violations for refs/heads/main`. The guard does not stop you; it only
  records that you ignored it. So branch **before the first commit**, not after. Let CI run on the
  PR, and do not merge or enable auto-merge unless asked.
- **Sign off every commit:** `git commit -s`, and `git merge --signoff` when you merge `main`
  into a branch. Hygiene's DCO check fails a PR with any commit, a merge commit included, that has
  no `Signed-off-by:` line (see `CONTRIBUTING.md`). To fix a branch: `git rebase --signoff
  origin/main`, then force-push your own branch with `--force-with-lease`.
- **Downloads never land in the working tree.** Anything fetched, pulled off a device or produced
  as a by-product (packages, firmware images, photos, logs, recordings) goes in a scratch directory
  outside the repo. Stage by path (`git add <files>`), never `git add -A` or `git add .`, and read
  `git status` before every commit. A 268 MB vendor app package pulled off a tablet was swept into
  a commit this way and stopped only by GitHub's file-size limit; a smaller file would have been
  published.
- **Never re-derive how to talk to the hardware.** Before writing a single command that flashes,
  resets, or reconfigures a board, read the table below and use the script. This rule exists
  because it has been broken repeatedly, at real cost: sessions have rebuilt the StamPLC and
  Tab5 flash procedures from first principles while `ota-from-thor.sh` and
  `docs/flashing-stamplc.md` sat unread — and one such attempt reset the robot's safety
  controller twice by opening a serial port that was never the right channel. If a procedure
  seems to be missing, say so and ask; do not experiment on the robot.
- **Shows have a skill: read it first.** Before you change a show, the composer, the studio, the
  twins or the runner, read `.claude/skills/opendubs-show/SKILL.md`. Most of what you might build
  already exists, and it lists the invariants no change may break.

## Lanes (team only)

The project team runs several agent sessions in parallel, one per area (a **lane**), coordinated by
an orchestrator session. Who owns which paths, and the rules the sessions follow, live in the team's
private `opendubs-team` repository, cloned to `~/opendubs-team`:

@~/opendubs-team/LANES.md

Outside contributors don't need it: the golden rules above and the recipes below are the whole guide.

## Hardware: how to flash each board

**Read this before touching a board. Do not improvise.**

| board | how | notes |
|---|---|---|
| **StamPLC** (ESP32-S3) | `sudo firmware/rc_serial_probe/ota-from-thor.sh` — on the Thor, **no IP** | Everyday path. Builds, serves the `.bin`, sends `OTA` over **UDP 5005**, watches it recover. **Not** over serial. **Do not go hunting for the board's address** — the script finds it (argument → `$OPENDUBS_STAMP_IP` → the dashboard's learned peer → `opendubs-stamplc.local`) and refuses one belonging to the Thor itself. `sudo` because PlatformIO must write `.pio/` under `/opt/opendubs`, which the deploying user owns. |
| StamPLC, first flash or recovery | `firmware/rc_serial_probe/flash-from-thor.sh`, or `pio run -t upload` over USB | Only for a virgin board or a corrupt image. Stop `opendubs-dash` first — it holds the port. See `docs/flashing-stamplc.md`. |
| Tab5 face (ESP32-P4) | **Retired, no longer on the robot.** The face is the web page on the head's Galaxy tablet (`software/face-web`), updated by the normal deploy. | `firmware/tab5_idf/` keeps its own flashing notes (`ota-from-thor.sh`, `FLASHING-THE-ROBOT.md`) for a Tab5 on a desk. |
| **Neck** (M5Dial, ESP32-S3) | OTA only: build `firmware/neck_dial/build.sh image`, copy `firmware.bin` to the Thor, serve it over HTTP there, then send `OTA <url>` to **UDP 4242**. Full recipe in `firmware/neck_dial/README.md`. **A `TILT` over UDP waits for a press of the Dial's button** (15 s) before it takes effect; serial `TILT` is not gated. | **No USB cable is routinely fitted.** It is on the 24 V bus, so a stranded neck means a visit to fit a USB cable for recovery; there is no serial console to fall back on remotely. The board PULLS the image; there is no push listener. Use `firmware.bin`, the app image, not `firmware.factory.bin`. **The head snaps to centre on boot**, not slewed, so warn whoever is standing there. `PING` reports the build's sha. |
| Neck, first flash or recovery | USB and `firmware/neck_dial/tools/flash.py` | Only for a virgin or bricked board. App-only at `0x10000`: a merged image at `0x0` erases `nvs` and takes the Wi-Fi credentials and the tilt limits with it. |

Full background: **`docs/flashing-stamplc.md`**, **`docs/stamplc-ethernet-transport.md`**, and
each firmware's own `README.md`.

## Config that changes runtime behaviour goes in the repo

If a file can change how the firmware behaves, it is tracked here — even when it belongs to an
external tree we only clone. For the Tab5 ESP-IDF work that means `firmware/tab5_idf/lv_conf.h`
and the modification list in `firmware/tab5_idf/README.md`. An untracked flag cannot appear in the
answer to "what did we change?": `LV_USE_DRAW_SW_COMPLEX_GRADIENTS` lived only in a cloned tree,
`git log -S` found nothing, and a freeze took several rounds to trace because of it.

Two debugging rules from the same incident. **Run-then-freeze means accumulation** — a cache, a
leak, fragmentation — where an over-budget frame fails immediately; they are different searches.
And **change one variable at a time**, or a failure cannot be attributed to either.

## The head: a Galaxy tablet on an M5Dial neck

The robot's face is the web page in `software/face-web`, shown by the kiosk app on the head's
Galaxy tablet and served by the dashboard; the M5Dial neck pans and tilts it. The Tab5 head and its
ESP-IDF firmware (`firmware/tab5_idf`) are **retired**: nothing on the robot runs them. Their notes
(`notes/design/tab5-*.md`, `firmware/tab5_idf/AUDIT.md`) stay as a record, not as instructions.

## Bench work on an M5Stack board over USB — the rote procedure

**This is settled. Do not investigate it, do not experiment, do not "verify" it. Follow it.**
It applies to every M5Stack board on a desk — Tab5, StamPLC, M5Dial — and only the port name and
the `-e` env differ. Re-deriving any of this has cost multiple sessions; everything below is the
answer, not a starting point.

### Flash

```sh
uv run --python 3.12 --with platformio --with intelhex pio run -e <env> -t upload --upload-port <port>
```

Ports: Tab5 `/dev/cu.usbmodem2101`. Always `/dev/cu.*`, never `/dev/tty.*`. No stub flags, no
`esptool` invocations, no bootloader button dance — **except M5Dial, which needs
`esptool --no-stub`** and whose crash-loop escape is holding G0 at power-on.

### Read telemetry

```sh
cat /dev/cu.usbmodem2101          # in a BACKGROUND task, or redirected to a file
```

- **Wait for USB to re-enumerate after any flash or reset before opening the port.** Opening it in
  the same breath as the reset yields a live file handle onto a dead endpoint: it reads **zero
  bytes forever** and looks exactly like crashed firmware. Put the read in a *separate* tool call.
- **`timeout` DOES NOT EXIST on this Mac** — neither `timeout` nor `gtimeout`. Any read written as
  `timeout N cat …` or `timeout N head -c …` fails instantly with *command not found*, produces
  **zero bytes, and looks exactly like dead firmware.** This wasted a sitting on 2026-10-04 and
  sent two investigations after imaginary faults. **Use a bare, self-terminating
  `head -c <bytes> /dev/cu.usbmodem2101`** — it blocks until that many bytes arrive and then exits
  on its own, which is why every read that ever worked here was written that way.
- Never use pyserial defaults, `screen`, or anything that asserts DTR/RTS: that resets an ESP32,
  and on a robot it has reset the safety controller.

### Getting data OFF a board — the rules that make it work first time

1. **`Serial.setTxTimeoutMs(0)` is mandatory** in `setup()`. Without it the first print blocks
   forever when no host is draining the port and the firmware hangs before doing anything.
2. **It also makes writes non-blocking, therefore lossy.** A bulk `Serial.write()` of more than
   roughly the 256-byte CDC FIFO silently discards the remainder. **Print line by line with a few
   ms between lines** — never one big block.
3. **Repeat every report on a timer** (5–20 s). Attaching resets nothing, but you will always miss
   boot-time output, and a result printed once is a result you cannot read.
4. **Do not design around host→device serial commands.** Whether writes to `/dev/cu.*` reach the
   device is *unverified* — DTR gating is a plausible reason and nothing here has established it.
   What is certain is that a feature triggered by a serial write cannot be distinguished from a
   broken feature when nothing happens. Anything you need to trigger must be **self-triggering in
   firmware**, on a timer, under a build flag, and must print its own verdict.
5. **A test that needs a human at the bench is not a test.** Nobody is usually there, and "no
   stimulus" reads identically to "broken hardware". Make the firmware produce its own stimulus and
   its own verdict, and have it say *inconclusive* rather than blame the hardware.

### Do not do these

Do not go looking for a board's IP. Do not add stub/no-stub flags to a working flash. Do not open a
serial port to "check" whether flashing worked. Do not re-measure a documented feature. Do not
write a bench that only reports on the panel.

**Build environment**, which has cost a session more than once:

- PlatformIO must run on **Python ≥ 3.10** — pioarduino (the only platform that builds ESP32-P4)
  refuses 3.9. `uv run --python 3.12 --with platformio --with intelhex pio run` works anywhere.
- `intelhex` is needed for the esptool image-packing step and is not pulled in automatically.
- Opening a serial port **asserts DTR/RTS and resets an ESP32**. If you must open one, clear
  both lines before writing, and never assume a command landed.

## The system in 30 seconds
- Module pages: `docs/build/<module>.md` (base-frame, otos, pinpoint, electronics-bay,
  power-bay, lidar). Each has hero, at-a-glance, CAD links, a **generated BOM**, tools,
  assembly steps, verify.
- `hardware/parts.csv` — one row per part. Columns:
  `id,name,type,group,sku,module,qty,need,source,notes,buy_qty,price_usd,price_source,price_date`
    - the four price columns go together: the price of one listed item (single, pack or kit), how
      many of it a robot needs, the listing, and the date read. `tools/bom.py` explains them.
    - `type`: `structure | component | fastener | tool | consumable | printed | fabricated`
    - `module`: a module id (`base-frame`, `otos`, …) **or** `shared` (used everywhere)
    - `group`: the sub-heading it appears under in that module's Build BOM
    - `source`: the buy link (shows as a clickable part name in the tables)
- `tools/bom.py` generates the tables + validates. Marker keys:
    - `build:<module>` → that module's grouped Build BOM (on the module page)
    - `purchase:{structure,fasteners,tools,consumables}` → global lists (on `docs/hardware-bom.md`)
    - `shared:{tools,consumables}` → only `module=shared` rows (on the Base Frame page)

## Recipe — add or change a BOM item
1. Edit `hardware/parts.csv` (copy an existing row for the column layout). Set `type`,
   `module`, a `group`, `qty`, and the buy link in `source`.
2. `python3 tools/bom.py --inject`  → regenerates every affected table.
3. `python3 tools/bom.py --check`   → must print `OK`.
4. Commit the CSV **and** the regenerated `.md` pages together.

## Recipe — add a troubleshooting entry
The troubleshooting page and the glossary (`docs/reference/`) are generated from
`docs/reference/registry/*.yaml` — one file per lane, the same idea as `parts.csv`. Schema and
usage are at the top of `tools/faults.py`.
1. Add the entry to **your lane's** registry file: a stable `code` (an `ARM_`, `SHOW_`, `HEAD_`…
   prefix), a `symptom` from `symptoms.yaml`, what it `means`, and what to `do`.
2. Name the code where the operator meets it: `refuse("CODE", …)` in the dashboard backend
   (`backend/refusals.py`), `helpLink("CODE")` or `setHelp(el, "CODE")` in the frontend
   (`app.js`), or an `@fault CODE` comment anywhere else.
3. `uv run tools/faults.py --inject`, then `--check` → must print `OK`. CI fails on a code the
   source uses that the registry lacks.

## Recipe — add assembly photos
1. Team photos are on the shared Drive, grouped by module, usually **HEIC**. Download the
   module's shots to a scratch folder.
2. Choose which to use in `docs/assets/build/<module>/photos.manifest` — one
   `SOURCE.HEIC   target.jpg` line per kept photo (convention: `docs/assets/build/README.md`).
3. Convert (macOS `sips`, converts + resizes):
   `tools/convert-build-photos.sh <scratch-dir> docs/assets/build/<module>/photos.manifest docs/assets/build/<module>`
4. Reference them in the page steps and replace the `*TODO photo:*` placeholders, e.g.
   `![Frame squared up](../assets/build/base-frame/01-frame-rectangle.jpg)`.
5. `mkdocs build --strict` — a missing image fails strict, so this catches typos.

## Recipe — flesh out a module page
- Edit prose freely; keep the BOM **inside** the `BOM-AUTO` markers (change the table via the CSV).
- Fill the `TODO`s: `Time` / `Difficulty`, the Fusion CAD permalink, STL exports, step text.
- Preview live: `mkdocs serve`, then open the local URL it prints.

## Recipe — add a NEW module
1. Create `docs/build/<id>.md` (copy an existing module as a template). Include an empty
   `<!-- BOM-AUTO:build:<id> -->` … `<!-- /BOM-AUTO:build:<id> -->` block.
2. Add it to `nav:` in `mkdocs.yml` under **Assembly Guide**.
3. `mkdir docs/assets/build/<id>` and add a `photos.manifest` stub.
4. Add its parts to `parts.csv` with `module=<id>`, then `python3 tools/bom.py --inject`.

## Map
| Path | What |
|---|---|
| `docs/build/` | module assembly pages |
| `docs/hardware-bom.md` | global Hardware/Purchase BOM |
| `hardware/parts.csv` | **the parts source of truth** |
| `tools/bom.py` | BOM generator + validator (`--inject`, `--check`, `--module <id>`) |
| `docs/reference/registry/` + `tools/faults.py` | troubleshooting + glossary source and generator (`--inject`, `--check`) |
| `tools/convert-build-photos.sh` | HEIC → web photos |
| `docs/assets/build/<module>/` | that module's photos (+ `photos.manifest`) |
| `mkdocs.yml` | nav + theme · `docs/stylesheets/extra.css` styling |
| `.github/workflows/docs.yml` | CI: runs `bom.py --check` + `mkdocs build --strict` |
