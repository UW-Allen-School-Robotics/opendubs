# Diagrams — assets & convention

Exported / authored diagrams (Figma, CAD, etc.) live here as **web-ready images** (PNG or SVG),
referenced from the relevant page as a `<figure>` with a link back to the **editable source** —
the same pattern the build pages use for CAD (in-repo render + a Fusion permalink).

- **Static image in the repo** → renders offline and on Pages, and is versioned.
- **Source link out** (Figma share link / Fusion permalink) → the editable original, not committed.

Naming: `<topic>.png` (e.g. `power-architecture.png`). Keep the source link on the page itself.

!!! note "Mermaid is different"
    Text-authored **Mermaid** diagrams stay as `.mmd` source in `docs/diagrams/` and render
    inline. Use *this* folder only for exported/authored raster/vector images.
