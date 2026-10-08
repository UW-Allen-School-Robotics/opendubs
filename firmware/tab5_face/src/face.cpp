#include "face.h"
#include "lift.h"   // Lift.request() -- the gaze leads on INTENT, not on reported motion
#include "ui_shared.h"
#include <M5Unified.h>
#include <cstring>


// ---- eye sprite: INTERNAL SRAM, glow drawn straight in (no PSRAM
// template/blit) ---- The 15 fps version kept the eye + a glow template in
// PSRAM and blitted the glow every frame; that per-frame PSRAM traffic was the
// bottleneck. Here the glow is rasterized directly into the sprite each frame —
// no PSRAM in the hot path.
//
// EW IS A MEMORY BUDGET, NOT A TASTE DECISION. Benched in
// firmware/tab5_face/bench/, a two-eye frame:
//
//     324x448  PSRAM     27.6 fps   <- what this does, and what it should do
//     276x448  PSRAM     33.4 fps   <- +21%, and NOT WORTH IT: nobody asked for
//                                       frame rate, and smaller eyes are a
//                                       regression. Shrinking was the wrong axis.
//     276x448  internal  39.5 fps   <- LOOKS BEST, BREAKS THE RADIO. See below.
//
// THE EYE MUST STAY IN PSRAM. 276x448 does fit a contiguous internal block
// (~247 KB free, sprite needs 241 KB) and putting it there is measurably faster
// -- but the ESP32-P4 HAS NO RADIO. Wi-Fi is an ESP32-C6 reached over SDIO, and
// esp_hosted's sdio_mempool_create() needs a DMA-capable INTERNAL pool. Give the
// eye that memory and the driver asserts at sdio_drv.c:255 and the whole board
// reboot-loops:
//
//     [FACE] up eyePsram=0 freeInt=134KB
//     assert failed: sdio_mempool_create sdio_drv.c:255 (buf_mp_g)
//     rst:0xc (SW_CPU_RESET)
//
// which costs OTA and payload comms -- i.e. everything -- to buy 6 fps.
//
// AND BECAUSE THE EYE LIVES IN PSRAM, 247 KB IS NOT THE CEILING. PSRAM is 32 MB.
// EW/EH are limited by the frame rate you will accept and by the panel, not by a
// memory block -- so the eye can grow well past 324 if a bigger face is wanted.
// That is the direction worth exploring; shrinking it was not.
//
// A bench that measures a sprite alone cannot see the SDIO problem, because there
// is no radio in it to starve. Measure the component to find a candidate; measure
// the system before believing it.
static M5Canvas eye(&M5.Display);
static const int EW = 324,
                 EH = 448;
// BW MUST TRACK EW. This is the eye BODY inside the sprite, scaled by a per-state
// multiplier reaching 1.06 (E_STOP in ui_shared.cpp): 284 x 1.06 = 301 px, which
// leaves 11 px for the glow in a 324-wide sprite. Change EW and this must move
// with it, or the widest state is clipped.
static const float
    BW = 284,
    BH = 376; // eye size; E_STOP height capped so the tall eye still fits
static bool g_eyePsram = false;
// Per-eye horizontal scale for this frame's gaze; 1.0 is symmetric. Set by the render loop just
// before each eye is drawn, read by drawEye -- the two eyes are drawn one after the other, so a
// single shared value is correct and avoids threading another argument through drawEye's signature.
static float gazeW = 1.0f;

static Expr P, T;
static int shownState = -1; // which STATES[] index is currently painted
static bool fullRepaint =
    true; // needs a fillScreen (boot / returned from controller)
static bool stateDirty = false; // state changed → remake glow+label, NO
                                // fillScreen (smooth, no flash)
static uint32_t lastFpsMs = 0, tPrev = 0, frames = 0, gNext = 0;
static float fps = 0, blinkPhase = 0, soc = 0.6f, breathe = 0;
static float gx = 0, gy = 0, gTX = 0, gTY = 0, radarAng = 0, radarPrev = 0;
static uint32_t nextBlink = 1500;   // declared before startSaccade(), which fires a blink
// Explicit "look here" intent, in SCREEN coordinates (already mirrored by faceLookAt).
static float lookX = 0, lookY = 0;
static uint32_t lookUntil = 0;

