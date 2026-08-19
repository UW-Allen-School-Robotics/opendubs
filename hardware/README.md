# OpenDubs hardware

Mechanical + electrical design assets. **Being populated as the second unit is built.**

## Layout

- **`cad/`** — CAD source and neutral exports (see `cad/README.md`).
- **`bom/`** — bill of materials (see `bom/README.md`). The rendered BOM lives at
  [`docs/hardware-bom.md`](../docs/hardware-bom.md).
- **`renders/`** — exploded views and assembly figures for the build guide.

## CAD approach (Fusion 360)

Fusion is the working environment. For open-source reproducibility we ship three layers:

1. **Live link** — a Fusion **permalink to the latest version**, so anyone can view and download
   **STEP** or **STL** without a Fusion account. (Links listed in `cad/README.md`.)
2. **Neutral exports** — **STEP** (interchange, opens in FreeCAD/Onshape/SolidWorks) and **3MF/STL**
   (printing) checked in or attached per release.
3. **Release snapshots** — on each hardware revision, snapshot the STEP/3MF exports into a GitHub
   Release so there is a permanent, offline, citable archive that doesn't depend on the cloud file
   staying alive. Live link for working; release snapshots for the record.

## BOM approach

Single source of truth is a **CSV** (`bom/bom.csv`, diffable/scriptable), rendered into the docs.
Prices are indicative and dated; note currency. See `bom/README.md` for the schema.

## License

Hardware designs are licensed **CERN-OHL-W-2.0** — see [`../LICENSE-HARDWARE`](../LICENSE-HARDWARE).
