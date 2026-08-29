# OTOS Module — assembly photos

Curated, web-ready assembly photos for [docs/build/otos.md](../../../build/otos.md).

This folder is the **published, versioned** home for the *final* step photos — not the raw
archive. The team's full shoot lives on the Drive ("2 - Optical Tracking Odometry Sensor
Module Build"); only the chosen shots get converted and committed here so the guide is
self-contained.

## Prep before committing

See [the shared convention](../README.md). In short: WebP or JPG, ~1600 px long edge, under
300 KB each, EXIF stripped. `tools/convert_build_photos.py` enforces all of that from
[`photos.manifest`](photos.manifest) — it needs no flags.

## Naming convention

The Drive folder splits into `Construction/` (bench build, **already numbered 1–16** in
build order) and `Attachment/` (fitting the module to the robot, labelled Part 1–3).

Numbering here runs straight through the page rather than restarting per folder:

- `01-…` … `16-…` — Construction. These **are** the source numbers, so any photo on the
  page traces back to its shot on Drive.
- `17-…` … `21f-…` — Attachment, continuing the sequence so the page reads in order.
  Letter suffixes (`21a`–`21f`) are the six views of the same mounted module.

The two `Wire_setup` HEICs land at `17`/`18` rather than with the rest of Attachment,
because they show the bus-side lead being made up — which has to happen before Part 1 puts
the converter into its housing.

## A note on the other modules

`base-frame/` prefixes by *source* number (its sources were numbered across the build);
`power-bay/` prefixes by *page step* (its sources were grouped by process, not numbered).
This folder is the mixed case: source numbers where they exist, continuing the page sequence
where they don't. All three follow `NN-short-description` from the shared convention — they
differ only in what NN counts, because the source folders differ.
