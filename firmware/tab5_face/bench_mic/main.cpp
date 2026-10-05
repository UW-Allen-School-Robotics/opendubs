// Tab5 MIC EVAL -- interactive. Nothing here drives a robot.
//
// Built because the previous mic "result" was worthless: the only stimulus was a human making a
// noise, the measurement ran while nobody was at the bench, and a quiet room duly read as low,
// correlated levels on both channels -- which was then misread as a dead chip. Two rules follow.
//
//   1. THE OPERATOR SETS THE PACE. Everything is touch-driven and live, so a reading is only ever
//      taken while someone is deliberately making the sound it is supposed to measure.
//   2. NO STIMULUS, NO VERDICT. The scripted pass refuses to conclude anything about a window
//      whose level never rose above the measured noise floor; it reports NO SIGNAL instead.
//
// The ES7210 IS configured on this board -- _microphone_enabled_cb_tab5() in M5Unified.inl is a
// complete ESP32-P4-guarded init. What that init does NOT do is write a gain register, where the
// sibling CoreP4x path sets 0x44-0x46 to 0x1B. So PGA gain is the first knob, and it is exposed
// here live rather than guessed at.
#include <M5Unified.h>
#include <math.h>

static constexpr uint8_t kEs7210 = 0x40;
static constexpr uint8_t kMic1Gain = 0x43;   // ES7210 MIC1_GAIN
static constexpr uint8_t kMic2Gain = 0x44;   // ES7210 MIC2_GAIN

// Stepped through live rather than reasoned about: the datasheet's gain encoding is not worth
// trusting over what the bars actually do. 0x00 is deliberately NOT in this table -- writing it
// at startup, on the assumption that it was "the chip default", silenced a working microphone and
// cost a round of testing. It is the bottom of the PGA range, not the reset value.
static const uint8_t kGains[] = {0x05, 0x0A, 0x0F, 0x14, 0x18, 0x1B, 0x1E};
static constexpr int  kNGains = sizeof(kGains) / sizeof(kGains[0]);

// -1 means UNTOUCHED: whatever _microphone_enabled_cb_tab5() left in the chip. The harness starts
// here and can always return here exactly, because the real default is read back, never assumed.
static int     g_gainIdx = -1;
static uint8_t g_defGain1 = 0, g_defGain2 = 0;

static constexpr size_t kSamples = 512;      // 32 ms at 16 kHz
static int16_t rec[kSamples * 2];

static float g_noiseL = 0, g_noiseR = 0;     // measured floor, set by the SILENCE step
static bool  g_haveFloor = false;

// idx -1 restores the values the M5Unified init actually left, byte for byte.
static void applyGain(int idx) {
  const uint8_t v1 = idx < 0 ? g_defGain1 : kGains[idx];
  const uint8_t v2 = idx < 0 ? g_defGain2 : kGains[idx];
  M5.In_I2C.writeRegister8(kEs7210, kMic1Gain, v1, 400000);
  M5.In_I2C.writeRegister8(kEs7210, kMic2Gain, v2, 400000);
  Serial.printf("[MIC] gain 0x43<-0x%02X 0x44<-0x%02X %s\n", v1, v2,
                idx < 0 ? "(restored the init's own values)" : "");
}

// Read the chip back rather than trusting that a write landed, or that the init ran at all.
static void dumpRegs(const char *when) {
  static const uint8_t regs[] = {0x01, 0x06, 0x08, 0x43, 0x44, 0x4B, 0x4C};
  static const char *names[] = {"CLK_ON_OFF", "DIGITAL_PDN", "MODE_CFG",
                                "MIC1_GAIN", "MIC2_GAIN", "MIC12_PDN", "MIC34_PDN"};
  Serial.printf("[MIC] ES7210 registers %s:\n", when);
  for (size_t i = 0; i < sizeof(regs); ++i) {
    Serial.printf("[MIC]   0x%02X %-12s = 0x%02X\n", regs[i], names[i],
                  M5.In_I2C.readRegister8(kEs7210, regs[i], 400000));
  }
}

// Returns false when record() refuses, so a dead path cannot masquerade as silence.
static bool levels(float &l, float &r) {
  if (!M5.Mic.record(rec, kSamples, 16000, true)) return false;
  while (M5.Mic.isRecording()) { delay(1); }
  double a = 0, b = 0;
  for (size_t i = 0; i < kSamples; ++i) {
    a += double(rec[2 * i]) * rec[2 * i];
    b += double(rec[2 * i + 1]) * rec[2 * i + 1];
  }
  l = sqrtf(a / kSamples); r = sqrtf(b / kSamples);
  return true;
}

