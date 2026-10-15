// MOTION, ported from the Tab5's percept/motion.cpp: where something is moving in the camera's view.
// Cheap frame differencing on a small luma copy, so it works at any range and any angle, where a
// face detector does not. Every number is the Tab5's, with its reason; none has been measured on the
// tablet yet (as on the Tab5, they are reasoned guesses: tune them here, in one place).
//
// Pure: no DOM, no clock. The tracker hands in a W x H luma plane per frame.

export const W = 320, H = 180;                  // the Tab5's percept frame
export const CELLS_X = 16, CELLS_Y = 9;         // 20 x 20 px cells, 144 of them
const CELL_W = W / CELLS_X, CELL_H = H / CELLS_Y, CELL_AREA = CELL_W * CELL_H;

// How far a pixel's luma must move to count as changed: comfortably above sensor noise, below what
// a person crossing a mid-contrast background produces.
export const PIXEL_DELTA = 16;
// How much of a cell must change before it is a candidate at all: 6 % (24 of its 400 pixels).
const CELL_FLOOR_PX = Math.floor((CELL_AREA * 6) / 100);
// More than this fraction of the whole frame changing is not an object in the scene: it is the
// lighting, the camera or the robot (the head turning). The frame is discarded, and its history.
// On the tablet this rule also stands in for the Tab5's gyroscope.
export const GLOBAL_CHANGE_FRAC = 0.30;
// Smoothing, alpha = 1/4 per frame: a flicker reaches a quarter, a person crossing the room wins.
const HEAT_SHIFT = 2, HEAT_FRAC = 16;

export class Motion {
  constructor() { this.prev = new Uint8Array(W * H); this.heat = new Uint16Array(CELLS_X * CELLS_Y); this.primed = false; }

  reset() { this.primed = false; this.heat.fill(0); }

  /** One frame (W x H luma). `suppress`: the robot knows it is moving; adopt the frame, report nothing.
   *  Returns {valid, nx, ny (-1..1), strength (0..1), changedFrac, gated}. */
  update(luma, suppress = false) {
    const r = { valid: false, nx: 0, ny: 0, strength: 0, changedFrac: 0, gated: false };
    if (!luma || luma.length < W * H) { r.gated = true; return r; }
    if (suppress || !this.primed) {              // first, and every untrusted, frame: history only
      this.prev.set(luma.subarray(0, W * H)); this.primed = true; this.heat.fill(0);
      r.gated = true; return r;
    }
    const count = new Uint16Array(CELLS_X * CELLS_Y);
    let changed = 0;
    for (let y = 0; y < H; y++) {
      const row = y * W, crow = Math.floor(y / CELL_H) * CELLS_X;
      for (let x = 0; x < W; x++) {
        const d = luma[row + x] - this.prev[row + x];
        this.prev[row + x] = luma[row + x];
        if (d > PIXEL_DELTA || d < -PIXEL_DELTA) { count[crow + Math.floor(x / CELL_W)]++; changed++; }
      }
    }
    r.changedFrac = changed / (W * H);
    if (r.changedFrac > GLOBAL_CHANGE_FRAC) { this.heat.fill(0); r.gated = true; return r; }
    let best = -1, bestHeat = 0;
    for (let i = 0; i < this.heat.length; i++) {
      const h = this.heat[i] - (this.heat[i] >> HEAT_SHIFT) + ((count[i] * HEAT_FRAC) >> HEAT_SHIFT);
      this.heat[i] = h;
      if (h > bestHeat) { bestHeat = h; best = i; }
    }
    const floorHeat = CELL_FLOOR_PX * HEAT_FRAC;
    if (best < 0 || bestHeat <= floorHeat) return r;
    const bx = best % CELLS_X, by = Math.floor(best / CELLS_X);
    r.strength = Math.min(1, bestHeat / (HEAT_FRAC * CELL_AREA));
    // Sub-cell centroid over the hottest cell and its neighbours, so the target moves continuously
    // instead of stepping cell to cell.
    let ws = 0, cx = 0, cy = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      const x = bx + dx, y = by + dy;
      if (x < 0 || y < 0 || x >= CELLS_X || y >= CELLS_Y) continue;
      const h = this.heat[y * CELLS_X + x];
      if (h <= floorHeat) continue;
      const w = h - floorHeat; ws += w; cx += w * (x + 0.5); cy += w * (y + 0.5);
    }
    if (ws <= 0) { cx = bx + 0.5; cy = by + 0.5; ws = 1; }
    r.nx = Math.max(-1, Math.min(1, (cx / ws) / CELLS_X * 2 - 1));
    r.ny = Math.max(-1, Math.min(1, (cy / ws) / CELLS_Y * 2 - 1));
    r.valid = true;
    return r;
  }
}
