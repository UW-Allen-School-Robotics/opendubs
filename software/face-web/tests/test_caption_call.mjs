// The state in plain words (head protocol v8 CAPTION): the page keeps what the dashboard sends, drops it
// when it is not renewed, refuses a long one, and draws it first, in the louder tier in a fault.
//
//   node software/face-web/tests/test_caption_call.mjs

const { handle, captionNow, CAPTION_MAX, CAPTION_EXPIRY_MS } = await import("../engine/protocol.js");
const { drawFace } = await import("../engine/frame.js");
let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };

const say = (line, now) => handle({}, { enabled: false }, line, now, null, null);
check("a caption is kept", say("CAPTION Problem: lift not responding", 1000) === "OK caption" &&
      captionNow(1000) === "Problem: lift not responding", captionNow(1000));
check("  as sent, spaces and all", say("CAPTION Charging 95%", 2000) === "OK caption" && captionNow(2000) === "Charging 95%", captionNow(2000));
check("  until it is not renewed", captionNow(2000 + CAPTION_EXPIRY_MS) === "Charging 95%" &&
      captionNow(2001 + CAPTION_EXPIRY_MS) === null, captionNow(2001 + CAPTION_EXPIRY_MS));
check("too long is refused, never cut, and changes nothing", say("CAPTION " + "x".repeat(CAPTION_MAX + 1), 3000).startsWith("ERR caption") &&
      captionNow(3000) === "Charging 95%", captionNow(3000));
check("  exactly the limit is fine", say("CAPTION " + "é".repeat(CAPTION_MAX), 3000) === "OK caption", captionNow(3000));
check("OFF clears it", say("CAPTION OFF", 4000) === "OK caption off" && captionNow(4000) === null, captionNow(4000));
check("empty is refused", say("CAPTION", 5000).startsWith("ERR caption"), say("CAPTION", 5000));

const ctx = new Proxy({}, { get: (_, k) => (k === "measureText" ? () => ({ width: 1 }) : () => {}), set: () => true });
function run({ words = "Ready", reserved = false, panel = true, armed = null } = {}) {
  const drawn = [], texts = [];
  const c = new Proxy(ctx, { get: (t, k) => (k === "fillText" ? (s) => texts.push(s) : t[k]), set: () => true });
  const face = { reserved: () => reserved, eyes: () => [], armedState: () => armed, quoteText: () => "a quote",
                 chargeText: () => "On charge", chargeState: () => ({}), target: null };
  const skins = { current: { draw: () => true }, library: new Map(), chosen: "default" };
  drawFace(c, 1280, 720, 1000, { face, skins, words,
    drawCaption: panel ? (_, w, o) => drawn.push([w, o.fault]) : null, drawArmed: () => {} });
  return { drawn, texts };
}
let r = run();
check("the words are drawn by the caption panel", r.drawn.length === 1 && r.drawn[0][0] === "Ready" && r.drawn[0][1] === false, JSON.stringify(r.drawn));
check("  and win over the quote and the charge line", !r.texts.includes("a quote") && !r.texts.includes("On charge"), r.texts.join("|"));
check("in a fault, the louder tier", run({ words: "Emergency stop", reserved: true }).drawn[0][1] === true, "quiet");
check("with ARMED up, still said", run({ armed: "wait" }).drawn.length === 1, "hidden");
r = run({ panel: false });
check("without the panel, the words take the line under the eyes", r.texts.includes("Ready") && !r.texts.includes("a quote"), r.texts.join("|"));
r = run({ words: null });
check("no words: the charge line as before", r.drawn.length === 0 && r.texts.includes("On charge"), r.texts.join("|"));

console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
