#include "pantilt.h"
#include "tof.h"
#include "touch_ui_config.h"

#ifndef OPENDUBS_PANTILT
#define OPENDUBS_PANTILT 0
#endif

#if defined(ESP_PLATFORM) && OPENDUBS_PANTILT

#include <bsp/m5stack_tab5.h>
#include <hal/hal.h>
#include <driver/i2c_master.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <nvs.h>
#include <nvs_flash.h>
#include <math.h>
#include <stdio.h>

namespace {

constexpr const char* kTag = "pantilt";

// ---- the 8Servo, exactly as the Arduino firmware drives it -------------------------------------
// Register map and channel assignment are taken verbatim from firmware/tab5_face/src/main.cpp:
// mode register 0x00 + channel takes SERVO_CTL_MODE (3); pulse register 0x60 + channel*2 takes a
// 16-bit little-endian microsecond value; 0..180 degrees maps to 500..2500 us.
constexpr uint8_t kAddr      = 0x25;
constexpr uint8_t kModeReg   = 0x00;   // + channel
constexpr uint8_t kPulseReg  = 0x60;   // + channel*2, 16-bit LE microseconds
constexpr uint8_t kModeServo = 3;      // SERVO_CTL_MODE
constexpr uint8_t kChTilt    = 0;
constexpr uint8_t kChPan     = 1;

// PER-UNIT MECHANISM, not a preference. Both axes are reversed on this head because of how the
// horns and the bracket went together, and the Arduino firmware corrects it once at the write for
// exactly the right reason: otherwise every input source needs its own sign and they will not agree
// for long. Its comment records the evidence -- "the D-pad drove it mirrored horizontally and
// vertically", observed on the bench. If a future head is built differently this is the one place
// to change, and it belongs with the per-robot values rather than being treated as a constant of
// the design.
constexpr bool kPanReverse  = true;
constexpr bool kTiltReverse = true;

// ---- the control law's thresholds. NAMED PLACEHOLDERS -- see head-turn-gaze.md "Status". --------
// Every one of these can only be judged by someone standing in front of the robot.
constexpr float kEyeOnly        = 0.25f;  // offset below which the head never moves
constexpr float kHeadConfidence = 0.60f;  // below this a detection costs eyes only, never a turn
constexpr float kPanRangeDeg    = 30.0f;  // hard bound, degrees either side of centre
constexpr float kTiltRangeDeg   = 18.0f;  // smaller: the head has less room vertically
constexpr float kSlewDps        = 25.0f;  // SLOW on purpose. "Err slow; this is a robot near people."
constexpr float kDeadbandDeg    = 1.5f;   // servo jitter at rest is worse than no motion
constexpr float kCentreDeg      = 90.0f;
constexpr uint32_t kPeriodMs    = 20;     // 50 Hz, matching the Arduino control task
constexpr uint32_t kRcHoldMs    = 600;    // RC's lease, matching the Arduino's kManualHoldMs
constexpr uint32_t kTouchHoldMs = 600;    // the same lease for on-face touch
constexpr float    kDpadDps     = 45.0f;  // the Arduino's on-screen d-pad slew, verbatim

// RC GETS THE FULL TRAVEL; autonomous motion does not.
//
// The conservative +/-30 / +/-18 envelope exists because autonomous motion near people should be
// modest and legible. An operator on a stick is a human deciding where to look, and per the
// authority boundary in head-ownership.md the head cannot realistically damage anything -- so
// clamping a human to a third of the travel is paternalistic rather than careful. The Arduino
// firmware gave the stick 0..180 and that is what it gets back.
constexpr float kRcPanMin  = 0.0f, kRcPanMax = 180.0f;   // pan was never homed: full travel is fine

// TILT IS MECHANICALLY LIMITED AND THE LIMITS ARE PER-ROBOT. Read from NVS at bring-up; see
// loadTiltLimits(). These are the fallback used only when the robot has never been commissioned.
//
// NOT 0..180. The Arduino firmware clamps tilt to limits it DISCOVERED with a stall-current sweep
// precisely because the axis has hard end-stops, and on this robot they are 25 and 107 -- an 82
// degree range. Allowing 0..180 would drive 25 degrees below the lower stop and 73 above the
// upper one, stalling the servo against the mechanism in both directions.
//
// So an uncommissioned robot gets a TIGHT band around centre rather than the full sweep: with the
// mechanical range unknown, the extremes are exactly where not to go. It is logged loudly, because
// a head that can only nod slightly should be explainable.
constexpr float kTiltFallbackHalfBand = 10.0f;
float g_tiltMin = kCentreDeg - kTiltFallbackHalfBand;
float g_tiltMax = kCentreDeg + kTiltFallbackHalfBand;
bool  g_tiltLimitsKnown = false;

i2c_master_dev_handle_t g_dev = nullptr;
bool     g_present  = false;
float    g_pan      = kCentreDeg;         // current commanded angle, degrees
float    g_tilt     = kCentreDeg;
float    g_panTgt   = kCentreDeg;
float    g_tiltTgt  = kCentreDeg;
uint32_t g_lastMs   = 0;
int      g_lastPanUs = -1, g_lastTiltUs = -1;
char     g_status[64] = "pantilt: not started";
float    g_rcPan = 0.0f, g_rcTilt = 0.0f;   // deg/s, as sent
uint32_t g_rcMs  = 0;                        // when the last frame arrived
float    g_tpPan = 0.0f, g_tpTilt = 0.0f;   // on-face touch d-pad, deg/s
uint32_t g_tpMs  = 0;
bool     g_tracking = true;                  // MODE button; autonomous tracking on by default

// THE COMMISSIONED TILT LIMITS, read from where the Arduino firmware put them.
//
// Arduino `Preferences` IS ESP-IDF NVS: prefs.begin("opendubs") opens that namespace and
// prefs.putInt("tiltMin", ...) is nvs_set_i32. The partition sits at 0x9000 and our flash never
// writes it, so these values have survived every reflash -- they did not need re-deriving and they
// must not be overwritten. This is exactly the class of per-robot value that stays on the robot.
void loadTiltLimits()
{
    nvs_flash_init();                       // idempotent; settingsBuild() normally got here first
    nvs_handle_t h;
    if (nvs_open("opendubs", NVS_READONLY, &h) != ESP_OK) {
        ESP_LOGW(kTag, "tilt limits: namespace 'opendubs' not found -- using the narrow fallback "
                       "band %.0f..%.0f. Tilt range is UNKNOWN on this unit.", g_tiltMin, g_tiltMax);
        return;
    }
    int32_t lo = -1, hi = -1;
    const bool okLo = nvs_get_i32(h, "tiltMin", &lo) == ESP_OK;
    const bool okHi = nvs_get_i32(h, "tiltMax", &hi) == ESP_OK;
    nvs_close(h);

    // -1 is the Arduino's own "not commissioned" sentinel, and a reversed or degenerate pair is
    // not trustworthy either -- treat any of those as unknown rather than clamping to nonsense.
    if (!okLo || !okHi || lo < 0 || hi < 0 || hi - lo < 10) {
        ESP_LOGW(kTag, "tilt limits: not commissioned (min %ld max %ld) -- narrow fallback band "
                       "%.0f..%.0f", (long)lo, (long)hi, g_tiltMin, g_tiltMax);
        return;
    }
    g_tiltMin = (float)lo;
    g_tiltMax = (float)hi;
    g_tiltLimitsKnown = true;
    ESP_LOGI(kTag, "tilt limits from NVS: %.0f..%.0f (%.0f deg of travel)",
             g_tiltMin, g_tiltMax, g_tiltMax - g_tiltMin);
}

bool write2(uint8_t reg, uint8_t v)
{
    if (!g_dev) return false;
    const uint8_t b[2] = {reg, v};
    const esp_err_t e = i2c_master_transmit(g_dev, b, 2, 100);
    if (e != ESP_OK) ESP_LOGE(kTag, "write reg 0x%02x = %u failed: %s", reg, v, esp_err_to_name(e));
    return e == ESP_OK;
}

bool writePulse(uint8_t ch, uint16_t us)
{
    const uint8_t b[3] = {(uint8_t)(kPulseReg + ch * 2), (uint8_t)(us & 0xff), (uint8_t)(us >> 8)};
    return g_dev && i2c_master_transmit(g_dev, b, 3, 50) == ESP_OK;
}

uint16_t degToUs(float deg, bool reverse)
{
    if (deg < 0.0f) deg = 0.0f;
    if (deg > 180.0f) deg = 180.0f;
    const float d = reverse ? (180.0f - deg) : deg;
    return (uint16_t)(500.0f + d * (2000.0f / 180.0f));   // 0..180 -> 500..2500 us
}

float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

}  // namespace