// ---- gaze is SACCADIC -------------------------------------------------------------------
// Eyes do not drift. They make ballistic jumps between dead-still fixations, and the stillness
// is half of what makes a fixation legible -- an observer reads WHERE a gaze settled, not the
// path it took. The previous model was `gx = lp(gx, target, k)`, an exponential approach that
// never settles, so the eyes were permanently in motion and never fixated on anything. That is
// what "jittery and conveys nothing" was: no fixations to read, and in AUTONOMOUS a literal
// continuous sine sweep, which is smooth pursuit of a target that does not exist.
//
// Model follows Lee, Badler & Badler, "Eyes Alive" (SIGGRAPH 2002), the standard statistical
// eye-movement model for character animation. The pieces that matter here:
//   * saccades last 20-200 ms, and successive fixations are typically 20-30 ms apart;
//   * duration follows the MAIN SEQUENCE -- a fixed relationship between amplitude, duration and
//     peak velocity -- so a big jump takes measurably longer than a small one, and getting that
//     ratio wrong is itself a cue that something is not alive;
//   * the inter-saccadic interval (fixation duration) is a modelled quantity, not a leftover;
//   * saccade RATE tracks arousal: excited or agitated raises it, lethargic lowers it.
static float    sacFromX = 0, sacFromY = 0, sacToX = 0, sacToY = 0;
static uint32_t sacStart = 0, sacDur = 0;     // sacDur == 0 means FIXATING, holding dead still
static uint32_t fixUntil = 0;                 // when the current idle fixation expires

// Full gaze deflection is about 30 degrees of eye rotation; the main sequence is then roughly
// 20 ms of fixed cost plus 2.2 ms per degree. Amplitude 1.0 -> ~86 ms, 0.1 -> ~27 ms.
static uint32_t saccadeMs(float dx, float dy) {
  const float deg = sqrtf(dx * dx + dy * dy) * 30.0f;
  const uint32_t ms = (uint32_t)(20.0f + deg * 2.2f);
  return ms > 200 ? 200 : ms;                 // physiological ceiling
}

static void startSaccade(float tx, float ty, uint32_t now) {
  if (tx < -1) tx = -1; else if (tx > 1) tx = 1;
  if (ty < -1) ty = -1; else if (ty > 1) ty = 1;
  const float dx = tx - sacToX, dy = ty - sacToY;
  if (fabsf(dx) < 0.03f && fabsf(dy) < 0.03f) return;   // below the smallest real saccade
  sacFromX = gx; sacFromY = gy;
  sacToX = tx;   sacToY = ty;
  sacDur = saccadeMs(dx, dy);
  sacStart = now;
  // A large gaze shift in a real face is often accompanied by a blink. Firing one here hides the
  // transit and makes the arrival read as deliberate rather than as a slide.
  if (fabsf(dx) > 0.55f) nextBlink = now;
}

// ---- gaze self-test ----------------------------------------------------------------------
// Steps on faceRender() rather than blocking, because gx/gy are integrated INSIDE the render
// loop: a handler that slept waiting for the gaze to settle would stop the very updates it was
// waiting for, and read back the starting value every time.
struct GazeStep { float x, y; const char *what; char axis; int wantSign; };
static const GazeStep kGazeTest[] = {
  {+1,  0, "robot RIGHT", 'x', -1},   // viewer mirror: the robot's right is the observer's left
  {-1,  0, "robot LEFT",  'x', +1},
  { 0, +1, "robot UP",    'y', -1},   // screen y is positive downward
  { 0, -1, "robot DOWN",  'y', +1},
};
static constexpr int kNGazeTest = sizeof(kGazeTest) / sizeof(kGazeTest[0]);
static int      gtIdx  = -1;
static uint32_t gtNext = 0;
static bool blinking = false;
static int turnDir = 0, prevTurn = 0;
static uint32_t turnMs = 0;
// pun ticker
static int punIdx = 0;
static uint32_t punMs = 0;
static bool punDirty = true;

static inline float lp(float a, float b, float k) { return a + (b - a) * k; }
static inline bool is(const char *n) {
  return strcmp(STATES[shownState].name, n) == 0;
}

