// OpenDubs Tab5 — pan/tilt bring-up tool (8Servo behind a PaHub).
//
// SAFETY MODEL. The servo rail (VM) on the 8Servo is fed from VCC_5V, which with nothing on
// the unit's terminal block means the Grove 5V — i.e. the Tab5's USB rail. Two goBILDA torque
// servos stalling would brown that out, so every default here is chosen to make motion hard
// rather than easy:
//
//   * Boot leaves ALL 8Servo pins in DIGITAL_INPUT_MODE. A pin not in SERVO_CTL emits no
//     pulse at all, so the servos are unpowered-idle until explicitly enabled.
//   * A channel only moves after EN <ch>, and its first emitted pulse is always kNeutralUs.
//     1500us is the safe first command in BOTH servo modes: centre in position mode, stop in
//     continuous mode. We do not yet know which mode these servos are in — this tells us.
//   * Targets are clamped to [g_lo, g_hi], default a deliberately tight +/-100us around neutral.
//     Widen with LIM once we trust the mechanism.
//   * The commanded value slews at g_slew us/tick (50 Hz) so a large GO becomes a slow ramp
//     instead of a slam. This bounds the *command*, not the servo's internal speed — the very
//     first EN can still move the horn from wherever it happens to sit.
//   * Servo current is polled continuously. Above g_trip the offending channel is disabled
//     immediately. Units of the 8Servo's current register are not documented, so g_trip starts
//     at 0 (disarmed) — read CUR while idle first, then set TRIP from a real number.
//
// Commands (USB serial, 115200, newline-terminated):
//   SCAN              full bus scan, including behind every PaHub channel
//   CUR               read 8Servo servo-rail current
//   EN <ch> [us]      enable a channel; starts at [us] (default 1500, clamped to LIM)
//   DIS <ch>          disable a channel (mode -> input, output stops)
//   STOP              disable every channel immediately
//   GO <ch> <us>      set target pulse, clamped and slewed
//   LIM <lo> <hi>     set the clamp window (default 1400 1600)
//   SLEW <us>         set slew rate per 20ms tick (default 2)
//   TRIP <mA>         current cutoff; 0 disarms it
//   RD <addr> <reg> <n>   raw register read, for identifying the ToF
//   TOF [n]           read the VL53L0X n times (default 1)
//   STAT              print current state
//   SVMODE POS|CONT CONFIRM   reprogram a dual-mode servo wired to G49
//   SVPING            sweep servo IDs 0-253 and report which answer
#include <Arduino.h>
#include <Wire.h>
#include <VL53L0X.h>

static constexpr int  kSda = 53;              // Grove Port A
static constexpr int  kScl = 54;
static constexpr uint32_t kBusHz = 100000;    // conservative for bring-up

static constexpr uint8_t kServo8Addr   = 0x25;
static constexpr uint8_t kModeReg      = 0x00;   // + pin
static constexpr uint8_t kPulseReg     = 0x60;   // + pin*2, 16-bit LE microseconds
static constexpr uint8_t kCurrentReg   = 0xA0;   // 4-byte float
static constexpr uint8_t kModeInput    = 0;      // DIGITAL_INPUT_MODE — no output
static constexpr uint8_t kModeServo    = 3;      // SERVO_CTL_MODE

static constexpr uint16_t kNeutralUs = 1500;
static constexpr uint8_t  kTofCh = 5;    // PaHub channel the VL53L0X sits on
static constexpr uint8_t  kPan  = 1;
static constexpr uint8_t  kTilt = 0;

// ---- runtime state -------------------------------------------------------------
static int      g_hubCh      = -1;      // PaHub channel the 8Servo answers on; -1 = flat bus
static uint8_t  g_hubAddr    = 0x70;
static bool     g_hubPresent = false;
static uint16_t g_lo = 1400, g_hi = 1600;
static uint16_t g_slew = 2;
static float    g_trip = 0.0f;          // 0 = disarmed until we know the units
static bool     g_en[8]  = {false};
static uint16_t g_tgt[8] = {0};
static uint16_t g_cur[8] = {0};
static uint32_t g_i2cFail = 0;

