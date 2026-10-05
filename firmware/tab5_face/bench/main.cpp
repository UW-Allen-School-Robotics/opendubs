// Tab5 bench harness. Reports over USB serial at 115200; nothing here touches a robot.
//
// Answers two questions that need a device with nothing attached:
//   A) what a second eye costs, and whether sprites fit in INTERNAL SRAM
//   B) whether the ES7210 microphone works, and whether it can share the I2S port with the speaker
//
// Everything is printed with the number AND what it means, because the point is to decide a
// design from it later, not to watch it scroll past now.

#include <M5Unified.h>
#include <stdarg.h>

// EXACTLY what face.cpp uses. The face allocates ONE sprite and pushes it twice (slots[2],
// mirrored by i==0?-1:+1), so "what does a second eye cost" is already answered by the shipping
// firmware: nothing. The real question is which memory the one sprite lands in.
static constexpr int kEyeW = 324;
static constexpr int kEyeH = 448;
static constexpr int kFrames = 60;     // enough to average out a stray scheduler tick

static int g_y = 4;

// Everything rep() prints is KEPT, so the whole report can be re-emitted over serial on a timer.
// Output written once during setup() is unreadable in practice: attaching to this port resets the
// chip, so a reader never sees the boot it caused.
static char g_log[2048];
static size_t g_logLen = 0;

// Report to the SCREEN as well as serial. The ESP32-P4's USB-serial-JTAG enters ROM download mode
// whenever a host touches DTR/RTS, which makes reading results back over USB unreliable -- and the
// device has a 1280x720 panel sitting right there. The screen is the primary output here.
static void rep(const char *fmt, ...) {
  char buf[200];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof buf, fmt, ap); va_end(ap);
  Serial.println(buf);
  const size_t n = strlen(buf);
  if (g_logLen + n + 2 < sizeof g_log) {
    memcpy(g_log + g_logLen, buf, n); g_logLen += n;
    g_log[g_logLen++] = '\n'; g_log[g_logLen] = 0;
  }
  if (g_y > M5.Display.height() - 20) { M5.Display.fillScreen(TFT_BLACK); g_y = 4; }
  M5.Display.setCursor(6, g_y);
  M5.Display.print(buf);
  g_y += 19;
}

static void line() { rep("------------------------------------------------------------"); }

static size_t freeInternal() { return heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT); }
// The number that actually governs. A 324x448x16bpp sprite needs 290 KB CONTIGUOUS; free-total
// can be comfortably larger while the largest block is not, which is why the first run reported
// 413 KB free and still failed to allocate.
static size_t largestInternal() { return heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT); }
static size_t freePsram()    { return heap_caps_get_free_size(MALLOC_CAP_SPIRAM); }

// Draw into a sprite the way the face does: the glow is rasterised each frame rather than
// blitted from a template, which is the change that took it from ~15 fps to 74.
static void paintEye(M5Canvas &s, int phase) {
  s.fillSprite(TFT_BLACK);
  const int cx = s.width() / 2, cy = s.height() / 2;
  for (int r = 0; r < 14; ++r) {
    const uint8_t v = 30 + r * 3;
    s.drawEllipse(cx, cy, s.width() / 2 - r, s.height() / 2 - r, s.color565(0, v, v));
  }
  s.fillEllipse(cx, cy, s.width() / 2 - 16, s.height() / 2 - 16, s.color565(0x19, 0xd1, 0xc4));
  s.fillEllipse(cx, cy + (phase % 7) - 3, 48, 48, TFT_BLACK);
}

// Takes ONE canvas and a PUSH COUNT -- not an array. An earlier version took `M5Canvas*` and
// indexed `eyes[i]`, so passing a single object with n=2 read past the end of it and put the
// device into a reboot loop.
//
// Re-rasterises per push because that is what face.cpp does: drawGlow() is inside its
// `for (i = 0; i < 2; ++i)` loop, so a two-eye frame costs two glows, not one.
static float timeDraws(M5Canvas &s, int pushes, int frames) {
  const uint32_t t0 = millis();
  for (int f = 0; f < frames; ++f) {
    for (int i = 0; i < pushes; ++i) {
      paintEye(s, f);
      s.pushSprite(i == 0 ? 120 : 640, 136);
    }
  }
  const uint32_t dt = millis() - t0;
  return dt ? (1000.0f * frames) / dt : 0.0f;
}

// Measure FIRST, report AFTER. The benchmark pushes 300x448 sprites to the panel, so anything
// printed between measurements is immediately painted over by the next draw -- the first run
// showed an eye and no numbers for exactly that reason. Results are collected, then the screen is
// cleared once and the whole block printed as text.
struct RenderResult {
  bool  fit[2]      = {false, false};
  int   used[2]     = {0, 0};
  size_t freeAfter  = 0;
  float oneFps      = 0;
  float twoFps      = 0;
  float psramFps    = 0;
  bool  psramOk     = false;
};

