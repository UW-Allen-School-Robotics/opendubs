// OpenDubs Tab5 — pan/tilt bring-up tool for two M5Stack Unit Roller485 motors.
//
// PURPOSE: find the motors, read their identity/state, and capture the TILT axis's
// mechanical limits BEFORE anything is driven.
//
// SAFETY: this tool issues NO motion commands. It can read, and it can put a motor into
// ROLLER_MODE_ENCODER (4) — a passive mode where the output is disabled and the unit only
// reports its encoder. That is how we find the tilt limits: motor passive, move the axis by
// HAND to each mechanical stop, and read the encoder at each end.
//
// Expected layout (per the build): TILT = 0x64, PAN = 0x65 (pan has a slip ring — unlimited).
//
// Buses (Tab5 pinmap):
//   Grove Port A I2C : SDA=G53 SCL=G54      → Roller I2C register interface (default addr 0x64)
//   RS485 (SIT3088)  : TX=G20 RX=G21 DE=G34 → Roller485 protocol, motor id 0..255
// "0x64" is ambiguous between the two (I2C address vs motor id 100), so we scan BOTH.
#include <M5Unified.h>
#include <Wire.h>
#include <math.h>
#include <esp_log.h>
#include "unit_roller485.hpp"

// ---- Roller I2C register map (from M5Unit-Roller/unit_roller_common.hpp) ----
static constexpr uint8_t REG_OUTPUT      = 0x00;  // motor enable
static constexpr uint8_t REG_MODE        = 0x01;  // 1=speed 2=position 3=current 4=encoder
static constexpr uint8_t REG_SYS_STATUS  = 0x0C;
static constexpr uint8_t REG_ERROR_CODE  = 0x0D;
static constexpr uint8_t REG_ID          = 0x10;
static constexpr uint8_t REG_VIN         = 0x34;
static constexpr uint8_t REG_TEMP        = 0x38;
static constexpr uint8_t REG_POS_REG     = 0x80;  // int32 target, 0.01 deg
static constexpr uint8_t REG_POS_MAX_CURRENT_REG = 0x20;  // int32, /100 -> mA
static constexpr uint8_t REG_POS_RB      = 0x90;  // int32
static constexpr uint8_t REG_POS_RB_TURN = 0x98;  // int32 signed turn count
static constexpr uint8_t REG_POS_RB_ANG  = 0x9C;  // uint16, 0.01 deg, 0..35999
static constexpr uint8_t REG_SPEED_REG   = 0x40;  // int32 speed setpoint
static constexpr uint8_t REG_SPEED_MAX_CURRENT_REG = 0x50;  // int32, /100 -> mA
static constexpr uint8_t REG_SPEED_READBACK_REG = 0x60;  // int32, /100
static constexpr uint8_t REG_CURRENT_RB  = 0xC0;  // int32
static constexpr uint8_t REG_FW_VER      = 0xFE;
static constexpr uint8_t REG_I2C_ADDR    = 0xFF;

static constexpr int PIN_SDA = 53, PIN_SCL = 54;
static constexpr int PIN_485_TX = 20, PIN_485_RX = 21, PIN_485_DE = 34;

static UnitRoller485 roller485;
static bool r485_ok = false;

// discovered devices
struct Found { bool i2c; uint8_t addr; uint8_t fw; uint8_t mode; };
static Found g_found[16];
static int   g_nFound = 0;

// ---------------- I2C helpers ----------------
// The ESP-IDF "i2c-ng" master driver wedges permanently (ESP_ERR_INVALID_STATE, 259) on the
// classic repeated-start pattern -- endTransmission(false) followed by requestFrom() -- under
// sustained polling. A STOP between the register pointer write and the read avoids that path
// entirely, and the Roller is fine with it. busRecover() unwedges the driver if it still trips.
// Report the IDLE state of the two lines. Both should read HIGH (pulled up). SDA stuck LOW
// means a slave is mid-transfer holding the line — the bus is dead until it is clocked out.
static void i2cLineState(bool *sda, bool *scl)
{
    Wire.end(); delay(2);
    pinMode(PIN_SDA, INPUT_PULLUP);
    pinMode(PIN_SCL, INPUT_PULLUP);
    delayMicroseconds(50);
    *sda = digitalRead(PIN_SDA); *scl = digitalRead(PIN_SCL);
}

// Classic I2C bus-unstick. An aborted read (e.g. the i2c-ng ESP_ERR_INVALID_STATE wedge) can
// leave a slave mid-byte holding SDA low; it will hold it forever because it is waiting for
// clocks that never come. Re-initialising the MASTER does not help — the slave must be clocked
// out. Pulse SCL until SDA releases, then issue a manual STOP to resync every device.
static int busRecover()
{
    bool sda, scl;
    i2cLineState(&sda, &scl);
    int pulses = 0;
    pinMode(PIN_SCL, OUTPUT_OPEN_DRAIN); digitalWrite(PIN_SCL, HIGH);
    pinMode(PIN_SDA, INPUT_PULLUP);
    delayMicroseconds(10);
    while (digitalRead(PIN_SDA) == LOW && pulses < 18) {      // clock the stuck slave out
        digitalWrite(PIN_SCL, LOW);  delayMicroseconds(10);
        digitalWrite(PIN_SCL, HIGH); delayMicroseconds(10);
        pulses++;
    }
    pinMode(PIN_SDA, OUTPUT_OPEN_DRAIN);                      // manual STOP: SDA low->high, SCL high
    digitalWrite(PIN_SDA, LOW);  delayMicroseconds(10);
    digitalWrite(PIN_SCL, HIGH); delayMicroseconds(10);
    digitalWrite(PIN_SDA, HIGH); delayMicroseconds(10);
    pinMode(PIN_SDA, INPUT_PULLUP); pinMode(PIN_SCL, INPUT_PULLUP);
    delay(5);
    Wire.begin(PIN_SDA, PIN_SCL, 400000UL);
    delay(5);
    return pulses;
}

// NEVER use endTransmission(false) here. That asks for a repeated start; if the device does not
// ACK we would return without ever issuing a STOP, leaving the i2c-ng driver dangling. It then
// answers ESP_ERR_INVALID_STATE (259) to EVERY later call — including plain address probes — so
// a single missed read kills the bus for the rest of the boot. A STOP-terminated pointer write
// always leaves the bus idle, and the Roller is happy with it.
static bool rdReg(uint8_t addr, uint8_t reg, uint8_t *buf, size_t n)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(true) != 0) return false;        // completed, bus left idle
    if (Wire.requestFrom((int)addr, (int)n) != (int)n) return false;
    for (size_t i = 0; i < n; ++i) buf[i] = Wire.read();
    return true;
}