// ---- low-level I2C -------------------------------------------------------------
static bool muxSelect(int ch)
{
    if (!g_hubPresent || ch < 0) return true;
    Wire.beginTransmission(g_hubAddr);
    Wire.write((uint8_t)(1u << ch));
    return Wire.endTransmission() == 0;
}

static bool wr(uint8_t addr, uint8_t reg, const uint8_t *d, size_t n)
{
    if (!muxSelect(g_hubCh)) { g_i2cFail++; return false; }
    Wire.beginTransmission(addr);
    Wire.write(reg);
    for (size_t i = 0; i < n; ++i) Wire.write(d[i]);
    const bool ok = (Wire.endTransmission() == 0);   // Arduino Wire always issues STOP
    if (!ok) g_i2cFail++;
    return ok;
}

static bool rd(uint8_t addr, uint8_t reg, uint8_t *d, size_t n)
{
    if (!muxSelect(g_hubCh)) { g_i2cFail++; return false; }
    Wire.beginTransmission(addr);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) { g_i2cFail++; return false; }
    if (Wire.requestFrom((int)addr, (int)n) != (int)n) { g_i2cFail++; return false; }
    for (size_t i = 0; i < n; ++i) d[i] = Wire.read();
    return true;
}

// The ESP-IDF i2c-ng driver can land in ESP_ERR_INVALID_STATE and stay there. Tear the
// peripheral down and back up rather than spinning on a dead bus.
static void busRecover()
{
    Wire.end();
    delay(5);
    Wire.begin(kSda, kScl, kBusHz);
    Serial.println("[I2C] bus re-init");
}

// ---- 8Servo ---------------------------------------------------------------------
static bool setMode(uint8_t pin, uint8_t mode)
{
    return wr(kServo8Addr, kModeReg + pin, &mode, 1);
}

static bool setPulse(uint8_t pin, uint16_t us)
{
    const uint8_t d[2] = { (uint8_t)(us & 0xFF), (uint8_t)(us >> 8) };
    return wr(kServo8Addr, kPulseReg + pin * 2, d, 2);
}

static bool readCurrent(float *out)
{
    uint8_t b[4];
    if (!rd(kServo8Addr, kCurrentReg, b, 4)) return false;
    memcpy(out, b, 4);
    return true;
}

static void disableCh(uint8_t ch, const char *why)
{
    g_en[ch] = false;
    setMode(ch, kModeInput);
    Serial.printf("[SERVO] ch%u DISABLED (%s)\n", ch, why);
}

static void stopAll(const char *why)
{
    for (uint8_t c = 0; c < 8; ++c) if (g_en[c]) disableCh(c, why);
}

// ---- dual-mode servo reprogramming ------------------------------------------------
// goBILDA/REV 2000-series "dual mode" servos switch between positional and continuous
// rotation over a Feetech/Dynamixel-style half-duplex serial link on the SIGNAL wire —
// not over PWM. 76800 baud, 8N1, packets of:
//     FF FF <ID> <LEN> <INST> <params...> <checksum>,  checksum = ~(sum of ID..params)
// The only difference between the two modes is the 44-byte config block written at
// address 6; everything around it is identical, so the change is reversible.
// Protocol per Asaf / FRC5951 on Chief Delphi, checksums re-derived and verified here.
//
// Wiring: kProgPin -> the servo's SIGNAL pin. Ground is already common through the
// 8Servo. The 8Servo channel MUST stay in input mode or it will fight the line.
static constexpr int kProgPin = 49;        // ExtIO header, TX -> servo SIGNAL
static constexpr int kProgRx  = 50;        // ExtIO header, RX <- servo SIGNAL (same wire)

static const uint8_t kSvPing[]   = {255,255,254,2,1,254};
static const uint8_t kSvRead[]   = {255,255,1,4,2,21,1,226};
static const uint8_t kSvUnlock[] = {255,255,1,4,3,52,0,195};   // addr 52 = 0
static const uint8_t kSvLock[]   = {255,255,1,4,3,52,1,194};   // addr 52 = 1 (save)
static const uint8_t kSvCont[]   = {255,255,1,47,3,6,50,20,0,5,0,10,0,10,0,30,0,0,0,3,
    255,1,0,0,0,65,3,197,1,244,1,255,0,0,2,6,14,5,170,3,232,0,20,0,0,0,0,3,232,0,153};