// ---- UI: full-screen, redrawn in place. No scrolling log -- a log walks the content off the
// panel, which is exactly what made the last harness unusable to stand in front of.
static constexpr int kBarX = 150, kBarW = 900, kBarH = 54;
static void drawStatic() {
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(3);
  M5.Display.setCursor(20, 16);
  M5.Display.print("Tab5 MIC EVAL");
  M5.Display.setTextSize(2);
  M5.Display.setCursor(20, 640);
  M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
  M5.Display.print("LEFT third: gain down (to DEFAULT)   MIDDLE: scripted test   RIGHT third: gain up");
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setTextSize(3);
  M5.Display.setCursor(20, 300); M5.Display.print("L");
  M5.Display.setCursor(20, 400); M5.Display.print("R");
}

static void drawBar(int y, float v, uint16_t col) {
  // Log scale: the interesting range is 10..10000 on a +/-32768 full scale, and a linear bar
  // leaves everything below a shout invisible.
  float frac = v <= 1 ? 0 : logf(v) / logf(32768.0f);
  frac = frac < 0 ? 0 : (frac > 1 ? 1 : frac);
  M5.Display.drawRect(kBarX, y, kBarW, kBarH, TFT_DARKGREY);
  const int w = (int)(frac * (kBarW - 4));
  M5.Display.fillRect(kBarX + 2, y + 2, w, kBarH - 4, col);
  M5.Display.fillRect(kBarX + 2 + w, y + 2, kBarW - 4 - w, kBarH - 4, TFT_BLACK);
}

static void drawLive(float l, float r) {
  drawBar(290, l, TFT_CYAN);
  drawBar(390, r, TFT_MAGENTA);
  M5.Display.setTextSize(3);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.setCursor(20, 90);
  if (g_gainIdx < 0)
    M5.Display.printf("gain DEFAULT 0x%02X/0x%02X   L %6.0f  R %6.0f      ",
                      g_defGain1, g_defGain2, l, r);
  else
    M5.Display.printf("gain 0x%02X (%d/%d)        L %6.0f  R %6.0f      ",
                      kGains[g_gainIdx], g_gainIdx + 1, kNGains, l, r);

  // The sign of the difference is the whole point: it is what "turn toward the voice" reads.
  M5.Display.setCursor(20, 160);
  M5.Display.setTextSize(4);
  const float lo = g_haveFloor ? (g_noiseL + g_noiseR) * 1.6f : 60.0f;
  if ((l + r) * 0.5f < lo) {
    M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
    M5.Display.print("-- no signal above floor --   ");
  } else {
    const float ratio = (l + 1) / (r + 1);
    M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
    if (ratio > 1.15f)      M5.Display.print("<<<< SOUND ON THE LEFT      ");
    else if (ratio < 0.87f) M5.Display.print("     SOUND ON THE RIGHT >>>>");
    else                    M5.Display.print("     ^^ CENTRE ^^           ");
  }
}

// ---- scripted pass: silence floor, then a self-stimulated tone, then left/right by voice.
static void banner(const char *s, uint16_t col) {
  M5.Display.fillRect(0, 160, 1280, 90, TFT_BLACK);
  M5.Display.setTextSize(4);
  M5.Display.setTextColor(col, TFT_BLACK);
  M5.Display.setCursor(20, 170);
  M5.Display.print(s);
}

// Averages over `ms`, keeping the PEAK window -- speech is not stationary, and a mean over a
// window containing a pause understates it.
static void window(const char *tag, int ms, float &pl, float &pr) {
  pl = pr = 0;
  const uint32_t t0 = millis();
  while (millis() - t0 < (uint32_t)ms) {
    float l, r;
    if (!levels(l, r)) { Serial.printf("[MIC] %s record() refused\n", tag); return; }
    if (l + r > pl + pr) { pl = l; pr = r; }
    drawLive(l, r);
    M5.Display.setTextSize(3);
    M5.Display.setTextColor(TFT_YELLOW, TFT_BLACK);
    M5.Display.setCursor(20, 470);
    M5.Display.printf("%s  %.1fs left   ", tag, (ms - (int)(millis() - t0)) / 1000.0f);
  }
  Serial.printf("[MIC] %-22s peak L %6.0f  R %6.0f  ratio %5.2f\n", tag, pl, pr, (pl + 1) / (pr + 1));
}