static bool wrReg(uint8_t addr, uint8_t reg, const uint8_t *buf, size_t n)
{
    Wire.beginTransmission(addr);
    Wire.write(reg);
    for (size_t i = 0; i < n; ++i) Wire.write(buf[i]);
    return Wire.endTransmission() == 0;
}
static bool present(uint8_t addr)
{
    Wire.beginTransmission(addr);
    return Wire.endTransmission() == 0;
}
static int32_t  rdI32(uint8_t a, uint8_t r, bool *ok) { uint8_t b[4]; *ok = rdReg(a,r,b,4);
    return *ok ? (int32_t)((uint32_t)b[0] | (uint32_t)b[1]<<8 | (uint32_t)b[2]<<16 | (uint32_t)b[3]<<24) : 0; }
static uint16_t rdU16(uint8_t a, uint8_t r, bool *ok) { uint8_t b[2]; *ok = rdReg(a,r,b,2);
    return *ok ? (uint16_t)(b[0] | b[1]<<8) : 0; }
static int32_t rdI32x2(uint8_t a, uint8_t r, bool *ok)
{
    bool o1, o2;
    int32_t v1 = rdI32(a, r, &o1);
    int32_t v2 = rdI32(a, r, &o2);
    *ok = o1 && o2 && (v1 == v2);
    return v1;
}
static uint8_t  rdU8 (uint8_t a, uint8_t r, bool *ok) { uint8_t b; *ok = rdReg(a,r,&b,1); return *ok ? b : 0; }

// 0x70..0x77 is the PCA9548A / PaHub address block. Its ONLY register is the channel-enable
// mask, so ANY write sets it — including the register-pointer write a probe does. Probing one
// silently re-muxes the bus and makes downstream devices vanish. Never register-probe them;
// instead open every channel once so the whole tree is visible as one flat bus.
static bool isMuxAddr(uint8_t a) { return a >= 0x70 && a <= 0x77; }

static int muxOpenAll()
{
    int n = 0;
    for (uint8_t a = 0x70; a <= 0x77; ++a) {
        Wire.beginTransmission(a);
        if (Wire.endTransmission() != 0) continue;      // no mux here
        Wire.beginTransmission(a);
        Wire.write(0xFF);                               // enable ALL 8 channels
        if (Wire.endTransmission() == 0) n++;
    }
    if (n) { delay(5); }
    return n;
}

static bool wrI32(uint8_t a, uint8_t r, int32_t v)
{ return wrReg(a, r, (const uint8_t *)&v, 4); }

// ---- motion limits. Deliberately conservative: this axis is low-friction and back-driveable,
// ---- so homing needs almost no torque, and a low ceiling means a stall cannot hurt anything.
static constexpr int32_t HOME_CURRENT_RAW = 20000;   // /100 -> 200 mA
static constexpr int32_t HOME_SPEED_RAW   = 30000;   // /100 -> 300 (slow creep)
static constexpr uint32_t MOVE_TIMEOUT_MS = 15000;
static constexpr double  OVERTRAVEL_DEG   = 130.0;   // measured span is ~99 deg; abort past this
static constexpr double  STALL_DEG        = 0.15;    // movement below this = not moving
static constexpr int     STALL_SAMPLES    = 8;       // ~400 ms at 20 Hz

static double g_homeDeg = 0; static bool g_homed = false;

static bool readDeg(uint8_t a, double *deg)
{ bool ok; int32_t c = rdI32(a, REG_POS_RB, &ok); if (ok) *deg = c / 100.0; return ok; }
// retry wrapper — a single miss should not be reported as a huge bogus delta
static bool readDegR(uint8_t a, double *deg)
{ for (int i = 0; i < 5; ++i) { if (readDeg(a, deg)) return true; delay(20); } return false; }

// Always leaves the motor OFF and passive, whatever happened.
static void motorSafe(uint8_t a)
{
    uint8_t off = 0, enc = 4;
    wrReg(a, REG_OUTPUT, &off, 1);
    wrReg(a, REG_MODE,   &enc, 1);
}

// Drive at low current until the axis stops moving (mechanical stop) or a guard trips.
// dir: +1 = increasing counts (DOWN), -1 = decreasing counts (UP).
// Returns true if a stall (the stop) was reached.
static bool creepToStop(uint8_t a, int dir, double *endDeg, const char *what)
{
    double start = 0, last = 0;
    if (!readDeg(a, &start)) { Serial.println("[MOVE] abort: cannot read position"); return false; }
    last = start;

    uint8_t spd = 1, on = 1;
    wrReg(a, REG_MODE, &spd, 1);
    wrI32(a, REG_SPEED_MAX_CURRENT_REG, HOME_CURRENT_RAW);
    wrI32(a, REG_SPEED_REG, dir > 0 ? HOME_SPEED_RAW : -HOME_SPEED_RAW);
    wrReg(a, REG_OUTPUT, &on, 1);
    Serial.printf("[MOVE] %s: creeping dir=%+d at %d mA limit (any serial byte aborts)\n",
                  what, dir, (int)(HOME_CURRENT_RAW / 100));
    Serial.flush();

    const uint32_t t0 = millis();
    int still = 0; bool stalled = false;
    while (true) {
        delay(50);
        double now;
        if (!readDeg(a, &now)) { Serial.println("[MOVE] abort: read failed"); break; }
        if (fabs(now - last) < STALL_DEG) { if (++still >= STALL_SAMPLES) { stalled = true; break; } }
        else still = 0;
        last = now;
        if (fabs(now - start) > OVERTRAVEL_DEG) { Serial.printf("[MOVE] ABORT: overtravel %.1f deg\n", now - start); break; }
        if (millis() - t0 > MOVE_TIMEOUT_MS)    { Serial.println("[MOVE] ABORT: timeout"); break; }
        if (Serial.available())                 { Serial.println("[MOVE] ABORT: operator"); break; }
    }
    motorSafe(a);                                   // output OFF before anything else
    readDeg(a, &last);
    *endDeg = last;
    Serial.printf("[MOVE] %s -> %s at %.2f deg (moved %.2f)\n",
                  what, stalled ? "STOP REACHED" : "aborted", last, last - start);
    Serial.flush();
    return stalled;
}

