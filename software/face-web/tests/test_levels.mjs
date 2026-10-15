// Head protocol v5 on the face: VOLUME / BRIGHT fades and clamps (engine/levels.js), and ARMED's
// lease (engine/protocol.js). Headless: the kiosk bridge and face.setArmed are stubbed.
//
//   node software/face-web/tests/test_levels.mjs

const writes = { bright: [], volume: [] };
globalThis.window = { OpenDubsKiosk: {
  getMediaVolume: () => 0.33,
  setBrightness: (v) => writes.bright.push(v),
  setMediaVolume: (v) => writes.volume.push(v),
} };

const { Levels } = await import("../engine/levels.js");
const { handle, ARMED_LEASE_MS } = await import("../engine/protocol.js");

let pass = 0, fail = 0;
const check = (name, ok, got) => { console.log(`${ok ? "PASS" : "FAIL"} ${name}${ok ? "" : `  (got ${got})`}`); ok ? pass++ : fail++; };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const near = (a, b) => Math.abs(a - b) < 1e-6;

const lv = new Levels();
check("idle volume defaults to the device's own", near(lv.cfg.idle.volume, 0.33), lv.cfg.idle.volume);
const say = (line) => handle({}, {}, line, 0, null, lv);

check("a step applies at once", say("BRIGHT 0.5") === "OK bright 0.50" && near(writes.bright.at(-1), 0.5), writes.bright.at(-1));
check("below the floor is clamped up, and the reply says so", say("BRIGHT 0.02") === "OK bright 0.15", say("BRIGHT 0.02"));
lv.configure({ volMax: 0.8 });
check("above vol_max is clamped down", say("VOLUME 1") === "OK volume 0.80", say("VOLUME 1"));

writes.bright.length = 0;
const r = say("BRIGHT 1 0.3");
check("a fade replies with its target at once", r === "OK bright 1.00", r);
await sleep(120);
const mid = writes.bright.at(-1);
check("mid-fade it is between", mid > 0.15 && mid < 1, mid);
await sleep(300);
check("and it ends on the target", near(writes.bright.at(-1), 1), writes.bright.at(-1));
check("in many small steps, not one jump", writes.bright.length > 4, writes.bright.length);

say("VOLUME 0.7");
lv.restoreIdle(0);
check("a lost door puts the idle levels back", near(lv.level.volume, 0.33) && near(lv.level.bright, 1), JSON.stringify(lv.level));
writes.volume.length = 0;
lv.restoreIdle(0);
check("and does nothing when nothing changed", writes.volume.length === 0, writes.volume.length);

check("a fade longer than 30 s is held to 30 s", say("VOLUME 0.5 999") === "OK volume 0.50", say("VOLUME 0.5 999"));
check("IDLE answers the idle level", say("VOLUME IDLE") === "OK volume idle 0.33", say("VOLUME IDLE"));
check("malformed is an error", say("BRIGHT 0.5 fast").startsWith("ERR bright "), say("BRIGHT 0.5 fast"));
check("no device levels: ERR unknown", handle({}, {}, "VOLUME 0.5", 0, null, null) === "ERR unknown", "");

// "Leave the tablet's own volume alone" (volume_idle null): idle follows the device, so a show's end
// gives back what the operator had set, not an old idle level.
{
  let device = 0.33;
  window.OpenDubsKiosk.getMediaVolume = () => device;
  window.OpenDubsKiosk.setMediaVolume = (v) => { device = v; writes.volume.push(v); };
  const f = new Levels();
  const s2 = (line) => handle({}, {}, line, 0, null, f);
  f.configure({ idle: { volume: 0.5 } });
  f.configure({ idle: { volume: null } });                // unticked on the Head & face page
  device = 0.8;                                           // the operator turns the tablet up
  s2("VOLUME 0.2");                                       // a show changes it
  check("null idle volume: a show's end gives back the operator's level", s2("VOLUME IDLE") === "OK volume idle 0.80", s2("VOLUME IDLE"));
  f.configure({ idle: { volume: 0.4 } });                 // ticked again: a fixed idle level
  device = 0.9; s2("VOLUME 0.2");
  check("a fixed idle volume still wins", s2("VOLUME IDLE") === "OK volume idle 0.40", s2("VOLUME IDLE"));
}

// ARMED, against a face that can draw it.
const shown = [];
const face = { setArmed: (s) => shown.push(s) };
const arm = (line) => handle(face, {}, line, 0, null, lv);
check("ARMED WAIT", arm("ARMED WAIT") === "OK armed wait" && shown.at(-1) === "wait", shown.at(-1));
await sleep(ARMED_LEASE_MS - 1000);
arm("ARMED WAIT");                                    // renewed before it lapses
await sleep(1500);
check("renewed, it stays", shown.at(-1) === "wait", shown.at(-1));
await sleep(ARMED_LEASE_MS);
check("not renewed, it clears itself", shown.at(-1) === null, shown.at(-1));
check("ARMED 5 is a countdown", arm("ARMED 5") === "OK armed 5" && shown.at(-1) === 5, shown.at(-1));
check("ARMED OFF", arm("ARMED OFF") === "OK armed off" && shown.at(-1) === null, shown.at(-1));
check("ARMED <malformed>", arm("ARMED SOON").startsWith("ERR armed "), arm("ARMED SOON"));
check("a face that cannot draw it: ERR unknown", handle({}, {}, "ARMED WAIT", 0, null, lv) === "ERR unknown", "");

console.log(`\n${pass} PASS, ${fail} FAIL`);
process.exit(fail ? 1 : 0);