static const uint8_t kSvPos[]    = {255,255,1,47,3,6,2,30,0,5,0,15,0,45,0,0,0,0,15,3,
    252,0,0,0,0,65,3,197,0,0,1,255,1,0,2,9,196,1,244,3,232,0,1,0,0,0,0,3,232,0,178};

// Feetech/Dynamixel checksum: ~(sum of every byte after the FF FF header) & 0xFF.
static uint8_t svChecksum(const uint8_t *b, size_t n)   // b points at ID, n = ID..params
{
    uint16_t sum = 0;
    for (size_t i = 0; i < n; ++i) sum += b[i];
    return (uint8_t)(~sum & 0xFF);
}

// Transmit, then release TX so the servo can drive the shared wire, and collect any reply.
static size_t svXfer(const uint8_t *pkt, size_t n, uint8_t *rx, size_t rxMax, uint16_t waitMs)
{
    Serial1.begin(76800, SERIAL_8N1, kProgRx, kProgPin);
    while (Serial1.available()) Serial1.read();
    Serial1.write(pkt, n);
    Serial1.flush();
    pinMode(kProgPin, INPUT_PULLUP);          // hand the wire back before the reply
    const uint32_t t0 = millis();
    size_t got = 0;
    while (millis() - t0 < waitMs) {
        while (Serial1.available() && got < rxMax) rx[got++] = Serial1.read();
    }
    return got;
}

// Broadcast-free ID sweep: PING each ID in turn and see who answers.
static void svPingSweep()
{
    Serial.println("[SVPING] sweeping IDs 0-253 ...");
    uint8_t rx[16];
    int found = 0;
    for (int id = 0; id <= 253; ++id) {
        uint8_t body[3] = { (uint8_t)id, 2, 1 };            // ID, LEN=2, INST=PING
        uint8_t pkt[6]  = { 0xFF, 0xFF, body[0], body[1], body[2], svChecksum(body, 3) };
        const size_t n = svXfer(pkt, sizeof(pkt), rx, sizeof(rx), 6);
        if (n >= 4) {
            Serial.printf("[SVPING] ID %d replied (%u bytes):", id, (unsigned)n);
            for (size_t i = 0; i < n; ++i) Serial.printf(" %02X", rx[i]);
            Serial.println();
            found++;
        }
    }
    Serial.printf("[SVPING] done — %d servo(s) answered\n", found);
    if (!found) Serial.println("[SVPING] nothing replied: check the G50 jumper, or 3.3V may be too weak to drive the line");
}

// Transmit one packet, then release the line. The servo answers READ packets, so the pin
// must go high-Z between packets or we would be driving against its reply.
static void svSend(const uint8_t *pkt, size_t n, uint16_t gapMs)
{
    Serial1.begin(76800, SERIAL_8N1, -1, kProgPin);
    Serial1.write(pkt, n);
    Serial1.flush();
    Serial1.end();
    pinMode(kProgPin, INPUT_PULLUP);
    delay(gapMs);
}

static void svProgram(bool positional)
{
    Serial.printf("[SVMODE] writing %s config on G%d ...\n",
                  positional ? "POSITIONAL" : "CONTINUOUS", kProgPin);
    svSend(kSvPing,   sizeof(kSvPing),   2);
    svSend(kSvRead,   sizeof(kSvRead),   2);
    svSend(kSvUnlock, sizeof(kSvUnlock), 3);
    if (positional) svSend(kSvPos,  sizeof(kSvPos),  6);
    else            svSend(kSvCont, sizeof(kSvCont), 6);
    svSend(kSvLock,   sizeof(kSvLock),   2);
    Serial.println("[SVMODE] sequence sent. Power-cycle the servo, then re-test.");
}

// ---- scan ------------------------------------------------------------------------
static bool probe(uint8_t a)
{
    Wire.beginTransmission(a);
    return Wire.endTransmission() == 0;
}

