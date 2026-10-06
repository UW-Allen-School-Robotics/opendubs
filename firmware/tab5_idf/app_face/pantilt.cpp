#include "pantilt.h"
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

// RC GETS THE FULL TRAVEL; autonomous motion does not.
//
// The conservative +/-30 / +/-18 envelope exists because autonomous motion near people should be
// modest and legible. An operator on a stick is a human deciding where to look, and per the
// authority boundary in head-ownership.md the head cannot realistically damage anything -- so
// clamping a human to a third of the travel is paternalistic rather than careful. The Arduino
// firmware gave the stick 0..180 and that is what it gets back.
constexpr float kRcPanMin  = 0.0f,   kRcPanMax  = 180.0f;
constexpr float kRcTiltMin = 0.0f,   kRcTiltMax = 180.0f;

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
    g_pan = g_tilt = g_panTgt = g_tiltTgt = kCentreDeg;
    writePulse(kChTilt, degToUs(g_tilt, kTiltReverse));
    writePulse(kChPan,  degToUs(g_pan,  kPanReverse));
    g_present = true;
    snprintf(g_status, sizeof(g_status), "pantilt: centred, ready");
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
        snprintf(g_status, sizeof(g_status), "pantilt: held (p%.0f t%.0f)", g_pan, g_tilt);
        return;
    }

    // ---- ARBITRATION. RC first: a human on a stick outranks the tracker. ----
    //
    // A LEASE, not a latch -- ownership expires kRcHoldMs after the last frame, so letting go of
    // the stick hands the head back to tracking. head-ownership.md specifies latches (and puts
    // on-face touch above RC); neither is built yet, and this is the increment that gives the
    // operator back what the Arduino firmware had.
    if (g_rcMs != 0 && (nowMs - g_rcMs) < kRcHoldMs) {
        g_pan  = clampf(g_pan  + g_rcPan  * dt, kRcPanMin,  kRcPanMax);
        g_tilt = clampf(g_tilt + g_rcTilt * dt, kRcTiltMin, kRcTiltMax);
        g_panTgt  = g_pan;          // no slew on top of a rate the operator is already shaping
        g_tiltTgt = g_tilt;
        writePulse(kChTilt, degToUs(g_tilt, kTiltReverse));
        writePulse(kChPan,  degToUs(g_pan,  kPanReverse));
        g_lastTiltUs = degToUs(g_tilt, kTiltReverse);
        g_lastPanUs  = degToUs(g_pan,  kPanReverse);
        snprintf(g_status, sizeof(g_status), "pantilt: RC p%.0f t%.0f", g_pan, g_tilt);
        return;
    }

    // THE EYE-ONLY BAND AND THE CONFIDENCE GATE. Inside either, the head has no target of its own
    // and returns to centre -- slowly, under the same slew limit as everything else.
    const float mag = sqrtf(nx * nx + ny * ny);
    if (haveTarget && confidence >= kHeadConfidence && mag > kEyeOnly) {
        g_panTgt  = clampf(kCentreDeg - nx * kPanRangeDeg,  kCentreDeg - kPanRangeDeg,
                           kCentreDeg + kPanRangeDeg);
        g_tiltTgt = clampf(kCentreDeg - ny * kTiltRangeDeg, kCentreDeg - kTiltRangeDeg,
                           kCentreDeg + kTiltRangeDeg);
    } else {
        g_panTgt = g_tiltTgt = kCentreDeg;
    }

    // SLEW LIMIT IN FIRMWARE. This is the line that makes a target jump a slow turn rather than a
    // lunge, and it is deliberately not something a caller can pass a rate into.
    const float step = kSlewDps * dt;
    if (fabsf(g_panTgt  - g_pan)  > step) g_pan  += (g_panTgt  > g_pan  ? step : -step);
    else                                  g_pan   = g_panTgt;
    if (fabsf(g_tiltTgt - g_tilt) > step) g_tilt += (g_tiltTgt > g_tilt ? step : -step);
    else                                  g_tilt  = g_tiltTgt;

    g_pan  = clampf(g_pan,  kCentreDeg - kPanRangeDeg,  kCentreDeg + kPanRangeDeg);
    g_tilt = clampf(g_tilt, kCentreDeg - kTiltRangeDeg, kCentreDeg + kTiltRangeDeg);

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

    snprintf(g_status, sizeof(g_status), "pantilt: p%.0f t%.0f%s", g_pan, g_tilt,
             (g_panTgt == kCentreDeg && g_tiltTgt == kCentreDeg) ? " (centring)" : " (tracking)");
}

// The VOR term: the fraction of the available range the head has taken on. The eyes subtract this
// so the two do not both aim at the target and overshoot it together. Same number used twice, which
// is the design note's point -- once the head moves, this is not an extra feature.
float pantiltHeadOffsetX()
{
    if (!g_present) return 0.0f;
    return -(g_pan - kCentreDeg) / kPanRangeDeg;
}
float pantiltHeadOffsetY()
{
    if (!g_present) return 0.0f;
    return -(g_tilt - kCentreDeg) / kTiltRangeDeg;
}

void pantiltRcRate(float panDps, float tiltDps)
{
    g_rcPan  = panDps;
    g_rcTilt = tiltDps;
    g_rcMs   = (uint32_t)(esp_timer_get_time() / 1000);
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

const char* pantiltStatus() { return g_status; }

#else   // pan/tilt disabled by flag, or the desktop SDL build: no I2C, no head

void pantiltBegin() {}
bool pantiltPresent() { return false; }
void pantiltTick(uint32_t, bool, bool, float, float, float) {}
void pantiltRcRate(float, float) {}
float pantiltHeadOffsetX() { return 0.0f; }
float pantiltHeadOffsetY() { return 0.0f; }
const char* pantiltStatus() { return "pantilt: off"; }
const char* pantiltI2cScan() { return "i2c scan: pantilt compiled out"; }

#endif  // ESP_PLATFORM