void pantiltBegin()
{
    // PORT A NEEDS ITS 5 V RAIL ENABLED FIRST, and that is the piece that was missing.
    //
    // Straight from the vendor demo's own working Port A code
    // (app_launcher/view/panel_i2c_scan.cpp), which REFUSES to touch the bus unless ext 5 V is on
    // and says why: "Scan without ext 5v cause no response blocking". It enables the rail, calls
    // initPortAI2c(), and only then scans. Our version called bsp_ext_i2c_init() directly and
    // never powered the port -- so transmits returned ESP_ERR_INVALID_STATE on a bus whose handle
    // was perfectly valid, which sent me chasing probes, clock rates and an imagined camera port
    // conflict instead of reading the one piece of code in this tree that drives this bus.
    //
    // The Arduino firmware never needed this because the rail was already on; the IO expander
    // retains its state across a soft reset, which is also why an early probe at 0x25 answered and
    // made the port look powered when it was not.
    loadTiltLimits();

    GetHAL()->setExt5vEnable(true);

    // The 8Servo has to boot after its rail comes up. The demo gates on a user pressing a button,
    // so it never needed a figure; 150 ms is a modest allowance for a small MCU's power-on.
    vTaskDelay(pdMS_TO_TICKS(150));

    GetHAL()->initPortAI2c();

    // SCAN BOTH BUSES AND LOG WHAT IS ACTUALLY THERE, via the HAL's own i2cScan() -- the vendor
    // demo's working Port A pattern (app_launcher/view/panel_i2c_scan.cpp). Facts before theories:
    // the writes to 0x25 are reaching the wire and going unacknowledged, and this says whether
    // anything is at that address on either bus at this moment.
    {
        auto ext = GetHAL()->i2cScan(false);   // Port A, G53/G54
        auto in  = GetHAL()->i2cScan(true);    // internal, G31/G32
        ESP_LOGI(kTag, "I2C SCAN ext(PortA) %d device(s):", (int)ext.size());
        for (auto a : ext) ESP_LOGI(kTag, "   ext 0x%02x", a);
        ESP_LOGI(kTag, "I2C SCAN internal %d device(s):", (int)in.size());
        for (auto a : in)  ESP_LOGI(kTag, "   int 0x%02x", a);
    }

    i2c_master_bus_handle_t bus = bsp_ext_i2c_get_handle();
    if (!bus) {
        snprintf(g_status, sizeof(g_status), "pantilt: no ext i2c bus");
        ESP_LOGW(kTag, "%s -- eyes-only mode", g_status);
        return;
    }

    i2c_device_config_t cfg = {};
    cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    cfg.device_address  = kAddr;
    // 100 kHz, NOT 400. The Arduino firmware drove this unit through Wire at its default
    // 100 kHz and it worked for a long time; 400 kHz was my assumption and the mode-register
    // write failed with the device plainly answering a probe at 0x25.
    cfg.scl_speed_hz    = 100000;
    if (i2c_master_bus_add_device(bus, &cfg, &g_dev) != ESP_OK) {
        snprintf(g_status, sizeof(g_status), "pantilt: add device failed");
        ESP_LOGW(kTag, "%s -- eyes-only mode", g_status);
        return;
    }

    const bool m0 = write2(kModeReg + kChTilt, kModeServo);
    const bool m1 = write2(kModeReg + kChPan,  kModeServo);
    if (!m0 || !m1) {
        snprintf(g_status, sizeof(g_status), "pantilt: servo mode write failed");
        ESP_LOGW(kTag, "%s -- eyes-only mode", g_status);
        g_dev = nullptr;
        return;
    }

    // CENTRE AT BOOT, once, so the head's commanded position and its physical position agree before
    // anything slews. Without this the first motion is from wherever it was left, at an unknown
    // offset, which is the one case where a slew limit does not protect anyone.
    // Centre, but inside the mechanical limits: on a unit whose range does not straddle 90 the
    // "safe centre" is the midpoint of the travel, not the nominal one.
    const float tiltCentre = clampf(kCentreDeg, g_tiltMin, g_tiltMax);
    g_pan = g_panTgt = kCentreDeg;
    g_tilt = g_tiltTgt = tiltCentre;
    writePulse(kChTilt, degToUs(g_tilt, kTiltReverse));
    writePulse(kChPan,  degToUs(g_pan,  kPanReverse));
    g_present = true;

    // The front ToF shares Port A and the same EXT_5V rail, so it is brought up here rather than
    // racing the servo init for the bus. A failure is reported, not fatal: no ToF is not a reason
    // for the head to stop aiming.
    tofBegin();
    snprintf(g_status, sizeof(g_status), "pantilt: ready p%.0f t%.0f tilt[%.0f..%.0f]%s",
             g_pan, g_tilt, g_tiltMin, g_tiltMax, g_tiltLimitsKnown ? "" : " FALLBACK");
    ESP_LOGI(kTag, "8Servo at 0x25, tilt=ch%u pan=ch%u, centred", kChTilt, kChPan);
}