// Rasterize the glow straight into the eye sprite each frame (replaces the
// flat-bg fill + the old PSRAM template blit). ~14 concentric ellipses reads
// smooth enough and stays fast because the sprite is internal SRAM.
static void drawGlow(M5Canvas &spr, uint8_t r, uint8_t g, uint8_t b) {
  uint8_t br = r / 12, bgg = g / 12, bb = b / 12;
  spr.fillScreen(M5.Display.color565(br, bgg, bb));
  int cx = EW / 2, cy = EH / 2, RX = EW / 2 - 3, RY = EH / 2 - 3;
  for (int i = 14; i >= 1; --i) {
    float f = i / 14.0f, bright = (1 - f) * (1 - f);
    spr.fillEllipse(cx, cy, (int)(RX * f), (int)(RY * f),
                    M5.Display.color565(br + (uint8_t)(r * 0.42f * bright),
                                        bgg + (uint8_t)(g * 0.42f * bright),
                                        bb + (uint8_t)(b * 0.42f * bright)));
  }
}

static void drawBrow(LovyanGFX *g, int cx, int eyeCy, int w, int h, int side,
                     uint16_t color) {
  int eyeTop = eyeCy - h / 2;
  int browY = std::max(15, eyeTop - 52 - (int)(P.brow * 58));
  int bw = (int)(w * 0.90f);
  int innerX = cx - side * bw / 2, outerX = cx + side * bw / 2;
  int innerY = browY + (int)(P.tilt * 90), outerY = browY - (int)(P.tilt * 34);
  const int TT = 9;
  g->fillTriangle(innerX, innerY - TT, outerX, outerY - TT, outerX, outerY + TT,
                  color);
  g->fillTriangle(innerX, innerY - TT, outerX, outerY + TT, innerX, innerY + TT,
                  color);
  g->fillCircle(innerX, innerY, TT, color);
  g->fillCircle(outerX, outerY, TT, color);
}

static void drawChevrons(int W, int H, int dir, uint16_t col, uint16_t bg,
                         float ph) {
  const int spacing = 38, ymid = H / 2, hh = 30;
  M5.Display.fillRect(dir < 0 ? 0 : W - 132, ymid - 48, 132, 96, bg);
  float off = fmodf(ph * 70.0f, (float)spacing);
  for (int i = 0; i < 3; ++i) {
    int cx = dir < 0 ? (int)(30 + i * spacing - off)
                     : (int)(W - 30 - i * spacing + off);
    int tipx = cx + dir * 15, backx = cx - dir * 15;
    M5.Display.drawWideLine(backx, ymid - hh, tipx, ymid, 9, col);
    M5.Display.drawWideLine(tipx, ymid, backx, ymid + hh, 9, col);
  }
}

static void drawEye(LovyanGFX *g, int cx, int cy, float blink, uint16_t color,
                    uint16_t bg, int side, bool gauge, uint16_t dimCol,
                    uint16_t glintCol) {
  float baseH = BH * P.h, squintCut = baseH * P.squint;
  int h = (int)std::max((baseH - squintCut) * blink, 12.0f);
  int w = (int)(BW * P.w * gazeW);
  int ey = cy + (int)(squintCut * 0.5f) + (int)(P.lookY * 66);
  int r = std::min(58, std::min(w, h) / 2);
  int x0 = cx - w / 2, y0 = ey - h / 2;

  if (gauge) {
    g->fillRoundRect(x0, y0, w, h, r, dimCol);
    int fillH = std::max(0, std::min(h, (int)(h * soc)));
    if (fillH > 0) {
      int fillTop = y0 + h - fillH;
      g->setClipRect(x0, fillTop, w, fillH);
      g->fillRoundRect(x0, y0, w, h, r, color);
      g->clearClipRect();
      g->fillRect(x0 + r / 2, fillTop - 1, w - r, 3, TFT_WHITE);
    }
    drawBrow(g, cx, ey, w, h, side, dimCol);
    return;
  }

  g->fillRoundRect(x0, y0, w, h, r, color);

  // ONE highlight, and it stays put. A second competing glint read as confusing and less like an
  // eye, and a highlight that chases the gaze is the wrong primitive for this kind of eye at all
  // -- see the per-eye shaping in the caller for what actually carries gaze here.
  if (blink > 0.5f && h > 70)
    g->fillEllipse(cx - w / 5, ey - (int)(h * 0.26f), std::max(w / 10, 6),
                   std::max(h / 12, 5), glintCol);

  float tiltMag = fabsf(P.tilt) * 235.0f;
  if (tiltMag > 3 && h > 40) {
    int tw = (int)(tiltMag * 0.95f), th = (int)tiltMag;
    bool cutLeft = (P.tilt < 0) ? (side < 0) : (side > 0);
    if (cutLeft)
      g->fillTriangle(x0, y0, x0 + tw, y0, x0, y0 + th, bg);
    else
      g->fillTriangle(x0 + w, y0, x0 + w - tw, y0, x0 + w, y0 + th, bg);
  }
  float bowMag = P.bow * 230.0f;
  if (bowMag > 3 && h > 40) {
    int ry = (int)(h * 0.45f), rx = (int)(w * 0.60f), carve = (int)bowMag;
    g->fillEllipse(cx, y0 + h - carve + ry, rx, ry, bg);
  }
  drawBrow(g, cx, ey, w, h, side, color);
}