static void scan()
{
    Serial.println("[SCAN] top level 0x08-0x77:");
    g_hubPresent = false;
    for (uint8_t a = 0x08; a <= 0x77; ++a) {
        if (!probe(a)) continue;
        const bool mux = (a >= 0x70 && a <= 0x77);
        Serial.printf("   0x%02X%s\n", a, mux ? "   <- PaHub / mux" : "");
        if (mux && !g_hubPresent) { g_hubPresent = true; g_hubAddr = a; }
    }
    if (!g_hubPresent) { Serial.println("[SCAN] no mux — flat bus"); g_hubCh = -1; return; }

    int found8 = -1;
    for (int ch = 0; ch < 6; ++ch) {
        g_hubCh = ch;
        if (!muxSelect(ch)) { Serial.printf("[SCAN] ch%d select FAILED\n", ch); continue; }
        Serial.printf("[SCAN] ch%d:", ch);
        bool any = false;
        for (uint8_t a = 0x08; a <= 0x77; ++a) {
            if (a >= 0x70 && a <= 0x77) continue;      // the mux never answers behind itself
            if (!probe(a)) continue;
            Serial.printf(" 0x%02X%s", a, a == kServo8Addr ? "(8Servo)" : "");
            any = true;
            if (a == kServo8Addr) { Serial.printf("  <== 8Servo"); found8 = ch; }
        }
        if (!any) Serial.print(" none");
        Serial.println();
    }
    // Point the bus at the 8Servo automatically — every servo op needs that channel
    // selected, and forgetting to is silent (writes just vanish into an unselected mux).
    if (found8 >= 0) g_hubCh = found8;
    Serial.printf("[SCAN] active channel = %d%s\n", g_hubCh,
                  found8 >= 0 ? "  (8Servo, auto-selected)" : "  (set with CH <n>)");
}

// ---- ToF (VL53L0X on its own PaHub channel) -----------------------------------------
// The sensor lives behind a different mux channel than the 8Servo, so every access has to
// swing the mux across and back — the 50Hz servoTick assumes it owns g_hubCh.
static VL53L0X g_tof;
static bool    g_tofReady = false;

static bool tofOnChannel(bool on)
{
    static int keep = -1;
    if (on) { keep = g_hubCh; g_hubCh = kTofCh; }
    else    { g_hubCh = keep; }
    return muxSelect(g_hubCh);
}

static bool tofInit()
{
    tofOnChannel(true);
    g_tof.setBus(&Wire1);
    g_tof.setTimeout(500);
    const bool ok = g_tof.init();
    if (ok) g_tof.startContinuous();
    tofOnChannel(false);
    return ok;
}

static void tofRead(int n)
{
    if (!g_tofReady) {
        g_tofReady = tofInit();
        if (!g_tofReady) { Serial.println("[TOF] init FAILED — check ch5 / wiring"); return; }
        Serial.println("[TOF] VL53L0X initialised, continuous ranging");
    }
    for (int i = 0; i < n; ++i) {
        tofOnChannel(true);
        const uint16_t mm = g_tof.readRangeContinuousMillimeters();
        const bool to = g_tof.timeoutOccurred();
        tofOnChannel(false);
        Serial.printf("[TOF] %u mm%s\n", mm, to ? "   (TIMEOUT)" : "");
        if (n > 1) delay(120);
    }
}

// ---- 50 Hz servo tick -------------------------------------------------------------
static void servoTick()
{
    for (uint8_t c = 0; c < 8; ++c) {
        if (!g_en[c]) continue;
        int cur = g_cur[c], tgt = g_tgt[c];
        const int d = tgt - cur;
        if (d >  (int)g_slew) cur += g_slew;
        else if (d < -(int)g_slew) cur -= g_slew;
        else cur = tgt;
        g_cur[c] = (uint16_t)cur;
        if (!setPulse(c, g_cur[c])) disableCh(c, "I2C write failed");
    }
}

