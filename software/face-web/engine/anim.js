// Animated values, after the Tab5 face's smooth_ui_toolkit AnimateValue: SPRUNG for shape (an emote
// change should settle like something with mass) and EASED for gaze and colour (a saccade must not
// overshoot, and an RGB overshoot lands on a different hue).

export class Spring {
  constructor(v, { stiffness = 170, damping = 19, mass = 1 } = {}) {
    this.value = v; this.target = v; this.vel = 0;
    this.k = stiffness; this.c = damping; this.m = mass;
  }
  set(v) { this.target = v; }
  snap(v) { this.value = this.target = v; this.vel = 0; }
  step(dt) {
    // Semi-implicit Euler in small steps: stable at any frame rate the page will see.
    for (let left = dt; left > 0; left -= 1 / 240) {
      const h = Math.min(left, 1 / 240);
      const a = (-this.k * (this.value - this.target) - this.c * this.vel) / this.m;
      this.vel += a * h;
      this.value += this.vel * h;
    }
    return this.value;
  }
}

export class Ease {
  constructor(v, duration = 0.2) { this.value = v; this.from = v; this.target = v; this.t = 1; this.duration = duration; }
  set(v, duration = this.duration) { this.from = this.value; this.target = v; this.duration = duration; this.t = 0; }
  snap(v) { this.value = this.from = this.target = v; this.t = 1; }
  step(dt) {
    if (this.t < 1) {
      this.t = Math.min(1, this.t + dt / Math.max(this.duration, 1e-3));
      const e = this.t < 0.5 ? 4 * this.t ** 3 : 1 - (-2 * this.t + 2) ** 3 / 2;   // cubic in-out
      this.value = this.from + (this.target - this.from) * e;
    }
    return this.value;
  }
}
