// Every skin the face can wear, and how one is chosen.
//
// BUILT-IN CODE SKINS are drawn by code in this folder; their names are fixed here, and the show
// validator (software/choreo/cue.py) keeps the same list. LIBRARY SKINS come from the robot's
// library: the entry names its `type` and its `params`.
//
// A CODE SKIN MAY NOW COME FROM THE CONTENT FOLDER, not only from this folder. The dashboard
// discovers `<content>/face/skins/<id>.js` and lists it in `/api/head/looks`; the face imports it
// and uses it exactly as a built-in. That is what lets a skin live in the private content
// repository instead of in this one.
//
// IT IS A CODE SKIN, NOT A LIBRARY ENTRY, and that was a real decision. The library is a catalogue
// of MEDIA -- sizes, hashes, durations, dimensions -- and making it hold a module meant carving
// exceptions into a schema for the one thing it was never built for. The line that matters is CODE
// versus ASSET, not in-repo versus content. Two signs confirmed it: cue.py's sparkle check uses
// "not in CODE_SKINS" to mean "a picture skin", which stays true this way and breaks the other; and
// library assets are synced by library_sync while content is deployed as a repository, so sharing
// one index would have implied the wrong owner.
//
// WHY THAT MATTERS BEYOND TIDINESS: a built-in's name has to be written into FOUR files across
// THREE lanes -- here, head_protocol.py, cue.py and library.py -- with cross-checking tests, so
// every new skin was a lockstep commit, and a partial one went green and broke another lane's next
// PR. A library code skin needs none of that: it is discovered, exactly as an image skin is, and
// the show validator checks it against the content on the machine and says NOTICE rather than
// PROBLEM when there is none. The built-in list stops churning and becomes what it should always
// have been -- the handful the engine itself ships.
//
// Choosing (head protocol v2, `SKIN <name>`): a code skin's name, or a library skin's id. `default`
// is the standard eyes, and an ordinary argument. A name that is not here, or a library skin whose
// file will not load, falls back to `default` and is reported — never a blank face. A reserved robot
// state (DEGRADED, E_STOP) draws `default` whatever is chosen (main.js).

import * as eyes from "./eyes.js";
import * as ring from "./ring.js";
import * as fireworks from "./fireworks.js";
import { makeAssetSkin } from "./asset.js";

// ON ONE LINE, and the names in the same ORDER as the other lanes' lists: the head protocol's
// cross-check reads this literal as TEXT, a line at a time, and compares the sequence.
export const CODE_SKINS = { default: eyes, ring, fireworks };
const ASSET_TYPES = ["image", "video"];

// What a CONTENT code skin has to be before it is allowed to draw. A built-in is checked by the
// compiler and by review; one arriving from the content folder is checked here, because the
// failure it prevents is a BLANK OR THROWING FACE -- and `draw` is called once per eye per frame,
// so a bad module would throw sixty times a second with nothing saying why.
function usableCodeSkin(mod) {
  if (!mod || typeof mod.draw !== "function") return "no draw() exported";
  if (mod.name !== undefined && typeof mod.name !== "string") return "name is not a string";
  if (mod.stop !== undefined && typeof mod.stop !== "function") return "stop is not a function";
  return null;
}