// TOP band (not bottom — bottom text reads as a "mouth" from a distance): state
// word (bold, letter-spaced) for most states; the cycling pun for SAFE_IDLE; a
// small touch hint.
static void drawTop(int W, int H, uint16_t bg) {
  M5.Display.fillRect(0, 16, W, 96, bg);
  if (is("SAFE_IDLE")) {
    M5.Display.setFont(&fonts::FreeSans18pt7b);
    M5.Display.setTextSize(1);
    M5.Display.setTextColor(M5.Display.color565(
        234, 241, 248)); // bright near-white — readable on-panel at distance
    M5.Display.setTextDatum(textdatum_t::top_center);
    M5.Display.drawString(PUNS[punIdx], W / 2, 26);
  } else {
    M5.Display.setFont(&fonts::FreeSansBold24pt7b);
    M5.Display.setTextSize(1);
    M5.Display.setTextColor(M5.Display.color565(188, 200, 218));
    const char *label = STATES[shownState].label;
    const int sp = 15;
    int total = 0;
    for (const char *p = label; *p; ++p) {
      char c[2] = {*p, 0};
      total += M5.Display.textWidth(c) + sp;
    }
    total -= sp;
    int x = (W - total) / 2, y = 22;
    M5.Display.setTextDatum(textdatum_t::top_left);
    for (const char *p = label; *p; ++p) {
      char c[2] = {*p, 0};
      M5.Display.setCursor(x, y);
      M5.Display.print(c);
      x += M5.Display.textWidth(c) + sp;
    }
  }
  // What the operator's RIGHT STICK is doing right now (CH7). Phrased from their point of
  // view rather than as a mode name: the failure this prevents is pushing the stick
  // expecting one thing and getting another, and "STICK: HEAD" answers that directly where
  // "PAN-TILT" would need translating. Colour-coded so it reads at a glance across a room.
  {
    const char *what = (g_payloadMode == PMODE_PANTILT) ? "STICK: HEAD"
                     : (g_payloadMode == PMODE_ZAXIS)   ? "STICK: LIFT" : "STICK: DRIVE";
    const uint16_t col = (g_payloadMode == PMODE_PANTILT) ? M5.Display.color565(0, 176, 235)
                       : (g_payloadMode == PMODE_ZAXIS)   ? M5.Display.color565(243, 182, 74)
                                                          : M5.Display.color565(70, 84, 98);
    M5.Display.setFont(&fonts::FreeSansBold9pt7b);
    M5.Display.setTextColor(col);
    M5.Display.setTextDatum(textdatum_t::top_center);
    M5.Display.drawString(what, W / 2, 62);
  }

  M5.Display.setFont(&fonts::FreeSans9pt7b);
  M5.Display.setTextColor(M5.Display.color565(70, 84, 98));
  M5.Display.setTextDatum(textdatum_t::top_center);
  M5.Display.drawString("touch for lift controls", W / 2, 80);
  M5.Display.setFont(&fonts::Font0);
  M5.Display.setTextDatum(textdatum_t::top_left);
}