// ---- commands ----------------------------------------------------------------------
static void handle(char *cmd)
{
    int a = 0, b = 0, n = 0;
    while (*cmd == ' ') cmd++;
    if (!*cmd) return;

    if (!strcasecmp(cmd, "SCAN")) { scan(); return; }
    if (!strcasecmp(cmd, "SVPING")) { svPingSweep(); return; }
    if (!strcasecmp(cmd, "TOF")) { tofRead(1); return; }
    if (sscanf(cmd, "TOF %d", &a) == 1) { tofRead(constrain(a,1,50)); return; }
    // Writes servo EEPROM — gated behind CONFIRM, and refuses while anything is driven.
    if (!strncasecmp(cmd, "SVMODE", 6)) {
        char which[16] = {0}, conf[16] = {0};
        const int got = sscanf(cmd, "%*s %15s %15s", which, conf);
        for (uint8_t c = 0; c < 8; ++c) if (g_en[c]) {
            Serial.println("[SVMODE] refused: disable all channels first (STOP)"); return; }
        const bool pos = !strcasecmp(which, "POS");
        if (got < 1 || (!pos && strcasecmp(which, "CONT"))) {
            Serial.println("usage: SVMODE POS|CONT CONFIRM"); return; }
        if (strcasecmp(conf, "CONFIRM")) {
            Serial.printf("[SVMODE] would write %s to the servo on G%d — this writes EEPROM.\n",
                          pos ? "POSITIONAL" : "CONTINUOUS", kProgPin);
            Serial.println("[SVMODE] re-send with CONFIRM to proceed."); return; }
        svProgram(pos);
        return;
    }
    if (!strcasecmp(cmd, "STOP")) { stopAll("STOP command"); return; }

    if (!strcasecmp(cmd, "CUR")) {
        float c;
        if (readCurrent(&c)) Serial.printf("[CUR] servo rail = %.3f A\n", c);
        else                 Serial.println("[CUR] read FAILED");
        return;
    }
    if (!strcasecmp(cmd, "STAT")) {
        Serial.printf("[STAT] hub=%d ch=%d  lim=%u..%u slew=%u trip=%.3fA i2cfail=%lu\n",
                      g_hubPresent, g_hubCh, g_lo, g_hi, g_slew, g_trip, (unsigned long)g_i2cFail);
        for (uint8_t c = 0; c < 8; ++c)
            if (g_en[c]) Serial.printf("        ch%u EN  cur=%u tgt=%u\n", c, g_cur[c], g_tgt[c]);
        return;
    }
    if (sscanf(cmd, "CH %d", &a) == 1) {
        g_hubCh = a; Serial.printf("[HUB] active channel = %d\n", g_hubCh); return;
    }
    // EN <ch> [us] — the starting pulse is settable because 1500us is not always safe to
    // begin at: tilt stalls into its forward stop there, so it must come up somewhere else.
    if (sscanf(cmd, "EN %d %d", &a, &b) == 2 || sscanf(cmd, "EN %d", &a) == 1) {
        if (a < 0 || a > 7) { Serial.println("usage: EN <0-7> [us]"); return; }
        uint16_t start = (b >= 500 && b <= 2500) ? (uint16_t)b : kNeutralUs;
        const uint16_t cl = constrain((int)start, (int)g_lo, (int)g_hi);
        if (cl != start) Serial.printf("[SERVO] start %u clamped to %u (LIM %u..%u)\n", start, cl, g_lo, g_hi);
        start = cl;
        g_tgt[a] = start; g_cur[a] = start;
        if (!setMode(a, kModeServo)) { Serial.println("[SERVO] mode write FAILED"); return; }
        if (!setPulse(a, start)) { Serial.println("[SERVO] pulse write FAILED"); return; }
        g_en[a] = true;
        Serial.printf("[SERVO] ch%d ENABLED at %uus%s\n", a, start,
                      start == kNeutralUs ? " (neutral)" : "");
        return;
    }
    if (sscanf(cmd, "DIS %d", &a) == 1) { if (a >= 0 && a < 8) disableCh(a, "DIS command"); return; }
    if (sscanf(cmd, "GO %d %d", &a, &b) == 2) {
        if (a < 0 || a > 7 || !g_en[a]) { Serial.println("[SERVO] channel not enabled"); return; }
        const int cl = constrain(b, (int)g_lo, (int)g_hi);
        if (cl != b) Serial.printf("[SERVO] %d clamped to %d (LIM %u..%u)\n", b, cl, g_lo, g_hi);
        g_tgt[a] = cl;
        Serial.printf("[SERVO] ch%d -> %d us (from %u, slew %u/tick)\n", a, cl, g_cur[a], g_slew);
        return;
    }
    if (sscanf(cmd, "LIM %d %d", &a, &b) == 2) {
        g_lo = a; g_hi = b; Serial.printf("[LIM] %u..%u\n", g_lo, g_hi); return;
    }
    if (sscanf(cmd, "SLEW %d", &a) == 1) { g_slew = max(1, a); Serial.printf("[SLEW] %u\n", g_slew); return; }
    float f = 0;
    if (sscanf(cmd, "TRIP %f", &f) == 1) {
        g_trip = f;
        Serial.printf("[TRIP] %.3f A %s\n", g_trip, g_trip > 0 ? "(armed)" : "(DISARMED)");
        return;
    }
    if (sscanf(cmd, "RD %x %x %d", &a, &b, &n) == 3) {
        uint8_t buf[16]; n = constrain(n, 1, 16);
        if (!rd((uint8_t)a, (uint8_t)b, buf, n)) { Serial.println("[RD] failed"); return; }
        Serial.printf("[RD] 0x%02X reg 0x%02X:", a, b);
        for (int i = 0; i < n; ++i) Serial.printf(" %02X", buf[i]);
        Serial.println();
        return;
    }
    Serial.println("? SCAN CUR STAT CH EN DIS GO LIM SLEW TRIP RD STOP");
}

