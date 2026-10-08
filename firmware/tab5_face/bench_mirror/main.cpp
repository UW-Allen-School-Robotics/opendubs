// Does pushRotateZoom's negative zoom_x actually MIRROR, and is it FASTER than re-rasterising?
// Nothing here drives a robot. Needs nobody present: it reads pixels back and prints a verdict.
//
// Why it matters: face.cpp pushes one eye sprite twice but RE-RASTERISES the whole eye between
// pushes, purely because the two eyes are mirror images (i == 0 ? -1 : +1). Measured, that is the
// difference between 55.1 fps (one push) and 27.6 fps (two). If a negative zoom_x mirrors at blit
// time, the second eye costs a blit instead of a full redraw.
//
// Both halves are genuinely open. An affine blit samples per destination pixel and may well cost
// MORE than the raster it saves, so "mirroring works" does not imply "mirroring is faster". The
// test answers the two separately, and a control case proves the test itself is valid before
// either verdict is believed.
#include <M5Unified.h>

static constexpr int kS = 64;          // correctness canvas
static constexpr int kEyeW = 324;      // face.cpp's real geometry, for the timing half
static constexpr int kEyeH = 448;
static constexpr int kFrames = 60;

static char log_[2600];
static size_t logLen_ = 0;
static void rep(const char *fmt, ...) {
  char b[220];
  va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof(b), fmt, ap); va_end(ap);
  size_t n = strlen(b);
  if (logLen_ + n + 2 < sizeof(log_)) { memcpy(log_ + logLen_, b, n); logLen_ += n; log_[logLen_++] = '\n'; }
}

// ---- half 1: does it mirror? ----------------------------------------------------------------
// An asymmetric source (white block on the LEFT quarter) pushed into an OFF-SCREEN canvas, then
// read back. No display, no camera, no human judgement -- just pixels.
static void correctness() {
  M5Canvas src(&M5.Display), dst(&M5.Display);
  src.setColorDepth(16); dst.setColorDepth(16);
  src.setPsram(true);    dst.setPsram(true);
  if (!src.createSprite(kS, kS) || !dst.createSprite(kS, kS)) { rep("A) MIRROR: sprites would not allocate"); return; }

  src.fillSprite(TFT_BLACK);
  src.fillRect(0, 0, kS / 4, kS, TFT_WHITE);     // marker on the LEFT
  src.setPivot(kS / 2.0f, kS / 2.0f);

  struct Case { float zx, zy; const char *name; bool wantRight, wantBottom; };
  const Case cases[] = {
    {+1, +1, "control  zoom +1,+1", false, false},   // must stay LEFT, or the test is wrong
    {-1, +1, "mirror X zoom -1,+1", true,  false},
    {+1, -1, "mirror Y zoom +1,-1", false, true },
  };

  rep("A) MIRROR  (off-screen push, pixels read back -- no human in the loop)");
  for (const Case &c : cases) {
    dst.fillSprite(TFT_BLACK);
    src.pushRotateZoom(&dst, kS / 2.0f, kS / 2.0f, 0.0f, c.zx, c.zy);
    // Sample well inside each half so edge interpolation cannot decide the answer.
    const bool left  = dst.readPixel(kS / 8,     kS / 2) != 0;
    const bool right = dst.readPixel(kS * 7 / 8, kS / 2) != 0;
    const char *where = (left && !right) ? "LEFT" : (right && !left) ? "RIGHT"
                      : (left && right)  ? "BOTH" : "NEITHER";
    const char *want = c.wantRight ? "RIGHT" : "LEFT";
    bool ok = c.wantRight ? (right && !left) : (left && !right);
    if (c.wantBottom) {   // vertical case: judge on rows instead of columns
      const bool top = dst.readPixel(kS / 8, kS / 8) != 0;
      const bool bot = dst.readPixel(kS / 8, kS * 7 / 8) != 0;
      ok = true; where = top ? "marker spans top" : (bot ? "marker spans bottom" : "marker absent");
      want = "(column marker: Y flip not observable on a full-height block)";
    }
    rep("   %-22s -> %-12s want %-8s %s", c.name, where, want, ok ? "OK" : "FAIL");
  }
  src.deleteSprite(); dst.deleteSprite();
}

