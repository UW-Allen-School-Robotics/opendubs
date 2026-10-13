// The face engine, HEADLESS: its own model and its own protocol code (the same files the page loads),
// behind a UDP door in Node. No browser, no drawing — so CI can hold the engine to the head protocol
// with the same conformance suite that judges the Tab5.
//
//   node software/face-web/tests/node_door.mjs [port]

import dgram from "node:dgram";
import { Face } from "../engine/face.js";
import { handle, Earcons } from "../engine/protocol.js";
import { Skins } from "../engine/skins/index.js";
import { Levels } from "../engine/levels.js";

const port = Number(process.argv[2]) || 4244;
const face = new Face(), audio = new Earcons(false), skins = new Skins(), levels = new Levels();

let last = performance.now();
setInterval(() => { const now = performance.now(); face.tick(now, (now - last) / 1000); last = now; }, 16);

const sock = dgram.createSocket("udp4");
sock.on("message", (msg, peer) => {
  const line = msg.toString("latin1").split("\0")[0].replace(/[\r\n]+$/, "");
  Promise.resolve(handle(face, audio, line, performance.now(), skins, levels))
    .then((reply) => sock.send(reply, peer.port, peer.address));
});
sock.bind(port, "127.0.0.1", () => console.log(`face engine (headless) on udp 127.0.0.1:${port}`));
