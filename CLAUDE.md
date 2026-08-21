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