static const char* modeName(uint8_t m)
{
    switch (m) { case 1: return "SPEED"; case 2: return "POSITION"; case 3: return "CURRENT";
                 case 4: return "ENCODER(passive)"; default: return "?"; }
}

// ---------------- scanning ----------------
static void scanI2C()
{
    Serial.flush();
    const int nmux = muxOpenAll();
    if (nmux) Serial.printf("[SCAN] opened all channels on %d I2C mux(es)\n", nmux);
    Serial.println("[SCAN] I2C bus (Grove Port A, G53/G54) 0x08..0x77");
    int hits = 0;
    for (uint8_t a = 0x08; a <= 0x77; ++a) {
        if (!present(a)) continue;
        hits++;
        if (isMuxAddr(a)) { Serial.printf("  0x%02X  I2C MUX (PCA9548A) - not probed\n", a); continue; }
        bool ok1, ok2, ok3;
        uint8_t fw   = rdU8(a, REG_FW_VER, &ok1);
        uint8_t mode = rdU8(a, REG_MODE,   &ok2);
        uint8_t id   = rdU8(a, REG_ID,     &ok3);
        const bool looksRoller = ok1 && ok2;
        Serial.printf("  0x%02X  %s", a, looksRoller ? "ROLLER" : "(other device)");
        if (looksRoller) {
            Serial.printf("  fw=%u mode=%u(%s) id=%u", fw, mode, modeName(mode), id);
            if (g_nFound < 16) g_found[g_nFound++] = {true, a, fw, mode};
        }
        Serial.println(); Serial.flush();
    }
    if (!hits) Serial.println("  (nothing on I2C)");
    Serial.flush();
}

static void scan485(int lo, int hi)
{
    if (!r485_ok) { Serial.println("[SCAN] RS485 not initialised"); return; }
    Serial.printf("[SCAN] RS485 (G20/G21, DE=G34) @115200 — ids %d..%d (~0.5s each)\n", lo, hi);
    Serial.flush();
    int hits = 0;
    for (int id = lo; id <= hi; ++id) {
        uint8_t ver = 0;
        if (roller485.getFirmwareVersion((uint8_t)id, &ver) == 1) {
            hits++;
            int8_t m = roller485.getMode((uint8_t)id);
            Serial.printf("  id=%d (0x%02X)  ROLLER485  fw=%u mode=%d(%s)\n",
                          id, id, ver, m, modeName((uint8_t)m));
            if (g_nFound < 16) g_found[g_nFound++] = {false, (uint8_t)id, ver, (uint8_t)m};
            Serial.flush();
        }
        Serial.print('.'); Serial.flush();
    }
    Serial.println();
    if (!hits) Serial.println("  (nothing on RS485 in that range)");
    Serial.flush();
}

static void scanAll()
{
    g_nFound = 0;
    Serial.println("\n================ SCAN ================");
    scanI2C();
    Serial.printf("[SCAN] %d roller(s) found\n", g_nFound);
    Serial.println("======================================\n");
    Serial.flush();
}

// ---------------- detail / monitor ----------------
static void infoI2C(uint8_t a)
{
    bool ok;
    Serial.printf("--- I2C 0x%02X ---\n", a);
    Serial.printf("  fw=%u  id=%u\n",            rdU8 (a, REG_FW_VER,  &ok), rdU8(a, REG_ID, &ok));
    uint8_t m = rdU8(a, REG_MODE, &ok);
    Serial.printf("  mode=%u (%s)  output=%u\n", m, modeName(m), rdU8(a, REG_OUTPUT, &ok));
    Serial.printf("  status=%u  error=%u\n",     rdU8 (a, REG_SYS_STATUS, &ok), rdU8(a, REG_ERROR_CODE, &ok));
    Serial.printf("  vin_raw=%ld  temp_raw=%ld\n", (long)rdI32(a, REG_VIN, &ok), (long)rdI32(a, REG_TEMP, &ok));
    bool okc, okt, oka;
    int32_t cnt  = rdI32(a, REG_POS_RB,      &okc);   // accumulated, RELATIVE to power-on
    int32_t turn = rdI32(a, REG_POS_RB_TURN, &okt);   // signed turn count
    uint16_t ang = rdU16(a, REG_POS_RB_ANG,  &oka);   // ABSOLUTE within-turn, 0.01deg, 0..35999
    Serial.printf("  pos   0x90 = %ld counts = %.2f deg   (%s)\n",
                  (long)cnt, cnt / 100.0, okc ? "ok" : "READ FAIL");
    Serial.printf("  turn  0x98 = %ld               (%s)\n", (long)turn, okt ? "ok" : "READ FAIL");
    Serial.printf("  angle 0x9C = %u = %.2f deg  ABSOLUTE within-turn (%s)\n",
                  ang, ang / 100.0, oka ? "ok" : "READ FAIL");
    Serial.printf("  current_raw=%ld\n", (long)rdI32(a, REG_CURRENT_RB, &ok));
}

// Passive encoder mode — output disabled, unit just reports its encoder. Safe.
static void setEncoderMode(bool isI2C, uint8_t a)
{
    if (isI2C) {
        uint8_t off = 0, mode = 4;                      // ROLLER_MODE_ENCODER
        bool o = wrReg(a, REG_OUTPUT, &off, 1);         // disable output FIRST
        bool m = wrReg(a, REG_MODE,   &mode, 1);
        Serial.printf("[ENC] I2C 0x%02X output=off(%s) mode=ENCODER(%s) — axis is now free to move by hand\n",
                      a, o ? "ok" : "FAIL", m ? "ok" : "FAIL");
    } else {
        int8_t o = roller485.setOutput(a, false);
        int8_t m = roller485.setMode(a, ROLLER_MODE_ENCODER);
        Serial.printf("[ENC] 485 id=%u output=off(%d) mode=ENCODER(%d) — axis is now free to move by hand\n",
                      a, o, m);
    }
}

// Stream position at 10 Hz + track min/max — this is the limit-capture tool.
static bool     mon_on = false, mon_i2c = true;
static uint8_t  mon_addr = 0x64;
static uint32_t mon_last = 0;
static double   mon_min = 1e9, mon_max = -1e9;
static constexpr double TILT_SPAN_DEG = 99.0;   // measured: -159 (down) .. -258 (up)
static double   g_lastDeg = 0;      // last good reading, for the on-screen readout
static bool     g_haveRead = false; // false until a read succeeds
static uint32_t g_readFail = 0;