bool pantiltPresent() { return g_present; }

void pantiltTick(uint32_t nowMs, bool motionAllowed, bool haveTarget, float nx, float ny,
                 float confidence)
{
    if (!g_present) return;
    if (nowMs - g_lastMs < kPeriodMs) return;
    const float dt = (float)(nowMs - g_lastMs) / 1000.0f;
    g_lastMs = nowMs;

    // FROZEN, NOT CENTRED, when motion is not allowed. Returning to centre would itself be
    // autonomous motion, and the state in which we are told not to move is exactly the state in
    // which we should not decide to move somewhere nicer.
    if (!motionAllowed) {
        snprintf(g_status, sizeof(g_status), "pantilt: held p%.0f t%.0f tilt[%.0f..%.0f]%s",
                 g_pan, g_tilt, g_tiltMin, g_tiltMax, g_tiltLimitsKnown ? "" : "FB");
        return;
    }

    // ---- ARBITRATION, highest first: on-face touch, then RC, then the local tracker. ----
    //
    // Touch above RC because the person at the glass is aiming the screen at themselves. Both are
    // LEASES: ownership expires after the hold window, so letting go hands the head back. Latches
    // are specified in head-ownership.md and are not built yet.
    const bool touchOwns = (g_tpMs != 0 && (nowMs - g_tpMs) < kTouchHoldMs);
    if (touchOwns) {
        g_pan  = clampf(g_pan  + g_tpPan  * dt, kRcPanMin, kRcPanMax);
        g_tilt = clampf(g_tilt + g_tpTilt * dt, g_tiltMin, g_tiltMax);
        g_panTgt = g_pan; g_tiltTgt = g_tilt;
        writePulse(kChTilt, degToUs(g_tilt, kTiltReverse));
        writePulse(kChPan,  degToUs(g_pan,  kPanReverse));
        g_lastTiltUs = degToUs(g_tilt, kTiltReverse);
        g_lastPanUs  = degToUs(g_pan,  kPanReverse);
        snprintf(g_status, sizeof(g_status), "pantilt: TOUCH p%.0f t%.0f tilt[%.0f..%.0f]",
                 g_pan, g_tilt, g_tiltMin, g_tiltMax);
        return;
    }

    // ---- RC next: a human on a stick outranks the tracker. ----
    //
    // A LEASE, not a latch -- ownership expires kRcHoldMs after the last frame, so letting go of
    // the stick hands the head back to tracking. head-ownership.md specifies latches (and puts
    // on-face touch above RC); neither is built yet, and this is the increment that gives the
    // operator back what the Arduino firmware had.
    if (g_rcMs != 0 && (nowMs - g_rcMs) < kRcHoldMs) {
        g_pan  = clampf(g_pan  + g_rcPan  * dt, kRcPanMin,  kRcPanMax);
        g_tilt = clampf(g_tilt + g_rcTilt * dt, g_tiltMin, g_tiltMax);
        g_panTgt  = g_pan;          // no slew on top of a rate the operator is already shaping
        g_tiltTgt = g_tilt;
        writePulse(kChTilt, degToUs(g_tilt, kTiltReverse));
        writePulse(kChPan,  degToUs(g_pan,  kPanReverse));
        g_lastTiltUs = degToUs(g_tilt, kTiltReverse);
        g_lastPanUs  = degToUs(g_pan,  kPanReverse);
        snprintf(g_status, sizeof(g_status), "pantilt: RC p%.0f t%.0f tilt[%.0f..%.0f]%s",
                 g_pan, g_tilt, g_tiltMin, g_tiltMax, g_tiltLimitsKnown ? "" : "FB");
        return;
    }

    // THE EYE-ONLY BAND AND THE CONFIDENCE GATE. Inside either, the head has no target of its own
    // and returns to centre -- slowly, under the same slew limit as everything else.
    const float mag = sqrtf(nx * nx + ny * ny);
    if (g_tracking && haveTarget && confidence >= kHeadConfidence && mag > kEyeOnly) {
        // NO NEGATION HERE. The mechanism's reversal is applied ONCE, at the write, by
        // kPanReverse/kTiltReverse -- which is where the Arduino firmware puts it and why: "Servo
        // direction is a property of the MECHANISM ... so it is corrected once here, at the write,
        // rather than in each input. Otherwise the RC stick, the D-pad and AI tracking each need
        // their own sign and they will not agree for long."
        //
        // I negated nx here as well, which stacked a second inversion on top of that one and
        // mirrored the head: a target to the left turned it right. The RC path never had the bug
        // because it integrates its rate positively and lets the write-time reversal do the work.
        g_panTgt  = clampf(kCentreDeg + nx * kPanRangeDeg,  kCentreDeg - kPanRangeDeg,
                           kCentreDeg + kPanRangeDeg);
        // The conservative autonomous envelope, INTERSECTED with the hard mechanical limits --
        // whichever is tighter wins, so a narrow mechanism is never driven to a wide envelope.
        const float aLo = fmaxf(kCentreDeg - kTiltRangeDeg, g_tiltMin);
        const float aHi = fminf(kCentreDeg + kTiltRangeDeg, g_tiltMax);
        // TILT KEEPS ITS NEGATION; PAN DOES NOT. Determined by eye on the robot, in two passes:
        // flipping both axes together fixed left/right and broke up/down. So the two axes do NOT
        // share a sign, and treating them as symmetric was the mistake -- image y grows DOWNWARD
        // while x grows right, and the two servos are mounted differently, so each axis's mapping
        // is its own empirical fact. Do not "tidy" these into one rule.
        g_tiltTgt = clampf(kCentreDeg - ny * kTiltRangeDeg, aLo, aHi);
    } else if (g_tracking) {
        // Tracking, with nothing to track: return to centre, slowly, under the same slew limit.
        g_panTgt = g_tiltTgt = kCentreDeg;
    } else {
        // MANUAL: HOLD WHERE THE OPERATOR LEFT IT. This is the fix for the authority struggle --
        // the head used to re-centre the instant the 600 ms touch lease lapsed, so aiming it and
        // letting go made it crawl back, and with tracking live a detected face yanked it away as
        // well. The Arduino had no such fight because TrackingMode was a PERSISTENT mode, not a
        // lease: AI tracked, MANUAL did not, and MANUAL held its position indefinitely.
        //
        // That persistence is the point of the control. Emma: the utility of the on-face steering
        // "is mostly to move it to make whatever text is on the screen readable" -- a heading that
        // expires after half a second cannot do that job.
        g_panTgt  = g_pan;
        g_tiltTgt = g_tilt;
    }

    // SLEW LIMIT IN FIRMWARE. This is the line that makes a target jump a slow turn rather than a
    // lunge, and it is deliberately not something a caller can pass a rate into.
    const float step = kSlewDps * dt;
    if (fabsf(g_panTgt  - g_pan)  > step) g_pan  += (g_panTgt  > g_pan  ? step : -step);
    else                                  g_pan   = g_panTgt;
    if (fabsf(g_tiltTgt - g_tilt) > step) g_tilt += (g_tiltTgt > g_tilt ? step : -step);
    else                                  g_tilt  = g_tiltTgt;

    // THE ENVELOPE DEPENDS ON WHO IS DRIVING. kPanRangeDeg/kTiltRangeDeg are the deliberately
    // conservative AUTONOMOUS envelope; a human aiming the head by hand gets the mechanism's real
    // limits, which is what the touch and RC branches above already clamp to. Applying the narrow
    // envelope here as well silently undid them.
    if (g_tracking) {
        g_pan  = clampf(g_pan,  kCentreDeg - kPanRangeDeg,  kCentreDeg + kPanRangeDeg);
        g_tilt = clampf(g_tilt, kCentreDeg - kTiltRangeDeg, kCentreDeg + kTiltRangeDeg);
    } else {
        g_pan  = clampf(g_pan,  kRcPanMin,  kRcPanMax);
        g_tilt = clampf(g_tilt, g_tiltMin,  g_tiltMax);
    }

    // DEADBAND ON THE WRITE, not on the angle: the angle may creep as finely as it likes, but a
    // servo asked to hold a position a microsecond away from its current one just buzzes.
    const uint16_t panUs  = degToUs(g_pan,  kPanReverse);
    const uint16_t tiltUs = degToUs(g_tilt, kTiltReverse);
    const float    minUs  = kDeadbandDeg * (2000.0f / 180.0f);
    if (g_lastTiltUs < 0 || fabsf((float)tiltUs - (float)g_lastTiltUs) >= minUs) {
        if (writePulse(kChTilt, tiltUs)) g_lastTiltUs = tiltUs;
    }
    if (g_lastPanUs < 0 || fabsf((float)panUs - (float)g_lastPanUs) >= minUs) {
        if (writePulse(kChPan, panUs)) g_lastPanUs = panUs;
    }

    // REPORT THE OWNER, not a guess inferred from the target. "centring" was derived from
    // g_panTgt == kCentreDeg, which is also true of a hand-aimed head left near centre -- so the
    // one thing worth knowing, who is driving, was exactly what it could not distinguish.
    snprintf(g_status, sizeof(g_status), "pantilt: p%.0f t%.0f tilt[%.0f..%.0f]%s %s",
             g_pan, g_tilt, g_tiltMin, g_tiltMax, g_tiltLimitsKnown ? "" : "FB",
             !g_tracking ? "HOLD(manual)"
                         : (g_panTgt == kCentreDeg && g_tiltTgt == kCentreDeg) ? "centring"
                                                                               : "tracking");
}