void faceBegin() {
  eye.setColorDepth(16);
  // DELIBERATELY PSRAM, not a fallback. At 276x448 this sprite WOULD fit internal
  // SRAM, and asking for it there starves esp_hosted's SDIO pool and reboot-loops
  // the board (see above). Leave this alone unless the radio stops needing
  // internal DMA memory.
  eye.setPsram(true);
  g_eyePsram = true;
  if (!eye.createSprite(EW, EH)) {
    eye.setPsram(false);
    eye.createSprite(EW, EH);
    g_eyePsram = false;
    // SAY SO LOUDLY. This fallback is not an edge case -- it was the steady
    // state for every build before EW came down to 276, and it cost a third of
    // the frame rate while the only evidence was one serial line nobody reads
    // on a robot with no console attached. If this fires again, EW no longer
    // fits a contiguous internal block and the number above needs re-measuring.
    Serial.printf("[FACE] *** WARNING: %dx%d eye would not fit PSRAM and fell "
                  "back to INTERNAL SRAM (largest contiguous block %u KB). This "
                  "may starve the SDIO radio pool -- watch for "
                  "sdio_mempool_create asserts. ***\n",
                  EW, EH,
                  (unsigned)(heap_caps_get_largest_free_block(
                                 MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT) /
                             1024));
  }
  P = T = STATES[g_robotState].e;
  shownState = g_robotState;
  lastFpsMs = tPrev = punMs = millis();
  Serial.printf(
      "[FACE] up eyePsram=%d freeInt=%uKB\n", g_eyePsram,
      (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
}

void faceGazeSelfTest() {
  gtIdx = 0;
  gtNext = 0;
  Serial.println("[GAZE] self-test: commanded direction -> resulting screen gaze");
}

// Called once per rendered frame, after gx/gy have been integrated for this frame.
static void gazeSelfTestTick(uint32_t now) {
#ifdef GAZE_SELFTEST_AT_BOOT
  // Fire once, a few seconds in, WITHOUT needing anything sent to the device. Serial input cannot
  // be relied on here: the host's /dev/cu.* does not assert DTR (on purpose -- asserting it resets
  // an ESP32), and USB-CDC RX is gated on it, so commands written to the port are silently
  // dropped. A test that depends on them reports nothing and looks like a failure of the feature
  // instead of a failure of the channel.
  static bool fired = false;
  if (!fired && now > 4000) { fired = true; faceGazeSelfTest(); }
#endif
  if (gtIdx < 0) return;
  if (gtNext == 0) {                       // first entry: issue step 0
    faceLookAt(kGazeTest[0].x, kGazeTest[0].y, 1400);
    gtNext = now + 1100;                   // longer than the lerp needs to settle
    return;
  }
  if (now < gtNext) return;

  const GazeStep &s = kGazeTest[gtIdx];
  const float got = (s.axis == 'x') ? gx : gy;
  // A sign test, not a magnitude one: magnitude is a tuning choice, but a flipped sign means the
  // robot looks away from where it is going, which is worse than not looking at all.
  const bool ok = (s.wantSign > 0) ? (got > 0.15f) : (got < -0.15f);
  Serial.printf("[GAZE] %-12s -> g%c=%+.2f  want %s  %s\n", s.what, s.axis, got,
                s.wantSign > 0 ? "positive" : "negative",
                ok ? "OK" : (got > -0.15f && got < 0.15f ? "FAIL (no movement)" : "FAIL (INVERTED)"));

  if (++gtIdx < kNGazeTest) {
    faceLookAt(kGazeTest[gtIdx].x, kGazeTest[gtIdx].y, 1400);
    gtNext = now + 1100;
  } else {
    gtIdx = -1; gtNext = 0;
    Serial.println("[GAZE] self-test done");
  }
}

void faceLookAt(float x, float y, uint32_t hold_ms) {
  // Mirror into the viewer's frame (the robot's right is the observer's left) and into screen
  // coordinates (y positive downward). Both negations are here, in one place, so no caller has to
  // reason about them -- and so this cannot disagree with the TURN handling above.
  lookX = -(x < -1 ? -1 : (x > 1 ? 1 : x));
  lookY = -(y < -1 ? -1 : (y > 1 ? 1 : y));
  lookUntil = millis() + (hold_ms ? hold_ms : 600);
}

void faceInvalidate() {
  fullRepaint = true;
  punDirty = true;
}

void faceRender() {
  const int W = M5.Display.width(), H = M5.Display.height();
  uint32_t t = millis();
  float dt = (t - tPrev) / 1000.0f;
  if (dt <= 0)
    dt = 0.001f;
  tPrev = t;
  breathe += dt;

  if (g_robotState != shownState) {
    shownState = g_robotState;
    T = STATES[shownState].e;
    stateDirty = true;
    if (is("CHARGING"))
      soc = 0.40f;
    punDirty = true;
  }

  // advance the pun ticker while idle
  if (is("SAFE_IDLE") && t - punMs > 4600) {
    punMs = t;
    punIdx = (punIdx + 1) % NPUN;
    punDirty = true;
  }

  float k = 1.0f - powf(0.004f, dt);
  P.h = lp(P.h, T.h, k);
  P.w = lp(P.w, T.w, k);
  P.tilt = lp(P.tilt, T.tilt, k);
  P.bow = lp(P.bow, T.bow, k);
  P.squint = lp(P.squint, T.squint, k);
  P.lookY = lp(P.lookY, T.lookY, k);
  P.brow = lp(P.brow, T.brow, k);

  turnDir = g_turnCmd; // real turn intent pushed by the Thor (TURN L/R/0) →
                       // chevrons + gaze-lean

  // Decide WHERE to look. Committed intent (a deliberate signal) outranks idle looking around.
  float tx = sacToX, ty = sacToY;
  bool committed = false;
  if (turnDir != 0) { tx = -turnDir * 0.85f; ty = 0; committed = true; }
  if (!committed) {
    // LIFT INTENT, NOT LIFT MOTION. Lift.request() is what the operator asked for and is set here
    // on the Tab5; state().motion comes back only after the StamPLC closes the relay. Gazing on
    // the request precedes visible travel by a full round trip.
    const Motion want = Lift.request();
    if (want != Motion::STOP) { ty = (want == Motion::UP) ? -0.75f : 0.75f; tx = 0; committed = true; }
  }
  if (t < lookUntil) { tx = lookX; ty = lookY; committed = true; }

  // Idle looking around, when nothing is being attended to. Amplitude is deliberately skewed
  // small: most real saccades are short, and a face that makes nothing but wide sweeps reads as
  // frantic. Arousal sets the rate, per Eyes Alive -- AUTONOMOUS is busy and looks about more
  // often; SAFE_IDLE is calm and holds longer.
  if (!committed && sacDur == 0 && t > fixUntil) {
    const bool busy = is("AUTONOMOUS");
    const long roll = random(100);
    const float amp = (roll < 60) ? random(0, 25) / 100.0f        // small, most of the time
                    : (roll < 90) ? random(25, 60) / 100.0f       // mid
                                  : random(60, 100) / 100.0f;     // occasional wide look
    const float ang = random(0, 628) / 100.0f;
    tx = cosf(ang) * amp;
    ty = sinf(ang) * amp * 0.55f;                                 // eyes range less vertically
    fixUntil = t + (busy ? 180 + random(420) : 320 + random(1300));
  }

  if (!committed || fabsf(tx - sacToX) > 0.03f || fabsf(ty - sacToY) > 0.03f)
    startSaccade(tx, ty, t);

  // Execute: ballistic during the saccade, EXACTLY still afterwards. The hold is the point --
  // any residual motion between saccades is what made this look jittery before.
  if (sacDur) {
    const uint32_t el = t - sacStart;
    if (el >= sacDur) {
      gx = sacToX; gy = sacToY; sacDur = 0;
      if (committed) fixUntil = t;            // let idle resume immediately once intent clears
    } else {
      const float u = (float)el / (float)sacDur;
      const float e = u * u * (3.0f - 2.0f * u);   // ease: fast middle, settles without overshoot
      gx = sacFromX + (sacToX - sacFromX) * e;
      gy = sacFromY + (sacToY - sacFromY) * e;
    }
  } else {
    gx = sacToX; gy = sacToY;                 // dead still
  }
  gazeSelfTestTick(t);

  if (!blinking && t > nextBlink) {
    blinking = true;
    blinkPhase = 0;
  }
  if (blinking) {
    blinkPhase += dt * (PI / 0.15f);
    if (blinkPhase >= PI) {
      blinking = false;
      nextBlink = t + 2000 + random(2600);
    }
  }
  float open = blinking ? 1.0f - 0.94f * sinf(blinkPhase) : 1.0f;

  St &s = STATES[shownState];
  bool gauge = is("CHARGING");
  if (gauge)
    soc = std::min(1.0f, soc + dt * 0.05f);
  uint16_t col = M5.Display.color565(s.r, s.g, s.b);
  uint16_t bg = M5.Display.color565(s.r / 12, s.g / 12, s.b / 12);
  uint16_t dimCol = M5.Display.color565(s.r / 4, s.g / 4, s.b / 4);
  uint16_t glintCol =
      (s.r + s.g + s.b > 620) ? M5.Display.color565(66, 88, 122) : TFT_WHITE;

  if (fullRepaint) {
    M5.Display.fillScreen(bg);
    drawTop(W, H, bg);
    radarPrev = radarAng = 0;
    fullRepaint = false;
    stateDirty = false;
    punDirty = false;
  } else if (stateDirty) {
    // State change: NO fillScreen (bg is always near-black → no visible
    // change), so the eyes just morph — no black flash on toggle. Clear only
    // the thin center gap between the eyes so a stale AUTONOMOUS radar beam
    // can't linger there; the eyes themselves repaint every frame.
    M5.Display.fillRect(W / 2 - 32, H / 2 - 192, 64, 384, bg);
    drawTop(W, H, bg);
    radarPrev = radarAng = 0;
    stateDirty = false;
    punDirty = false;
  } else if (punDirty) {
    drawTop(W, H, bg);
    punDirty = false;
  }

  if (is("AUTONOMOUS")) {
    int rcx = W / 2, rcy = H / 2 + 10,
        len = (int)(W * 0.15f); // smaller + centered on eyes; clears the TOP
                                // text band
    M5.Display.drawWideLine(rcx, rcy, rcx + cosf(radarPrev) * len,
                            rcy + sinf(radarPrev) * len, 7, bg);
    radarAng += dt * 1.7f;
    if (radarAng > 2 * PI)
      radarAng -= 2 * PI;
    uint16_t beam = M5.Display.color565(s.r / 3, s.g / 3, s.b / 3);
    M5.Display.drawWideLine(rcx, rcy, rcx + cosf(radarAng) * len,
                            rcy + sinf(radarAng) * len, 4, beam);
    radarPrev = radarAng;
  }
  if (turnDir != prevTurn) {
    M5.Display.fillRect(0, H / 2 - 52, 132, 104, bg);
    M5.Display.fillRect(W - 132, H / 2 - 52, 132, 104, bg);
    prevTurn = turnDir;
  }
  if (turnDir != 0)
    drawChevrons(W, H, -turnDir, col, bg,
                 breathe); // VIEWER frame: robot-right → screen-left

  const int eyeY = H / 2 + 16; // nudged down; the text lives up top now
  int slots[2] = {W / 2 - 190, W / 2 + 190}; // spacing for the eyes
  for (int i = 0; i < 2; ++i) {
    drawGlow(eye, s.r, s.g, s.b); // glow straight into the (internal) sprite

    // GAZE LIVES IN THE ASYMMETRY BETWEEN THE TWO EYES, not inside either one.
    //
    // These eyes have no pupil, and a pupil is the strongest gaze cue there is. Anki hit exactly
    // this when building Cozmo: he originally HAD pupils, which made it easy to tell where he was
    // looking, but they made him read as too human -- so they removed them and recovered the same
    // effect through eye SIZE AND RATIO. Shape, not a marker inside the eye.
    //
    // This renderer could not do that, for a structural reason. Both eyes come from one sprite
    // pushed twice and mirrored, so the pair was ALWAYS perfectly symmetric -- and a symmetric
    // pair of pupil-less eyes has almost nothing left to say about direction. Moving both eyes
    // 12 px in lockstep is the whole vocabulary, which is why the gaze read as subtle, and why
    // faking a rotating specular highlight instead was solving the wrong problem.
    //
    // It costs nothing to fix: the loop already re-rasterises for each eye, so per-eye geometry is
    // free. The symmetry was a choice, not a performance constraint.
    //
    // Physically: turning toward one side foreshortens the eye on that side and opens up the far
    // one. So the leading eye narrows, the trailing eye widens, and both translate. Small numbers
    // on purpose -- asymmetry reads strongly, and overdoing it looks like a squint or a wink.
    const float lead = (i == 0) ? -gx : gx;        // +1 when this eye leads the turn
    gazeW = 1.0f - lead * 0.13f;                   // leading eye foreshortens
    const int ecx = EW / 2 + (int)(gx * 12), ecy = EH / 2 + (int)(gy * 12);
    drawEye(&eye, ecx, ecy, open, col, bg, i == 0 ? -1 : +1, gauge, dimCol,
            glintCol);
    eye.pushSprite(slots[i] - EW / 2, eyeY - EH / 2);
  }

  frames++;
  if (t - lastFpsMs >= 1000) {
    fps = frames * 1000.0f / (t - lastFpsMs);
    frames = 0;
    lastFpsMs = t;
    Serial.printf("[FACE] fps=%.0f state=%s eyePsram=%d\n", fps,
                  STATES[shownState].name, g_eyePsram);
  }
}