// --- dual-axis live tracking: [0]=TILT 0x64, [1]=PAN 0x65 ---
struct Axis { uint8_t addr; const char *name; double deg, mn, mx; bool ok; uint32_t fails; };
static Axis g_ax[2] = { {0x64, "TILT", 0, 1e9, -1e9, false, 0},
                        {0x65, "PAN ", 0, 1e9, -1e9, false, 0} };

static void axisSample()
{
    for (int i = 0; i < 2; ++i) {
        bool o; int32_t c = rdI32(g_ax[i].addr, REG_POS_RB, &o);
        if (!o) { if (++g_ax[i].fails > 8) g_ax[i].ok = false; continue; }
        g_ax[i].fails = 0;
        const double d = c / 100.0;
        if (!(fabs(d) < 3600.0)) continue;              // reject corrupt reads
        g_ax[i].deg = d; g_ax[i].ok = true;
        if (d < g_ax[i].mn) g_ax[i].mn = d;
        if (d > g_ax[i].mx) g_ax[i].mx = d;
    }
}

static void monitorTick()
{
    if (!mon_addr || millis() - mon_last < 100) return;   // 10 Hz
    { bool known = false;                       // only poll an address the scan has actually seen
      for (int i = 0; i < g_nFound; ++i) if (g_found[i].addr == mon_addr) known = true;
      if (!known) { mon_last = millis(); g_haveRead = false; return; } }
    mon_last = millis();
    double deg; bool ok = false;
    if (mon_i2c) {
        bool o1;
        int32_t cnt = rdI32(mon_addr, REG_POS_RB, &o1);     // 100 counts/deg; band filter below rejects corruption
        ok = o1; deg = cnt / 100.0;
    } else {
        int32_t turn; uint16_t ang;
        ok = (roller485.getPosReadbackTurnAngle(mon_addr, &turn, &ang) == 1);
        deg = turn * 360.0 + ang / 100.0;
    }
    if (!ok) { g_readFail++; if (g_readFail > 8) g_haveRead = false;
               if (mon_on) { Serial.println("[MON] read failed"); Serial.flush(); } return; }
    g_readFail = 0;
    // Peak tracking runs ALWAYS once an axis is watched — so the limits can be captured with no
    // host attached. Printing is separate (and only while streaming), because a 10 Hz print into
    // a CDC nobody is draining would stall the loop on the TX timeout.
    // Corrupt reads land absurdly far out (e.g. 0x06000000 -> ~1.0e6 deg), while any real
    // pan/tilt travel is a few turns at most. An ABSOLUTE band rejects the garbage without
    // ever clipping legitimate hand motion (a delta gate would, since sample rate varies).
    static uint32_t rejected = 0;
    if (!(fabs(deg) < 3600.0)) {              // +/- 10 turns; also catches NaN
        rejected++;
        if (mon_on) { Serial.printf("[MON] rejected out-of-band %.1f deg (%lu total)\n",
                                    deg, (unsigned long)rejected); Serial.flush(); }
        return;
    }
    g_lastDeg = deg; g_haveRead = true;
    if (deg < mon_min) mon_min = deg;
    if (deg > mon_max) mon_max = deg;
    if (mon_on) {
        Serial.printf("[MON] %9.2f deg (%8ld cnt)   min=%9.2f  max=%9.2f  span=%8.2f deg\n",
                      deg, (long)lround(deg * 100.0), mon_min, mon_max, mon_max - mon_min);
        Serial.flush();
    }
}

static void reportPeak()
{
    if (mon_min > mon_max) { Serial.println("[PEAK] nothing captured yet"); Serial.flush(); return; }
    Serial.printf("[PEAK] watching %s 0x%02X\n", mon_i2c ? "i2c" : "485", mon_addr);
    Serial.printf("[PEAK]   min  %9.2f deg  (%8ld counts)\n", mon_min, (long)lround(mon_min * 100.0));
    Serial.printf("[PEAK]   max  %9.2f deg  (%8ld counts)\n", mon_max, (long)lround(mon_max * 100.0));
    Serial.printf("[PEAK]   span %9.2f deg  (%8ld counts)\n",
                  mon_max - mon_min, (long)lround((mon_max - mon_min) * 100.0));
    Serial.flush();
}

// ---------------- serial command console ----------------
static void help()
{
    Serial.println(
      "\nCommands (NO motion is ever commanded by this tool):\n"
      "  scan                    rescan I2C (fast)\n"
      "  scan485 [lo hi]         sweep RS485 ids (default 96..110 = 0x60..0x6E); ~0.5s per id\n"
      "  info <i2c|485> <addr>   identity/mode/position of one motor  (addr hex e.g. 64)\n"
      "  enc  <i2c|485> <addr>   output OFF + ENCODER mode -> axis free to move BY HAND\n"
      "  watch <i2c|485> <addr>  track min/max in background (no stream) — use for limit capture\n"
      "  peak                    report captured min/max/span\n"
      "  jog <+1|-1>             ONE 0.6s bounded move at 200mA - learn direction sign\n"
      "  home [margin_deg]       creep to the DOWN stop, zero there, report soft limits\n"
      "  drivetest <deg> <mA>    POSITION-mode move at a current ceiling (brownout-guarded)\n"
      "  sweep <ah> <lo> <hi> <cycles> <deg/s> <mA>   ramped smooth sweep, bounded\n"
      "  mon  <i2c|485> <addr>   same as watch, but also streams at 10 Hz\n"
      "  reset                   clear the monitor's min/max\n"
      "  stop                    stop streaming\n"
      "  scanfull                sweep 0x00..0x7F incl. reserved addresses\n"
      "  scanpins <sda> <scl>    re-init I2C on other pins and sweep (e.g. 31 32)\n"
      "  pullup                  test for EXTERNAL pullups on Port A (is anything wired?)\n"
      "  lines                   report idle SDA/SCL state\n"
      "  unstick                 clock a stuck slave off the bus, then rescan\n"
      "  help\n");
}