static RenderResult measureRender() {
  RenderResult r;
  M5Canvas eye(&M5.Display);
  eye.setColorDepth(16);

  // 1. internal, at the face's real size
  eye.setPsram(false);
  size_t before = freeInternal();
  r.fit[0]  = eye.createSprite(kEyeW, kEyeH) != nullptr;
  r.used[0] = (int)(before - freeInternal());

  // 2. the face's own fallback, so we learn what it would actually be running
  if (!r.fit[0]) {
    eye.setPsram(true);
    r.fit[1] = eye.createSprite(kEyeW, kEyeH) != nullptr;   // fit[1] reused: "landed in PSRAM"
  }
  r.freeAfter = largestInternal();

  // The face pushes the SAME sprite twice, so measure that, not two sprites.
  if (eye.width()) { r.oneFps = timeDraws(eye, 1, kFrames); r.twoFps = timeDraws(eye, 2, kFrames); }
  eye.deleteSprite();

  // 3. the largest eye that DOES fit internal, which is the real design budget
  for (int w = kEyeW; w >= 96; w -= 12) {
    M5Canvas probe(&M5.Display);
    probe.setColorDepth(16);
    probe.setPsram(false);
    if (probe.createSprite(w, kEyeH)) {
      r.psramFps = (float)w;                                 // reused: widest internal-fitting eye
      r.psramOk = true;
      probe.deleteSprite();
      break;
    }
  }
  return r;
}

static void reportRender(const RenderResult &r) {
  line();
  rep("A) RENDER  panel %dx%d  eye %dx%d (face's own size)",
      (int)M5.Display.width(), (int)M5.Display.height(), kEyeW, kEyeH);
  rep("   needs %d KB CONTIGUOUS", (kEyeW * kEyeH * 2) / 1024);
  rep("   internal: %s%s", r.fit[0] ? "OK" : "FAILED",
      r.fit[0] ? "" : (r.fit[1] ? "  -> fell back to PSRAM, as face.cpp does" : "  -> PSRAM FAILED TOO"));
  rep("   largest contiguous internal now: %u KB", (unsigned)(r.freeAfter / 1024));
  rep("   ONE push : %5.1f fps", r.oneFps);
  rep("   TWO pushes (what the face does): %5.1f fps", r.twoFps);
  if (r.psramOk) rep("   widest eye that FITS internal: %dx%d", (int)r.psramFps, kEyeH);
  else           rep("   no eye >=96 px wide fits internal SRAM");
}

// The ES8388 keeps state across a software reset because it has its own power rail, so it must be
// quiesced and then held in reset. Lifted from tab5_face/src/main.cpp, where this was measured:
// sound worked after a power cycle and never after an OTA, while begin() reported success both
// times. Reproduced here so the bench starts from the same known state as the real face.
static void resetCodec() {
  constexpr uint8_t kEs = 0x10;
  M5.In_I2C.bitOff(0x43, 0x05, 0b00000010, 400000);    // amp off, no pops
  delay(5);
  M5.In_I2C.writeRegister8(kEs, 0x19, 0x04, 400000);   // DAC mute
  M5.In_I2C.writeRegister8(kEs, 0x04, 0xC0, 400000);   // DAC down
  M5.In_I2C.writeRegister8(kEs, 0x03, 0xFF, 400000);   // ADC down
  M5.In_I2C.writeRegister8(kEs, 0x02, 0xFF, 400000);   // chip down
  delay(20);
  M5.In_I2C.writeRegister8(kEs, 0x00, 0x80, 400000);   // assert reset
  delay(100);                                          // and HOLD it
  M5.In_I2C.writeRegister8(kEs, 0x00, 0x00, 400000);   // release
  delay(50);
  const uint8_t c1 = M5.In_I2C.readRegister8(kEs, 0x00, 400000);
  rep("   ES8388 ctrl1=0x%02X%s\n", c1, c1 == 0xFF ? "  (NOT RESPONDING)" : "");
}

static constexpr size_t kSamples = 1024;               // 64 ms at 16 kHz, x2 for stereo
static int16_t rec[kSamples * 2];

// RMS per channel. Stereo matters because a left/right energy difference is enough to turn
// toward a voice, which is the behaviour people actually notice -- we do not need a 4-mic array.
// A reserved bottom line that overwrites itself, so the live readout never scrolls the one-shot
// measurements off the panel -- which it did on the first run, taking the render numbers with it.
static void liveLine(const char *s) {
  const int y = M5.Display.height() - 26;
  M5.Display.fillRect(0, y, M5.Display.width(), 24, TFT_BLACK);
  M5.Display.setCursor(6, y);
  M5.Display.setTextColor(TFT_CYAN, TFT_BLACK);
  M5.Display.print(s);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
}