export class Skins {
  constructor() {
    this.library = new Map();          // id -> library entry (kind: skin)
    this.content = new Map();          // id -> {url} for a content code skin the dashboard lists
    this.code = new Map();             // id -> an imported code skin, once it has loaded
    this.looksUrl = null;              // /api/head/looks, re-read once when a name is unknown
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

  /**
   * The content folder's code skins, from the dashboard's `/api/head/looks`.
   *
   * SEPARATE FROM THE LIBRARY ON PURPOSE (see the header): the library is media, this is code, and
   * they arrive on the robot by different routes. Failing is not fatal — a robot with no content,
   * or a dashboard too old to answer, simply has the built-ins.
   */
  async loadLooks(looksUrl, contentBase) {
    this.looksUrl = looksUrl;
    this.contentBase = contentBase;
    try {
      const r = await fetch(looksUrl, { cache: "no-store" });
      const doc = await r.json();
      this.content.clear();
      for (const s of (doc && doc.skins) || []) {
        if (s.kind !== "code" || s.source === "builtin") continue;
        // A CONTENT SKIN MAY NOT TAKE A BUILT-IN'S NAME. Silently preferring one or the other is
        // how a show gets a face nobody can account for; saying so costs a line.
        //
        // THIS IS THE BACKSTOP, NOT THE PRIMARY GUARD, and it is worth knowing which. The
        // dashboard's discovery already refuses a shadowing id and reports it in the looks
        // document's `problems`, where a person can see it -- so in normal operation this branch
        // never runs. It stays because the face should not be fooled by a looks payload that is
        // wrong, and because an unreachable guard is cheap where the thing it prevents is a robot
        // wearing a face no one can account for. Do not delete it as dead code; do not rely on it
        // instead of the dashboard's, which is the one that tells anybody.
        if (s.id in CODE_SKINS) { console.warn(`content skin ${s.id} shadows a built-in; ignored`); continue; }
        this.content.set(s.id, { id: s.id, url: new URL(`${s.id}.js`, contentBase).href });
      }
    } catch (err) { this.looksError = String(err); }
  }

  names() { return [...Object.keys(CODE_SKINS), ...this.content.keys(), ...this.library.keys()]; }

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
    // A LIBRARY CODE SKIN THAT IS STILL IMPORTING counts as not ready, for the same reason an
    // undecoded image does: an offline render would otherwise write its first frames with the
    // default eyes standing in.
    const pending = this.content.get(this.chosen);
    if (pending && !this.code.has(this.chosen)) {
      return this.importCode(this.chosen, pending).then((ok) => !!ok);
    }
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
    // A CONTENT CODE SKIN, which behaves as a built-in once it is here.
    const c = this.content.get(name);
    if (c) {
      // ALREADY IMPORTED: use it with no await. A module is fetched once and cached, so a show
      // selecting the same skin on a later beat must not pay a round trip, and must not go through
      // the default eyes on the way.
      const had = this.code.get(name);
      if (had) { this.current = had; return { ok: true, shown: name }; }

      // NOT YET: the default eyes draw while it loads, exactly as they do for an undecoded image,
      // and the skin takes over when it arrives. `select` stays synchronous because the protocol's
      // reply says what was ACCEPTED, and the page cannot hold a UDP reply open for a fetch.
      this.current = CODE_SKINS.default;
      this.importCode(name, c);
      return { ok: true, shown: name };
    }

    const e = this.library.get(name);
    if (!e) return this.fallBack(name, "unknown skin");
    if (!ASSET_TYPES.includes(e.type)) return this.fallBack(name, `type ${e.type} not built yet`);
    this.current = makeAssetSkin(e, this.baseUrl, (id, why) => { if (this.chosen === id) this.fallBack(id, why); });
    return { ok: true, shown: name };
  }

  // Import a CONTENT code skin and, if it is still the one chosen, put it on. NEVER A LIBRARY
  // ENTRY: the library is uploaded through the dashboard, and the maintainers' rule is that DATA
  // arrives that way while CODE arrives only through a repository. That rule is kept by the shape
  // of `select` -- a library entry can only ever become an asset skin, gated on ASSET_TYPES -- and
  // this is the only caller of `import()`. Do not give it a library entry; the upload path is not
  // review, and a module imported from one would run on the robot having passed nobody. Late
  // arrival is
  // handled the same way an image's is: whatever is current when it lands decides whether it shows,
  // so a show that has already moved on is not interrupted by a module finishing late.
  async importCode(name, entry) {
    let mod = null, why = null;
    try {
      mod = await import(/* webpackIgnore: true */ entry.url);
    } catch (err) {
      why = `cannot load ${name}.js`;
    }
    if (!why) why = usableCodeSkin(mod);
    if (why) {
      this.code.delete(name);
      if (this.chosen === name) this.fallBack(name, why);
      return null;
    }
    // Named from the ENTRY, not from the module: the id in the index is what a show writes and what
    // the protocol reports, and a module naming itself something else would make `shownName()` lie.
    const skin = { name, draw: mod.draw, ...(mod.stop ? { stop: () => mod.stop() } : {}) };
    this.code.set(name, skin);
    if (this.chosen === name) this.current = skin;
    return skin;
  }

  fallBack(name, why) {
    this.current = CODE_SKINS.default;
    this.error = `${name}: ${why}`;
    return { ok: false, shown: "default", error: why };
  }

  shownName() { return this.current === CODE_SKINS.default ? "default" : (this.current.name || this.chosen); }
}