static void handle(char *line)
{
    char what[8] = {0}, bus[8] = {0}; unsigned addr = 0;
    if (!strcmp(line, "help"))  { help(); return; }
    if (!strcmp(line, "scan"))  { scanAll(); return; }

    // Full 0x00..0x7F sweep INCLUDING the reserved ranges. A corrupted write during the earlier
    // bus trouble could have landed on I2C_ADDRESS_REG (0xFF) and moved a device to an address
    // a normal 0x08..0x77 scan would never probe.
    if (!strcmp(line, "scanfull")) {
        Serial.println("[SCAN] FULL 0x00..0x7F (incl. reserved)");
        int hits = 0;
        muxOpenAll();
        for (uint8_t a = 0x00; a <= 0x7F; ++a) {
            if (!present(a)) continue;
            hits++;
            if (isMuxAddr(a)) { Serial.printf("  0x%02X  ACK  I2C MUX - not probed\n", a); continue; }
            bool o1, o2;
            uint8_t fw = rdU8(a, REG_FW_VER, &o1), md = rdU8(a, REG_MODE, &o2);
            Serial.printf("  0x%02X  ACK  %s", a, (o1 && o2) ? "ROLLER" : "(unknown)");
            if (o1 && o2) Serial.printf("  fw=%u mode=%u(%s)", fw, md, modeName(md));
            Serial.println(); Serial.flush();
        }
        Serial.printf("[SCAN] %d address(es) ACKed\n", hits); Serial.flush();
        return;
    }

    // Re-init Wire on an arbitrary pin pair and sweep — in case the motors are not on the pins
    // I assumed. Tab5: Grove Port A = 53/54, M5-Bus/internal = 31/32.
    { int sda = -1, scl = -1;
      if (sscanf(line, "scanpins %d %d", &sda, &scl) == 2) {
          Serial.printf("[SCAN] re-init Wire on SDA=G%d SCL=G%d\n", sda, scl);
          Wire.end(); delay(20);
          const bool ok = Wire.begin(sda, scl, 100000UL);
          delay(20);
          Serial.printf("[SCAN] Wire.begin = %s\n", ok ? "OK" : "FAILED"); Serial.flush();
          int hits = 0;
          for (uint8_t a = 0x00; a <= 0x7F; ++a) {
              if (!present(a)) continue;
              hits++; Serial.printf("  0x%02X ACK\n", a); Serial.flush();
          }
          Serial.printf("[SCAN] %d address(es) on G%d/G%d\n", hits, sda, scl);
          Wire.end(); delay(20);
          Wire.begin(PIN_SDA, PIN_SCL, 400000UL);      // restore Port A
          Serial.flush();
          return;
      } }
    { int lo=0, hi=127;
      if (!strcmp(line, "scan485")) { scan485(96, 110); return; }          // 0x60..0x6E covers 0x64/0x65
      if (sscanf(line, "scan485 %d %d", &lo, &hi) == 2) { scan485(lo, hi); return; } }
    if (!strcmp(line, "stop"))  { mon_on = false; Serial.println("[MON] streaming off (peak tracking continues)"); return; }
    if (!strcmp(line, "reset")) { mon_min = 1e9; mon_max = -1e9; Serial.println("[MON] min/max cleared"); return; }
    // sweep <addrhex> <lo> <hi> <cycles> <deg_per_s> <mA>
    // Ramped position sweep: the target is interpolated at 50 Hz so motion is smooth and its
    // speed is set by us, rather than letting the position loop slam between endpoints.
    { unsigned sa = 0x65; double lo = 0, hi = 0, rate = 25; int cyc = 3, ma = 400;
      if (sscanf(line, "sweep %x %lf %lf %d %lf %d", &sa, &lo, &hi, &cyc, &rate, &ma) == 6) {
          if (hi < lo) { double t = lo; lo = hi; hi = t; }
          if (ma < 50 || ma > 1200 || rate <= 0 || rate > 180 || cyc < 1 || cyc > 20) {
              Serial.println("[SWEEP] bad args"); return; }
          double cur = 0;
          if (!readDegR((uint8_t)sa, &cur)) { Serial.println("[SWEEP] abort: no position"); return; }
          Serial.printf("[SWEEP] 0x%02X  %.1f .. %.1f deg  x%d  @%.0f deg/s  %dmA\n",
                        sa, lo, hi, cyc, rate, ma);
          Serial.flush();

          uint8_t pmode = 2, on = 1;
          wrReg((uint8_t)sa, REG_MODE, &pmode, 1);
          wrI32((uint8_t)sa, REG_POS_MAX_CURRENT_REG, (int32_t)ma * 100);
          wrI32((uint8_t)sa, REG_POS_REG, (int32_t)lround(cur * 100.0));
          wrReg((uint8_t)sa, REG_OUTPUT, &on, 1);

          const double step = rate / 50.0;          // deg per 20 ms tick
          double tgt = cur; const char *why = "done";
          double imax = 0; int32_t vmin = 99999;
          // leg 0: current -> lo, then alternate hi/lo for the requested cycles
          for (int leg = 0; leg <= cyc * 2 && why == (const char*)"done"; ++leg) {
              const double dest = (leg == 0) ? lo : ((leg % 2) ? hi : lo);
              // Leash the setpoint to the ACTUAL position. Advancing the target on a timer
              // alone lets it run away from an under-tuned loop, so the axis trails and never
              // arrives. Clamping the lead to MAX_LEAD keeps the setpoint pulling the motor
              // rather than sprinting off without it.
              static constexpr double MAX_LEAD = 4.0;
              uint32_t legStart = millis();
              while (fabs(tgt - dest) > step) {
                  bool o1, o2; double act = 0;
                  int32_t cu = rdI32((uint8_t)sa, REG_CURRENT_RB, &o1);
                  int32_t vi = rdI32((uint8_t)sa, REG_VIN, &o2);
                  const bool haveAct = readDeg((uint8_t)sa, &act);
                  tgt += (dest > tgt) ? step : -step;
                  if (haveAct) {                       // never lead the actual by more than MAX_LEAD
                      if (tgt > act + MAX_LEAD) tgt = act + MAX_LEAD;
                      if (tgt < act - MAX_LEAD) tgt = act - MAX_LEAD;
                  }
                  if (tgt < lo - 0.01) tgt = lo;
                  if (tgt > hi + 0.01) tgt = hi;     // never command outside the safe window
                  wrI32((uint8_t)sa, REG_POS_REG, (int32_t)lround(tgt * 100.0));
                  delay(20);
                  if (haveAct && fabs(act - dest) < 1.5) break;      // arrived
                  if (millis() - legStart > 12000) { why = "ABORT: leg timeout"; break; }
                  if (o1 && fabs(cu / 100.0) > imax) imax = fabs(cu / 100.0);
                  if (o2 && vi < vmin) vmin = vi;
                  if (act < lo - 10.0 || act > hi + 10.0) { why = "ABORT: outside safe window"; break; }
                  if (Serial.available())              { why = "ABORT: operator"; break; }
              }
              if (why != (const char*)"done") break;
              double act = 0; readDeg((uint8_t)sa, &act);
              Serial.printf("   leg %d -> %.1f  (actual %.2f)  Imax %.0fmA  vin_min %.2fV\n",
                            leg, dest, act, imax, vmin / 100.0);
              Serial.flush();
              delay(250);                              // settle at each end
          }
          motorSafe((uint8_t)sa);
          double fin = 0; readDegR((uint8_t)sa, &fin);
          Serial.printf("[SWEEP] %s | final %.2f | peak current %.0f mA | vin min %.2fV\n",
                        why, fin, imax, vmin / 100.0);
          Serial.flush(); return; } }

    if (!strcmp(line, "peak")) {
        for (int i = 0; i < 2; ++i) {
            if (g_ax[i].mn > g_ax[i].mx) { Serial.printf("[PEAK] %s 0x%02X: nothing captured\n",
                                                          g_ax[i].name, g_ax[i].addr); continue; }
            Serial.printf("[PEAK] %s 0x%02X  min %9.2f  max %9.2f  span %8.2f deg   (counts %ld .. %ld)\n",
                          g_ax[i].name, g_ax[i].addr, g_ax[i].mn, g_ax[i].mx, g_ax[i].mx - g_ax[i].mn,
                          (long)lround(g_ax[i].mn * 100), (long)lround(g_ax[i].mx * 100));
        }
        Serial.flush(); return; }
    if (!strcmp(line, "clear")) { for (int i = 0; i < 2; ++i) { g_ax[i].mn = 1e9; g_ax[i].mx = -1e9; }
        Serial.println("[PEAK] cleared"); return; }

    // drivetest <target_deg> <mA> — POSITION mode at a given current ceiling. Answers the only
    // question that matters right now: can this motor move the tilt on the 5V Grove rail?
    // Aborts on rail brownout, because collapsing the bus costs us the whole I2C segment.
    { double tgt = 0; int ma = 0;
      unsigned addr = 0x64;
      int nf = sscanf(line, "drivetest %x %lf %d", &addr, &tgt, &ma);
      if (nf != 3) { addr = 0x64; nf = sscanf(line, "drivetest %lf %d", &tgt, &ma) == 2 ? 3 : 0; }
      if (nf == 3) {
          if (ma < 50 || ma > 1200) { Serial.println("[DRIVE] mA out of range 50..1200"); return; }
          double start = 0;
          if (!readDegR((uint8_t)addr, &start)) { Serial.println("[DRIVE] abort: cannot read position"); return; }
          bool vok; int32_t vin0 = rdI32((uint8_t)addr, REG_VIN, &vok);
          Serial.printf("[DRIVE] 0x%02X: %.2f -> %.2f deg, ceiling %d mA, vin %.2fV\n",
                        addr, start, tgt, ma, vin0 / 100.0);
          Serial.flush();

          uint8_t pos = 2, on = 1;
          wrReg((uint8_t)addr, REG_MODE, &pos, 1);
          wrI32((uint8_t)addr, REG_POS_MAX_CURRENT_REG, (int32_t)ma * 100);
          wrI32((uint8_t)addr, REG_POS_REG, (int32_t)lround(tgt * 100.0));
          wrReg((uint8_t)addr, REG_OUTPUT, &on, 1);

          double best = start, now = start; int32_t vmin = vin0;
          const uint32_t t0 = millis(); const char *why = "timeout";
          while (millis() - t0 < 4000) {
              delay(120);
              bool o1, o2, o3;
              int32_t cur = rdI32((uint8_t)addr, REG_CURRENT_RB, &o1);
              int32_t vin = rdI32((uint8_t)addr, REG_VIN, &o2);
              uint8_t err = rdU8((uint8_t)addr, REG_ERROR_CODE, &o3);
              readDeg((uint8_t)addr, &now);
              if (o2 && vin < vmin) vmin = vin;
              if (fabs(now - start) > fabs(best - start)) best = now;
              Serial.printf("   t=%4lums  pos=%8.2f  I=%7.1fmA  vin=%.2fV  err=%u\n",
                            (unsigned long)(millis() - t0), now, cur / 100.0, vin / 100.0, err);
              Serial.flush();
              if (o2 && vin < 390) { why = "RAIL BROWNOUT (<3.9V)"; break; }
              if (fabs(now - tgt) < 1.0) { why = "TARGET REACHED"; break; }
              if (Serial.available()) { why = "operator abort"; break; }
          }
          motorSafe((uint8_t)addr);
          readDegR((uint8_t)addr, &now);
          Serial.printf("[DRIVE] %s | moved %.2f deg (max excursion %.2f) | vin min %.2fV\n",
                        why, now - start, best - start, vmin / 100.0);
          Serial.flush(); return; } }

    // jog <dir> — one short bounded move, to learn which sign is DOWN before committing.
    { int d = 0;
      if (sscanf(line, "jog %d", &d) == 1 && (d == 1 || d == -1)) {
          double before = 0, after = 0; readDeg(0x64, &before);
          uint8_t spd = 1, on = 1, off = 0;
          wrReg(0x64, REG_MODE, &spd, 1);
          wrI32(0x64, REG_SPEED_MAX_CURRENT_REG, HOME_CURRENT_RAW);
          wrI32(0x64, REG_SPEED_REG, d > 0 ? HOME_SPEED_RAW : -HOME_SPEED_RAW);
          wrReg(0x64, REG_OUTPUT, &on, 1);
          // sample DURING the burst: is the unit actually enabled, commanded, and drawing?
          for (int k = 0; k < 6; ++k) {
              delay(100);
              bool o1,o2,o3,o4,o5;
              uint8_t outp = rdU8(0x64, REG_OUTPUT, &o1);
              uint8_t md   = rdU8(0x64, REG_MODE,   &o2);
              uint8_t err  = rdU8((uint8_t)addr, REG_ERROR_CODE, &o3);
              int32_t sp   = rdI32(0x64, REG_SPEED_READBACK_REG, &o4);
              int32_t cur  = rdI32((uint8_t)addr, REG_CURRENT_RB, &o5);
              double  now  = 0; readDeg(0x64, &now);
              Serial.printf("   [t=%3dms] out=%u mode=%u err=%u  speed_rb=%.2f  I=%.1fmA  pos=%.2f\n",
                            (k+1)*100, outp, md, err, sp/100.0, cur/100.0, now);
              Serial.flush();
          }
          wrReg(0x64, REG_OUTPUT, &off, 1);
          Serial.printf("[JOG] dir=%+d  %.2f -> %.2f deg  (delta %+.2f) %s\n",
                        d, before, after, after - before,
                        fabs(after-before) < 0.2 ? " -- NO MOVEMENT (check supply voltage)" : "");
          Serial.flush(); return; } }

    // home — creep to the DOWN stop, zero there, and report the usable soft range.
    { double margin = 3.0;
      if (!strcmp(line, "home") || sscanf(line, "home %lf", &margin) == 1) {
          double downStop = 0;
          if (!creepToStop(0x64, +1, &downStop, "DOWN stop")) {
              Serial.println("[HOME] failed - not homed"); Serial.flush(); return; }
          g_homeDeg = downStop; g_homed = true;
          Serial.printf("\n[HOME] home set at %.2f deg (physical DOWN)\n", g_homeDeg);
          Serial.printf("[HOME] tilt convention: UP is POSITIVE, 0 = DOWN stop\n");
          Serial.printf("[HOME] travel 0.00 .. %.2f deg\n", TILT_SPAN_DEG);
          Serial.printf("[HOME] soft limits with %.1f deg margin: %.2f .. %.2f deg\n",
                        margin, margin, TILT_SPAN_DEG - margin);
          Serial.printf("[HOME]   raw counts: %ld .. %ld\n",
                        (long)lround((g_homeDeg - margin) * 100.0),
                        (long)lround((g_homeDeg - (TILT_SPAN_DEG - margin)) * 100.0));
          Serial.flush(); return; } }
    // Real pullup test: drive the line LOW, release it as a plain INPUT (no internal pull),
    // and see whether it snaps back HIGH. Only an EXTERNAL pullup can do that. An earlier
    // version of this read with INPUT_PULLUP enabled, which reads HIGH even with nothing
    // connected -- that test was meaningless.
    if (!strcmp(line, "pullup")) {
        Wire.end(); delay(5);
        const int pins[2] = {PIN_SDA, PIN_SCL};
        const char *nm[2] = {"SDA", "SCL"};
        for (int i = 0; i < 2; ++i) {
            pinMode(pins[i], OUTPUT); digitalWrite(pins[i], LOW);
            delayMicroseconds(200);
            pinMode(pins[i], INPUT);              // release, NO internal pull
            delayMicroseconds(5);   int t5   = digitalRead(pins[i]);
            delayMicroseconds(45);  int t50  = digitalRead(pins[i]);
            delayMicroseconds(950); int t1ms = digitalRead(pins[i]);
            Serial.printf("[PULLUP] G%-2d %s: 5us=%d 50us=%d 1ms=%d  -> %s\n",
                          pins[i], nm[i], t5, t50, t1ms,
                          t50 ? "EXTERNAL PULLUP PRESENT (bus wired)"
                              : (t1ms ? "weak/slow - marginal" : "NO PULLUP - nothing on this segment"));
            Serial.flush();
        }
        Wire.begin(PIN_SDA, PIN_SCL, 400000UL);
        return;
    }
    if (!strcmp(line, "lines")) { bool a,b; i2cLineState(&a,&b);
        Serial.printf("[BUS] SDA=%s  SCL=%s   (both HIGH = idle/healthy; SDA LOW = slave stuck)\n",
                      a?"HIGH":"LOW", b?"HIGH":"LOW");
        Wire.begin(PIN_SDA, PIN_SCL, 400000UL); Serial.flush(); return; }
    if (!strcmp(line, "unstick")) { bool a,b; i2cLineState(&a,&b);
        Serial.printf("[BUS] before: SDA=%s SCL=%s\n", a?"HIGH":"LOW", b?"HIGH":"LOW");
        int n = busRecover();
        i2cLineState(&a,&b); Wire.begin(PIN_SDA, PIN_SCL, 400000UL);
        Serial.printf("[BUS] clocked %d pulse(s); after: SDA=%s SCL=%s\n",
                      n, a?"HIGH":"LOW", b?"HIGH":"LOW");
        Serial.flush(); scanAll(); return; }

    if (sscanf(line, "%7s %7s %x", what, bus, &addr) == 3) {
        const bool isI2C = (strcmp(bus, "i2c") == 0);
        if (!strcmp(what, "info")) { if (isI2C) infoI2C((uint8_t)addr);
                                     else { uint8_t v=0; int r=roller485.getFirmwareVersion(addr,&v);
                                            Serial.printf("--- 485 id=%u --- fw=%u (rc=%d) mode=%d\n",
                                                          addr, v, r, roller485.getMode(addr)); }
                                     return; }
        if (!strcmp(what, "enc"))  { setEncoderMode(isI2C, (uint8_t)addr); return; }
        if (!strcmp(what, "mon") || !strcmp(what, "watch")) {
            const bool stream = (what[0] == 'm');
            mon_i2c = isI2C; mon_addr = (uint8_t)addr;
            mon_min = 1e9; mon_max = -1e9; mon_on = stream;
            Serial.printf("[MON] %s %s 0x%02X — move the axis by hand to each stop, then 'peak'\n",
                          stream ? "streaming" : "background peak-tracking", bus, addr);
            Serial.flush(); return; }
    }
    Serial.println("? unknown — 'help'");
}