static void verdict(const char *tag, float l, float r, const char *expect) {
  const float lo = (g_noiseL + g_noiseR) * 1.6f;
  const char *got;
  if ((l + r) * 0.5f < lo)          got = "NO SIGNAL";
  else if ((l + 1) / (r + 1) > 1.15f) got = "LEFT";
  else if ((l + 1) / (r + 1) < 0.87f) got = "RIGHT";
  else                                got = "CENTRE";
  const bool ok = strcmp(got, expect) == 0;
  Serial.printf("[MIC] %-10s expected %-7s got %-9s %s\n", tag, expect, got,
                strcmp(got, "NO SIGNAL") == 0 ? "(inconclusive -- no stimulus reached the mic)"
                                              : (ok ? "OK" : "MISMATCH"));
}

static void scripted() {
  drawStatic();
  banner("QUIET PLEASE - measuring the noise floor", TFT_CYAN);
  delay(1200);
  window("SILENCE", 2500, g_noiseL, g_noiseR);
  g_haveFloor = true;
  Serial.printf("[MIC] noise floor L %.0f R %.0f  (threshold %.0f)\n",
                g_noiseL, g_noiseR, (g_noiseL + g_noiseR) * 1.6f);

  // Self-stimulating: proves the capture path works without needing anyone in the room.
  banner("SELF TEST - the device plays its own tone", TFT_CYAN);
  float tl, tr;
  M5.Speaker.begin();
  M5.Speaker.tone(3000, 2200);
  window("OWN 3kHz TONE", 2000, tl, tr);
  Serial.printf("[MIC] tone vs floor: %.1fx\n", ((tl + tr) / 2) / ((g_noiseL + g_noiseR) / 2 + 1));

  banner("SPEAK NOW - FROM THE LEFT SIDE", TFT_GREEN);
  float ll, lr; window("VOICE LEFT", 4000, ll, lr);
  banner("SPEAK NOW - FROM THE RIGHT SIDE", TFT_GREEN);
  float rl, rr; window("VOICE RIGHT", 4000, rl, rr);
  banner("SPEAK NOW - FROM STRAIGHT IN FRONT", TFT_GREEN);
  float cl, cr; window("VOICE CENTRE", 4000, cl, cr);

  Serial.println("[MIC] ---- scripted pass ----");
  Serial.printf("[MIC] gain %s\n", g_gainIdx < 0 ? "DEFAULT (as the init left it)" : "stepped");
  dumpRegs("during the scripted pass");
  verdict("left",   ll, lr, "LEFT");
  verdict("right",  rl, rr, "RIGHT");
  verdict("centre", cl, cr, "CENTRE");
  Serial.println("[MIC] ------------------------");

  banner("done - see serial. Touch to return to live.", TFT_WHITE);
  while (!M5.Touch.getCount()) { M5.update(); delay(20); }
  drawStatic();
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  M5.Display.setRotation(3);
  Serial.begin(115200);
  // Without this the firmware HANGS here: USB-CDC writes block while no host is draining.
  Serial.setTxTimeoutMs(0);
  delay(2000);
  M5.Speaker.begin();
  const bool mic = M5.Mic.begin();
  Serial.printf("[MIC] Mic.begin() -> %s   Speaker -> %s\n",
                mic ? "READY" : "FAILED", M5.Speaker.isEnabled() ? "READY" : "FAILED");
  // DO NOT WRITE A GAIN HERE. An earlier version wrote 0x00 at startup believing it to be the
  // default; it is the bottom of the range, and it silenced a microphone that had been working.
  // Read what the init left instead, and only ever change it on an explicit touch.
  g_defGain1 = M5.In_I2C.readRegister8(kEs7210, kMic1Gain, 400000);
  g_defGain2 = M5.In_I2C.readRegister8(kEs7210, kMic2Gain, 400000);
  dumpRegs("as _microphone_enabled_cb_tab5() left them");
  drawStatic();
}

void loop() {
  M5.update();
  if (M5.Touch.getCount()) {
    const auto p = M5.Touch.getDetail();
    if (p.wasPressed()) {
      if (p.x < 1280 / 3)        { if (g_gainIdx >= 0) applyGain(--g_gainIdx); }
      else if (p.x > 2 * 1280 / 3) { if (g_gainIdx < kNGains - 1) applyGain(++g_gainIdx); }
      else                       { scripted(); }
    }
  }
  float l, r;
  if (levels(l, r)) drawLive(l, r);

  static uint32_t last = 0;
  if (millis() - last > 1000) {   // 1 Hz to serial: enough to follow, not enough to flood
    last = millis();
    Serial.printf("[MIC] live gain=%-6s L %6.0f R %6.0f ratio %5.2f\n",
                  g_gainIdx < 0 ? "DEF" : String(kGains[g_gainIdx], HEX).c_str(),
                  l, r, (l + 1) / (r + 1));
  }
}
