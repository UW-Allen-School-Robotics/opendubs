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
- **Ask the human** before pushing, before anything destructive, or before big restructuring.
  Secrets never go in the repo (`.env` / keys belong in the git-ignored `ops/`).
- **Never push to `main`. Branch, then open a PR — every time.** `main` requires a pull request,
  but a maintainer's own push *succeeds anyway* and merely prints
  `remote: Bypassed rule violations for refs/heads/main`. The guard does not stop you; it only
  records that you ignored it. So branch **before the first commit**, not after. Let CI run on the
  PR, and do not merge or enable auto-merge unless asked.
- **Never re-derive how to talk to the hardware.** Before writing a single command that flashes,
  resets, or reconfigures a board, read the table below and use the script. This rule exists
  because it has been broken repeatedly, at real cost: sessions have rebuilt the StamPLC and
  Tab5 flash procedures from first principles while `ota-from-thor.sh` and
  `docs/flashing-stamplc.md` sat unread — and one such attempt reset the robot's safety
  controller twice by opening a serial port that was never the right channel. If a procedure
  seems to be missing, say so and ask; do not experiment on the robot.

## Hardware: how to flash each board

**Read this before touching a board. Do not improvise.**

| board | how | notes |
|---|---|---|
| **StamPLC** (ESP32-S3) | `sudo firmware/rc_serial_probe/ota-from-thor.sh` — on the Thor, **no IP** | Everyday path. Builds, serves the `.bin`, sends `OTA` over **UDP 5005**, watches it recover. **Not** over serial. **Do not go hunting for the board's address** — the script finds it (argument → `$OPENDUBS_STAMP_IP` → the dashboard's learned peer → `opendubs-stamplc.local`) and refuses one belonging to the Thor itself. `sudo` because PlatformIO must write `.pio/` under `/opt/opendubs`, which the deploying user owns. |
| StamPLC, first flash or recovery | `firmware/rc_serial_probe/flash-from-thor.sh`, or `pio run -t upload` over USB | Only for a virgin board or a corrupt image. Stop `opendubs-dash` first — it holds the port. See `docs/flashing-stamplc.md`. |
| **Tab5 face** (ESP32-P4) | `firmware/tab5_face/ota-from-thor.sh [host]` — run **on the Thor** | **THE TAB5 IS POWERED FROM THE E-STOP PROTECTED DRIVE BRANCH.** With the contactor open it has no power — it is off, not offline. So flash the StamPLC **first**, clear the e-stop, and only then the head; and when the head looks like a network fault, check the contactor before anything else. Run the script **on the Thor** — a laptop on the same subnet may not reach the head. `espota` has never worked here (listener behind the ESP-Hosted co-processor, fails silently). |

Full background: **`docs/flashing-stamplc.md`**, **`docs/stamplc-ethernet-transport.md`**, and
each firmware's own `README.md`.

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
  `id,name,type,group,sku,module,qty,need,source,notes`
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
| `tools/convert-build-photos.sh` | HEIC → web photos |
| `docs/assets/build/<module>/` | that module's photos (+ `photos.manifest`) |
| `mkdocs.yml` | nav + theme · `docs/stylesheets/extra.css` styling |
| `.github/workflows/docs.yml` | CI: runs `bom.py --check` + `mkdocs build --strict` |