static char rx[96]; static int rxn = 0;
static void pollSerial()
{
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') { if (rxn) { rx[rxn] = 0; handle(rx); rxn = 0; } }
        else if (rxn < (int)sizeof(rx) - 1) rx[rxn++] = c;
    }
}

// ---------------- display ----------------
// Live tilt readout. Static furniture is drawn once; only the value areas repaint, so the
// 10 Hz update stays cheap (a full-screen redraw on this panel is very slow).
static int  SW = 0, SH = 0;
static bool shellDrawn = false;

static void drawShell()
{
    SW = M5.Display.width(); SH = M5.Display.height();
    M5.Display.fillScreen(TFT_BLACK);
    M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK); M5.Display.setTextSize(2);
    M5.Display.setCursor(20, 12); M5.Display.print("OpenDubs pan/tilt  -  passive encoder mode, no motion commanded");
    shellDrawn = true;
}

static void drawAxis(int slot, const Axis &a)
{
    const int y = 60 + slot * (SH - 80) / 2;
    const int h = (SH - 90) / 2;
    M5.Display.fillRect(0, y, SW, h, TFT_BLACK);

    M5.Display.setTextSize(3); M5.Display.setTextColor(TFT_CYAN, TFT_BLACK);
    M5.Display.setCursor(20, y); M5.Display.printf("%s  0x%02X", a.name, a.addr);

    if (!a.ok) {
        M5.Display.setTextSize(4); M5.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
        M5.Display.setCursor(20, y + 40); M5.Display.print("-- no data --");
        return;
    }
    M5.Display.setTextSize(9); M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
    M5.Display.setCursor(20, y + 34); M5.Display.printf("%.1f", a.deg);
    M5.Display.setTextSize(3); M5.Display.setTextColor(TFT_DARKGREY, TFT_BLACK);
    M5.Display.print(" deg");

    if (a.mn <= a.mx) {
        M5.Display.setTextSize(3);
        M5.Display.setTextColor(TFT_GREEN, TFT_BLACK);
        M5.Display.setCursor(SW / 2 + 40, y + 40);  M5.Display.printf("min %8.1f", a.mn);
        M5.Display.setTextColor(TFT_ORANGE, TFT_BLACK);
        M5.Display.setCursor(SW / 2 + 40, y + 76);  M5.Display.printf("max %8.1f", a.mx);
        M5.Display.setTextColor(TFT_YELLOW, TFT_BLACK);
        M5.Display.setCursor(SW / 2 + 40, y + 112); M5.Display.printf("rng %8.1f", a.mx - a.mn);
    }
}

