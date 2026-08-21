# Base Frame — assembly photos

Curated, web-ready assembly photos for [docs/build/base-frame.md](../../../build/base-frame.md).

This folder is the **published, versioned** home for the *final* step photos — not the
raw archive. The team's full shoot lives on the Drive ("1 - Base Frame Build"); only the
chosen shots get converted and committed here so the guide is self-contained.

## Prep before committing
- **Convert HEIC → JPG or WebP.** Phone photos are HEIC, which browsers don't render.
- **Resize** to ~1600 px on the long edge, aim for **< 300 KB** each (these are docs, not prints).
- **Strip EXIF** (removes GPS/location metadata).

## Naming convention
`NN-short-description.jpg`, numbered to match the steps in the page:

- `01-frame-rectangle.jpg`
- `02-drive-pod.jpg`
- `03-pods-mounted.jpg`
- `04-leads-routed.jpg`
- `completed-01.jpg`, `completed-02.jpg` … (finished-frame reference shots)

Then replace the matching `TODO photo:` placeholder in the page with, e.g.:

```markdown
![Frame rectangle squared up](../assets/build/base-frame/01-frame-rectangle.jpg)
```
