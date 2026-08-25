# Power Bay — assembly photos

Curated, web-ready assembly photos for [docs/build/power-bay.md](../../../build/power-bay.md).

This folder is the **published, versioned** home for the *final* step photos — not the
raw archive. The team's full shoot lives on the Drive ("5 - Power Bay Build"); only the
chosen shots get converted and committed here so the guide is self-contained.

## Prep before committing

See [the shared convention](../README.md). In short: WebP or JPG, ~1600 px long edge,
under 300 KB each, EXIF stripped. `tools/convert_build_photos.py` enforces all of that
from [`photos.manifest`](photos.manifest) — it needs no flags.

## Naming convention

The Drive folder groups shots into sub-folders **by process** rather than numbering them
across the build, and only "1 Back Plate Screw Mount" carries a number. So the prefix
here is the **step number on the page**, with a letter for position within that step:

- `01a-backplate-bare.webp` … `01d-modules-screwed-down.webp` — Step 1
- `02a-regen-clamp-carrier.webp` … — Step 2
- … through `06b-bay-overview.webp`

This differs from `base-frame/`, where the sources were already numbered in build order
and the prefix is the *source* number. Both follow `NN-short-description` from the shared
convention; they differ only in what NN counts, because the source folders differ.

## Source-name corrections

The Drive folder names carry a few typos, fixed in the target names but kept verbatim on
the manifest's left-hand side so a photo can still be traced back:

| On Drive | Here |
|---|---|
| `Regan Clamp Screw Terminal` | `02*-regen-clamp-*` (regenerative, not "Regan") |
| `… to GoBUILDA Rev I2C` | `05*-*` (the vendor is **goBILDA**) |
| `Front.jpg` / `Back.jpg` (regen clamp) | `02a-…-carrier` / `02b-…-resistor` — they are *before* and *after* the resistor is fitted, not two sides of one thing |
| `ResistorAdd.jpg` | `02c-regen-clamp-underside` — it shows the carrier's underside legend |