static void drawLive()
{
    if (!shellDrawn) drawShell();
    static double pd[2] = {1e9, 1e9}, pn[2] = {1e9, 1e9}, px[2] = {1e9, 1e9};
    static bool   po[2] = {false, false};
    for (int i = 0; i < 2; ++i) {
        if (fabs(g_ax[i].deg - pd[i]) >= 0.05 || g_ax[i].ok != po[i] ||
            g_ax[i].mn != pn[i] || g_ax[i].mx != px[i]) {
            drawAxis(i, g_ax[i]);
            pd[i] = g_ax[i].deg; po[i] = g_ax[i].ok; pn[i] = g_ax[i].mn; px[i] = g_ax[i].mx;
        }
    }
}

void setup()
{
    auto cfg = M5.config(); M5.begin(cfg);
    M5.Display.setRotation(1);
    Serial.begin(115200);
    // tab5_face uses setTxTimeoutMs(0) so the face render never blocks — but a 0 timeout
    // SILENTLY DROPS output when the CDC TX buffer fills, which eats a whole scan dump.
    // This tool has no render loop to starve, so allow writes to wait for buffer space.
    Serial.setTxTimeoutMs(100);

    const bool wok = Wire.begin(PIN_SDA, PIN_SCL, 400000UL);
    esp_log_level_set("i2c.master", ESP_LOG_NONE);
    // short serial timeout so a 128-id sweep doesn't take minutes
    roller485.begin(&Serial1, 115200, SERIAL_8N1, PIN_485_RX, PIN_485_TX, PIN_485_DE, false, 60UL);
    r485_ok = true;

    delay(300);
    Serial.println("\n[TAB5-ROLLER] pan/tilt bring-up — READ ONLY, no motion commands");
    Serial.printf("[BUS] Wire.begin(G%d,G%d) = %s\n", PIN_SDA, PIN_SCL, wok ? "OK" : "FAILED");
    Serial.printf("[TAB5-ROLLER] I2C SDA=G%d SCL=G%d (Wire.begin=%s) | RS485 TX=G%d RX=G%d DE=G%d\n",
                  PIN_SDA, PIN_SCL, wok ? "OK" : "FAILED", PIN_485_TX, PIN_485_RX, PIN_485_DE);
    scanAll();
    help();
    mon_i2c = true; mon_addr = 0x64;
    setEncoderMode(true, 0x64);        // both axes passive + output OFF. The pan is TETHERED
    setEncoderMode(true, 0x65);        // (12V feed + USB) so it must never be driven.
    drawShell();
}

