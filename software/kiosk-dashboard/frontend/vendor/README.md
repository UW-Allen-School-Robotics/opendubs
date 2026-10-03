# Vendored browser libraries

| | | licence |
|---|---|---|
| `three.module.min.js` | three.js r160.1, ESM build | MIT |
| `STLLoader.js` | three.js r160.1 `examples/jsm/loaders/STLLoader.js` | MIT |

Vendored rather than loaded from a CDN because the dashboard is the robot's operator interface and
must work on a field network with no internet. The tablet caches them after the first load.

**One modification:** `STLLoader.js` imports the bare specifier `'three'`, which a browser cannot
resolve without an import map. It is rewritten to `'./three.module.min.js'` — a relative path the
browser resolves directly, so no import map is needed and nothing else has to know these exist.

Used only by `armview.js`. Refresh by re-downloading both from the same release and re-applying
that one rewrite.
