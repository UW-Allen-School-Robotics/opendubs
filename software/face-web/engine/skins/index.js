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
import * as fireworks from "./fireworks.js";
import {
  monster_stitched, monster_vampire, monster_wolf, monster_mummy,
  monster_zombie, monster_ghoul, monster_lab,
} from "./monsters.js";
import { makeAssetSkin } from "./asset.js";

// ON ONE LINE, and the names in the same ORDER as the other lanes' lists: the head protocol's
// cross-check reads this literal as TEXT, a line at a time, and compares the sequence.
export const CODE_SKINS = { default: eyes, ring, fireworks, monster_stitched, monster_vampire, monster_wolf, monster_mummy, monster_zombie, monster_ghoul, monster_lab };
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

  /**
   * Resolves when the skin now selected can actually draw. True if it can, false if its asset
   * failed or did not arrive inside `timeoutMs`.
   *
   * FOR AN OFFLINE RENDER, and nothing else needs it. The live face is a loop at 60 fps: an asset
   * skin returns false from `draw` until it has decoded, the caller stands the default eyes in for
   * a few frames, and nobody sees it. A render writes frames as fast as it can, with no wall clock
   * holding it back, so the SAME behaviour puts the default eyes in the first second of the video.
   * Awaiting this before the first frame is what fixes that.
   *
   * A code skin resolves at once -- there is nothing to load -- so a renderer can await this
   * unconditionally after every `select()` without caring which kind of skin it got.
   *
   * IT RESOLVES RATHER THAN HANGS. An asset whose load neither succeeds nor errors (a dead network)
   * would otherwise stall the whole render with no output and no reason given; the cap turns that
   * into a false, which the caller can report. The error itself already went to the onError path.
   */
  skinsReady(timeoutMs = 10000) {
    const s = this.current;
    if (!s || typeof s.ready !== "function") return Promise.resolve(true);   // a code skin: nothing to load
    if (!(timeoutMs > 0)) return Promise.resolve(s.ready());
    let timer;
    const capped = new Promise((res) => { timer = setTimeout(() => res(false), timeoutMs); });
    return Promise.race([Promise.resolve(s.ready()), capped]).then((v) => { clearTimeout(timer); return v; });
  }

  /** The same thing under the name the page calls it by. One implementation, two callers. */
  ready(timeoutMs = 10000) { return this.skinsReady(timeoutMs); }

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