// ---- half 2: is it faster? ------------------------------------------------------------------
static void paintEye(M5Canvas &s, int phase) {
  s.fillSprite(TFT_BLACK);
  const int cx = s.width() / 2, cy = s.height() / 2;
  for (int r = 0; r < 14; ++r) s.drawEllipse(cx, cy, s.width() / 2 - r, s.height() / 2 - r, s.color565(0, 30 + r * 3, 30 + r * 3));
  s.fillEllipse(cx, cy, s.width() / 2 - 16, s.height() / 2 - 16, s.color565(0x19, 0xd1, 0xc4));
  s.fillEllipse(cx, cy + (phase % 7) - 3, 48, 48, TFT_BLACK);
}

static void timing() {
  M5Canvas eye(&M5.Display);
  eye.setColorDepth(16);
  eye.setPsram(true);                            // face.cpp pins the eye to PSRAM: the radio needs internal
  if (!eye.createSprite(kEyeW, kEyeH)) { rep("B) TIMING: eye sprite would not allocate"); return; }
  eye.setPivot(kEyeW / 2.0f, kEyeH / 2.0f);

  // A: today -- rasterise per eye, plain blit per eye.
  uint32_t t0 = millis();
  for (int f = 0; f < kFrames; ++f)
    for (int i = 0; i < 2; ++i) { paintEye(eye, f); eye.pushSprite(i == 0 ? 120 : 640, 136); }
  const float fpsA = 1000.0f * kFrames / (millis() - t0);

  // B: proposed -- rasterise ONCE, blit twice, second one mirrored.
  t0 = millis();
  for (int f = 0; f < kFrames; ++f) {
    paintEye(eye, f);
    eye.pushSprite(120, 136);
    eye.pushRotateZoom(&M5.Display, 640 + kEyeW / 2.0f, 136 + kEyeH / 2.0f, 0.0f, -1.0f, 1.0f);
  }
  const float fpsB = 1000.0f * kFrames / (millis() - t0);

  // C: how expensive is the affine blit on its own? Separates "saved a raster" from "paid for a
  // transform", which is the only way to know whether this generalises to other uses.
  t0 = millis();
  for (int f = 0; f < kFrames; ++f) { paintEye(eye, f); eye.pushRotateZoom(&M5.Display, 120 + kEyeW / 2.0f, 136 + kEyeH / 2.0f, 0.0f, 1.0f, 1.0f); }
  const float fpsC = 1000.0f * kFrames / (millis() - t0);

  rep("B) TIMING  eye %dx%d in PSRAM, %d frames", kEyeW, kEyeH, kFrames);
  rep("   A  2 rasters + 2 pushSprite  (today) : %5.1f fps", fpsA);
  rep("   B  1 raster  + push + mirror (new)   : %5.1f fps   %+.0f%%", fpsB, 100.0f * (fpsB / fpsA - 1.0f));
  rep("   C  1 raster  + 1 pushRotateZoom      : %5.1f fps  (affine blit cost alone)", fpsC);
  eye.deleteSprite();
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(3);
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);            // mandatory; also makes writes lossy -> emit line by line
  delay(2000);
  correctness();
  timing();
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextSize(2);
  M5.Display.setCursor(6, 6);
  M5.Display.print("mirror bench - see serial");
}

void loop() {
  static uint32_t last = 0;
  if (millis() - last < 5000) return;  // repeat: a result printed once cannot be read
  last = millis();
  Serial.println("\n===== pushRotateZoom mirror bench (repeats every 5 s) =====");
  for (size_t i = 0, s = 0; i <= logLen_; ++i)
    if (i == logLen_ || log_[i] == '\n') { Serial.write((const uint8_t *)(log_ + s), i - s); Serial.println(); delay(4); s = i + 1; }
  Serial.println("==========================================================");
}