// The VOR term: the fraction of the available range the head has taken on. The eyes subtract this
// so the two do not both aim at the target and overshoot it together. Same number used twice, which
// is the design note's point -- once the head moves, this is not an extra feature.
float pantiltHeadOffsetX()
{
    if (!g_present) return 0.0f;
    // Sign follows the target mapping above. With panTgt = centre + nx*range, the offset the
    // head has covered IS (pan - centre)/range in the same nx units -- so the eyes subtract the
    // right amount. When this was negated and the target was too, the two cancelled and the error
    // only showed up as a mirrored head.
    return (g_pan - kCentreDeg) / kPanRangeDeg;
}
float pantiltHeadOffsetY()
{
    if (!g_present) return 0.0f;
    // Negated, matching tilt's target mapping above -- not pan's. The two axes differ.
    return -(g_tilt - kCentreDeg) / kTiltRangeDeg;
}

void pantiltRcRate(float panDps, float tiltDps)
{
    g_rcPan  = panDps;
    g_rcTilt = tiltDps;
    g_rcMs   = (uint32_t)(esp_timer_get_time() / 1000);
}

// IDENTIFY THE FRONT ToF BEFORE WRITING A DRIVER FOR IT.
//
// 0x29 answers on Port A, but which part is fitted is a per-build fact and getting it wrong is
// silent: the ToF4M is a VL53L1X with SIXTEEN-BIT registers, the older unit a VL53L0X with 8-bit
// ones, and a driver for one simply fails to init against the other. The Arduino firmware made the
// same point and identified at run time; it could then lean on Pololu's Arduino libraries, which
// do not exist here, so the init sequence has to be written out and it is worth writing exactly
// one of them.
//
//   VL53L1X answers 0xEACC at 16-bit register 0x010F
//   VL53L0X answers 0xEE   at  8-bit register 0xC0
const char* pantiltTofId()
{
    static char out[96];
    i2c_master_bus_handle_t bus = bsp_ext_i2c_get_handle();
    if (!bus) { snprintf(out, sizeof(out), "tof: port A bus not up"); return out; }

    i2c_master_dev_handle_t dev = nullptr;
    i2c_device_config_t cfg = {};
    cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    cfg.device_address  = 0x29;
    cfg.scl_speed_hz    = 100000;
    if (i2c_master_bus_add_device(bus, &cfg, &dev) != ESP_OK || !dev) {
        snprintf(out, sizeof(out), "tof: add_device(0x29) failed");
        return out;
    }

    uint8_t  w16[2] = {0x01, 0x0F}, r16[2] = {0, 0};
    uint8_t  w8     = 0xC0,         r8     = 0;
    const esp_err_t e16 = i2c_master_transmit_receive(dev, w16, 2, r16, 2, 100);
    const esp_err_t e8  = i2c_master_transmit_receive(dev, &w8, 1, &r8, 1, 100);
    const uint16_t id16 = (uint16_t)((r16[0] << 8) | r16[1]);

    const char* part = (e16 == ESP_OK && id16 == 0xEACC) ? "VL53L1X (ToF4M)"
                     : (e8  == ESP_OK && r8   == 0xEE)   ? "VL53L0X"
                                                         : "UNKNOWN";
    snprintf(out, sizeof(out), "tof 0x29: %s | id16=0x%04x(%s) id8=0x%02x(%s)",
             part, id16, esp_err_to_name(e16), r8, esp_err_to_name(e8));
    i2c_master_bus_rm_device(dev);
    return out;
}

