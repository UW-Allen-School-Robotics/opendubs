#include "prompt.h"

#include <M5Unified.h>

#include "face.h"
#include "hmi.h"
#include "lift.h"
#include "ui_shared.h"

// ---- tuning -----------------------------------------------------------------------------
// Frequencies live in 2-4 kHz because that is what this speaker actually carries — judged by
// ear on the robot with a 400 Hz..4 kHz sweep, where everything below ~2 kHz was faint across
// a bay. PROVISIONAL: to be re-validated before anything depends on them.
// QUIET AND LOW, DELIBERATELY. These were 2600/3200 Hz at whatever volume the speaker came up
// at, which sits in the ear's most sensitive band (~2-4 kHz) and reads as a smoke alarm. The
// listener is the person holding the controls, an arm's length from the lift, in a quiet lab --
// nobody across the room can be pinched by this machine, so the sound does not have to carry.
// ~900 Hz is unmistakably audible and obviously deliberate without being painful.
// 1500/1900 Hz, not 900. Two things fight each other here: the ear is most sensitive around
// 2-4 kHz (which is why the original 2600 Hz read as a smoke alarm) and a speaker this small is
// badly inefficient below about 1 kHz. Going to 900 Hz lost on BOTH counts at once and the
// result was inaudible. 1500 Hz is below the harsh band and still within what the driver can
// actually move air at.
static constexpr uint32_t kAlertHz    = 1500;
static constexpr uint32_t kConfirmLo  = 1500;
static constexpr uint32_t kConfirmHi  = 1900;
static constexpr uint32_t kToneMs     = 120;   // 90 was brief enough to miss at lower volume
// M5Unified's range is 0-255 and its DEFAULT IS 64 (Speaker_Class.hpp, _master_volume). 48 was
// below the default -- quieter than doing nothing, which is not what "set it explicitly" was
// supposed to mean. 140 is comfortably audible at arm's length without carrying across a lab.
// 125. The taper is not linear and there seems to be a threshold low down: 140 was "a little
// loud", 100 was inaudible, and a 300 ms test tone at the library default of 64 had been clearly
// audible before that. Rather than keep bisecting a curve nobody has measured, step down from a
// value that was heard. If this still needs tuning, BEEP <hz> <ms> over UDP 4242 plays a tone
// with the current master volume and costs no flash cycle.
static constexpr uint8_t  kVolume     = 125;   // of 255; library default is 64

// The guard flag can flicker: the zone boundary sits ~60 mm up and the height sensor carries
// about +/-10 mm of noise, so parking near it crosses back and forth. Debounce before SAYING
// anything — an alert that chatters is worse than no alert.
static constexpr uint32_t kDebounceMs = 200;
// One alert per episode, then a reminder if it is still holding. Operators walk away.
static constexpr uint32_t kRemindMs   = 10000;
// After the guard clears, a descent that starts within this window is the consent being spent —
// that is what earns the confirming tone. (Phase 2 replaces this inference with the StamPLC
// publishing the grant itself.)
static constexpr uint32_t kConfirmWinMs = 1500;
static constexpr uint32_t kConfirmShowMs = 1200;   // how long "LOWERING" stays up

static constexpr int kBannerH = 132;

enum class PState : uint8_t { IDLE, HOLDING, CLEARED, CONFIRMED };
static PState   g_st = PState::IDLE;
static bool     g_raw = false;            // last raw guard flag
static uint32_t g_rawSince = 0;           // when the raw flag last changed
static uint32_t g_enteredMs = 0;          // when we entered the current state
static uint32_t g_lastAlertMs = 0;
static bool     g_drawn = false;          // banner is on screen
static uint32_t g_lastDrawMs = 0;

static void beepAlert() {
  M5.Speaker.setVolume(kVolume);
  if (!spkReady()) return;
  M5.Speaker.tone(kAlertHz, kToneMs);     // two short — "stopped, and waiting on you"
  M5.Speaker.tone(kAlertHz, kToneMs);
}

static void beepConfirm() {
  M5.Speaker.setVolume(kVolume);
  if (!spkReady()) return;
  M5.Speaker.tone(kConfirmLo, kToneMs);   // rising — "thank you, going down"
  M5.Speaker.tone(kConfirmHi, kToneMs + 40);
}

// Whatever is underneath has been painting around us; make it repaint itself cleanly.
static void releaseScreen() {
  g_drawn = false;
  faceInvalidate();
  hmiInvalidate();
}

void promptTick(uint32_t nowMs) {
  const auto s = Lift.snapshot();

  // ---- debounce the raw flag ----
  if (s.guardHold != g_raw) {
    g_raw = s.guardHold;
    g_rawSince = nowMs;
  }
  const bool stable = (nowMs - g_rawSince) >= kDebounceMs;
  const bool held = stable ? g_raw : (g_st == PState::HOLDING);

  switch (g_st) {
    case PState::IDLE:
      if (held) {
        g_st = PState::HOLDING;
        g_enteredMs = nowMs;
        g_lastAlertMs = nowMs;
        beepAlert();
        g_drawn = false;                  // force a draw on the next render
      }
      break;

    case PState::HOLDING:
      if (!held) {
        g_st = PState::CLEARED;           // wait briefly to see whether a descent follows
        g_enteredMs = nowMs;
      } else if (nowMs - g_lastAlertMs >= kRemindMs) {
        g_lastAlertMs = nowMs;
        beepAlert();
      }
      break;

    case PState::CLEARED:
      if (held) {                          // it came back: still holding, never really cleared
        g_st = PState::HOLDING;
        g_enteredMs = nowMs;
      } else if (s.motion == Motion::DOWN) {
        g_st = PState::CONFIRMED;          // consent was spent — the lift is moving again
        g_enteredMs = nowMs;
        beepConfirm();
        g_drawn = false;
      } else if (nowMs - g_enteredMs >= kConfirmWinMs) {
        g_st = PState::IDLE;               // cleared without a descent: say nothing
        releaseScreen();
      }
      break;

    case PState::CONFIRMED:
      if (nowMs - g_enteredMs >= kConfirmShowMs) {
        g_st = PState::IDLE;
        releaseScreen();
      }
      break;
  }
}

bool promptActive() { return g_st == PState::HOLDING || g_st == PState::CONFIRMED; }

void promptRender(uint32_t nowMs) {
  if (!promptActive()) return;
  // Redraw periodically rather than every frame: the renderer underneath repaints its own dirty
  // regions and could clip us, but repainting a full-width banner at frame rate is wasted work.
  if (g_drawn && (nowMs - g_lastDrawMs) < 200) return;
  g_drawn = true;
  g_lastDrawMs = nowMs;

  const bool ok = (g_st == PState::CONFIRMED);
  const uint16_t bg = ok ? M5.Display.color888(20, 120, 50)     // green: consent spent, moving
                         : M5.Display.color888(190, 60, 0);     // amber-red: held, waiting
  const int w = M5.Display.width();

  M5.Display.fillRect(0, 0, w, kBannerH, bg);
  M5.Display.fillRect(0, kBannerH - 4, w, 4, M5.Display.color888(0, 0, 0));
  M5.Display.setTextColor(TFT_WHITE, bg);
  M5.Display.setTextDatum(middle_center);

  M5.Display.setTextSize(3);
  M5.Display.drawString(ok ? "LOWERING" : "Z-AXIS HELD", w / 2, 40);
  M5.Display.setTextSize(2);
  M5.Display.drawString(ok ? "consent accepted"
                           : "RELEASE, THEN PRESS DOWN AGAIN",
                        w / 2, 92);
  M5.Display.setTextDatum(top_left);
}
