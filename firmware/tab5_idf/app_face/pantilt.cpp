#include "pantilt.h"

#ifdef ESP_PLATFORM

#include <bsp/m5stack_tab5.h>
#include <driver/i2c_master.h>
#include <esp_log.h>
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

i2c_master_dev_handle_t g_dev = nullptr;
bool     g_present  = false;
float    g_pan      = kCentreDeg;         // current commanded angle, degrees
float    g_tilt     = kCentreDeg;
float    g_panTgt   = kCentreDeg;
float    g_tiltTgt  = kCentreDeg;
uint32_t g_lastMs   = 0;
int      g_lastPanUs = -1, g_lastTiltUs = -1;
char     g_status[64] = "pantilt: not started";

bool write2(uint8_t reg, uint8_t v)
{
    const uint8_t b[2] = {reg, v};
    return g_dev && i2c_master_transmit(g_dev, b, 2, 50) == ESP_OK;
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
    if (bsp_ext_i2c_init() != ESP_OK) {
        snprintf(g_status, sizeof(g_status), "pantilt: ext i2c init failed");
        ESP_LOGW(kTag, "%s -- eyes-only mode", g_status);
        return;
    }
    i2c_master_bus_handle_t bus = bsp_ext_i2c_get_handle();
    if (!bus) {
        snprintf(g_status, sizeof(g_status), "pantilt: no ext i2c bus");
        ESP_LOGW(kTag, "%s -- eyes-only mode", g_status);
        return;
    }

    // PROBE BEFORE CLAIMING THE HEAD EXISTS. The bench Tab5 has no 8Servo, and a module that
    // reports a head it cannot see is how a bench result gets mistaken for a robot one.
    if (i2c_master_probe(bus, kAddr, 100) != ESP_OK) {
        snprintf(g_status, sizeof(g_status), "pantilt: no 8Servo at 0x25 (eyes only)");
        ESP_LOGI(kTag, "%s", g_status);
        return;
    }

    i2c_device_config_t cfg = {};
    cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    cfg.device_address  = kAddr;
    cfg.scl_speed_hz    = 400000;
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

const char* pantiltStatus() { return g_status; }

#else   // desktop SDL build: no I2C, no head

void pantiltBegin() {}
bool pantiltPresent() { return false; }
void pantiltTick(uint32_t, bool, bool, float, float, float) {}
float pantiltHeadOffsetX() { return 0.0f; }
float pantiltHeadOffsetY() { return 0.0f; }
const char* pantiltStatus() { return "pantilt: desktop build"; }

#endif  // ESP_PLATFORM