// ---- main ---------------------------------------------------------------------------
void setup()
{
    Serial.begin(115200);
    Serial.setTxTimeoutMs(0);              // never block on an absent host
    delay(600);
    // Read the idle line levels BEFORE claiming the pins. Healthy I2C idles HIGH on both,
    // held up by the pull-ups on the connected devices. LOW or floating means the cable or
    // the devices are not there — which looks identical to a driver fault from up here.
    pinMode(kSda, INPUT); pinMode(kScl, INPUT);
    delayMicroseconds(200);
    const int sdaFloat = digitalRead(kSda), sclFloat = digitalRead(kScl);
    const bool wok = Wire.begin(kSda, kScl, kBusHz);
    Serial.println("\n=== Tab5 pan/tilt bring-up ===");
    Serial.printf("[BUS] idle (no pullup) SDA=%s SCL=%s   Wire.begin=%s\n",
                  sdaFloat ? "HIGH" : "LOW", sclFloat ? "HIGH" : "LOW", wok ? "OK" : "FAILED");
    Serial.printf("I2C on G%d/G%d @ %luHz\n", kSda, kScl, (unsigned long)kBusHz);
    scan();
    // Force every channel to a non-output mode so nothing moves on boot.
    for (uint8_t c = 0; c < 8; ++c) setMode(c, kModeInput);
    Serial.println("all channels in INPUT mode — no servo output");
    Serial.println("suggested order: CUR (idle baseline) -> TRIP <n> -> EN 0 -> GO 0 <us>");
}

void loop()
{
    static char line[96];
    static uint8_t len = 0;
    while (Serial.available()) {
        const char c = Serial.read();
        if (c == '\n' || c == '\r') { line[len] = 0; if (len) handle(line); len = 0; }
        else if (len < sizeof(line) - 1) line[len++] = c;
    }

    static uint32_t lastTick = 0, lastCur = 0, lastFail = 0, lastPrint = 0;
    const uint32_t now = millis();

    if (now - lastTick >= 20) { lastTick = now; servoTick(); }

    // Current watchdog while anything is enabled. CHECKED at 25 Hz so a stall against a hard
    // stop is cut inside ~40ms — the USB rail is what we are protecting, and 200ms of stall
    // current is long enough to brown it out. Printed only at 2 Hz so the log stays readable.
    if (now - lastCur >= 40) {
        lastCur = now;
        bool anyOn = false;
        for (uint8_t c = 0; c < 8; ++c) anyOn |= g_en[c];
        if (anyOn) {
            float amps;
            if (readCurrent(&amps)) {
                if (g_trip > 0 && amps > g_trip) {
                    Serial.printf("[CUR] %.3f A  >  trip %.3f A\n", amps, g_trip);
                    stopAll("OVER-CURRENT");
                } else if (now - lastPrint >= 500) {
                    lastPrint = now;
                    Serial.printf("[CUR] %.3f A   ch0=%u ch1=%u\n", amps, g_cur[0], g_cur[1]);
                }
            }
        }
    }

    // If the bus itself has gone bad, re-init rather than spinning on it.
    if (now - lastFail >= 2000) {
        lastFail = now;
        static uint32_t prev = 0;
        if (g_i2cFail - prev > 20) { busRecover(); stopAll("I2C bus fault"); }
        prev = g_i2cFail;
    }
}