static void levels(const char *tag) {
  if (!M5.Mic.record(rec, kSamples, 16000, true)) { Serial.printf("   %s record() refused\n", tag); return; }
  while (M5.Mic.isRecording()) { delay(1); }
  double l = 0, r = 0;
  for (size_t i = 0; i < kSamples; ++i) { l += double(rec[2*i]) * rec[2*i]; r += double(rec[2*i+1]) * rec[2*i+1]; }
  l = sqrt(l / kSamples); r = sqrt(r / kSamples);
  rep("   %-22s L %6.0f  R %6.0f  diff %+6.0f\n", tag, l, r, l - r);
}

static void audioBench() {
  line();
  rep("B) AUDIO");
  resetCodec();

  M5.Speaker.setVolume(96);
  const bool spk = M5.Speaker.begin();
  rep("   Speaker.begin() -> %s\n", spk ? "READY" : "FAILED");

  const bool mic = M5.Mic.begin();
  Serial.printf("   Mic.begin()     -> %s%s\n", mic ? "READY" : "FAILED",
                mic && !M5.Speaker.isEnabled() ? "   <- NOTE: starting the mic DISABLED the speaker" : "");
  if (!mic) { Serial.println("   -> nothing can read this microphone; stop here"); return; }

  rep("   quiet baseline (say nothing):");
  for (int i = 0; i < 3; ++i) { levels("baseline"); delay(200); }

  // The decisive question for conversational use: can both hold the port at once?
  rep("   speaker + mic together:");
  const bool reEnabled = M5.Speaker.begin();
  Serial.printf("   Speaker.begin() again -> %s   mic still running: %s\n",
                reEnabled ? "READY" : "FAILED", M5.Mic.isEnabled() ? "YES" : "NO");
  if (reEnabled && M5.Mic.isEnabled()) {
    rep("   -> BOTH can hold the port: the robot can listen while it speaks");
    M5.Speaker.tone(3000, 300);                        // 2-4 kHz is what this speaker carries
    levels("during a 3 kHz tone");
  } else {
    rep("   -> MUTUALLY EXCLUSIVE: listening and speaking must be taken in turns");
  }
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(3);
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
  M5.Display.setTextSize(2);
  Serial.begin(115200);
  // WITHOUT THIS THE FIRMWARE HANGS IN setup(). The Tab5 is USB-CDC (ARDUINO_USB_MODE=1), so
  // Serial writes BLOCK whenever no host is draining the port -- and at boot nothing is. The face
  // firmware learned this already; the bench rediscovered it the hard way.
  Serial.setTxTimeoutMs(0);
  delay(2500);                                         // let the CDC enumerate after a reset
  line();
  rep("Tab5 BENCH — render headroom and microphone. Nothing here drives a robot.");
  // Eyes flash on the panel while this runs; that is the measurement, not the output.
  const RenderResult rr = measureRender();
  M5.Display.fillScreen(TFT_BLACK);
  g_y = 4;
  reportRender(rr);
  audioBench();
  line();
  rep("done. Speak near the device; levels print every 2 s.");
}

void loop() {
  // Re-emit the whole report on a timer. Serial on this board is the USB-serial-JTAG: touching
  // DTR/RTS drops it into ROM download mode, so a reader must attach without handshaking and
  // simply wait -- which only works if the device keeps saying it.
  static uint32_t lastReport = 0;
  if (millis() - lastReport > 5000) {
    lastReport = millis();
    Serial.println();
    Serial.println("===== Tab5 bench report (repeats every 5 s) =====");
    Serial.write((const uint8_t *)g_log, g_logLen);
    Serial.println("================================================");
  }
  static uint32_t last = 0;
  if (millis() - last > 300 && M5.Mic.isEnabled()) {
    last = millis();
    if (M5.Mic.record(rec, kSamples, 16000, true)) {
      while (M5.Mic.isRecording()) { delay(1); }
      double l = 0, r = 0;
      for (size_t i = 0; i < kSamples; ++i) {
        l += double(rec[2*i]) * rec[2*i]; r += double(rec[2*i+1]) * rec[2*i+1];
      }
      l = sqrt(l / kSamples); r = sqrt(r / kSamples);
      char buf[128];
      // The sign of the difference is the whole point: it is what "turn toward the voice" reads.
      snprintf(buf, sizeof buf, "LIVE  L %6.0f  R %6.0f  diff %+7.0f  %s",
               l, r, l - r, (l - r) > 150 ? "<- LEFT" : (r - l) > 150 ? "RIGHT ->" : "centre");
      liveLine(buf);
      Serial.println(buf);
    }
  }
  M5.update();
  delay(10);
}
