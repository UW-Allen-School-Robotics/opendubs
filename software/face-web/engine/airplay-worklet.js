// AirPlay's player, on the audio thread (engine/airplay.js feeds it). Live PCM from the Thor arrives
// in bursts over the network; this holds a small buffer and plays it out at the audio clock's pace.
//
//   buffer      starts playing at TARGET (250 ms); after an underrun, waits for TARGET again
//   drift       the Thor's 44.1 kHz and this device's audio clock differ by a few parts per million,
//               so the buffer creeps. Outside TARGET ± BAND one frame in every 1000 is dropped (too
//               full) or repeated (too empty): a 0.1 % speed change, inaudible. Beyond MAX it jumps.
//
// Reports {buffered_ms, underruns, dropped, repeated, jumps} every half second.

const RATE = 44100;
const TARGET = Math.round(0.25 * RATE), BAND = Math.round(0.1 * RATE), MAX = Math.round(0.6 * RATE);
const CAP = 2 * RATE;                                   // 2 s of stereo frames

class AirplayPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buf = new Float32Array(CAP * 2);
    this.r = 0; this.w = 0; this.n = 0;                 // read/write frame index, frames held
    this.playing = false; this.tick = 0; this.since = 0;
    this.stats = { underruns: 0, dropped: 0, repeated: 0, jumps: 0 };
    this.port.onmessage = (e) => this.push(new Int16Array(e.data));
  }

  push(pcm) {                                           // interleaved s16 stereo
    const frames = pcm.length >> 1;
    for (let i = 0; i < frames; i++) {
      if (this.n >= CAP) { this.r = (this.r + 1) % CAP; this.n--; }   // full: lose the oldest
      this.buf[this.w * 2] = pcm[i * 2] / 32768;
      this.buf[this.w * 2 + 1] = pcm[i * 2 + 1] / 32768;
      this.w = (this.w + 1) % CAP; this.n++;
    }
    if (this.n > MAX) {                                 // far behind: jump back to the target
      const skip = this.n - TARGET;
      this.r = (this.r + skip) % CAP; this.n -= skip; this.stats.jumps++;
    }
  }

  process(_, outputs) {
    const out = outputs[0], L = out[0], R = out[1] || out[0];
    if (!this.playing && this.n >= TARGET) this.playing = true;
    for (let i = 0; i < L.length; i++) {
      if (!this.playing || this.n === 0) {
        if (this.playing) { this.playing = false; this.stats.underruns++; }
        L[i] = 0; R[i] = 0; continue;
      }
      L[i] = this.buf[this.r * 2]; R[i] = this.buf[this.r * 2 + 1];
      this.tick++;
      // Drift: once in 1000 frames, outside the band, drop one (too full) or repeat one (too empty).
      if (this.tick % 1000 === 0 && this.n > TARGET + BAND && this.n > 1) {
        this.r = (this.r + 1) % CAP; this.n--; this.stats.dropped++;
      } else if (this.tick % 1000 === 0 && this.n < TARGET - BAND) {
        this.stats.repeated++;                          // hold this frame once more: do not advance
        continue;
      }
      this.r = (this.r + 1) % CAP; this.n--;
    }
    this.since += L.length;                            // reported even while starved: that is when it matters
    if (this.since >= RATE / 2) {
      this.since = 0;
      this.port.postMessage({ buffered_ms: Math.round(this.n / RATE * 1000), playing: this.playing, ...this.stats });
    }
    return true;
  }
}

registerProcessor("airplay-player", AirplayPlayer);