void loop()
{
    M5.update();
    pollSerial();
    monitorTick();
    // Auto-rescan every 2 s while the bus looks empty, so a motor that powers up (or gets
    // plugged in) after we booted is picked up without anyone typing 'scan'.
    static uint32_t lastRescan = 0;
    if (!g_haveRead && millis() - lastRescan > 1000) {
        lastRescan = millis();
        const int before = g_nFound;
        g_nFound = 0;
        muxOpenAll();
        for (uint8_t a = 0x08; a <= 0x77; ++a) {
            if (!present(a) || isMuxAddr(a)) continue;
            bool o1, o2;
            uint8_t fw = rdU8(a, REG_FW_VER, &o1), md = rdU8(a, REG_MODE, &o2);
            if (o1 && o2 && g_nFound < 16) g_found[g_nFound++] = {true, a, fw, md};
        }
        if (g_nFound != before) {
            Serial.printf("[SCAN] auto: %d roller(s) present\n", g_nFound);
            for (int i = 0; i < g_nFound; ++i)
                Serial.printf("  0x%02X fw=%u mode=%u(%s)\n", g_found[i].addr, g_found[i].fw,
                              g_found[i].mode, modeName(g_found[i].mode));
            Serial.flush();
            // a motor that just appeared comes up in SPEED mode -> put the tilt back to passive
            for (int i = 0; i < g_nFound; ++i)
                if (g_found[i].addr == 0x64 && g_found[i].mode != 4) setEncoderMode(true, 0x64);
        }
    }

    static uint32_t lastSample = 0;
    if (millis() - lastSample > 100) {
        axisSample(); lastSample = millis();
        g_haveRead = g_ax[0].ok || g_ax[1].ok;
    }
    static uint32_t lastDraw = 0;
    if (millis() - lastDraw > 120) { drawLive(); lastDraw = millis(); }
    delay(2);
}
