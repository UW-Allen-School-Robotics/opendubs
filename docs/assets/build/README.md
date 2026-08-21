# Build photos — convention

Curated, web-ready photos for the assembly guides live under
`docs/assets/build/<module>/`. This is the **published, versioned** home for the *final*
step photos — the raw shoot stays on the team Drive; only chosen shots get committed.

## Prep
- **Convert HEIC → JPG/WebP** (phone photos are HEIC; browsers don't render them).
- **Resize** to ~1600 px on the long edge, aim **< 300 KB**; strip EXIF.
- Run `tools/convert-build-photos.sh <staging-dir> docs/assets/build/<module>/photos.manifest docs/assets/build/<module>`.

## Naming
`NN-short-description.jpg` matching the page's steps (`01-…`, `02-…`), plus
`completed-01.jpg` for finished shots and `hero.png` for the module hero.