const char* pantiltI2cScan()
{
    static char buf[256];
    int n = snprintf(buf, sizeof(buf), "i2c ext(PortA G53/54):");
    auto ext = GetHAL()->i2cScan(false);
    if (ext.empty()) n += snprintf(buf + n, sizeof(buf) - n, " none");
    for (auto a : ext) n += snprintf(buf + n, sizeof(buf) - n, " 0x%02x", a);
    n += snprintf(buf + n, sizeof(buf) - n, " | internal(G31/32):");
    auto in = GetHAL()->i2cScan(true);
    if (in.empty()) n += snprintf(buf + n, sizeof(buf) - n, " none");
    for (auto a : in) n += snprintf(buf + n, sizeof(buf) - n, " 0x%02x", a);
    return buf;
}

// Aiming by hand TAKES OVER, and KEEPS it until MODE hands it back. Without this the release fell
// through to the tracking branch, whose target is kCentreDeg -- so the head crawled back to centre
// the moment the lease lapsed, which is the "fighting me" the operator reported. The Arduino had no
// such struggle because TrackingMode was a persistent mode rather than a lease.
void pantiltTouchRate(float panDps, float tiltDps)
{
    if (panDps != 0.0f || tiltDps != 0.0f) g_tracking = false;
    g_tpPan  = panDps;
    g_tpTilt = tiltDps;
    // 0,0 on release clears ownership immediately rather than waiting out the lease.
    g_tpMs   = (panDps == 0.0f && tiltDps == 0.0f) ? 0 : (uint32_t)(esp_timer_get_time() / 1000);
}

void pantiltSetTracking(bool on) { g_tracking = on; }
bool pantiltTracking()           { return g_tracking; }

const char* pantiltStatus() { return g_status; }

#else   // pan/tilt disabled by flag, or the desktop SDL build: no I2C, no head

void pantiltBegin() {}
bool pantiltPresent() { return false; }
void pantiltTick(uint32_t, bool, bool, float, float, float) {}
void pantiltRcRate(float, float) {}
void pantiltTouchRate(float, float) {}
void pantiltSetTracking(bool) {}
bool pantiltTracking() { return false; }
float pantiltHeadOffsetX() { return 0.0f; }
float pantiltHeadOffsetY() { return 0.0f; }
const char* pantiltStatus() { return "pantilt: off"; }
const char* pantiltI2cScan() { return "i2c scan: pantilt compiled out"; }
const char* pantiltTofId()   { return "tof: pantilt compiled out"; }

#endif  // ESP_PLATFORM
