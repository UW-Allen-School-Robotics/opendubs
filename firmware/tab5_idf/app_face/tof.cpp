#include "tof.h"

#ifdef ESP_PLATFORM

#include <stdio.h>
#include <string.h>
#include "driver/i2c_master.h"
#include "esp_timer.h"
#include "bsp/m5stack_tab5.h"
#include "mooncake_log.h"

// VL53L0X on Grove Port A. This is a PORT OF POLOLU'S init SEQUENCE, not a reimplementation: the
// chip has no documented single-shot bring-up and ST's own API performs the same steps, so the
// register order below is load-bearing and should not be "tidied".
//
// Two deliberate omissions from that library, both to keep this to one readable file:
//
//  * NO TIMING-BUDGET MATH. Pololu's init reads the budget, rewrites SYSTEM_SEQUENCE_CONFIG, then
//    re-applies the budget -- which only PRESERVES the default. Skipping both leaves the default
//    (~33 ms), which is what we want anyway, and removes ~150 lines of macro-period encoding that
//    would exist only to compute a value we do not change.
//  * NO SINGLE-SHOT PATH. We range continuously and poll, because this runs on the same task that
//    drives the servos and must never block.

namespace {

constexpr uint8_t kAddr = 0x29;

// The registers this driver touches, named as ST names them.
enum : uint8_t {
    SYSRANGE_START                              = 0x00,
    SYSTEM_SEQUENCE_CONFIG                      = 0x01,
    SYSTEM_INTERRUPT_CONFIG_GPIO                = 0x0A,
    SYSTEM_INTERRUPT_CLEAR                      = 0x0B,
    RESULT_INTERRUPT_STATUS                     = 0x13,
    RESULT_RANGE_STATUS                         = 0x14,
    MSRC_CONFIG_CONTROL                         = 0x60,
    FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT = 0x44,
    GPIO_HV_MUX_ACTIVE_HIGH                     = 0x84,
    DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD         = 0x4E,
    DYNAMIC_SPAD_REF_EN_START_OFFSET            = 0x4F,
    GLOBAL_CONFIG_SPAD_ENABLES_REF_0            = 0xB0,
    GLOBAL_CONFIG_REF_EN_START_SELECT           = 0xB6,
    IDENTIFICATION_MODEL_ID                     = 0xC0,
};

i2c_master_dev_handle_t g_dev  = nullptr;
bool     g_ok        = false;
uint8_t  g_stopVar   = 0;
char     g_status[80] = "tof: not started";
// The LAST RAW reading and its status byte, kept even when rejected. "no sample" and "ranged, but
// the sensor says the result is not trustworthy" are completely different problems and they were
// indistinguishable: the filter dropped both on the floor and reported nothing.
uint16_t g_lastRaw    = 0xFFFF;
uint8_t  g_lastSts    = 0xFF;
uint32_t g_samples    = 0, g_rejects = 0;
const char* kTag = "tof";

bool wr(uint8_t reg, uint8_t val)
{
    const uint8_t b[2] = {reg, val};
    return g_dev && i2c_master_transmit(g_dev, b, 2, 50) == ESP_OK;
}
bool rd(uint8_t reg, uint8_t* val)
{
    return g_dev && i2c_master_transmit_receive(g_dev, &reg, 1, val, 1, 50) == ESP_OK;
}
bool rd16(uint8_t reg, uint16_t* val)
{
    uint8_t b[2] = {0, 0};
    if (!g_dev || i2c_master_transmit_receive(g_dev, &reg, 1, b, 2, 50) != ESP_OK) return false;
    *val = (uint16_t)((b[0] << 8) | b[1]);
    return true;
}
bool rdMulti(uint8_t reg, uint8_t* buf, size_t n)
{
    return g_dev && i2c_master_transmit_receive(g_dev, &reg, 1, buf, n, 50) == ESP_OK;
}
bool wrMulti(uint8_t reg, const uint8_t* buf, size_t n)
{
    uint8_t b[16];
    if (n + 1 > sizeof(b)) return false;
    b[0] = reg;
    memcpy(b + 1, buf, n);
    return g_dev && i2c_master_transmit(g_dev, b, n + 1, 50) == ESP_OK;
}
uint8_t rdOr0(uint8_t reg) { uint8_t v = 0; rd(reg, &v); return v; }

// Poll, with a deadline, instead of spinning. Pololu's library busy-waits here; on a robot a chip
// that never raises its interrupt would wedge the task that also writes the servos.
bool waitForInterrupt(uint32_t timeoutMs)
{
    const int64_t end = esp_timer_get_time() + (int64_t)timeoutMs * 1000;
    while (esp_timer_get_time() < end) {
        if ((rdOr0(RESULT_INTERRUPT_STATUS) & 0x07) != 0) return true;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return false;
}

bool singleRefCalibration(uint8_t vhvInitByte)
{
    if (!wr(SYSRANGE_START, 0x01 | vhvInitByte)) return false;
    if (!waitForInterrupt(200)) return false;
    wr(SYSTEM_INTERRUPT_CLEAR, 0x01);
    wr(SYSRANGE_START, 0x00);
    return true;
}

// ST's reference SPAD map read, which needs this exact register dance to expose the count and
// whether the reference SPADs are the aperture type.
bool spadInfo(uint8_t* count, bool* aperture)
{
    wr(0x80, 0x01); wr(0xFF, 0x01); wr(0x00, 0x00);
    wr(0xFF, 0x06); wr(0x83, rdOr0(0x83) | 0x04);
    wr(0xFF, 0x07); wr(0x81, 0x01);
    wr(0x80, 0x01); wr(0x94, 0x6b); wr(0x83, 0x00);

    const int64_t end = esp_timer_get_time() + 200000;
    while (rdOr0(0x83) == 0x00) {
        if (esp_timer_get_time() > end) return false;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    wr(0x83, 0x01);
    const uint8_t tmp = rdOr0(0x92);
    *count    = tmp & 0x7f;
    *aperture = ((tmp >> 7) & 0x01) != 0;

    wr(0x81, 0x00); wr(0xFF, 0x06); wr(0x83, rdOr0(0x83) & ~0x04);
    wr(0xFF, 0x01); wr(0x00, 0x01); wr(0xFF, 0x00); wr(0x80, 0x00);
    return true;
}

// ST's default tuning settings, verbatim. Undocumented by design -- these are the values the
// vendor's own API loads, and the part does not range usefully without them.
const uint8_t kTuning[][2] = {
    {0xFF,0x01},{0x00,0x00},{0xFF,0x00},{0x09,0x00},{0x10,0x00},{0x11,0x00},{0x24,0x01},
    {0x25,0xFF},{0x75,0x00},{0xFF,0x01},{0x4E,0x2C},{0x48,0x00},{0x30,0x20},{0xFF,0x00},
    {0x30,0x09},{0x54,0x00},{0x31,0x04},{0x32,0x03},{0x40,0x83},{0x46,0x25},{0x60,0x00},
    {0x27,0x00},{0x50,0x06},{0x51,0x00},{0x52,0x96},{0x56,0x08},{0x57,0x30},{0x61,0x00},
    {0x62,0x00},{0x64,0x00},{0x65,0x00},{0x66,0xA0},{0xFF,0x01},{0x22,0x32},{0x47,0x14},
    {0x49,0xFF},{0x4A,0x00},{0xFF,0x00},{0x7A,0x0A},{0x7B,0x00},{0x78,0x21},{0xFF,0x01},
    {0x23,0x34},{0x42,0x00},{0x44,0xFF},{0x45,0x26},{0x46,0x05},{0x40,0x40},{0x0E,0x06},
    {0x20,0x1A},{0x43,0x40},{0xFF,0x00},{0x34,0x03},{0x35,0x44},{0xFF,0x01},{0x31,0x04},
    {0x4B,0x09},{0x4C,0x05},{0x4D,0x04},{0xFF,0x00},{0x44,0x00},{0x45,0x20},{0x47,0x08},
    {0x48,0x28},{0x67,0x00},{0x70,0x04},{0x71,0x01},{0x72,0xFE},{0x76,0x00},{0x77,0x00},
    {0xFF,0x01},{0x0D,0x01},{0xFF,0x00},{0x80,0x01},{0x01,0xF8},{0xFF,0x01},{0x8E,0x01},
    {0x00,0x01},{0xFF,0x00},{0x80,0x00},
};

}  // namespace

void tofBegin()
{
    i2c_master_bus_handle_t bus = bsp_ext_i2c_get_handle();
    if (!bus) { snprintf(g_status, sizeof(g_status), "tof: port A bus not up"); return; }

    i2c_device_config_t cfg = {};
    cfg.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    cfg.device_address  = kAddr;
    cfg.scl_speed_hz    = 100000;
    if (i2c_master_bus_add_device(bus, &cfg, &g_dev) != ESP_OK || !g_dev) {
        snprintf(g_status, sizeof(g_status), "tof: add_device failed");
        return;
    }

    uint8_t id = 0;
    if (!rd(IDENTIFICATION_MODEL_ID, &id) || id != 0xEE) {
        snprintf(g_status, sizeof(g_status), "tof: no VL53L0X at 0x29 (id=0x%02x)", id);
        return;
    }

    // 2V8 mode, then standard-mode I2C.
    wr(0x89, rdOr0(0x89) | 0x01);
    wr(0x88, 0x00);

    // The "stop variable" has to be captured here and replayed on every ranging start.
    wr(0x80, 0x01); wr(0xFF, 0x01); wr(0x00, 0x00);
    g_stopVar = rdOr0(0x91);
    wr(0x00, 0x01); wr(0xFF, 0x00); wr(0x80, 0x00);

    // Drop the MSRC and pre-range signal-rate limit checks, and set the final-range limit to
    // 0.25 MCPS in 9.7 fixed point.
    wr(MSRC_CONFIG_CONTROL, rdOr0(MSRC_CONFIG_CONTROL) | 0x12);
    const uint8_t limit[2] = {0x00, 0x20};
    wrMulti(FINAL_RANGE_CONFIG_MIN_COUNT_RATE_RTN_LIMIT, limit, 2);
    wr(SYSTEM_SEQUENCE_CONFIG, 0xFF);

    uint8_t spads = 0; bool aperture = false;
    if (!spadInfo(&spads, &aperture)) {
        snprintf(g_status, sizeof(g_status), "tof: spad info timed out");
        return;
    }

    uint8_t map[6] = {0};
    rdMulti(GLOBAL_CONFIG_SPAD_ENABLES_REF_0, map, 6);
    wr(0xFF, 0x01);
    wr(DYNAMIC_SPAD_REF_EN_START_OFFSET, 0x00);
    wr(DYNAMIC_SPAD_NUM_REQUESTED_REF_SPAD, 0x2C);
    wr(0xFF, 0x00);
    wr(GLOBAL_CONFIG_REF_EN_START_SELECT, 0xB4);

    const uint8_t first = aperture ? 12 : 0;
    uint8_t enabled = 0;
    for (uint8_t i = 0; i < 48; ++i) {
        if (i < first || enabled == spads)            map[i / 8] &= ~(1 << (i % 8));
        else if ((map[i / 8] >> (i % 8)) & 0x01)      ++enabled;
    }
    wrMulti(GLOBAL_CONFIG_SPAD_ENABLES_REF_0, map, 6);

    for (auto& kv : kTuning) wr(kv[0], kv[1]);

    // Interrupt on "new sample ready", active low.
    wr(SYSTEM_INTERRUPT_CONFIG_GPIO, 0x04);
    wr(GPIO_HV_MUX_ACTIVE_HIGH, rdOr0(GPIO_HV_MUX_ACTIVE_HIGH) & ~0x10);
    wr(SYSTEM_INTERRUPT_CLEAR, 0x01);

    // Reference calibration: VHV first, then phase. Each needs its own sequence config, and the
    // full 0xE8 sequence goes back afterwards.
    wr(SYSTEM_SEQUENCE_CONFIG, 0x01);
    if (!singleRefCalibration(0x40)) {
        snprintf(g_status, sizeof(g_status), "tof: VHV calibration failed");
        return;
    }
    wr(SYSTEM_SEQUENCE_CONFIG, 0x02);
    if (!singleRefCalibration(0x00)) {
        snprintf(g_status, sizeof(g_status), "tof: phase calibration failed");
        return;
    }
    wr(SYSTEM_SEQUENCE_CONFIG, 0xE8);

    // Continuous, back-to-back. The stop variable is replayed here, as ST's API does.
    wr(0x80, 0x01); wr(0xFF, 0x01); wr(0x00, 0x00);
    wr(0x91, g_stopVar);
    wr(0x00, 0x01); wr(0xFF, 0x00); wr(0x80, 0x00);
    wr(SYSRANGE_START, 0x02);

    g_ok = true;
    snprintf(g_status, sizeof(g_status), "tof: VL53L0X ranging (%u spads%s)",
             (unsigned)spads, aperture ? ", aperture" : "");
    mclog::tagInfo(kTag, "{}", g_status);
}

bool tofReadMm(float* mm)
{
    if (!g_ok) return false;
    // NON-BLOCKING: if no sample is ready we say so rather than wait. This is called from the
    // 50 Hz loop that also writes the pan/tilt servos.
    if ((rdOr0(RESULT_INTERRUPT_STATUS) & 0x07) == 0) return false;
    uint16_t d = 0;
    const bool got = rd16(RESULT_RANGE_STATUS + 10, &d);
    g_lastSts = rdOr0(RESULT_RANGE_STATUS);
    wr(SYSTEM_INTERRUPT_CLEAR, 0x01);
    if (!got) return false;
    g_lastRaw = d;
    ++g_samples;
    // The sensor's own verdict, in bits 6:3. Anything but 11 means it does not vouch for the
    // number -- 8190/8191 are the out-of-range sentinels and 0 is not a distance either.
    const uint8_t rs = (uint8_t)((g_lastSts & 0x78) >> 3);
    if (rs != 11 || d == 8190 || d == 8191 || d == 0) { ++g_rejects; return false; }
    *mm = (float)d;
    return true;
}

const char* tofStatus() { return g_status; }

// What the panel should SAY. Three outcomes that must not be conflated: no driver, a driver that
// is ranging but gets nothing it trusts, and a real distance.
const char* tofReason()
{
    if (!g_ok)             return "no sensor";
    if (g_samples == 0)    return "no sample";
    if (g_lastRaw == 8190 || g_lastRaw == 8191) return "out of range";
    return "weak return";          // ranged, but the sensor does not vouch for it
}

// RAW REGISTERS, because "no sample ready" has several causes that look identical from outside:
// ranging never started, the sequence config is inconsistent with the default timeouts, or the
// interrupt is configured but never latches. CLAUDE.md's rule applies -- change one variable at a
// time, which means measuring before writing the timing-budget math that was deliberately omitted.
const char* tofRegs()
{
    static char out[160];
    if (!g_dev) { snprintf(out, sizeof(out), "tof: no device"); return out; }
    snprintf(out, sizeof(out),
             "raw=%u rs=%u samples=%lu rejects=%lu | start=0x%02x seq=0x%02x irqsts=0x%02x "
             "rngsts=0x%02x",
             (unsigned)g_lastRaw, (unsigned)((g_lastSts & 0x78) >> 3),
             (unsigned long)g_samples, (unsigned long)g_rejects,
             rdOr0(SYSRANGE_START), rdOr0(SYSTEM_SEQUENCE_CONFIG),
             rdOr0(RESULT_INTERRUPT_STATUS), rdOr0(RESULT_RANGE_STATUS));
    return out;
}

#else   // desktop

// The twin has no sensor, so it reports the SIMULATED distance when one is set and nothing when it
// is not -- the same two outcomes the head has, so the HMI readout is exercised either way.
#include "sim_inputs.h"

void tofBegin() {}
bool tofReadMm(float* mm) { return simTofMm(mm); }
const char* tofStatus() { return "tof: simulated on the twin"; }
const char* tofRegs()   { return "tof: no sensor on the desktop build"; }
const char* tofReason() { return "no sensor"; }

#endif
