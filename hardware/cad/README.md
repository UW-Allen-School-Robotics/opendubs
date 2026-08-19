# CAD

Fusion 360 source + neutral exports. **TODO — populate as the design finalizes.**

## Fusion permalinks (latest version)

<!-- Add public "share latest version" links here. End users can View, then Download → STEP / STL. -->
- Full assembly — _TODO: paste Fusion permalink_
- Chassis (`CH`) — _TODO_
- Drive module (`DT`) — _TODO_

## Exports (per hardware revision)

Recommended tree once populated:

```
cad/
  source/        # native .f3d / .f3z (Git LFS if committed)
  step/          # .step — neutral interchange (opens without Fusion)
  print/         # .3mf / .stl + a printed-parts table (material, layer height, infill, orientation)
  drawings/      # dimensioned .pdf for machined/cut parts
```

All geometry uses the frame in [`../../docs/naming-frames.md`](../../docs/naming-frames.md):
REP-103 (X-forward, Y-left, Z-up), origin at the rear-most 432 mm rail's center hole.
