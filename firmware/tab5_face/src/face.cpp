#include "face.h"
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
//     324x448  PSRAM     27.6 fps   <- what this used to do
//     276x448  PSRAM     33.4 fps   <- what it does now: +21%, from pixels alone
//     276x448  internal  39.5 fps   <- LOOKS BETTER, BREAKS THE RADIO. See below.
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
// which costs OTA and payload comms -- i.e. everything -- to buy 6 fps. So the
// +21% here is from the PIXEL REDUCTION only, which is free; the further +18%
// from internal SRAM is NOT available on this hardware and should not be chased.
// A bench that measures a sprite alone cannot see this; only the real firmware,
// with a radio to starve, can.
static M5Canvas eye(&M5.Display);
static const int EW = 276,
                 EH = 448;
// BW MUST TRACK EW. This is the eye BODY inside the sprite, and it is scaled by
// a per-state multiplier that reaches 1.06 (E_STOP in ui_shared.cpp). At the old
// EW=324, 284x1.06 = 301 px left 11 px for the glow; left at 284 with EW=276 the
// widest state would be clipped by 25 px. 242x1.06 = 257 keeps the same margin.
static const float
    BW = 242,
    BH = 376; // eye size; E_STOP height capped so the tall eye still fits
static bool g_eyePsram = false;

static Expr P, T;
static int shownState = -1; // which STATES[] index is currently painted
static bool fullRepaint =
    true; // needs a fillScreen (boot / returned from controller)
static bool stateDirty = false; // state changed → remake glow+label, NO
                                // fillScreen (smooth, no flash)
static uint32_t lastFpsMs = 0, tPrev = 0, frames = 0, nextBlink = 1500,
                gNext = 0;
static float fps = 0, blinkPhase = 0, soc = 0.6f, breathe = 0;
static float gx = 0, gy = 0, gTX = 0, gTY = 0, radarAng = 0, radarPrev = 0;
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
  int w = (int)(BW * P.w);
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

  float dgx = 0, dgy = 0;
  if (is("SAFE_IDLE")) {
    if (t > gNext) {
      gTX = random(-100, 101) / 100.0f;
      gTY = random(-55, 56) / 100.0f;
      gNext = t + 1200 + random(2400);
    }
    dgx = gTX;
    dgy = gTY;
  } else if (is("AUTONOMOUS"))
    dgx = sinf(breathe * 0.7f) * 0.6f;
  if (turnDir != 0) {
    dgx = -turnDir * 0.85f;
    dgy = 0;
  } // gaze leans toward the turn in the VIEWER frame
  gx = lp(gx, dgx, k * 0.5f);
  gy = lp(gy, dgy, k * 0.5f);

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
    int ecx = EW / 2 + (int)(gx * 12), ecy = EH / 2 + (int)(gy * 12);
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
