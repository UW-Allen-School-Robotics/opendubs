// Every skin the face can wear, and how one is chosen.
//
// CODE SKINS are drawn by code in this folder; their names are fixed here, and the show validator
// (software/choreo/cue.py) keeps the same list. LIBRARY SKINS come from the robot's library: the
// entry names its `type` (image, video; later svg, riv) and its `params`.
//
// Choosing (head protocol v2, `SKIN <name>`): a code skin's name, or a library skin's id. `default`
// is the standard eyes, and an ordinary argument. A name that is not here, or a library skin whose
// file will not load, falls back to `default` and is reported — never a blank face. A reserved robot
// state (DEGRADED, E_STOP) draws `default` whatever is chosen (main.js).

import * as eyes from "./eyes.js";
import * as ring from "./ring.js";
import { makeAssetSkin } from "./asset.js";

export const CODE_SKINS = { default: eyes, ring };
const ASSET_TYPES = ["image", "video"];

export class Skins {
  constructor() {
    this.library = new Map();          // id -> library entry (kind: skin)
    this.baseUrl = null;
    this.current = CODE_SKINS.default;
    this.chosen = "default";           // what was asked for
    this.error = null;                 // why what was asked for is not what shows
  }

  async loadLibrary(baseUrl) {
    this.baseUrl = baseUrl;
    try {
      const r = await fetch(new URL("index.json", baseUrl), { cache: "no-store" });
      for (const e of await r.json()) {
        if (e.kind !== "skin") continue;
        if (e.id in CODE_SKINS) { console.warn(`library skin ${e.id} shadows a code skin; ignored`); continue; }
        this.library.set(e.id, e);
      }
    } catch (err) { this.libraryError = String(err); }
  }

  names() { return [...Object.keys(CODE_SKINS), ...this.library.keys()]; }

  // -> the reply's outcome: {ok, shown, error}
  select(name) {
    if (this.current && this.current.stop) this.current.stop();
    this.chosen = name; this.error = null;
    if (name in CODE_SKINS) { this.current = CODE_SKINS[name]; return { ok: true, shown: name }; }
    const e = this.library.get(name);
    if (!e) return this.fallBack(name, "unknown skin");
    if (!ASSET_TYPES.includes(e.type)) return this.fallBack(name, `type ${e.type} not built yet`);
    this.current = makeAssetSkin(e, this.baseUrl, (id, why) => { if (this.chosen === id) this.fallBack(id, why); });
    return { ok: true, shown: name };
  }

  fallBack(name, why) {
    this.current = CODE_SKINS.default;
    this.error = `${name}: ${why}`;
    return { ok: false, shown: "default", error: why };
  }

  shownName() { return this.current === CODE_SKINS.default ? "default" : (this.current.name || this.chosen); }
}
