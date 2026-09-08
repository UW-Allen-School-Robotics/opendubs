#include <Arduino.h>
#include <M5StamPLC.h>
#include <SPI.h>
#include <M5_Ethernet.h>   // PoE module W5500 (SPI on G7/G9/G8, CS G11)
#include "driver/uart.h"     // ESP-IDF UART for the RS485 port (BMS)
#include "esp_task_wdt.h"
#include "rc_config.h"
#include "mast.h"
#include "tof.h"    // task watchdog — auto-reset if the loop hangs
#include <stdarg.h>          // tprintf() telemetry tee
#include <Update.h>          // ESP32 OTA writer — Ethernet firmware pull (OTA command)

// ---- Telemetry tee (USB Serial + PoE UDP) -------------------------------------------------
// Mirror a telemetry line to USB Serial AND, when the PoE link is up, out over UDP so the SAME
// [TAG] text protocol reaches the Thor over EITHER transport. Crucially the UDP send is
// INDEPENDENT of the USB TX buffer: with USB unplugged the CDC TX buffer fills and
// availableForWrite() drops to ~0, so we must NOT gate the UDP path on it — telemetry keeps
// flowing over Ethernet regardless. netSendLine() is defined with the Ethernet section below.
void netSendLine(const char* buf, int n);
static bool netBringUp(bool dhcp);   // Ethernet bring-up / self-heal — defined in the Ethernet section, used in setup()
static void netMaintain();           // Ethernet link watchdog — defined in the Ethernet section, used in loop()
static void otaHttpPull(const char* url);   // Ethernet OTA — HTTP-GET a .bin off the Thor and self-flash (OTA command)

static void temit(const char* buf, int n)   // raw line (already formatted, includes trailing \n)
{
    if (n <= 0) return;
    if (Serial.availableForWrite() >= n) Serial.write((const uint8_t*)buf, n);  // USB: drop, never block
    netSendLine(buf, n);                                                        // Ethernet: independent
}

#include "telem.h"
void tprintf(const char* fmt, ...)
{
    char buf[384];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n >= (int)sizeof(buf)) n = (int)sizeof(buf) - 1;   // truncated — still emit what we have
    temit(buf, n);
}

// From M5StamPLC/src/pin_config.h:
// STAMPLC_PIN_GROVE_BLUE_SDA = 5, STAMPLC_PIN_GROVE_BLUE_SCL = 4
static constexpr int kBluePortRxPin = 5;
static constexpr int kBluePortTxPin = 4;

// Port A = StampPLC Grove RED port (external I2C). pin_config.h:
//   STAMPLC_PIN_GROVE_RED_SDA = 2  (G2),  STAMPLC_PIN_GROVE_RED_SCL = 1  (G1).
// This is a SEPARATE bus from the internal peripheral bus (m5::In_I2C, I2C_NUM_0
// on G13/G15: INA226 0x40, RX8130 0x32, PI4IOE5V6408 0x43, LM75B 0x48, AW9523B 0x59).
// We bind our external scanner to Port A on I2C_NUM_1 so it can never fall back to
// the internal bus (which is what M5.Ex_I2C's default was doing before).
static constexpr int kPortASda = 2;   // G2
static constexpr int kPortAScl = 1;   // G1

// --- External I2C topology: M5Stack PaHUB (PCA9548A 1-to-6 mux) on Port A -------
// Devices were moved BEHIND the hub (2026-06). Each device access selects its
// channel first (selectChannel()); if no hub is detected at boot the firmware
// falls back to flat Port-A access so it still works without the hub. NOTE: some
// of these are Qwiic 3.3 V parts behind the 5 V level shifter — if one doesn't
// detect, suspect the level conversion on that channel, not the firmware.
static constexpr uint8_t kPaHubAddr          = 0x70;  // PCA9548A mux
static constexpr uint8_t kExtEncoderAddr     = 0x59;  // M5 ExtEncoder Unit (mast position)
static constexpr uint8_t kExtEncoderCountReg = 0x00;  // int32 count
static constexpr uint8_t kExtEncoderResetReg = 0x30;
static constexpr uint8_t kTofAddr            = 0x29;  // M5 ToF4M (VL53L1X)
static constexpr uint8_t kServo8Addr         = 0x25;  // M5 8Servo
static constexpr uint8_t kServo8ModeReg      = 0x00;  // per-pin mode  (MODE_REG + pin)
static constexpr uint8_t kServo8PulseReg     = 0x60;  // per-pin pulse (REG + pin*2), 16-bit LE us
static constexpr uint8_t kServo8ModeServoCtl = 3;     // SERVO_CTL_MODE
static constexpr uint32_t kServo8Hz          = 400000; // 8Servo bus speed

// ============================ DRIVE OUTPUT ================================
// RC/ROS command -> mixer -> 8Servo PWM -> two goBILDA 2x80A ESCs.
//
// SAFETY: DRIVE_ENABLE gates only the physical PWM write. The gate/arm/mix logic
// runs regardless and streams to [DRIVE] telemetry, so you can verify arming +
// protection on the bench with NO signal reaching the ESCs (flag = 0). Flip it to
// 1 only when the wheels may move. Even then the arm gate blocks output under any
// un-cleared condition, and the hardware e-stop CONTACTOR (relay 0, drive-24V bus)
// is the true motion kill — commanding neutral here is defense-in-depth.
#ifndef DRIVE_ENABLE
#define DRIVE_ENABLE 1   // ENABLED 2026-08-19: RC/ROS drive writes the ESCs (bench-verified map below)
#endif

// 8Servo pins for the 4 ESCs, indexed [FL, FR, RL, RR] to match driveMix's w[]. Bench-mapped
// 2026-08-19 (wheels off): FL=ch1, FR=ch3, RL=ch0, RR=ch2. PRISM is on I2C now (not ch0).
static constexpr uint8_t  kDriveCh[4]       = {1, 3, 0, 2};
// Per-wheel ESC direction sign. RR (ch2) is wired reversed vs the others → invert it so a
// forward command drives all four wheels robot-forward. Indexed [FL, FR, RL, RR].
static constexpr int8_t   kDriveInvert[4]   = {1, 1, 1, -1};
static constexpr uint16_t kEscNeutralUs     = 1500;   // 2x80A neutral == arm-zone center
static constexpr uint16_t kEscSpanUs        = 150;    // ±span → 1350..1650 (gentle re-test band; widen toward 400 once ESCs hold)
static constexpr uint16_t kEscDeadzoneUs    = 6;      // treat |cmd|<this as 0 (matches the ESC ±10µs deadzone)
static constexpr uint32_t kArmHoldMs        = 2000;   // hold neutral this long, gate satisfied, to ARM the ESCs
static constexpr uint16_t kEscSlewUs        = 12;     // max µs step per 20ms tick — the decel/accel ramp (mass!)
static constexpr uint8_t  kEscRevDwellTicks = 10;     // on a direction flip: brake to neutral, then hold this many
                                                      // ticks (×20ms) at neutral before reversing. Spreads regen so
                                                      // the bus clamp can absorb it — a full FWD→REV slam tripped the ESCs.
static constexpr uint32_t kDriveTickMs      = 20;     // 50 Hz output (matches the 2x80A's expected RC rate)
static constexpr uint32_t kRosCmdTimeoutMs  = 250;    // autonomous heartbeat: a stale Thor/ROS cmd → neutral
static constexpr uint32_t kWdtTimeoutS      = 8;      // task watchdog: loop hang > this → auto chip reset
static constexpr uint32_t kRcTimeoutMs      = 500;    // RC FAILSAFE: no valid SBUS frame within this → NO-LINK (disarms drive, PRISM alarm)
static constexpr uint32_t kBmsQueryTimeoutMs = 200;   // JBD reply window: a PRESENT pack early-breaks in ~50-90 ms; must exceed its latency or frames cross/corrupt. Absent-pack stall is bounded by the backoff below, NOT this.
static constexpr uint16_t kSbusValidLo      = 160;    // analog stick floor: full deflection is ~172, so anything below is corrupt / a receiver failsafe-preset pegged to an extreme
static constexpr uint16_t kSbusValidHi      = 1840;   // analog stick ceiling: full deflection is ~1811, so anything above is corrupt / pegged
static constexpr uint32_t kBmsPollMs        = 1000;   // BMS auto-poll cadence while the pack is answering
static constexpr uint32_t kBmsBackoffMs     = 15000;  // after repeated no-replies, retry this slowly so an absent BMS can't starve the control loop
static constexpr int      kBmsFailBackoff   = 3;      // consecutive failed polls before backing off to kBmsBackoffMs
static constexpr int      kSbusCtr = 1000, kSbusHalf = 810;   // SBUS raw (~200..1820) → normalized -1..1
enum DriveMode { DM_MECANUM = 0, DM_SKID4 = 1, DM_DIFF2 = 2 };
static DriveMode g_driveMode         = DM_MECANUM;    // default; changed only by the DRIVEMODE command (software-only, no physical jumper)
static bool      g_driveModeOverride = false;         // true once a DRIVEMODE command has been issued

// Autonomous command from the Thor/ROS: host `DRIVE <fwd> <rot> <str>` (each -1..1).
// Under RC_AUTO authority this REPLACES the RC sticks as the command source. The
// timestamp is the heartbeat — if the Thor stops publishing, the cmd goes stale
// and drive falls to neutral (the autonomous equivalent of releasing the deadman).
struct RosDriveCmd { float fwd = 0, rot = 0, str = 0; uint32_t ms = 0; };

// Last commanded drive intent + derived turn direction — the single "movement intent" that
// feeds [DRIVE] telemetry, the DRIVE screen, the Tab5 chevrons, and the PRISM turn-signals.
// turn = net rightward intent (rotate right OR strafe right) → +1 right, -1 left, 0 straight.
static float g_intentFwd = 0, g_intentRot = 0, g_intentStr = 0;
static float g_wheelMix[4] = {0, 0, 0, 0};   // commanded mix (for the DRIVE screen; ESC output is arm-gated)
static float g_speed = 0;                    // CH8 speed knob 0..1 (DRIVE-screen annunciation)
static int   g_turnDir = 0;
static uint32_t g_turnTestUntil = 0;         // TURNTEST override window (bench PRISM turn-signal test)
// MASTTEST override: force the Z-axis hazard overlay without moving anything, so the colours
// and blink rates can be judged by eye with the lift unpowered. 0=off 1=up 2=down 3=hold.
static uint8_t  g_mastTest = 0;
static uint32_t g_mastTestUntil = 0;
static constexpr float kTurnIntentThresh = 0.15f;
static RosDriveCmd g_rosCmd;
// Z-axis intent from the Thor, same heartbeat discipline as the drive command.
struct RosMastCmd { float v = 0; uint32_t ms = 0; };
static RosMastCmd g_rosMast;

static bool     g_escArmed    = false;                 // ESCs have completed the neutral-hold arm
static uint32_t g_armStart_ms = 0;
static uint16_t g_escOut[4]   = {kEscNeutralUs, kEscNeutralUs, kEscNeutralUs, kEscNeutralUs};
static uint32_t g_s8Fail      = 0;                     // cumulative 8Servo I2C write failures (signal-dropout diag)
static uint32_t last_drive_ms = 0, last_drive_pub_ms = 0, last_rc_pub_ms = 0;
// =========================================================================

// ============================ RS485 (BMS) ================================
// Raw UART1 half-duplex probe for the ECO-WORTHY BMS on the RS485 port
// (TX=G0, RX=G39, DIR=G46 — the M5StamPLC library only offers a Modbus *slave*,
// so we drive the UART directly). Passive RX is logged as `[485] rx:`; host
// commands `RS485BAUD <n>`, `RS485TX <hex...>`, and `BMS` (JBD read-basic-info)
// drive active probing while we determine the battery's protocol/baud.
static constexpr uart_port_t kRs485Uart   = UART_NUM_1;
static constexpr int         kRs485TxPin  = 0, kRs485RxPin = 39, kRs485DirPin = 46;
static uint32_t g_rs485Baud = 9600;    // JBD BMS default; change with RS485BAUD
static bool     g_rs485Up   = false;

static void rs485Init(uint32_t baud)
{
    if (g_rs485Up) uart_driver_delete(kRs485Uart);
    uart_config_t cfg = {};
    cfg.baud_rate  = (int)baud;
    cfg.data_bits  = UART_DATA_8_BITS;
    cfg.parity     = UART_PARITY_DISABLE;
    cfg.stop_bits  = UART_STOP_BITS_1;
    cfg.flow_ctrl  = UART_HW_FLOWCTRL_DISABLE;
    cfg.source_clk = UART_SCLK_APB;
    uart_driver_install(kRs485Uart, 1024, 0, 0, NULL, 0);
    uart_param_config(kRs485Uart, &cfg);
    uart_set_pin(kRs485Uart, kRs485TxPin, kRs485RxPin, kRs485DirPin, UART_PIN_NO_CHANGE);
    uart_set_mode(kRs485Uart, UART_MODE_RS485_HALF_DUPLEX);   // DIR auto-driven as RTS
    g_rs485Baud = baud; g_rs485Up = true;
    Serial.printf("[485] init UART1 baud=%lu TX=%d RX=%d DIR=%d half-duplex\n",
                  (unsigned long)baud, kRs485TxPin, kRs485RxPin, kRs485DirPin);
}

// Drain RS485 RX (non-blocking) and log as hex. Called every loop.
static void rs485Poll()
{
    if (!g_rs485Up) return;
    uint8_t buf[160];
    int n = uart_read_bytes(kRs485Uart, buf, sizeof(buf), 0);
    if (n > 0) {
        Serial.printf("[485] rx %d:", n);
        for (int i = 0; i < n; ++i) Serial.printf(" %02X", buf[i]);
        Serial.println();
    }
}

// Parse a hex string ("DD A5 03 ..") → bytes. Skips any non-hex separators.
static int hexToBytes(const char* s, uint8_t* out, int maxlen)
{
    int n = 0, hi = -1;
    for (; *s && n < maxlen; ++s) {
        int v;
        if (*s >= '0' && *s <= '9')      v = *s - '0';
        else if (*s >= 'a' && *s <= 'f') v = *s - 'a' + 10;
        else if (*s >= 'A' && *s <= 'F') v = *s - 'A' + 10;
        else continue;
        if (hi < 0) hi = v; else { out[n++] = (uint8_t)((hi << 4) | v); hi = -1; }
    }
    return n;
}

static void rs485Send(const uint8_t* data, int len)
{
    if (!g_rs485Up) { Serial.println("[485] not initialized"); return; }
    Serial.printf("[485] tx %d:", len);
    for (int i = 0; i < len; ++i) Serial.printf(" %02X", data[i]);
    Serial.println();
    uart_write_bytes(kRs485Uart, (const char*)data, len);
    uart_wait_tx_done(kRs485Uart, pdMS_TO_TICKS(100));
}

// ---- JBD BMS reader (auto): protocol confirmed JBD @ 9600 (probed live). ----
static bool     g_bmsAuto   = true;    // auto-poll + emit [BMS]/[BMSC]; BMSAUTO 0 for manual probing

// ---- Non-blocking JBD/BMS poller (state machine) ----
// The JBD pack answers with ~100-200 ms latency; a blocking read that long stalls the
// 50 Hz control loop, which stops SBUS servicing and makes the dashboard see [RC] gaps /
// spurious NO-LINK. So we SEND a query, then poll the RS485 RX non-blocking (0-timeout)
// across loop iterations until the framed reply lands or a per-query timeout elapses —
// each loop pass costs a few µs. The interactive BMS/BMS_CELLS/RS485TX commands are
// independent (rs485Send + rs485Poll logging, used with BMSAUTO 0) so there's no contention.
enum BmsState { BMS_IDLE, BMS_WAIT_CELLS, BMS_WAIT_BASIC };
static BmsState g_bmsState    = BMS_IDLE;
static uint8_t  g_bmsBuf[96];
static int      g_bmsLen      = 0;
static uint32_t g_bmsSentMs   = 0;
static uint32_t g_bmsCycleMs  = 0;      // last poll-cycle start (throttle/backoff timer)
static int      g_bmsFail     = 0;
static bool     g_bmsAnswered = false;  // did the pack reply at all this cycle?
static float    g_cmin = 0, g_cmax = 0; // carried from the cells frame into the [BMS] line

// Fire a len=0 read query for `reg`; flush stale RX first. Non-blocking: no tx-done wait —
// the RS485 half-duplex driver manages DIR, and the reply can't arrive before the next poll.
static void bmsSend(uint8_t reg)
{
    uint8_t junk[64];
    while (uart_read_bytes(kRs485Uart, junk, sizeof(junk), 0) > 0) {}   // flush stale RX
    const uint16_t chk = (uint16_t)(0x10000u - reg);
    uint8_t q[7] = {0xDD, 0xA5, reg, 0x00, (uint8_t)(chk >> 8), (uint8_t)(chk & 0xFF), 0x77};
    uart_write_bytes(kRs485Uart, (const char*)q, 7);
    g_bmsLen = 0; g_bmsSentMs = millis();
}

// Non-blocking: accumulate whatever RX is available into g_bmsBuf; true once a full frame is in.
static bool bmsRxComplete()
{
    if (g_bmsLen < (int)sizeof(g_bmsBuf)) {
        int r = uart_read_bytes(kRs485Uart, g_bmsBuf + g_bmsLen, sizeof(g_bmsBuf) - g_bmsLen, 0);
        if (r > 0) g_bmsLen += r;
    }
    return (g_bmsLen >= 4 && g_bmsLen >= 4 + g_bmsBuf[3] + 3);
}

static bool bmsFrameValid() { return g_bmsLen >= 7 && g_bmsBuf[0] == 0xDD && g_bmsBuf[2] == 0x00; }

static void bmsDecodeCells()   // 0x04 → [BMSC] + cache cmin/cmax for the [BMS] line
{
    int ncell = g_bmsBuf[3] / 2; if (ncell > 16) ncell = 16;
    g_cmin = 1e9; g_cmax = -1e9;
    Serial.print("[BMSC]");
    for (int k = 0; k < ncell; ++k) {
        float cv = (float)((g_bmsBuf[4 + k*2] << 8) | g_bmsBuf[4 + k*2 + 1]) / 1000.0f;
        if (cv < g_cmin) g_cmin = cv;
        if (cv > g_cmax) g_cmax = cv;
        Serial.printf(" c%d=%.3f", k, cv);
    }
    Serial.println();
    g_bmsAnswered = true;
}

static void bmsDecodeBasic()   // 0x03 → [BMS]. Sign is JBD-native (+chg/−dsg); flip if inverted.
{
    const int n = g_bmsLen;
    const uint8_t* d = g_bmsBuf + 4;
    auto u16 = [&](int o) { return (int)((d[o] << 8) | d[o + 1]); };
    auto s16 = [&](int o) { return (int)(int16_t)((d[o] << 8) | d[o + 1]); };
    const float    v    = u16(0) / 100.0f;
    const float    i    = s16(2) / 100.0f;
    const uint16_t prot = (uint16_t)u16(16);
    const int      soc  = d[19];
    const int      nntc = d[22];
    float tmin = 1e9, tmax = -1e9;
    for (int k = 0; k < nntc && (23 + k * 2 + 1) <= (n - 4 - 3); ++k) {
        const float t = (u16(23 + k * 2) - 2731) / 10.0f;
        if (t < tmin) tmin = t;
        if (t > tmax) tmax = t;
    }
    const bool charging = i > 0.05f, discharging = i < -0.05f;
    tprintf("[BMS] soc=%d v=%.2f i=%.2f cmin=%.3f cmax=%.3f tmin=%.1f tmax=%.1f "
                  "chg=%d dsg=%d chgr=%d flags=0x%04X\n",
                  soc, v, i, g_cmin, g_cmax, tmin, tmax,
                  charging ? 1 : 0, discharging ? 1 : 0, charging ? 1 : 0, prot);
    g_bmsAnswered = true;
}

static void bmsBackoffTick()   // end-of-cycle: reset or count toward the slow-retry backoff
{
    if (g_bmsAnswered) { if (g_bmsFail) Serial.println("[BMS] pack online — resuming poll"); g_bmsFail = 0; }
    else if (g_bmsFail < 1000 && ++g_bmsFail == kBmsFailBackoff)
        Serial.printf("[BMS] no reply ×%d — backing off to %lus (RS485/pack absent?)\n",
                      kBmsFailBackoff, (unsigned long)(kBmsBackoffMs / 1000));
    g_bmsCycleMs = millis(); g_bmsState = BMS_IDLE;
}

// Called every loop iteration — NEVER blocks. Advances one step of the query/response cycle.
static void bmsStep()
{
    if (!g_rs485Up) return;
    const uint32_t interval = (g_bmsFail >= kBmsFailBackoff) ? kBmsBackoffMs : kBmsPollMs;
    switch (g_bmsState) {
        case BMS_IDLE:
            if (millis() - g_bmsCycleMs >= interval) {
                g_bmsAnswered = false; g_cmin = 0; g_cmax = 0;
                bmsSend(0x04); g_bmsState = BMS_WAIT_CELLS;
            }
            break;
        case BMS_WAIT_CELLS:
            if (bmsRxComplete()) {
                if (bmsFrameValid()) bmsDecodeCells();
                bmsSend(0x03); g_bmsState = BMS_WAIT_BASIC;
            } else if (millis() - g_bmsSentMs > kBmsQueryTimeoutMs) {
                bmsSend(0x03); g_bmsState = BMS_WAIT_BASIC;   // no cells this round; still try basic
            }
            break;
        case BMS_WAIT_BASIC:
            if (bmsRxComplete()) {
                if (bmsFrameValid() && g_bmsLen >= 27) bmsDecodeBasic();
                bmsBackoffTick();
            } else if (millis() - g_bmsSentMs > kBmsQueryTimeoutMs) {
                bmsBackoffTick();
            }
            break;
    }
}
// =========================================================================

// PRISM state indicator: robot state -> PRISM PWM pulse (us), driven on 8Servo ch0.
// 580 = police (unambiguous alarm). Solid-color hues (1050-1949 sweep) are APPROXIMATE
// and meant to be tuned by eye — see the PRISM PWM/color table. Prism is on 8Servo pin 0.
static constexpr uint8_t  kPrismServoPin = 0;
static constexpr uint16_t kPrismPolice   = 580;    // flashing police -> alarm
static constexpr uint16_t kPrismAmber    = 1230;   // manual, deadman released (tuned: was 1280, read slightly green)
static constexpr uint16_t kPrismGreen    = 1430;   // manual, deadman held/live (tuned: was 1500, read too blue)
static constexpr uint16_t kPrismBlue     = 1633;   // mode OFF (idle)
static constexpr uint16_t kPrismAuto     = 2100;   // autonomous: violet PULSE (kinetic). Pulse range ~1950-2199; tune hue by eye.
static constexpr uint8_t kPinpointAddr       = 0x31;  // goBILDA Pinpoint odometry
static constexpr uint8_t kOtosAddr           = 0x17;  // SparkFun OTOS (optical tracking odo)
static constexpr uint8_t kEnvAddr            = 0x44;  // M5 ENV III SHT30 (temp/humidity); 2 units,
                                                     // one per PaHub channel, both @0x44 (mux-isolated)

// PaHUB channel assignments — corrected 2026-08-13 to the AS-WIRED robot from an I2C_SCAN
// (each device's fixed I2C address pinned its channel unambiguously). The prior 2026-06 map
// went stale after reassembly: the firmware was selecting the wrong channel for every device,
// so drive (8Servo), PRISM, and the scan all failed until this remap.
static constexpr uint8_t kChPrism      = 0;   // PRISM_LED 0x38 measured on ch0 (was 1)
static constexpr uint8_t kChServo8     = 1;   // 8Servo   0x25 measured on ch1 (was 5) — drive + PRISM PWM
static constexpr uint8_t kChEnv        = 2;   // ENVIII SHT30 0x44 on ch2 (NEW — regen-resistor thermal)
static constexpr uint8_t kChTof        = 3;   // M5 ToF4M (VL53L1X) 0x29 — mast height
static constexpr uint8_t kChExtEncoder = 3;   // ExtEncoder 0x59 (currently absent)
static constexpr uint8_t kChPinpoint   = 4;   // Pinpoint 0x31 measured on ch4 (was 0)
static constexpr uint8_t kChOtos       = 5;   // OTOS     0x17 measured on ch5 (was 4)

// goBILDA PRISM RGB LED driver (Port A I2C). Protocol from the goBILDA_Prism
// Arduino library: writes are [register, payload...]. CONTROL=6 carries strip
// length/clear; per-layer animation regs live at ANIMATION_SLOT_0(8)+layer.
static constexpr uint8_t kPrismAddr        = 0x38;
static constexpr uint8_t kPrismRegControl  = 6;   // CONTROL
static constexpr uint8_t kPrismRegLayer0   = 8;   // ANIMATION_SLOT_0 (LAYER_0)
static constexpr uint8_t kPrismAnimSolid   = 1;   // AnimationType::SOLID
// Per-layer animation sub-registers:
static constexpr uint8_t kPrismSubSelect   = 0x00;  // AnimationSelection
static constexpr uint8_t kPrismSubBright   = 0x01;  // Brightness (0-100)
static constexpr uint8_t kPrismSubStart    = 0x02;  // StartIndex
static constexpr uint8_t kPrismSubStop     = 0x03;  // StopIndex
static constexpr uint8_t kPrismSubPrimary  = 0x04;  // PrimaryColor (r,g,b)
// PRISM strip = 48 LEDs, daisy-chained. Segment map (order from the strip start = RIGHT FRONT):
//   [ 0.. 5] RIGHT FRONT  (6)   [ 6..11] RIGHT REAR (6)   [12..23] CENTER REAR  (12)
//   [24..29] LEFT REAR    (6)   [30..35] LEFT FRONT (6)   [36..47] CENTER FRONT (12)
// Whole-strip solid color (state) lights all 48; per-segment control (turn signals / per-side
// status) uses these index ranges via the layer start/stop registers — TODO.
static uint8_t prism_strip_len = 48;

m5::I2C_Class* extI2C = nullptr;

// Expected device map behind the PaHUB (channel, addr, name) — drives the scan
// report + per-device channel routing.
struct HubDevice { uint8_t channel; uint8_t addr; const char* name; };
static const HubDevice kHubDevices[] = {
    {kChPrism,      kPrismAddr,      "PRISM_LED"},
    {kChServo8,     kServo8Addr,     "8Servo"},
    {kChEnv,        kEnvAddr,        "ENVIII_z0"},   // ch2 — regen clamp resistor
    {kChExtEncoder, kEnvAddr,        "ENVIII_z1"},   // ch3 — 2nd probe (not yet wired; ExtEncoder deferred)
    {kChPinpoint,   kPinpointAddr,   "Pinpoint"},
    {kChOtos,       kOtosAddr,       "OTOS"},
};
static bool g_paHubPresent = false;   // detected at boot
static int  g_currentHubCh = -1;      // last-selected channel (cache)

// Defined further down; forward-declared so selectChannel() can use them.
bool probeI2CAddress(m5::I2C_Class& i2c, uint8_t addr);
bool selectPaHubChannel(m5::I2C_Class& i2c, uint8_t channel);

// Route the external bus to a device's PaHUB channel before access. No-op when no
// hub was detected (flat wiring). Caches the current channel to skip redundant selects.
bool selectChannel(uint8_t channel)
{
    if (!g_paHubPresent) return true;
    if (g_currentHubCh == static_cast<int>(channel)) return true;
    if (selectPaHubChannel(*extI2C, channel)) { g_currentHubCh = channel; return true; }
    g_currentHubCh = -1;
    return false;
}

struct UartProfile {
    const char* name;
    uint32_t baud;
    uint32_t config;
    bool inverted;
};

static const UartProfile kProfiles[] = {
    {"SBUS (inverted)", 100000, SERIAL_8E2, true},
};

// Per-frame [SBUS] OK line is debug only (RC telemetry the dashboard uses comes from [RC]
// @10Hz + the 1 Hz [SBUS] status line). At ~83 frames/s, printing every frame was ~83
// lines/s — the dominant serial firehose that filled the USB-CDC buffer and stalled the
// loop on Serial.printf whenever the host reader hiccuped. Throttle to ~2/s.
static constexpr uint16_t kSbusPrintEveryNFrames = 40;
static constexpr uint32_t kPdhSetSwitchChannelFrameBase = 0x8050840u;
static constexpr uint32_t kPdhStatus0FrameBase          = 0x8051800u;
static constexpr uint32_t kPdhStatus1FrameBase          = 0x8051840u;
static constexpr uint32_t kPdhStatus2FrameBase          = 0x8051880u;
static constexpr uint32_t kPdhStatus3FrameBase          = 0x80518C0u;
static constexpr uint32_t kPdhStatus4FrameBase          = 0x8051900u;
static constexpr uint32_t kPdhClearFaultsFrameBase      = 0x8051B80u;
static constexpr uint32_t kPdhFrameApiMask              = 0x1FFFFFC0u;
static constexpr uint32_t kPdhDeviceIdMask              = 0x3Fu;
static constexpr uint8_t kCanMaxMsgsPerLoop             = 8;
static constexpr uint32_t kCanBudgetUs                  = 700;

HardwareSerial& rc = Serial2;
// Latest decoded RC state, mirrored from consumeByte() for the local UI.
static uint16_t rc_ch[16] = {0};
static bool     rc_failsafe = false;
static bool     rc_frame_lost = false;
static bool     rc_range_bad = false;   // a drive stick pegged outside the plausible SBUS band → treat as failsafe

static inline bool sbusInBand(uint16_t v) { return v >= kSbusValidLo && v <= kSbusValidHi; }

// True only if the USB-CDC TX buffer has room for a full line — so a high-rate telemetry
// print can be SKIPPED (dropped) rather than blocking the control loop when the host reader
// lags. The dashboard keeps last values, so an occasional dropped line is harmless.
static inline bool txReady(int need) { return Serial.availableForWrite() >= need; }
unsigned long last_valid_frame_ms = 0;
unsigned long last_status_ms = 0;
unsigned long last_i2c_scan_ms = 0;
unsigned long last_sens_ms = 0;
unsigned long last_can_status_ms = 0;
unsigned long last_can_rx_ms = 0;
unsigned long last_pdh_pub_ms = 0;
unsigned long last_io_pub_ms = 0;
unsigned long total_bytes = 0;
uint32_t valid_frames = 0;
uint32_t invalid_frames = 0;
uint32_t can_rx_frames = 0;
uint32_t can_rx_std_unique = 0;
uint32_t can_rx_ext_unique = 0;
uint8_t pdh_device_id = 1;
bool pdh_id_locked = false;

struct PdhState {
    bool s0_seen = false;
    bool s1_seen = false;
    bool s2_seen = false;
    bool s3_seen = false;
    bool s4_seen = false;
    float channel_a[24] = {0.0f};
    bool breaker_fault[24] = {false};   // live per-channel breaker/fuse fault (Status0-3)
    uint32_t sticky_breaker = 0;        // latched per-channel breaker faults (Status4)
    float v_bus = 0.0f;
    float total_current = 0.0f;
    float total_power = 0.0f;
    float energy_wh = 0.0f;
    bool switch_channel = false;
    bool brownout_fault = false;
    bool can_warning_fault = false;
    bool hardware_fault = false;
    unsigned long last_update_ms = 0;
    unsigned long last_s4_ms = 0;
};

PdhState pdh;

static bool can_std_seen[2048] = {false};
static uint32_t can_ext_seen[64] = {0};
static uint8_t can_ext_seen_count = 0;
char host_cmd_buf[96] = {0};
size_t host_cmd_len = 0;

uint8_t frame_buf[25];
size_t frame_pos = 0;

// ================= Local UI: onboard screen + buttons + status LED =================
// Shows safety + health straight from THIS MCU — valid even when Thor / ROS / the
// dashboard is down. Deliberately NOT a copy of the touchscreen. Safety rule: buttons
// may only DE-energize (BtnC hold = local e-stop); re-arm stays host/dashboard-guarded.
static M5Canvas*     ui_canvas          = nullptr;
static int           ui_page            = 0;      // 0 = HEALTH, 1 = SAFETY
static const int     kUiPages           = 2;
static unsigned long ui_last_draw_ms    = 0;
unsigned long        last_host_cmd_ms   = 0;       // 0 = host never seen (bench/headless)
static bool          local_estop_active = false;   // latched by BtnC; cleared by host re-arm
static bool          estopc_down        = false;
static bool          estopc_fired       = false;
static unsigned long estopc_start_ms    = 0;
static const uint32_t kEstopHoldMs      = 1500;    // hold-to-confirm to avoid accidental trip
static unsigned long identify_until_ms  = 0;
static uint32_t      ui_last_fault_mask = 0;

// --- PoE W5500 Ethernet state ---
static bool           eth_up = false;
static unsigned long  last_eth_ms = 0;
static EthernetUDP     eth_udp;
static IPAddress       eth_bcast;
static char            eth_ip_str[20] = "--";
static unsigned long   last_net_tx = 0;
static uint8_t        eth_mac[6]  = {0};      // filled in setup() from the eFuse MAC; reused for self-heal re-init
static IPAddress      eth_cfg_ip, eth_cfg_dns, eth_cfg_gw, eth_cfg_subnet;  // DHCP-acquired, re-asserted statically on self-heal
static bool           eth_have_cfg = false;   // we have a captured address to re-assert
static uint16_t       eth_reinit_count = 0;   // self-heal re-inits since boot (observability)
static uint8_t        eth_link_down_ticks = 0;
static unsigned long  last_eth_reinit_ms = 0;
static unsigned long  last_hb_rx_ms = 0;       // last time we RECEIVED a Thor "HB" over UDP (RX proof-of-life)
static bool           hb_seen = false;         // we've heard at least one HB → the watchdog is armed
static constexpr uint32_t kHbTimeoutMs = 6000; // no HB for this long (while link reads up) ⇒ RX wedged ⇒ re-init
static uint32_t        net_seq = 0;
static const uint16_t  kNetPort = 5005;

static inline uint32_t unpackBitsLE(const uint8_t* data, uint8_t start_bit, uint8_t bit_len)
{
    uint32_t out = 0;
    for (uint8_t i = 0; i < bit_len; ++i) {
        const uint8_t bit = start_bit + i;
        if (data[bit / 8] & (1u << (bit % 8))) {
            out |= (1u << i);
        }
    }
    return out;
}

static inline bool isPdhFrame(uint32_t id, uint32_t base)
{
    return (id & kPdhFrameApiMask) == base;
}

void publishPdhSummary()
{
    tprintf("[PDH] stat id=%u v=%.3fV i=%.1fA p=%.1fW e=%.3fWh sw=%d brn=%d can=%d hw=%d seen=%d%d%d%d%d\n",
                  static_cast<unsigned>(pdh_device_id),
                  pdh.v_bus,
                  pdh.total_current,
                  pdh.total_power,
                  pdh.energy_wh,
                  pdh.switch_channel ? 1 : 0,
                  pdh.brownout_fault ? 1 : 0,
                  pdh.can_warning_fault ? 1 : 0,
                  pdh.hardware_fault ? 1 : 0,
                  pdh.s0_seen ? 1 : 0,
                  pdh.s1_seen ? 1 : 0,
                  pdh.s2_seen ? 1 : 0,
                  pdh.s3_seen ? 1 : 0,
                  pdh.s4_seen ? 1 : 0);

    tprintf("[PDH] ch c0=%.3f c1=%.3f c2=%.3f c3=%.3f c4=%.3f c5=%.3f c6=%.3f c7=%.3f c8=%.3f c9=%.3f c10=%.3f c11=%.3f c12=%.3f c13=%.3f c14=%.3f c15=%.3f c16=%.3f c17=%.3f c18=%.3f c19=%.3f c20=%.3f c21=%.3f c22=%.3f c23=%.3f\n",
                  pdh.channel_a[0], pdh.channel_a[1], pdh.channel_a[2], pdh.channel_a[3], pdh.channel_a[4], pdh.channel_a[5],
                  pdh.channel_a[6], pdh.channel_a[7], pdh.channel_a[8], pdh.channel_a[9], pdh.channel_a[10], pdh.channel_a[11],
                  pdh.channel_a[12], pdh.channel_a[13], pdh.channel_a[14], pdh.channel_a[15], pdh.channel_a[16], pdh.channel_a[17],
                  pdh.channel_a[18], pdh.channel_a[19], pdh.channel_a[20], pdh.channel_a[21], pdh.channel_a[22], pdh.channel_a[23]);

    uint32_t live = 0;
    for (int i = 0; i < 24; ++i)
        if (pdh.breaker_fault[i]) live |= (1u << i);
    tprintf("[PDH] flt live=0x%06lX sticky=0x%06lX\n",
                  static_cast<unsigned long>(live),
                  static_cast<unsigned long>(pdh.sticky_breaker));
}

bool sendPdhSwitchCommand(bool enabled)
{
    twai_message_t tx = {};
    tx.identifier = (kPdhSetSwitchChannelFrameBase | (pdh_device_id & kPdhDeviceIdMask));
    tx.extd = 1;
    tx.rtr = 0;
    tx.data_length_code = 1;
    tx.data[0] = enabled ? 1 : 0;
    return twai_transmit(&tx, 0) == ESP_OK;
}

bool sendPdhClearFaultsCommand()
{
    twai_message_t tx = {};
    tx.identifier = (kPdhClearFaultsFrameBase | (pdh_device_id & kPdhDeviceIdMask));
    tx.extd = 1;
    tx.rtr = 0;
    tx.data_length_code = 0;
    return twai_transmit(&tx, 0) == ESP_OK;
}

bool readExtEncoder(int32_t& out)
{
    if (extI2C == nullptr || !extI2C->isEnabled()) return false;
    if (!selectChannel(kChExtEncoder)) return false;
    uint8_t b[4];
    if (!extI2C->readRegister(kExtEncoderAddr, kExtEncoderCountReg, b, 4, 400000)) return false;
    out = static_cast<int32_t>(static_cast<uint32_t>(b[0]) | (static_cast<uint32_t>(b[1]) << 8) |
                               (static_cast<uint32_t>(b[2]) << 16) | (static_cast<uint32_t>(b[3]) << 24));
    return true;
}

// ================= goBILDA Pinpoint odometry (0x31, PaHub ch4) =================
// Protocol per goBILDA's official Arduino library. Registers are INDEXED 4-BYTE WORDS: write the
// one-byte register index, then read 4 bytes little-endian. Pose/velocity are IEEE-754 floats
// (position in mm, heading in radians); encoder counts and status are integers. Newer Pinpoint
// firmware appends a CRC8 byte — we request only 4 bytes and simply never read it, which is
// harmless. DEVICE_ID reads 2 on a genuine Pinpoint; we use that to confirm identity.
static constexpr uint8_t kPpRegDeviceId  = 1;
static constexpr uint8_t kPpRegStatus    = 3;
static constexpr uint8_t kPpRegLoopTime  = 5;
static constexpr uint8_t kPpRegEncX      = 6;
static constexpr uint8_t kPpRegEncY      = 7;
static constexpr uint8_t kPpRegPosX      = 8;
static constexpr uint8_t kPpRegPosY      = 9;
static constexpr uint8_t kPpRegHeading   = 10;
static constexpr uint8_t kPpRegVelX      = 11;
static constexpr uint8_t kPpRegVelY      = 12;
static constexpr uint8_t kPpRegVelH      = 13;
static constexpr uint8_t kPpDeviceIdVal  = 2;

// Cached last-good Pinpoint state (published even when a frame is missed, with ok=0).
static bool     pp_present = false;
static uint32_t pp_status = 0, pp_loop_us = 0, pp_fails = 0;
static int32_t  pp_encx = 0, pp_ency = 0;
static float    pp_x = 0, pp_y = 0, pp_h = 0, pp_vx = 0, pp_vy = 0, pp_vh = 0;

// The Pinpoint requires a STOP between the register-pointer write and the read -- goBILDA's own
// library does endTransmission(TRUE). readRegister() issues a repeated start instead, which the
// device does not answer (the address probe still ACKs, so it looks present but every register
// read fails). Compose the two transactions explicitly. Also 100 kHz, not 400: the Pinpoint sits
// behind the 3.3/5 V level shifter, where fast edges are marginal.
static constexpr uint32_t kPpHz = 100000;

// CRC-8, poly 0x31, init 0x00, MSB-first — matches goBILDA's crc8_table (CRC(0x01)==0x31).
// Newer Pinpoint firmware appends this byte after the 4 data bytes. Without checking it,
// occasional corrupt frames sail through as plausible-looking floats (we saw y=-nan and
// loop=4294967143), which is far worse than a dropped frame.
static uint8_t ppCrc8(const uint8_t *d, size_t n)
{
    uint8_t c = 0;
    for (size_t i = 0; i < n; ++i) {
        c ^= d[i];
        for (int b = 0; b < 8; ++b) c = (c & 0x80) ? (uint8_t)((c << 1) ^ 0x31) : (uint8_t)(c << 1);
    }
    return c;
}

static int pp_crc_mode = -1;      // -1 = probe, 1 = device sends CRC, 0 = it does not
static uint32_t pp_crc_bad = 0;

static bool ppXfer(uint8_t reg, uint8_t *buf, uint8_t n)
{
    if (!extI2C->start(kPinpointAddr, false, kPpHz)) return false;   // write mode
    if (!extI2C->write(reg)) { extI2C->stop(); return false; }
    extI2C->stop();                                                   // STOP, not repeated start
    if (!extI2C->start(kPinpointAddr, true, kPpHz)) return false;     // read mode
    const bool ok = extI2C->read(buf, n, true);
    extI2C->stop();
    return ok;
}

static bool ppRead32(uint8_t reg, uint8_t *b4)
{
    if (extI2C == nullptr || !extI2C->isEnabled()) return false;
    if (!selectChannel(kChPinpoint)) return false;

    if (pp_crc_mode != 0) {                       // try the 5-byte (data+CRC) form
        uint8_t b5[5];
        if (ppXfer(reg, b5, 5)) {
            if (ppCrc8(b5, 4) == b5[4]) { memcpy(b4, b5, 4); pp_crc_mode = 1; return true; }
            if (pp_crc_mode == 1) { pp_crc_bad++; return false; }   // known-CRC device: reject
        }
        if (pp_crc_mode == -1) pp_crc_mode = 0;   // no CRC on this firmware — fall through
    }
    return ppXfer(reg, b4, 4);
}

static bool ppU32(uint8_t reg, uint32_t *out)
{
    uint8_t b[4]; if (!ppRead32(reg, b)) return false;
    *out = (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24);
    return true;
}
static bool ppI32(uint8_t reg, int32_t *out)
{ uint32_t u; if (!ppU32(reg, &u)) return false; *out = (int32_t)u; return true; }
static bool ppF32(uint8_t reg, float *out)
{ uint32_t u; if (!ppU32(reg, &u)) return false; memcpy(out, &u, sizeof(u)); return true; }

// One poll of the whole pose. Any failed register aborts the frame so we never publish a
// half-updated pose (mixing a new X with a stale Y would look like a real jump).
static void pinpointPoll()
{
    uint32_t id = 0;
    if (!ppU32(kPpRegDeviceId, &id) || id != kPpDeviceIdVal) { pp_present = false; pp_fails++; return; }
    uint32_t st = 0, lt = 0; int32_t ex = 0, ey = 0;
    float x = 0, y = 0, h = 0, vx = 0, vy = 0, vh = 0;
    const bool ok = ppU32(kPpRegStatus, &st) && ppU32(kPpRegLoopTime, &lt)
                 && ppI32(kPpRegEncX, &ex)   && ppI32(kPpRegEncY, &ey)
                 && ppF32(kPpRegPosX, &x)    && ppF32(kPpRegPosY, &y)  && ppF32(kPpRegHeading, &h)
                 && ppF32(kPpRegVelX, &vx)   && ppF32(kPpRegVelY, &vy) && ppF32(kPpRegVelH, &vh);
    if (!ok) { pp_present = false; pp_fails++; return; }
    pp_present = true; pp_status = st; pp_loop_us = lt;
    pp_encx = ex; pp_ency = ey;
    pp_x = x; pp_y = y; pp_h = h; pp_vx = vx; pp_vy = vy; pp_vh = vh;
}

// [ODO] — pose in mm / rad, plus the raw pod counts and the device status bitfield.
// Status bits (goBILDA): b0 !ready, b1 calibrating, b2 X pod, b3 Y pod, b4 IMU runaway,
// b5 bad write CRC, b6 bad read.
static void publishOdo()
{
    // DEVICE_STATUS bits per goBILDA's FTC driver enum (authoritative):
    //   b0 READY | b1 CALIBRATING | b2 FAULT_X_POD_NOT_DETECTED | b3 FAULT_Y_POD_NOT_DETECTED
    //   b4 FAULT_IMU_RUNAWAY | b5 FAULT_BAD_READ
    // NOTE the pod bits are FAULT flags: SET means the pod is MISSING, not present.
    tprintf("[ODO] ok=%d st=0x%02X rdy=%d cal=%d xpod=%d ypod=%d imurun=%d badread=%d "
            "x=%.1f y=%.1f h=%.4f vx=%.1f vy=%.1f vh=%.4f "
            "ex=%ld ey=%ld loop=%lu fail=%lu crcbad=%lu\n",
            pp_present ? 1 : 0, (unsigned)(pp_status & 0xFF),
            (pp_status & 0x01) ? 1 : 0, (pp_status & 0x02) ? 1 : 0,
            (pp_status & 0x04) ? 0 : 1, (pp_status & 0x08) ? 0 : 1,
            (pp_status & 0x10) ? 1 : 0, (pp_status & 0x20) ? 1 : 0,
            pp_x, pp_y, pp_h, pp_vx, pp_vy, pp_vh,
            (long)pp_encx, (long)pp_ency,
            (unsigned long)pp_loop_us, (unsigned long)pp_fails, (unsigned long)pp_crc_bad);
}

void publishIo()
{
    // No txReady() gate: tprintf() self-guards the USB write + always tees over Ethernet.
    uint8_t di = 0;
    for (uint8_t i = 0; i < 8; ++i)
        if (M5StamPLC.readPlcInput(i)) di |= (1u << i);
    uint8_t relay = 0;
    for (uint8_t i = 0; i < 4; ++i)
        if (M5StamPLC.readPlcRelay(i)) relay |= (1u << i);
    tprintf("[IO] di=0x%02X relay=0x%01X temp=%.1f isocket=%.3f\n",
                  di, relay, M5StamPLC.getTemp(), M5StamPLC.getIoSocketOutputCurrent());

    int32_t enc = 0;
    if (readExtEncoder(enc))
        tprintf("[ENC] mast=%ld present=1\n", static_cast<long>(enc));
    else
        Serial.println("[ENC] mast=0 present=0");
}

// --- goBILDA PRISM RGB driver (raw I2C, replicating goBILDA_Prism lib) ---
bool prismWrite(uint8_t reg, const uint8_t* data, size_t len)
{
    if (extI2C == nullptr || !extI2C->isEnabled()) return false;
    if (!selectChannel(kChPrism)) return false;
    return extI2C->writeRegister(kPrismAddr, reg, data, len, 400000);
}

bool prismSetStripLength(uint8_t len)
{
    prism_strip_len = len;
    const uint8_t d[4] = {0, 0, len, 0x01};   // CONTROL: bit24 + length
    return prismWrite(kPrismRegControl, d, sizeof(d));
}

// Last base color pushed to LAYER_0 (by the Thor via LED_COLOR). Remembered so the turn-signal
// overlay on LAYER_1 can be repainted with THIS color to hide it — see prismOverlayHide().
static uint8_t g_baseR = 0, g_baseG = 0, g_baseB = 0, g_baseBr = 100;
// Who currently owns the LAYER_1 overlay. Declared here because prismSolid() has to know
// whether repainting the hidden overlay would stomp a live hazard signal.
enum class PrismOverlay : uint8_t { NONE = 0, TURN, MAST_MOVE, MAST_HOLD };
static PrismOverlay g_overlay = PrismOverlay::NONE;
static void prismOverlayHide();   // fwd decl (defined with the overlay block below)

// Fill the whole strip with one solid color via a Solid animation on LAYER_0.
bool prismSolid(uint8_t r, uint8_t g, uint8_t b, uint8_t bright)
{
    if (bright > 100) bright = 100;
    const uint8_t stop = prism_strip_len ? (uint8_t)(prism_strip_len - 1) : 0;
    bool ok = true;
    uint8_t sel[2]   = {kPrismSubSelect, kPrismAnimSolid};   ok &= prismWrite(kPrismRegLayer0, sel, 2);   delay(5);
    uint8_t br[2]    = {kPrismSubBright, bright};            ok &= prismWrite(kPrismRegLayer0, br, 2);    delay(5);
    uint8_t st[2]    = {kPrismSubStart, 0};                  ok &= prismWrite(kPrismRegLayer0, st, 2);    delay(5);
    uint8_t sp[2]    = {kPrismSubStop, stop};                ok &= prismWrite(kPrismRegLayer0, sp, 2);    delay(5);
    uint8_t col[4]   = {kPrismSubPrimary, r, g, b};          ok &= prismWrite(kPrismRegLayer0, col, 4);   delay(5);
    g_baseR = r; g_baseG = g; g_baseB = b; g_baseBr = bright;
    // Keep the (invisible) LAYER_1 overlay in sync with the new base so a later overlay-OFF
    // reveals THIS color, not a stale one. Skip while an overlay is live — that is a hazard
    // signal, don't stomp it.
    if (g_overlay == PrismOverlay::NONE) prismOverlayHide();
    return ok;
}

bool prismClear()
{
    const uint8_t d[4] = {0, 0, 0, 0x02};   // CONTROL: clear all animations
    return prismWrite(kPrismRegControl, d, sizeof(d));
}

// --- PRISM overlay on LAYER_1: turn-signals AND the Z-axis hazard, arbitrated ---
//
// LAYER_1 sits above the state base color on LAYER_0, and there is exactly one of it, so
// the two things that want to flash have to be arbitrated rather than both writing. The
// order is by hazard, not by who asked first:
//
//   MAST_HOLD   red strobe,  whole strip   descent stopped just above full-down, waiting
//                                          for the operator to consent to the last stretch
//   MAST_MOVE   amber blink, whole strip   the lift is travelling — DOWN blinks twice as
//                                          fast as UP, because descent is the direction
//                                          that closes the gap on a hand
//   TURN        amber blink, one side      steering intent, as before
//
// The Z-axis outranks the turn-signal, and takes the WHOLE strip, because the pinch hazard
// has no side: someone standing behind the robot is as exposed as the operator. It also
// moves while the robot is otherwise still and silent, which is exactly when a bystander
// has no other cue. Losing the turn-signal for the few seconds the lift is moving costs
// nothing — in that moment the robot is a hazard for a different reason, and that is the
// thing worth saying.
//
// Flash by toggling ONLY the layer brightness (one I2C write) so it never blocks the RC
// loop; segment range and color are (re)written only when the owner or its shape changes.
static constexpr uint8_t kPrismRegLayer1 = kPrismRegLayer0 + 1;   // ANIMATION_SLOT_1
static constexpr uint8_t kPrismAnimNone  = 0;                     // transparent
static constexpr uint8_t kTurnBright     = 90;
// Blink half-periods, ms. Tune by eye — these are readability values, not measurements.
static constexpr uint32_t kBlinkTurn     = 380;
static constexpr uint32_t kBlinkMastUp   = 400;
static constexpr uint32_t kBlinkMastDown = 200;   // descent: deliberately more urgent
static constexpr uint32_t kBlinkMastHold = 110;   // strobe — "this is waiting on you"

static void prismOverlayConfig(uint8_t start, uint8_t stop,
                               uint8_t r, uint8_t g, uint8_t b, uint8_t bright)
{
    uint8_t sel[2] = {kPrismSubSelect, kPrismAnimSolid};   prismWrite(kPrismRegLayer1, sel, 2); delay(3);
    uint8_t st[2]  = {kPrismSubStart, start};              prismWrite(kPrismRegLayer1, st, 2);  delay(3);
    uint8_t sp[2]  = {kPrismSubStop, stop};                prismWrite(kPrismRegLayer1, sp, 2);  delay(3);
    uint8_t col[4] = {kPrismSubPrimary, r, g, b};          prismWrite(kPrismRegLayer1, col, 4); delay(3);
    uint8_t br[2]  = {kPrismSubBright, bright};            prismWrite(kPrismRegLayer1, br, 2);
}
static void prismOverlayBright(uint8_t bright) { uint8_t br[2] = {kPrismSubBright, bright}; prismWrite(kPrismRegLayer1, br, 2); }

// Hide the overlay by repainting LAYER_1 with the CURRENT base color over the whole strip, so it
// matches LAYER_0 and disappears. On this PRISM, select=NONE does NOT clear a layer's last render
// (the amber sticks), so we paint-to-match instead of relying on a "none" opcode. (void)kPrismAnimNone.
static void prismOverlayHide()
{
    const uint8_t stop = prism_strip_len ? (uint8_t)(prism_strip_len - 1) : 0;
    prismOverlayConfig(0, stop, g_baseR, g_baseG, g_baseB, g_baseBr);
}

void scanI2CFull();  // defined below; invoked on-demand by the I2C_SCAN command

// --- M5 ENV III (SHT30 @0x44) — MULTI-ZONE, non-blocking single-shot reads. ---
// One ENVIII per PaHub channel (both at 0x44, mux-isolated). Round-robins the zones, one
// two-phase read at a time (issue measure, then read ~16 ms later on a later loop) so it NEVER
// blocks the 50 Hz control loop / SBUS servicing (would risk [RC] gaps). An absent zone (e.g.
// z1 until it's wired) fails the measure fast and is skipped silently — it simply starts
// appearing in [ENV] the moment it's plugged in. Emits "[ENV] z=<zone> t=<C> h=<%>".
struct EnvZone { uint8_t zone; uint8_t channel; };
static const EnvZone   kEnvZones[]    = { {0, kChEnv}, {1, kChExtEncoder} };  // z0=ch2 resistor, z1=ch3 (2nd probe)
static const int       kNumEnvZones   = (int)(sizeof(kEnvZones) / sizeof(kEnvZones[0]));
static const uint32_t  kEnvIntervalMs = 500;   // per-zone service gate → each present zone ≈1 Hz

static int           env_idx     = 0;   // zone currently being serviced
static uint8_t       env_phase   = 0;   // 0 = idle/wait, 1 = measurement in flight
static unsigned long env_meas_ms = 0;
static unsigned long last_env_ms = 0;

void envStep()
{
    if (extI2C == nullptr || !extI2C->isEnabled()) return;
    const EnvZone& z = kEnvZones[env_idx];
    if (env_phase == 0) {
        if (millis() - last_env_ms < kEnvIntervalMs) return;
        // 0x24,0x00 = single-shot, high repeatability, clock stretching DISABLED (non-blocking).
        if (!selectChannel(z.channel) || !extI2C->writeRegister8(kEnvAddr, 0x24, 0x00, 100000)) {
            last_env_ms = millis();
            env_idx = (env_idx + 1) % kNumEnvZones;   // sensor absent on this channel → skip quietly
            return;
        }
        env_meas_ms = millis();
        env_phase   = 1;
        return;
    }
    if (millis() - env_meas_ms < 16) return;   // wait out the conversion (~15 ms high-rep)
    env_phase   = 0;
    last_env_ms = millis();
    const uint8_t zone = z.zone;
    env_idx = (env_idx + 1) % kNumEnvZones;
    uint8_t rx[6];
    bool ok = false;
    if (selectChannel(z.channel) && extI2C->start(kEnvAddr, true, 100000)) {
        ok = extI2C->read(rx, sizeof(rx), true);
        extI2C->stop();
    }
    if (!ok) return;                            // transient/absent — keep dashboard's last value, no spam
    const uint16_t rawT = ((uint16_t)rx[0] << 8) | rx[1];   // rx[2] = CRC (not checked)
    const uint16_t rawH = ((uint16_t)rx[3] << 8) | rx[4];   // rx[5] = CRC
    const float tC = -45.0f + 175.0f * (rawT / 65535.0f);
    const float rh = 100.0f  * (rawH / 65535.0f);
    if (tC < -40.0f || tC > 125.0f) return;
    tprintf("[ENV] z=%u t=%.2f h=%.1f\n", (unsigned)zone, tC, rh);
}

// 8Servo per-pin MODE cache. The pin's mode register (SERVO_CTL) latches in the 8Servo until it is
// reset, so writing it on EVERY pulse (4 channels × 50 Hz = 400 extra I2C txns/s) was pure overhead
// — and a leading cause of the `s8fail` I2C-write drops that track the ESC red-fault at idle. Set
// each pin's mode once; re-assert only after a write failure or an explicit 8Servo power-cycle.
static uint8_t g_servo8ModeSet = 0;                                  // bit p set ⇒ pin p already in SERVO_CTL mode
static inline void servo8InvalidateModes() { g_servo8ModeSet = 0; } // force mode re-assert (In_I2C re-init / S8RESET)

// ---- ToF4M / VL53L1X (mast height) ------------------------------------------------
// NOTE the VL53L1X uses SIXTEEN-BIT register addresses, unlike the VL53L0X on the Tab5
// (8-bit). A driver for one will not read the other, and the ID read below is what tells
// them apart: VL53L1X answers EA CC 10 at 0x010F; VL53L0X answers EE AA 10 at 0xC0.
//
// STOP-separated, not a repeated start — the same thing Pinpoint needed on this bus, and
// the thing that wedged the 8Servo when we tried a repeated start there.
static constexpr uint32_t kTofHz = 100000;

static bool tofRead16(uint16_t reg, uint8_t *buf, uint8_t n)
{
    if (extI2C == nullptr || !extI2C->isEnabled()) return false;
    if (!selectChannel(kChTof)) return false;
    if (!extI2C->start(kTofAddr, false, kTofHz)) { extI2C->stop(); return false; }
    if (!extI2C->write((uint8_t)(reg >> 8)))     { extI2C->stop(); return false; }
    if (!extI2C->write((uint8_t)(reg & 0xFF)))   { extI2C->stop(); return false; }
    extI2C->stop();
    if (!extI2C->start(kTofAddr, true, kTofHz))  { extI2C->stop(); return false; }
    const bool ok = extI2C->read(buf, n, true);
    extI2C->stop();
    return ok;
}

// Confirm the sensor is on ch3 and is the part we think it is, before any driver work.
static void tofProbe()
{
    if (!selectChannel(kChTof)) { tprintf("[TOF] mux ch3 select FAILED\n"); return; }
    const bool ack = probeI2CAddress(*extI2C, kTofAddr);
    tprintf("[TOF] ch%u addr 0x%02X: %s\n", kChTof, kTofAddr, ack ? "ACK" : "no response");
    if (!ack) { tprintf("[TOF] check wiring/power before anything else\n"); return; }
    uint8_t id[3] = {0};
    if (!tofRead16(0x010F, id, 3)) { tprintf("[TOF] ID read FAILED\n"); return; }
    tprintf("[TOF] ID @0x010F: %02X %02X %02X  -> %s\n", id[0], id[1], id[2],
                  (id[0] == 0xEA) ? "VL53L1X (ToF4M) as expected"
                                  : "NOT a VL53L1X — check the part");
}

// ---- Z-axis (mast / lift) -------------------------------------------------------
// R2/R3 are the spare relays; NO contacts, so energized = pressed. WHICH relay drives
// which leg is a per-robot wiring fact, discovered on the bench with `MAST UP|DOWN` --
// swap these two if the bench says they are the other way round.
// Bench-discovered on opendubs2 2026-09-04 by pulsing each relay and watching the lift:
//   code R2 (SILK R3) = UP     code R1 (SILK R2) = DOWN
// Silk is 1-indexed and code is 0-indexed -- that offset cost us a debugging cycle, so
// both are written out here. Re-discover on any new build; it depends only on which of
// two wires was landed.
static constexpr uint8_t kRelayMastUp   = 2;   // silk R3
static constexpr uint8_t kRelayMastDown = 1;   // silk R2
// Height source: the ToF on PaHub ch3. Returns false until the sensor is BOTH answering and
// calibrated (TOFCAL ZERO at the bottom limit), which leaves the lift open-loop but working
// -- enough for relay bring-up. Belief correction and the pinch guard switch themselves on
// the moment this starts returning readings, and off again if it stops.
static ScissorRelayMast g_mast(kRelayMastUp, kRelayMastDown, tofHeightMm);

// Drive the LAYER_1 overlay from robot state. Defined here rather than with the other PRISM
// code because it is the one part that has to see the mast driver. Call every loop.
void prismOverlayStep()
{
    // Who owns the layer this tick, and what it looks like.
    const uint8_t last = prism_strip_len ? (uint8_t)(prism_strip_len - 1) : 0;
    PrismOverlay want = PrismOverlay::NONE;
    uint8_t start = 0, stop = last, r = 255, g = 96, b = 0, bright = kTurnBright;
    uint32_t half = kBlinkTurn;

    // Bench override (MASTTEST) — same code path as the real thing, so what you tune by eye
    // is what you get in service.
    const uint8_t mt = (millis() < g_mastTestUntil) ? g_mastTest : 0;
    if (mt == 3 || g_mast.pinchHold()) {
        want = PrismOverlay::MAST_HOLD;
        r = 255; g = 0; b = 0; bright = 100; half = kBlinkMastHold;
    } else if (mt != 0 || g_mast.motion() != MastMotion::STOP) {
        want = PrismOverlay::MAST_MOVE;
        const bool down = mt ? (mt == 2) : (g_mast.motion() == MastMotion::DOWN);
        half = down ? kBlinkMastDown : kBlinkMastUp;
    } else if (g_turnDir != 0) {
        want = PrismOverlay::TURN;
        start = g_turnDir > 0 ? 0 : 24;
        stop  = g_turnDir > 0 ? 11 : 35;
    }

    // Reconfigure only when the picture actually changes — the flash itself is one write.
    static uint8_t prevStart = 255, prevStop = 255, prevR = 0, prevG = 0, prevB = 0;
    static bool     flashOn = false;
    static uint32_t flashMs = 0;
    const bool shapeChanged = (want != g_overlay) || start != prevStart || stop != prevStop
                              || r != prevR || g != prevG || b != prevB;
    if (shapeChanged) {
        g_overlay = want;
        prevStart = start; prevStop = stop; prevR = r; prevG = g; prevB = b;
        if (want == PrismOverlay::NONE) prismOverlayHide();
        else { prismOverlayConfig(start, stop, r, g, b, bright); flashOn = true; flashMs = millis(); }
    }
    if (want != PrismOverlay::NONE && millis() - flashMs > half) {
        flashMs = millis();
        flashOn = !flashOn;
        prismOverlayBright(flashOn ? bright : 0);
    }
}

// ---- I2C bus recovery ---------------------------------------------------------
// M5Unified's writeRegister() is a short-circuiting && chain:
//     beginTransaction(..) && writeBytes(&reg,1) && writeBytes(data,len) && endTransaction(..)
// If ANY step fails, endTransaction() is never reached, so NO STOP is issued and the
// slave is abandoned mid-transaction. The 8Servo's STM32F030F4 latches in exactly that
// state: it keeps ACKing its address in hardware but never services data again, and every
// subsequent write fails. That is what turns a single glitched write into a permanent
// wedge — and why Pinpoint survives on the same bus, since ppXfer() stops on every path.
static uint32_t      g_i2cRecoverCount = 0;   // bus recoveries since boot (observability)
static uint8_t       g_s8ConsecFail    = 0;   // consecutive 8Servo write failures
static unsigned long g_lastRecover_ms  = 0;
static constexpr uint8_t  kS8FailBeforeRecover  = 8;    // ~40 ms at 200 writes/s = 2 drive ticks
static constexpr uint32_t kRecoverMinIntervalMs = 250;  // don't spin on a genuinely dead bus

// Clock out up to 9 pulses on SCL with SDA released, then a manual STOP. A slave stuck
// mid-byte releases SDA once the rest of that byte is clocked through; the STOP then
// returns it to idle. Bit-banged, because the peripheral will not drive clocks while it
// believes the bus is busy.
static void i2cBusRecover()
{
    if (extI2C == nullptr) return;
    const int sda = extI2C->getSDA();
    const int scl = extI2C->getSCL();
    if (sda < 0 || scl < 0) return;

    g_i2cRecoverCount++;
    g_lastRecover_ms = millis();
    extI2C->release();                       // hand the pins back to GPIO

    pinMode(sda, INPUT_PULLUP);
    pinMode(scl, OUTPUT_OPEN_DRAIN);
    digitalWrite(scl, HIGH);
    delayMicroseconds(5);
    for (int i = 0; i < 9 && digitalRead(sda) == LOW; ++i) {
        digitalWrite(scl, LOW);  delayMicroseconds(5);
        digitalWrite(scl, HIGH); delayMicroseconds(5);
    }
    pinMode(sda, OUTPUT_OPEN_DRAIN);         // manual STOP: SDA low->high while SCL is high
    digitalWrite(sda, LOW);  delayMicroseconds(5);
    digitalWrite(scl, HIGH); delayMicroseconds(5);
    digitalWrite(sda, HIGH); delayMicroseconds(5);

    extI2C->begin(I2C_NUM_1, kPortASda, kPortAScl);
    g_currentHubCh = -1;                     // mux selection is unknown after a re-init
    servo8InvalidateModes();                 // the 8Servo may have missed its mode writes
    tprintf("[I2C] bus recovery #%lu\n", (unsigned long)g_i2cRecoverCount);
}

// A wedged slave fails every write, so escalate to a bus recovery after a short run of
// consecutive failures rather than retrying the same broken transaction forever.
static inline void servo8NoteFail()
{
    if (++g_s8ConsecFail < kS8FailBeforeRecover) return;
    g_s8ConsecFail = 0;
    if (millis() - g_lastRecover_ms >= kRecoverMinIntervalMs) i2cBusRecover();
}

// Write to an 8Servo register with a STOP on EVERY exit path — the thing writeRegister()
// does not do. Mirrors ppXfer()'s discipline.
static bool servo8Write(uint8_t reg, const uint8_t* d, size_t n)
{
    if (!extI2C->start(kServo8Addr, false, kServo8Hz)) { extI2C->stop(); return false; }
    if (!extI2C->write(reg))                          { extI2C->stop(); return false; }
    if (n && !extI2C->write(d, n))                    { extI2C->stop(); return false; }
    return extI2C->stop();
}

// Drive one 8Servo channel as a servo PWM pulse (microseconds) — the 4 goBILDA 2x80A ESCs.
// (PRISM is NOT here: it is an I2C RGB driver at 0x38; ch0 is the Rear-Left ESC.)
bool servo8SetPulse(uint8_t pin, uint16_t pulse_us)
{
    if (extI2C == nullptr || !extI2C->isEnabled()) return false;
    if (pin > 7) return false;
    if (!selectChannel(kChServo8)) { servo8NoteFail(); return false; }
    // Put the pin in servo-control mode ONCE (see the mode-cache note above), then write the
    // 16-bit pulse (low byte, high byte). A pulse-write failure invalidates the pin's mode bit so
    // mode is re-asserted next call — covers a bus glitch or an 8Servo that lost power/reset.
    if (!(g_servo8ModeSet & (1u << pin))) {
        const uint8_t m = kServo8ModeServoCtl;
        if (!servo8Write(kServo8ModeReg + pin, &m, 1)) { servo8NoteFail(); return false; }
        g_servo8ModeSet |= (1u << pin);
    }
    const uint8_t d[2] = { (uint8_t)(pulse_us & 0xFF), (uint8_t)((pulse_us >> 8) & 0xFF) };
    if (!servo8Write(kServo8PulseReg + pin * 2, d, 2)) {
        g_servo8ModeSet &= ~(1u << pin);
        servo8NoteFail();
        return false;
    }
    g_s8ConsecFail = 0;   // a clean write means the bus is healthy again
    return true;
}

void handleHostCommand(const char* cmd)
{
    if (rcCalCommand(cmd, rc_ch)) return;
    if (tofCalCommand(cmd)) return;
    if (!strcasecmp(cmd, "TOF")) { tofProbe(); return; }
    // Bench discovery of which relay moves the lift which way -- a per-robot wiring fact.
    // Bypasses intent, so it works with the sticks centred.
    int mpulse = -1, mgap = -1;
    if (sscanf(cmd, "MAST PULSE %d %d", &mpulse, &mgap) >= 1) {
        if (mgap < 0) mgap = (int)g_mast.gapMs();
        g_mast.setTiming((uint32_t)mpulse, (uint32_t)mgap);
        g_mast.saveCfg();
        tprintf("[MAST] pulse=%lums gap=%lums (saved)\n",
                (unsigned long)g_mast.pulseMs(), (unsigned long)g_mast.gapMs());
        return;
    }
    // Pinch-guard threshold + release dwell. Per-robot: it depends on where the ToF is
    // mounted and how far the lift coasts after the stop pulse, so it is commissioned on
    // the machine rather than assumed here.
    int mpinch = -1, mdwell = -1;
    if (sscanf(cmd, "MAST PINCH %d %d", &mpinch, &mdwell) >= 1) {
        if (mdwell < 0) mdwell = (int)g_mast.pinchDwellMs();
        g_mast.setPinch((float)mpinch, (uint32_t)mdwell);
        g_mast.saveCfg();               // commissioned value — must survive a reboot
        tprintf("[MAST] pinch=%.0fmm dwell=%lums (saved) guard=%s\n", g_mast.pinchMm(),
                (unsigned long)g_mast.pinchDwellMs(),
                g_mast.pinchActive() ? "active" : "NO HEIGHT SENSOR - descent unguarded");
        return;
    }
    // Consent for one descent through the pinch zone, for an automated lowering. Spent when
    // that descent stops, and stale after 15 s, so it can't be pre-armed and left lying about.
    if (!strcasecmp(cmd, "MAST ACK")) {
        g_mast.pinchAck(millis());
        tprintf("[MAST] pinch ACK — one descent through the zone authorized\n");
        return;
    }
    if (!strcasecmp(cmd, "MAST UP") || !strcasecmp(cmd, "MAST DOWN")) {
        const bool up = (strcasecmp(cmd, "MAST UP") == 0);
        g_mast.pulseLeg(up, millis());
        tprintf("[MAST] cmd='%s' -> %s leg, code R%u (silk R%u)\n", cmd,
                up ? "UP" : "DOWN",
                up ? kRelayMastUp : kRelayMastDown,
                (up ? kRelayMastUp : kRelayMastDown) + 1);
        return;
    }
    int value = -1;
    if (sscanf(cmd, "PDH_SWITCH %d", &value) == 1) {
        const bool en = value != 0;
        const bool ok = sendPdhSwitchCommand(en);
        Serial.printf("[PDH] cmd switch=%d %s\n", en ? 1 : 0, ok ? "queued" : "failed");
        return;
    }

    if (strcmp(cmd, "PDH_CLEAR_FAULTS") == 0) {
        const bool ok = sendPdhClearFaultsCommand();
        Serial.printf("[PDH] cmd clear_faults %s\n", ok ? "queued" : "failed");
        return;
    }

    int module = -1;
    if (sscanf(cmd, "PDH_ID %d", &module) == 1) {
        if (module >= 0 && module <= 63) {
            pdh_device_id = static_cast<uint8_t>(module);
            pdh_id_locked = true;
            Serial.printf("[PDH] cmd id=%u locked=1\n", static_cast<unsigned>(pdh_device_id));
        } else {
            Serial.println("[PDH] cmd invalid id (expected 0..63)");
        }
        return;
    }

    if (strcmp(cmd, "PDH_ID_AUTO") == 0) {
        pdh_id_locked = false;
        Serial.println("[PDH] cmd id auto-detect enabled");
        return;
    }

    int relay_ch = -1, relay_st = -1;
    if (sscanf(cmd, "RELAY %d %d", &relay_ch, &relay_st) == 2) {
        if (relay_ch >= 0 && relay_ch <= 3) {
            M5StamPLC.writePlcRelay(static_cast<uint8_t>(relay_ch), relay_st != 0);
            if (relay_ch == 0 && relay_st != 0) local_estop_active = false;  // host re-arm clears local latch
            Serial.printf("[IO] cmd relay %d=%d\n", relay_ch, relay_st != 0 ? 1 : 0);
        } else {
            Serial.println("[IO] cmd invalid relay (expected 0..3)");
        }
        return;
    }

    if (strcmp(cmd, "ESTOP") == 0) {
        // Safe-direction software E-stop: drop the contactor coil (relay 0).
        M5StamPLC.writePlcRelay(0, false);
        local_estop_active = true;
        Serial.println("[IO] cmd ESTOP -> relay0 OFF (contactor open)");
        return;
    }

    if (strcmp(cmd, "ENC_RESET") == 0) {
        bool ok = false;
        if (extI2C != nullptr && extI2C->isEnabled())
            ok = extI2C->writeRegister8(kExtEncoderAddr, kExtEncoderResetReg, 1, 400000);
        Serial.printf("[ENC] cmd reset %s\n", ok ? "ok" : "failed");
        return;
    }

    int page = -1;
    if (sscanf(cmd, "PAGE %d", &page) == 1) {
        if (page >= 0 && page < kUiPages) { ui_page = page; Serial.printf("[UI] page=%d\n", page); }
        else Serial.println("[UI] invalid page");
        return;
    }

    int s8pin = -1, s8pulse = -1;
    if (sscanf(cmd, "SERVO8 %d %d", &s8pin, &s8pulse) == 2) {
        if (s8pin >= 0 && s8pin <= 7 && s8pulse >= 0 && s8pulse <= 3000) {
            const bool ok = servo8SetPulse((uint8_t)s8pin, (uint16_t)s8pulse);
            Serial.printf("[8SERVO] pin=%d pulse=%dus %s\n", s8pin, s8pulse, ok ? "ok" : "FAILED");
        } else {
            Serial.println("[8SERVO] invalid (pin 0..7, pulse 0..3000)");
        }
        return;
    }

    // 8Servo power-cycle via spare relay R1. The ONLY reliable clear for the ESC solid-red fault is
    // cutting the 8Servo's 5V — a firmware reset does not clear it (the fault rides the ESC BEC).
    // Assumes R1 gates the PaHub/8Servo 5V feed, CLOSED = powered. Inert until that rewire is done
    // (R1 is currently spare), so it is safe to ship ahead of the wiring. `S8RESET [off_ms]`.
    int s8cycle_ms = -1;
    if (strncmp(cmd, "S8RESET", 7) == 0) {
        // DISABLED. This used to power-cycle the 8Servo via relay R1, but R1 is now the
        // lift's DOWN leg -- running it would drive the lift, not reset anything. The
        // allocation is dead anyway: the wedge was shown to be electrical (ground shift
        // measured at >=2.8 V against a ~0.7 V injection threshold), not something a
        // power-cycle prevents.
        tprintf("[S8RESET] disabled: R1 is now the lift DOWN leg. See notes/history/8servo-wedge/.\n");
        return;
    }

    // Ethernet OTA: pull a .bin over HTTP off the Thor and self-flash, then reboot into it. This is
    // what makes the StampPLC reflashable with USB unplugged. `OTA http://<thor-ip>:<port>/fw.bin`.
    if (strncmp(cmd, "OTA ", 4) == 0) {
        otaHttpPull(cmd + 4);
        return;
    }

    // Autonomous drive command from the Thor/ROS: `DRIVE <fwd> <rot> <str>` (-1..1).
    // Only takes effect under RC_AUTO authority (CH5) + the arm gate; each message
    // also refreshes the heartbeat, so stopping the stream stops the robot.
    // Z-axis intent from the Thor/ROS. Deliberately the ONLY remote way to move the lift:
    // it feeds the same setIntent() the sticks do, so the pinch guard, the limits and the
    // belief correction all apply. Driving the relays directly from the network -- which is
    // what the Tab5 bridge used to do -- goes round every one of them.
    // Heartbeat-gated like DRIVE: stop sending and the lift stops.
    float dmast = 0;
    if (sscanf(cmd, "MAST CMD %f", &dmast) == 1) {
        g_rosMast.v  = constrain(dmast, -1.0f, 1.0f);
        g_rosMast.ms = millis();          // silent ack — arrives at command rate
        return;
    }

    float dfwd = 0, drot = 0, dstr = 0;
    if (sscanf(cmd, "DRIVE %f %f %f", &dfwd, &drot, &dstr) == 3) {
        g_rosCmd.fwd = constrain(dfwd, -1.0f, 1.0f);
        g_rosCmd.rot = constrain(drot, -1.0f, 1.0f);
        g_rosCmd.str = constrain(dstr, -1.0f, 1.0f);
        g_rosCmd.ms  = millis();          // heartbeat (silent ack — arrives at command rate)
        return;
    }
    int dmode = -1;
    if (sscanf(cmd, "DRIVEMODE %d", &dmode) == 1) {
        if (dmode >= 0 && dmode <= 2) {
            g_driveMode = (DriveMode)dmode; g_driveModeOverride = true;   // bench override (ignores DI7 jumper)
            Serial.printf("[DRIVE] mode override=%d (0=mecanum 1=skid4 2=diff2)\n", dmode);
        } else if (dmode == -1) {
            g_driveModeOverride = false; Serial.println("[DRIVE] mode override cleared (jumper DI7)");
        } else Serial.println("[DRIVE] invalid mode (0..2, or -1 to clear override)");
        return;
    }

    // ---- RS485 / BMS probe ----
    unsigned int rbaud;
    if (sscanf(cmd, "RS485BAUD %u", &rbaud) == 1) { rs485Init(rbaud); return; }
    if (strncmp(cmd, "RS485TX ", 8) == 0) {
        uint8_t tx[64]; int n = hexToBytes(cmd + 8, tx, sizeof(tx));
        if (n > 0) rs485Send(tx, n); else Serial.println("[485] RS485TX: no hex bytes");
        return;
    }
    if (strcmp(cmd, "BMS") == 0) {          // JBD read-basic-info (0x03)
        static const uint8_t q[] = {0xDD, 0xA5, 0x03, 0x00, 0xFF, 0xFD, 0x77};
        rs485Send(q, sizeof(q)); return;
    }
    if (strcmp(cmd, "BMS_CELLS") == 0) {    // JBD read cell voltages (0x04)
        static const uint8_t q[] = {0xDD, 0xA5, 0x04, 0x00, 0xFF, 0xFC, 0x77};
        rs485Send(q, sizeof(q)); return;
    }
    int bauto = -1;
    if (sscanf(cmd, "BMSAUTO %d", &bauto) == 1) {
        g_bmsAuto = (bauto != 0);
        Serial.printf("[BMS] auto=%d\n", g_bmsAuto ? 1 : 0); return;
    }

    if (strcmp(cmd, "I2C_SCAN") == 0) {
        // On-demand full bus sweep (high-latency; not run periodically).
        scanI2CFull();
        return;
    }

    int r = -1, g = -1, b = -1, br = 100;
    int n = sscanf(cmd, "LED_COLOR %d %d %d %d", &r, &g, &b, &br);
    if (n >= 3) {
        auto cl = [](int v) { return (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v)); };
        const bool ok = prismSolid(cl(r), cl(g), cl(b), (uint8_t)(br < 0 ? 0 : (br > 100 ? 100 : br)));
        tprintf("[PRISM] color r=%d g=%d b=%d bright=%d len=%d %s\n",
                cl(r), cl(g), cl(b), br, prism_strip_len, ok ? "ok" : "FAILED");
        return;
    }

    int len = -1;
    if (sscanf(cmd, "LED_LEN %d", &len) == 1) {
        const bool ok = (len >= 0 && len <= 255) && prismSetStripLength((uint8_t)len);
        tprintf("[PRISM] strip_len=%d %s\n", len, ok ? "ok" : "FAILED");
        return;
    }

    if (strcmp(cmd, "LED_OFF") == 0) {
        const bool ok = prismClear();
        tprintf("[PRISM] clear %s\n", ok ? "ok" : "FAILED");
        return;
    }

    char mtw[8] = {0};
    int  mtsecs = 0;
    if (sscanf(cmd, "MASTTEST %7s %d", mtw, &mtsecs) >= 1) {
        if (mtsecs <= 0) mtsecs = 10;
        g_mastTestUntil = millis() + (uint32_t)mtsecs * 1000;
        if      (!strcasecmp(mtw, "UP"))   g_mastTest = 1;
        else if (!strcasecmp(mtw, "DOWN")) g_mastTest = 2;
        else if (!strcasecmp(mtw, "HOLD")) g_mastTest = 3;
        else { g_mastTest = 0; g_mastTestUntil = 0; }
        tprintf("[MASTTEST] %s for %ds — LIGHTS ONLY, no relay is touched\n",
                g_mastTest == 1 ? "UP" : g_mastTest == 2 ? "DOWN" : g_mastTest == 3 ? "HOLD" : "off",
                mtsecs);
        return;
    }

    int tval;
    if (sscanf(cmd, "TURNTEST %d", &tval) == 1) {   // bench-force the turn-signal for 12s
        g_turnDir = (tval > 0) ? 1 : (tval < 0) ? -1 : 0;
        g_turnTestUntil = millis() + 12000;
        tprintf("[TURN] test dir=%d (12s)\n", g_turnDir);
        return;
    }

    if (strcmp(cmd, "I2C_INFO") == 0) {
        // Report which physical bus the external scanner is bound to.
        if (extI2C)
            Serial.printf("[I2C] info bus=PortA(GroveRED,I2C_NUM_1) SDA=G%d SCL=G%d enabled=%d pahub=%d\n",
                          extI2C->getSDA(), extI2C->getSCL(), extI2C->isEnabled() ? 1 : 0,
                          g_paHubPresent ? 1 : 0);
        else
            Serial.println("[I2C] info: extI2C is null");
        return;
    }

    Serial.printf("[PDH] cmd unknown: %s\n", cmd);
}

void pollHostCommands()
{
    while (Serial.available() > 0) {
        const int c = Serial.read();
        if (c < 0) {
            break;
        }

        if (c == '\r') {
            continue;
        }

        if (c == '\n') {
            if (host_cmd_len > 0) {
                host_cmd_buf[host_cmd_len] = '\0';
                handleHostCommand(host_cmd_buf);
                host_cmd_len = 0;
                last_host_cmd_ms = millis();   // host-link heartbeat for the local UI
            }
            continue;
        }

        if (host_cmd_len + 1 < sizeof(host_cmd_buf)) {
            host_cmd_buf[host_cmd_len++] = static_cast<char>(c);
        } else {
            host_cmd_len = 0;
            Serial.println("[PDH] cmd buffer overflow");
        }
    }
}

void decodePdhStatusFrame(const twai_message_t& can_msg)
{
    const uint32_t id = can_msg.identifier;
    const uint8_t dev_id = static_cast<uint8_t>(id & kPdhDeviceIdMask);
    if (!pdh_id_locked) {
        pdh_device_id = dev_id;
    } else if (dev_id != pdh_device_id) {
        return;
    }

    const uint8_t* d = can_msg.data;
    pdh.last_update_ms = millis();

    if (isPdhFrame(id, kPdhStatus0FrameBase)) {
        pdh.s0_seen = true;
        pdh.channel_a[0] = static_cast<float>(unpackBitsLE(d, 0, 10)) * 0.125f;
        pdh.channel_a[1] = static_cast<float>(unpackBitsLE(d, 10, 10)) * 0.125f;
        pdh.channel_a[2] = static_cast<float>(unpackBitsLE(d, 20, 10)) * 0.125f;
        pdh.channel_a[3] = static_cast<float>(unpackBitsLE(d, 32, 10)) * 0.125f;
        pdh.channel_a[4] = static_cast<float>(unpackBitsLE(d, 42, 10)) * 0.125f;
        pdh.channel_a[5] = static_cast<float>(unpackBitsLE(d, 52, 10)) * 0.125f;
        pdh.breaker_fault[0] = unpackBitsLE(d, 30, 1) != 0;
        pdh.breaker_fault[1] = unpackBitsLE(d, 31, 1) != 0;
        pdh.breaker_fault[2] = unpackBitsLE(d, 62, 1) != 0;
        pdh.breaker_fault[3] = unpackBitsLE(d, 63, 1) != 0;
        return;
    }

    if (isPdhFrame(id, kPdhStatus1FrameBase)) {
        pdh.s1_seen = true;
        pdh.channel_a[6] = static_cast<float>(unpackBitsLE(d, 0, 10)) * 0.125f;
        pdh.channel_a[7] = static_cast<float>(unpackBitsLE(d, 10, 10)) * 0.125f;
        pdh.channel_a[8] = static_cast<float>(unpackBitsLE(d, 20, 10)) * 0.125f;
        pdh.channel_a[9] = static_cast<float>(unpackBitsLE(d, 32, 10)) * 0.125f;
        pdh.channel_a[10] = static_cast<float>(unpackBitsLE(d, 42, 10)) * 0.125f;
        pdh.channel_a[11] = static_cast<float>(unpackBitsLE(d, 52, 10)) * 0.125f;
        pdh.breaker_fault[4] = unpackBitsLE(d, 30, 1) != 0;
        pdh.breaker_fault[5] = unpackBitsLE(d, 31, 1) != 0;
        pdh.breaker_fault[6] = unpackBitsLE(d, 62, 1) != 0;
        pdh.breaker_fault[7] = unpackBitsLE(d, 63, 1) != 0;
        return;
    }

    if (isPdhFrame(id, kPdhStatus2FrameBase)) {
        pdh.s2_seen = true;
        pdh.channel_a[12] = static_cast<float>(unpackBitsLE(d, 0, 10)) * 0.125f;
        pdh.channel_a[13] = static_cast<float>(unpackBitsLE(d, 10, 10)) * 0.125f;
        pdh.channel_a[14] = static_cast<float>(unpackBitsLE(d, 20, 10)) * 0.125f;
        pdh.channel_a[15] = static_cast<float>(unpackBitsLE(d, 32, 10)) * 0.125f;
        pdh.channel_a[16] = static_cast<float>(unpackBitsLE(d, 42, 10)) * 0.125f;
        pdh.channel_a[17] = static_cast<float>(unpackBitsLE(d, 52, 10)) * 0.125f;
        pdh.breaker_fault[8] = unpackBitsLE(d, 30, 1) != 0;
        pdh.breaker_fault[9] = unpackBitsLE(d, 31, 1) != 0;
        pdh.breaker_fault[10] = unpackBitsLE(d, 62, 1) != 0;
        pdh.breaker_fault[11] = unpackBitsLE(d, 63, 1) != 0;
        return;
    }

    if (isPdhFrame(id, kPdhStatus3FrameBase)) {
        pdh.s3_seen = true;
        pdh.channel_a[18] = static_cast<float>(unpackBitsLE(d, 0, 10)) * 0.125f;
        pdh.channel_a[19] = static_cast<float>(unpackBitsLE(d, 10, 10)) * 0.125f;
        pdh.channel_a[20] = static_cast<float>(unpackBitsLE(d, 24, 8)) * 0.0625f;
        pdh.channel_a[21] = static_cast<float>(unpackBitsLE(d, 32, 8)) * 0.0625f;
        pdh.channel_a[22] = static_cast<float>(unpackBitsLE(d, 40, 8)) * 0.0625f;
        pdh.channel_a[23] = static_cast<float>(unpackBitsLE(d, 48, 8)) * 0.0625f;
        pdh.breaker_fault[12] = unpackBitsLE(d, 20, 1) != 0;
        pdh.breaker_fault[13] = unpackBitsLE(d, 21, 1) != 0;
        pdh.breaker_fault[14] = unpackBitsLE(d, 22, 1) != 0;
        pdh.breaker_fault[15] = unpackBitsLE(d, 23, 1) != 0;
        pdh.breaker_fault[16] = unpackBitsLE(d, 56, 1) != 0;
        pdh.breaker_fault[17] = unpackBitsLE(d, 57, 1) != 0;
        pdh.breaker_fault[18] = unpackBitsLE(d, 58, 1) != 0;
        pdh.breaker_fault[19] = unpackBitsLE(d, 59, 1) != 0;
        pdh.breaker_fault[20] = unpackBitsLE(d, 60, 1) != 0;
        pdh.breaker_fault[21] = unpackBitsLE(d, 61, 1) != 0;
        pdh.breaker_fault[22] = unpackBitsLE(d, 62, 1) != 0;
        pdh.breaker_fault[23] = unpackBitsLE(d, 63, 1) != 0;
        return;
    }

    if (isPdhFrame(id, kPdhStatus4FrameBase)) {
        pdh.s4_seen = true;
        pdh.v_bus = static_cast<float>(unpackBitsLE(d, 0, 12)) * 0.0078125f;
        pdh.brownout_fault = unpackBitsLE(d, 16, 1) != 0;
        pdh.can_warning_fault = unpackBitsLE(d, 18, 1) != 0;
        pdh.hardware_fault = unpackBitsLE(d, 19, 1) != 0;
        pdh.switch_channel = unpackBitsLE(d, 20, 1) != 0;
        pdh.total_current = static_cast<float>(unpackBitsLE(d, 32, 8)) * 2.0f;
        pdh.total_power = pdh.v_bus * pdh.total_current;
        {
            uint32_t sb = 0;
            for (int i = 0; i < 20; ++i)
                if (unpackBitsLE(d, 40 + i, 1)) sb |= (1u << i);   // sticky ch0-19
            for (int i = 0; i < 4; ++i)
                if (unpackBitsLE(d, 27 + i, 1)) sb |= (1u << (20 + i));  // sticky ch20-23
            pdh.sticky_breaker = sb;
        }

        const unsigned long now_ms = millis();
        if (pdh.last_s4_ms != 0 && now_ms > pdh.last_s4_ms) {
            const float dt_h = static_cast<float>(now_ms - pdh.last_s4_ms) / 3600000.0f;
            pdh.energy_wh += pdh.total_power * dt_h;
        }
        pdh.last_s4_ms = now_ms;
        return;
    }
}

bool decodeSbusFrame(const uint8_t* frame, uint16_t* ch, bool* failsafe, bool* frame_lost)
{
    if (frame[0] != 0x0F) {
        return false;
    }

    // Accept common SBUS footer variants seen across receivers.
    const uint8_t footer = frame[24];
    if (!(footer == 0x00 || footer == 0x04 || footer == 0x14 || footer == 0x24)) {
        return false;
    }

    ch[0]  = (frame[1] | frame[2] << 8) & 0x07FF;
    ch[1]  = (frame[2] >> 3 | frame[3] << 5) & 0x07FF;
    ch[2]  = (frame[3] >> 6 | frame[4] << 2 | frame[5] << 10) & 0x07FF;
    ch[3]  = (frame[5] >> 1 | frame[6] << 7) & 0x07FF;
    ch[4]  = (frame[6] >> 4 | frame[7] << 4) & 0x07FF;
    ch[5]  = (frame[7] >> 7 | frame[8] << 1 | frame[9] << 9) & 0x07FF;
    ch[6]  = (frame[9] >> 2 | frame[10] << 6) & 0x07FF;
    ch[7]  = (frame[10] >> 5 | frame[11] << 3) & 0x07FF;
    ch[8]  = (frame[12] | frame[13] << 8) & 0x07FF;
    ch[9]  = (frame[13] >> 3 | frame[14] << 5) & 0x07FF;
    ch[10] = (frame[14] >> 6 | frame[15] << 2 | frame[16] << 10) & 0x07FF;
    ch[11] = (frame[16] >> 1 | frame[17] << 7) & 0x07FF;
    ch[12] = (frame[17] >> 4 | frame[18] << 4) & 0x07FF;
    ch[13] = (frame[18] >> 7 | frame[19] << 1 | frame[20] << 9) & 0x07FF;
    ch[14] = (frame[20] >> 2 | frame[21] << 6) & 0x07FF;
    ch[15] = (frame[21] >> 5 | frame[22] << 3) & 0x07FF;

    const uint8_t flags = frame[23];
    *frame_lost         = (flags & (1 << 2)) != 0;
    *failsafe           = (flags & (1 << 3)) != 0;
    return true;
}

bool probeI2CAddress(m5::I2C_Class& i2c, uint8_t addr)
{
    return i2c.scanID(addr, 400000);
}

bool selectPaHubChannel(m5::I2C_Class& i2c, uint8_t channel)
{
    if (channel >= 6) {
        return false;
    }

    if (!i2c.start(kPaHubAddr, false, 400000)) {
        return false;
    }
    bool ok = i2c.write(1 << channel);
    ok      = i2c.stop() && ok;
    return ok;
}

// Deselect every PaHUB channel (mux passes nothing through).
void deselectHub()
{
    g_currentHubCh = -1;
    if (extI2C && extI2C->start(kPaHubAddr, false, 400000)) {
        extI2C->write(0x00);
        extI2C->stop();
    }
}

void scanExternalBus()
{
    if (extI2C == nullptr || !extI2C->isEnabled()) {
        Serial.println("[I2C] External I2C bus is not enabled");
        return;
    }

    const float power_v = M5StamPLC.getPowerVoltage();
    tprintf("[SENS] power=%.2fV\n", power_v);

    // Cheap steady-state probe (low latency). With the PaHUB, probe each expected
    // device on its channel; without it, probe Port A flat. Bare 0xNN tokens keep
    // the dashboard parser happy; per-channel detail is in the I2C_SCAN command.
    // Full bus sweep = "I2C_SCAN" host command (scanI2CFull()).
    // Build the whole line into one buffer, then TEE it (USB + UDP). This line was serial-only, so
    // the dashboard's I2C device list went blank the moment USB was unplugged — tee it for parity.
    char sbuf[160];
    int  p = snprintf(sbuf, sizeof(sbuf), "[I2C] scan:");
    bool any = false;
    auto add = [&](uint8_t addr) {
        if (p > 0 && p < (int)sizeof(sbuf) - 6) p += snprintf(sbuf + p, sizeof(sbuf) - p, " 0x%02X", addr);
        any = true;
    };
    if (g_paHubPresent) {
        for (const HubDevice& d : kHubDevices) {
            if (!selectChannel(d.channel)) continue;
            if (!probeI2CAddress(*extI2C, d.addr)) continue;
            add(d.addr);
        }
    } else {
        static const uint8_t kFlat[] = {
            kServo8Addr, kPinpointAddr, kPrismAddr, kExtEncoderAddr, kOtosAddr};
        for (uint8_t addr : kFlat) {
            if (!probeI2CAddress(*extI2C, addr)) continue;
            add(addr);
        }
    }
    if (!any && p > 0 && p < (int)sizeof(sbuf) - 6) p += snprintf(sbuf + p, sizeof(sbuf) - p, " none");
    if (p > 0 && p < (int)sizeof(sbuf) - 1) { sbuf[p++] = '\n'; sbuf[p] = '\0'; }
    temit(sbuf, p);
}

// On-demand full 7-bit bus sweep (0x08-0x77). Triggered by the "I2C_SCAN" host
// command — NOT run periodically, since probing 112 addresses is high-latency.
// PaHUB-aware: top-level sweep first (the hub shows at 0x70), then a sweep behind
// each channel. The "[I2C] full:" line carries bare 0xNN for the dashboard; the
// "[I2C] hub chN:" lines carry the per-channel detail + expected-device labels.
void scanI2CFull()
{
    if (extI2C == nullptr || !extI2C->isEnabled()) {
        tprintf("[I2C] full: external I2C bus is not enabled\n");
        return;
    }
    // Each line is built into a buffer and TEE'd, so the scan is readable over Ethernet.
    // It used to be Serial-only, which made it useless without USB — and USB is exactly
    // what a robot in the field does not have.
    char b[192];
    int  n = snprintf(b, sizeof(b), "[I2C] full:");
    bool any = false;
    for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
        if (!probeI2CAddress(*extI2C, addr)) continue;
        if (n > 0 && n < (int)sizeof(b) - 6) n += snprintf(b + n, sizeof(b) - n, " 0x%02X", addr);
        any = true;
    }
    if (!any) snprintf(b + n, sizeof(b) - n, " none");
    tprintf("%s\n", b);
    delay(25);

    if (!probeI2CAddress(*extI2C, kPaHubAddr)) {
        tprintf("[I2C] hub: not present at 0x70 (flat wiring)\n");
        return;
    }
    for (uint8_t ch = 0; ch < 6; ++ch) {
        if (!selectPaHubChannel(*extI2C, ch)) {
            tprintf("[I2C] hub ch%u: select failed\n", ch);
            continue;
        }
        n = snprintf(b, sizeof(b), "[I2C] hub ch%u:", ch);
        bool found = false;
        for (uint8_t addr = 0x08; addr <= 0x77; addr++) {
            if (addr == kPaHubAddr) continue;   // the mux doesn't answer behind itself
            if (!probeI2CAddress(*extI2C, addr)) continue;
            if (n > 0 && n < (int)sizeof(b) - 6) n += snprintf(b + n, sizeof(b) - n, " 0x%02X", addr);
            found = true;
        }
        if (!found && n > 0) n += snprintf(b + n, sizeof(b) - n, " none");
        for (const HubDevice& d : kHubDevices)
            if (d.channel == ch && n > 0 && n < (int)sizeof(b) - 32)
                n += snprintf(b + n, sizeof(b) - n, " [exp %s@0x%02X]", d.name, d.addr);
        tprintf("%s\n", b);
        delay(25);
    }
    deselectHub();
}

void consumeByte(uint8_t b)
{
    if (frame_pos == 0) {
        if (b != 0x0F) {
            return;
        }
        frame_buf[frame_pos++] = b;
        return;
    }

    frame_buf[frame_pos++] = b;
    if (frame_pos < sizeof(frame_buf)) {
        return;
    }

    uint16_t ch[16];
    bool failsafe = false;
    bool lost = false;

    if (decodeSbusFrame(frame_buf, ch, &failsafe, &lost)) {
        valid_frames++;
        last_valid_frame_ms = millis();
        memcpy(rc_ch, ch, sizeof(rc_ch));   // mirror to the local UI
        rc_failsafe   = failsafe;
        rc_frame_lost = lost;
        // Range-sanitize the analog sticks CH1-CH4 (ROTATE/MAST/FWD-REV/STRAFE). A value
        // outside the plausible SBUS band is not a real command — a receiver failsafe-preset
        // pegged to an extreme (e.g. CH3=0) or bus corruption. Flag it so the drive arm gate
        // blocks motion EVEN IF the SBUS failsafe flag was not set. Switch channels CH5-8
        // legitimately sit at their endpoints, so they are excluded.
        // Sanitize the channels actually carrying axes, whichever they are.
        rc_range_bad = false;
        for (const RcFn f : {FN_FWD, FN_ROT, FN_STR, FN_MAST}) {
            const int8_t c = rcCfgGet(f).ch;
            if (c >= 0 && !sbusInBand(ch[c])) { rc_range_bad = true; break; }
        }

        // Print frequently for responsive GUI updates.
        if ((valid_frames % kSbusPrintEveryNFrames) == 0) {
            Serial.printf("[SBUS] OK f=%lu prof=%s ch1=%u ch2=%u ch3=%u ch4=%u ch5=%u ch6=%u ch7=%u ch8=%u fs=%d lost=%d\n",
                          static_cast<unsigned long>(valid_frames),
                          kProfiles[0].name,
                          ch[0], ch[1], ch[2], ch[3], ch[4], ch[5], ch[6], ch[7],
                          failsafe ? 1 : 0,
                          lost ? 1 : 0);
        }
    } else {
        invalid_frames++;
    }

    frame_pos = 0;
}

void beginProfile(int idx)
{
    const auto& p = kProfiles[idx];
    rc.end();
    delay(20);
    rc.begin(p.baud, p.config, kBluePortRxPin, kBluePortTxPin, p.inverted);

    frame_pos          = 0;
    valid_frames       = 0;
    invalid_frames     = 0;
    total_bytes        = 0;
    last_valid_frame_ms = 0;
    last_status_ms      = millis();

    Serial.println();
    Serial.printf("[RC] Profile: %s | baud=%lu | cfg=0x%lX | inverted=%s | RX=G%d TX=G%d\n", p.name,
                  static_cast<unsigned long>(p.baud), static_cast<unsigned long>(p.config),
                  p.inverted ? "yes" : "no", kBluePortRxPin, kBluePortTxPin);
}

// ---- Local UI implementation ----
enum UiState { ST_BENCH, ST_ARMED, ST_WARN, ST_FAULT, ST_ESTOP };

// RC-derived operational state (drives the hero label + the PRISM indicator).
enum RcMode { RCM_NOLINK, RCM_FAILSAFE, RCM_ESTOP, RCM_OFF, RCM_MANUAL_SAFE, RCM_MANUAL_LIVE, RCM_AUTO };

static RcMode computeRcMode()
{
    if (local_estop_active) return RCM_ESTOP;
    const bool rc_live = (last_valid_frame_ms != 0) && (millis() - last_valid_frame_ms < kRcTimeoutMs);
    if (!rc_live)     return RCM_NOLINK;
    if (rc_failsafe || rc_range_bad) return RCM_FAILSAFE;   // flag OR a stick pegged out-of-band
    // Channel and polarity come from the per-robot RC map (RCCAL), not from fixed
    // indices — "which channel is the deadman, and which way round" is a transmitter
    // question, answered once in rc_config rather than re-litigated per subsystem.
    const uint16_t m    = rcRaw(FN_AUTH, rc_ch);
    const bool     held = rcAxis(FN_DEAD, rc_ch) > 0.5f;
    if (m < 600)  return RCM_OFF;
    if (m < 1400) return held ? RCM_MANUAL_LIVE : RCM_MANUAL_SAFE;
    return RCM_AUTO;
}

static uint8_t uiDi();   // fwd decl — defined below with the local-UI helpers

// ---- Drive: SBUS axis → normalized -1..1 ----
static float sbusAxis(uint16_t raw)
{
    const float v = (float)((int)raw - kSbusCtr) / (float)kSbusHalf;
    return constrain(v, -1.0f, 1.0f);
}

// ---- Mixer: (fwd, rot, str) -1..1 → wheel[4] {FL,FR,RL,RR} -1..1 ----
// Mode-aware and source-agnostic (same math whether the inputs came from the RC
// sticks or a ROS/Thor DRIVE command). Strafe collapses to 0 in non-mecanum modes.
static void driveMix(float fwd, float rot, float str, DriveMode mode, float w[4])
{
    if (mode == DM_MECANUM) {
        w[0] = fwd + str + rot;   // FL
        w[1] = fwd - str - rot;   // FR
        w[2] = fwd - str + rot;   // RL
        w[3] = fwd + str - rot;   // RR
    } else {                      // SKID4 / DIFF2: arcade, strafe ignored
        const float l = fwd + rot, r = fwd - rot;
        w[0] = (mode == DM_DIFF2) ? 0.0f : l;   // FL (idle → caster/ball in 2WD)
        w[1] = (mode == DM_DIFF2) ? 0.0f : r;   // FR (idle in 2WD)
        w[2] = l;                                // RL
        w[3] = r;                                // RR
    }
    // Scale down uniformly if any wheel exceeds ±1 (preserves heading/ratio).
    float mx = 1.0f;
    for (int i = 0; i < 4; ++i) mx = max(mx, fabsf(w[i]));
    for (int i = 0; i < 4; ++i) w[i] = constrain(w[i] / mx, -1.0f, 1.0f);
}

// ---- driveTick(): the whole safety-gated output path, called at 50 Hz ----
static void driveTick()
{
    const RcMode  mode      = computeRcMode();
    const uint8_t di        = uiDi();
    const bool    contactor = M5StamPLC.readPlcRelay(0);        // R0 = e-stop contactor (motion power)
    // DI0 is a NORMALLY-CLOSED e-stop chain: HIGH = released/safe, LOW = pressed OR a broken
    // wire. Fail-safe, so a severed conductor reads as e-stop rather than as "all clear".
    const bool    di0estop  = (di & 0x01) == 0;
    const bool    rc_live   = (last_valid_frame_ms != 0) && (millis() - last_valid_frame_ms < kRcTimeoutMs);
    const bool    rosFresh  = (g_rosCmd.ms != 0) && (millis() - g_rosCmd.ms < kRosCmdTimeoutMs);

    // Drive mode is software-only (set by the DRIVEMODE command). DI7 is spare — no physical jumper.

    // ==== (B) ARM GATE — EVERY condition must hold, re-checked every tick ====
    const bool manual   = (mode == RCM_MANUAL_LIVE || mode == RCM_MANUAL_SAFE);
    const bool autoAuth = (mode == RCM_AUTO) && rosFresh;       // autonomous needs a fresh Thor heartbeat
    const bool armOK    = (manual || autoAuth)                  // manual or autonomous authority (CH5)
                       &&  contactor                            // drive bus actually powered
                       && !local_estop_active && !di0estop      // no e-stop (host/btn or DI0)
                       &&  rc_live && !rc_failsafe && !rc_range_bad;  // RC fresh, not failsafe, sticks in-band

    // ==== (A) ARM SEQUENCE — 2 s of neutral with the gate satisfied ====
    if (!armOK)            { g_escArmed = false; g_armStart_ms = 0; }
    else if (!g_escArmed)  { if (g_armStart_ms == 0) g_armStart_ms = millis();
                             if (millis() - g_armStart_ms >= kArmHoldMs) g_escArmed = true; }

    // ==== COMMAND SOURCE (deadman-gated) — computed ALWAYS, independent of ARMING, so the DRIVE
    // screen + turn-signals reflect commanded motion on the bench (drive bus unpowered). Arming
    // gates only the physical ESC output below, NOT the intent/mix display.
    float fwd = 0, rot = 0, str = 0;
    if (mode == RCM_MANUAL_LIVE) {                     // manual + deadman HELD → sticks
        const float spd = rcUnit(FN_SPEED, rc_ch);
        fwd = rcAxis(FN_FWD, rc_ch) * spd;
        rot = rcAxis(FN_ROT, rc_ch) * spd;
        str = rcAxis(FN_STR, rc_ch) * spd;
    } else if (autoAuth) {                             // autonomous → Thor/ROS DRIVE command
        fwd = g_rosCmd.fwd; rot = g_rosCmd.rot; str = g_rosCmd.str;
    }
    // RCM_MANUAL_SAFE (deadman released) → 0,0,0 = idle.
    g_intentFwd = fwd; g_intentRot = rot; g_intentStr = str;
    g_speed = rcUnit(FN_SPEED, rc_ch);   // speed knob (annunciation)
    if (millis() > g_turnTestUntil) {   // real turn intent unless a TURNTEST override is active
        const float lat = str + rot;
        g_turnDir = (lat > kTurnIntentThresh) ? 1 : (lat < -kTurnIntentThresh) ? -1 : 0;
    }

    // ==== MIX (always → g_wheelMix for the display) → ESC target, GATED by arming ====
    driveMix(fwd, rot, str, g_driveMode, g_wheelMix);
    const bool driveOut = armOK && g_escArmed;         // physical output only when truly armed
    uint16_t target[4];
    for (int i = 0; i < 4; ++i) {
        const float wi = (driveOut ? g_wheelMix[i] : 0.0f) * (float)kDriveInvert[i];
        int us = kEscNeutralUs + (int)lroundf(wi * kEscSpanUs);
        if (abs(us - (int)kEscNeutralUs) < (int)kEscDeadzoneUs) us = kEscNeutralUs;
        target[i] = (uint16_t)constrain(us, (int)kEscNeutralUs - (int)kEscSpanUs,
                                            (int)kEscNeutralUs + (int)kEscSpanUs);
    }

    // ==== Z-AXIS ====
    // Same gate as the wheels: authority, deadman, no e-stop, contactor closed, RC fresh.
    // The lift's 24 V comes off the drive bus, so it is unpowered whenever the contactor
    // is open -- software and hardware agree by construction. Intent is asserted EVERY
    // tick (0 when not permitted), so "stop" is continuous rather than a one-shot that
    // could go missing.
    // Z-axis intent: sticks under manual authority, Thor under autonomous authority. Both go
    // through the same driver, so neither can skip the pinch guard. Anything else -- no
    // authority, deadman released, stale heartbeat -- asserts 0, every tick.
    float mastIntent = 0.0f;
    if (armOK && mode == RCM_MANUAL_LIVE)  mastIntent = rcAxis(FN_MAST, rc_ch);
    else if (armOK && autoAuth)            mastIntent = (millis() - g_rosMast.ms < kRosCmdTimeoutMs)
                                                         ? g_rosMast.v : 0.0f;
    g_mast.setIntent(mastIntent);
    g_mast.tick(millis());

    // ==== SLEW LIMIT + brake-through-neutral on reversal → OUTPUT ====
    // A direct FWD→REV (or REV→FWD) command reverses a still-spinning hub, dumping regen onto
    // the bus faster than the clamp can bleed → overvoltage trip (all ESC LEDs red). So on any
    // sign flip we force the target to neutral first, ramp down, hold a dwell at neutral, and
    // only then ramp up the other way. The slew limit spreads the regen over time either way.
    static uint8_t revDwell[4] = {0, 0, 0, 0};
    for (int i = 0; i < 4; ++i) {
        int cur = g_escOut[i];
        int tgt = (int)target[i];
        const int curOff = cur - (int)kEscNeutralUs;
        const int tgtOff = tgt - (int)kEscNeutralUs;
        if (curOff != 0 && tgtOff != 0 && ((curOff > 0) != (tgtOff > 0))) {
            tgt = (int)kEscNeutralUs;            // sign flip while still moving → brake to neutral first
            revDwell[i] = kEscRevDwellTicks;     // arm the post-neutral hold
        } else if (revDwell[i] > 0) {
            if (abs(curOff) <= (int)kEscDeadzoneUs) revDwell[i]--;  // only count down once actually at neutral
            tgt = (int)kEscNeutralUs;            // hold neutral through the dwell before the new direction
        }
        const int d = tgt - cur;
        if (d >  (int)kEscSlewUs) cur += kEscSlewUs;
        else if (d < -(int)kEscSlewUs) cur -= kEscSlewUs;
        else cur = tgt;
        g_escOut[i] = (uint16_t)cur;
#if DRIVE_ENABLE
        if (!servo8SetPulse(kDriveCh[i], g_escOut[i])) g_s8Fail++;   // physical PWM to the ESCs — count I2C drops
#endif
    }
}

// [DRIVE] telemetry — always published (even when DRIVE_ENABLE=0, armed=0) so the
// dashboard's DRIVE page reflects mode/armed/per-wheel. w[] normalized -1..1.
static void publishDrive()
{
    // No txReady() gate: tprintf() self-guards the USB write + always tees over Ethernet.
    const char* mn = (g_driveMode == DM_MECANUM) ? "MECANUM"
                   : (g_driveMode == DM_SKID4)   ? "SKID4" : "DIFF2";
    const char* turn = g_turnDir > 0 ? "R" : g_turnDir < 0 ? "L" : "0";
    tprintf("[DRIVE] mode=%s armed=%d spd=%.2f fwd=%.2f rot=%.2f str=%.2f turn=%s w=%.2f,%.2f,%.2f,%.2f out=%u,%u,%u,%u s8fail=%lu i2crec=%lu mast=%s:%d h=%.0f pinch=%s\n",
                  mn, g_escArmed ? 1 : 0, g_speed, g_intentFwd, g_intentRot, g_intentStr, turn,
                  g_wheelMix[0], g_wheelMix[1], g_wheelMix[2], g_wheelMix[3],
                  g_escOut[0], g_escOut[1], g_escOut[2], g_escOut[3], (unsigned long)g_s8Fail,
                  (unsigned long)g_i2cRecoverCount,
                  g_mast.name(),
                  (int)g_mast.motion(),
                  g_mast.heightMm(),
                  // Pinch guard, in the order an operator needs it: is it even able to work,
                  // is it holding, has someone consented.
                  !g_mast.pinchActive() ? "nosensor"
                  : g_mast.pinchHold()  ? "HOLD"
                  : g_mast.pinchConsent() ? "ack" : "ok");
}

// [RC] telemetry — interpreted mode/deadman/speed + raw 16 channels, for the
// dashboard RC/DRIVE page + FSM (client interprets raw via the SBUS range).
static void publishRc()
{
    // No txReady() gate: the telemetry tee (temit) self-guards the USB write and always sends over
    // Ethernet, so we must not skip the whole function under USB back-pressure (else Eth loses [RC]).
    const RcMode m = computeRcMode();
    const char* mode = (m == RCM_NOLINK)   ? "NOLINK"
                     : (m == RCM_FAILSAFE) ? "FAILSAFE"
                     : (m == RCM_ESTOP)    ? "ESTOP"
                     : (m == RCM_OFF)      ? "OFF"
                     : (m == RCM_AUTO)     ? "AUTO" : "MANUAL";
    const bool live = (last_valid_frame_ms != 0) && (millis() - last_valid_frame_ms < kRcTimeoutMs);
    const bool dead = rc_ch[5] > 1000;    // CH6 deadman
    const int  spd  = (int)constrain(((int)rc_ch[7] - 200) * 100.0f / 1620.0f, 0.0f, 100.0f);  // CH8 %
    // fr=CH3(idx2) rot=CH1(idx0) str=CH4(idx3) mast=CH2(idx1); raw = all 16 channels. Build the whole
    // line into one buffer so the tee emits it as a SINGLE serial write / UDP datagram (not fragments).
    char b[224];
    int n = snprintf(b, sizeof(b),
        "[RC] live=%d mode=%s dead=%d spd=%d fs=%d rng=%d mat=%d fr=%d rot=%d str=%d mast=%d raw=",
        live ? 1 : 0, mode, dead ? 1 : 0, spd, rc_failsafe ? 1 : 0, rc_range_bad ? 1 : 0,
        rc_ch[6], rc_ch[2], rc_ch[0], rc_ch[3], rc_ch[1]);
    if (n < 0) return;
    if (n >= (int)sizeof(b)) n = (int)sizeof(b) - 1;
    for (int i = 0; i < 16 && n < (int)sizeof(b) - 8; ++i)
        n += snprintf(b + n, sizeof(b) - n, "%s%d", i ? "," : "", rc_ch[i]);
    if (n < (int)sizeof(b) - 1) { b[n++] = '\n'; b[n] = '\0'; }
    temit(b, n);
}

static uint8_t uiDi()    { uint8_t v = 0; for (uint8_t i = 0; i < 8; ++i) if (M5StamPLC.readPlcInput(i)) v |= (1u << i); return v; }
static uint8_t uiRelay() { uint8_t v = 0; for (uint8_t i = 0; i < 4; ++i) if (M5StamPLC.readPlcRelay(i)) v |= (1u << i); return v; }

// Overall state, computed only from things this MCU can trust locally. `di` bit 1 =
// contactor weld sense (DI1). fault_mask carries the individual fault bits for the buzzer.
static UiState uiState(uint8_t di, uint32_t& fault_mask)
{
    const bool weld = (di & (1u << 1)) != 0;
    fault_mask = (weld ? 1u : 0) | (pdh.hardware_fault ? 2u : 0) |
                 (pdh.brownout_fault ? 4u : 0) | (pdh.sticky_breaker ? 8u : 0);
    if (local_estop_active) return ST_ESTOP;
    if (fault_mask)         return ST_FAULT;
    if (last_host_cmd_ms == 0)                    return ST_BENCH;   // host never talked → headless
    if (millis() - last_host_cmd_ms > 3000)       return ST_WARN;    // host link stale
    return ST_ARMED;
}

static void uiLed(UiState s)
{
    // setStatusLight drives R/G/B on/off per channel (not PWM) — 8 colors. Blink by
    // toggling with off. Values are 0/1 as in the StatusLight example.
    const uint8_t blink = (((millis() / 300) % 2) == 0) ? 1 : 0;
    if (millis() < identify_until_ms) { M5StamPLC.setStatusLight(blink, blink, blink); return; }  // white blink
    switch (s) {
        case ST_ESTOP: M5StamPLC.setStatusLight(1, 0, 0);     break;   // solid red
        case ST_FAULT: M5StamPLC.setStatusLight(blink, 0, 0); break;   // blinking red
        case ST_WARN:  M5StamPLC.setStatusLight(1, 1, 0);     break;   // yellow
        case ST_ARMED: M5StamPLC.setStatusLight(0, 1, 0);     break;   // green
        default:       M5StamPLC.setStatusLight(0, 0, blink); break;   // bench: blinking blue
    }
}

static void uiStateWord(UiState s, M5Canvas& g, const char*& word, uint16_t& col)
{
    switch (s) {
        case ST_ARMED: word = "ARMED";  col = TFT_GREEN;              break;
        case ST_WARN:  word = "WARN";   col = g.color565(255, 150, 0); break;
        case ST_FAULT: word = "FAULT";  col = TFT_RED;               break;
        case ST_ESTOP: word = "E-STOP"; col = TFT_RED;               break;
        default:       word = "BENCH";  col = g.color565(0, 180, 255); break;
    }
}

// RC-derived robot state -> PRISM PWM on 8Servo ch0. Updates only on change so we don't
// spam I2C; if the write fails it retries next tick (state not latched).
static void uiPrism(RcMode m)
{
    static RcMode last = (RcMode)255;
    if (m == last) return;
    uint16_t pulse;
    switch (m) {
        case RCM_ESTOP:
        case RCM_NOLINK:
        case RCM_FAILSAFE:    pulse = kPrismPolice; break;   // alarm
        case RCM_OFF:         pulse = kPrismBlue;   break;
        case RCM_MANUAL_SAFE: pulse = kPrismAmber;  break;
        case RCM_MANUAL_LIVE: pulse = kPrismGreen;  break;
        case RCM_AUTO:        pulse = kPrismAuto;   break;   // violet pulse (kinetic)
        default:              pulse = kPrismBlue;   break;
    }
    (void)pulse; last = m;   // PRISM is on I2C now (LED_COLOR); ch0 = RL drive motor — never PWM ch0 here
}

static void uiRender(uint8_t di, UiState st)
{
    if (ui_canvas == nullptr || ui_canvas->getBuffer() == nullptr) return;
    M5Canvas& g = *ui_canvas;
    const int W = g.width(), H = g.height();
    const uint8_t relay = uiRelay();

    g.fillScreen(TFT_BLACK);
    g.setTextSize(1);

    // header: page name + page-indicator dots (no host tag — state shown at the bottom)
    g.setTextColor(TFT_DARKGREY, TFT_BLACK); g.setCursor(3, 3);
    g.print(ui_page == 0 ? "RC / DRIVE" : "SAFETY");
    for (int i = 0; i < kUiPages; ++i) {
        const int dx = W - 8 - (kUiPages - 1 - i) * 8;
        if (i == ui_page) g.fillCircle(dx, 6, 2, TFT_CYAN);
        else              g.drawCircle(dx, 6, 2, TFT_DARKGREY);
    }
    g.drawFastHLine(0, 13, W, TFT_DARKGREY);

    char buf[40];
    if (ui_page == 0) {                     // ---- RC / DRIVE ----
        const bool rc_live = (last_valid_frame_ms != 0) && (millis() - last_valid_frame_ms < kRcTimeoutMs);
        // hero = MODE (OFF/MANUAL/AUTO); link/e-stop problems override it in red.
        const RcMode rcm = computeRcMode();
        const char* rcw; uint16_t rcc;
        switch (rcm) {
            case RCM_ESTOP:       rcw = "E-STOP";   rcc = TFT_RED;                 break;
            case RCM_NOLINK:      rcw = "NO LINK";  rcc = TFT_RED;                 break;
            case RCM_FAILSAFE:    rcw = "FAILSAFE"; rcc = TFT_RED;                 break;
            case RCM_OFF:         rcw = "OFF";      rcc = g.color565(150,150,150); break;
            case RCM_MANUAL_SAFE: rcw = "MANUAL";   rcc = g.color565(255,150,0);   break;  // amber: deadman released
            case RCM_MANUAL_LIVE: rcw = "MANUAL";   rcc = TFT_GREEN;               break;  // green: deadman held
            case RCM_AUTO:        rcw = "AUTO";     rcc = g.color565(180,120,255); break;  // violet
            default:              rcw = "--";       rcc = TFT_DARKGREY;            break;
        }
        g.setTextSize(3); g.setTextColor(rcc, TFT_BLACK);
        g.setCursor(6, 18); g.print(rcw);
        static unsigned long hz_t = 0; static uint32_t hz_f = 0; static int hz = 0;
        const unsigned long now = millis();
        if (now - hz_t >= 500) { hz = (int)((uint64_t)(valid_frames - hz_f) * 1000 / (now - hz_t)); hz_f = valid_frames; hz_t = now; }
        g.setTextSize(1); g.setTextColor(TFT_DARKGREY, TFT_BLACK);
        snprintf(buf, sizeof(buf), "%dHz", rc_live ? hz : 0);
        g.setCursor(W - 6 - g.textWidth(buf), 42); g.print(buf);

        // drive bars — real mapping: FWD/REV=CH3(idx2), ROT=CH1(idx0), STR=CH4(idx3)
        static const char* labels[3] = { "F/R", "ROT", "STR" };
        static const int   chidx[3]  = { 2,     0,     3     };
        const int y0 = 50, rh = 20, bx = 46, bw = W - bx - 8, bh = 12;
        const int mid = 992, lo = 172, hi = 1811, half = bw / 2 - 2;
        for (int i = 0; i < 3; ++i) {
            const int y = y0 + i * rh;
            const uint16_t val = rc_live ? rc_ch[chidx[i]] : (uint16_t)mid;
            g.setTextColor(TFT_WHITE, TFT_BLACK); g.setTextSize(1); g.setCursor(6, y + 3); g.print(labels[i]);
            g.drawRect(bx, y, bw, bh, TFT_DARKGREY);
            const int cx = bx + bw / 2;
            g.drawFastVLine(cx, y - 2, bh + 4, g.color565(110, 110, 110));
            float frac = (val >= mid) ? (float)(val - mid) / (hi - mid) : (float)(val - mid) / (mid - lo);
            if (frac > 1.f) frac = 1.f; if (frac < -1.f) frac = -1.f;
            const int px = (int)(frac * half);
            const uint16_t fc = rc_live ? TFT_CYAN : g.color565(60, 60, 60);
            if (px >= 0) g.fillRect(cx, y + 1, px, bh - 2, fc);
            else         g.fillRect(cx + px, y + 1, -px, bh - 2, fc);
        }
        // bottom: DEADMAN (CH6, left) + SPEED (CH8, right) — MODE is now the hero line
        const uint16_t dm = rc_live ? rc_ch[5] : 0;   // CH6 deadman
        const bool dead   = rc_live && dm > 1000;     // >1000 assumed = held (verify polarity)
        const char* dtxt  = !rc_live ? "DEAD:--" : (dead ? "DEAD:HELD" : "DEAD:rel");
        g.setTextColor(dead ? TFT_GREEN : g.color565(150,150,150), TFT_BLACK);
        g.setCursor(4, H - 10); g.print(dtxt);
        int spd = rc_live ? (int)(((long)rc_ch[7] - lo) * 100 / (hi - lo)) : 0;   // CH8 -> %
        if (spd < 0) spd = 0; if (spd > 100) spd = 100;
        snprintf(buf, sizeof(buf), "SPD:%d%%", spd);
        g.setTextColor(TFT_DARKGREY, TFT_BLACK);
        g.setCursor(W - 6 - g.textWidth(buf), H - 10); g.print(buf);
    } else {                                // ---- SAFETY ----
        auto row = [&](int y, const char* k, const char* v, uint16_t vc) {
            g.setTextColor(TFT_DARKGREY, TFT_BLACK); g.setCursor(6, y);  g.print(k);
            g.setTextColor(vc,          TFT_BLACK); g.setCursor(104, y); g.print(v);
        };
        row(22, "E-STOP IN1",   (di & 1)        ? "ACTIVE" : "clear",  (di & 1)        ? TFT_RED : TFT_GREEN);
        row(34, "CONTACTOR R1", (relay & 1)      ? "CLOSED" : "OPEN",   (relay & 1)      ? TFT_GREEN : g.color565(255, 150, 0));
        row(46, "WELD IN2",     (di & (1 << 1)) ? "WELD!"  : "ok",     (di & (1 << 1)) ? TFT_RED : TFT_GREEN);
        snprintf(buf, sizeof(buf), "R2:%d R3:%d R4:%d", (relay >> 1) & 1, (relay >> 2) & 1, (relay >> 3) & 1);
        row(58, "RELAYS", buf, TFT_WHITE);
        row(70, "IP", eth_ip_str, eth_up ? TFT_GREEN : g.color565(150,150,150));
        if (estopc_down && !estopc_fired) {         // hold-to-e-stop progress
            uint32_t held = millis() - estopc_start_ms; if (held > kEstopHoldMs) held = kEstopHoldMs;
            const int bw = W - 12;
            g.drawRect(6, 84, bw, 12, TFT_RED);
            g.fillRect(6, 84, (int)((uint64_t)bw * held / kEstopHoldMs), 12, TFT_RED);
            g.setTextColor(TFT_WHITE, TFT_BLACK); g.setCursor(10, 100); g.print("HOLD to E-STOP");
        } else if (local_estop_active) {
            g.setTextColor(TFT_RED, TFT_BLACK);      g.setCursor(6, 86);  g.print("LOCAL E-STOP ACTIVE");
            g.setTextColor(TFT_DARKGREY, TFT_BLACK); g.setCursor(6, 100); g.print("host RELAY 0 1 to clear");
        }
        g.drawFastHLine(0, H - 13, W, TFT_DARKGREY);   // SAFETY page keeps a footer rule
    }

    g.pushSprite(0, 0);
}

static void uiButtons()
{
    // Buttons stubbed: M5.begin()+M5StamPLC.begin() double-init breaks M5Unified's StampPLC
    // button state, so touching BtnA/B/C faults. Our firmware needs that explicit M5.begin()
    // (single-begin panics in setup). Screen + LED work without buttons. See setup() note.
    return;
    if (M5StamPLC.BtnA.wasClicked()) ui_page = (ui_page + 1) % kUiPages;
    if (M5StamPLC.BtnA.wasHold())    ui_page = (ui_page + kUiPages - 1) % kUiPages;
    if (M5StamPLC.BtnB.wasClicked()) M5StamPLC.tone(1500, 60);
    if (M5StamPLC.BtnB.wasHold())    { identify_until_ms = millis() + 2000; M5StamPLC.tone(2000, 180); Serial.println("[UI] identify"); }

    // BtnC: hold-to-confirm LOCAL e-stop (de-energize only — drops the R0 contactor).
    if (M5StamPLC.BtnC.isPressed()) {
        if (!estopc_down) { estopc_down = true; estopc_start_ms = millis(); estopc_fired = false; }
        if (!estopc_fired && (millis() - estopc_start_ms) >= kEstopHoldMs) {
            M5StamPLC.writePlcRelay(0, false);
            local_estop_active = true; estopc_fired = true;
            M5StamPLC.tone(2600, 300);
            Serial.println("[IO] cmd ESTOP (local btn) -> relay0 OFF (contactor open)");
        }
    } else {
        estopc_down = false;
    }
}

static void uiSetup()
{
    auto& d = M5StamPLC.Display;
    d.setRotation(1);
    d.setTextFont(1);                 // ensure a valid font on the display path too
    d.fillScreen(TFT_BLACK);
    Serial.printf("[UI] display %dx%d, free heap %u\n",
                  d.width(), d.height(), (unsigned)ESP.getFreeHeap());

    ui_canvas = new M5Canvas(&d);
    ui_canvas->setColorDepth(16);
    void* buf = ui_canvas->createSprite(d.width(), d.height());   // ~64KB; retry at 8bpp
    if (buf == nullptr) {
        ui_canvas->setColorDepth(8);
        buf = ui_canvas->createSprite(d.width(), d.height());
    }
    if (buf == nullptr) {
        Serial.println("[UI] sprite alloc failed — screen disabled");
        delete ui_canvas; ui_canvas = nullptr;
    } else {
        ui_canvas->setTextFont(1);    // a new sprite has no font selected → set GLCD font
        ui_canvas->setTextWrap(false);
        Serial.printf("[UI] sprite ok %dx%d bpp=%d\n",
                      ui_canvas->width(), ui_canvas->height(), 16);
    }
    M5StamPLC.setStatusLight(0, 0, 0);
}

void setup()
{
    // NOTE on buttons: our firmware needs this explicit M5.begin(cfg) first (removing it
    // panics during M5StamPLC.begin() with our CAN/ext-I2C/UART setup). But M5StamPLC.begin()
    // then calls M5.begin() AGAIN, and that double-init leaves M5Unified's StampPLC button
    // state broken, so touching BtnA/B/C faults. The examples avoid this by calling only
    // M5StamPLC.begin(). Reconciling the two needs deeper work — buttons are stubbed for now
    // (see uiButtons). Screen + status LED are unaffected.
    auto cfg            = M5.config();
    cfg.serial_baudrate = 115200;
    M5.begin(cfg);

    // Non-blocking USB-CDC TX: if the host reader (dashboard parser) lags, telemetry lines
    // DROP instead of blocking Serial.printf and stalling the 50 Hz control loop. A stalled
    // loop was starving SBUS servicing → [RC] gaps → the intermittent dashboard "RC DISCONNECTED".
    Serial.setTxTimeoutMs(0);

    // Enable CAN (TWAI) so we can sniff PDH traffic on the StampPLC CAN pins.
    auto stamplc_cfg        = M5StamPLC.config();
    stamplc_cfg.enableCan   = true;
    stamplc_cfg.canBaudRate = 1000000;
    M5StamPLC.config(stamplc_cfg);
    M5StamPLC.begin();

    // Bind the external scanner explicitly to Port A (Grove RED, G2/G1) on
    // I2C_NUM_1. Release first in case M5.begin()/M5StamPLC.begin() already
    // enabled Ex_I2C on a different (internal) pin set.
    extI2C = &M5.Ex_I2C;
    extI2C->release();
    extI2C->begin(I2C_NUM_1, kPortASda, kPortAScl);

    Serial.println("\nM5StamPLC RC Probe");
    Serial.println("Receiver TX should go to blue-port G5 (RX).");
    Serial.println("SBUS is locked to inverted mode on the blue port.");
    if (extI2C && extI2C->isEnabled()) {
        Serial.printf("[I2C] external scan bus = Port A (Grove RED) SDA=G%d SCL=G%d (I2C_NUM_1)\n",
                      extI2C->getSDA(), extI2C->getSCL());
        // Detect the PaHUB once at boot. Present => device access routes through the
        // mux channels; absent => flat Port-A access (back-compat with no-hub wiring).
        g_paHubPresent = probeI2CAddress(*extI2C, kPaHubAddr);
        Serial.printf("[I2C] PaHub @0x70: %s\n", g_paHubPresent ? "present (mux routing on)" : "absent (flat)");
        if (g_paHubPresent) deselectHub();
    } else {
        Serial.println("[I2C] external bus (Port A) failed to initialize");
    }

    // Tell the PRISM its true strip length so ALL segments light — the CONTROL-reg length is
    // NOT set by LED_COLOR (prismSolid), so without this the tail LEDs can stay dark.
    prismSetStripLength(prism_strip_len);
    prismOverlayHide();   // ensure the LAYER_1 overlay (turn / Z-axis) starts hidden

    beginProfile(0);
    scanExternalBus();
    last_i2c_scan_ms = millis();
    Serial.println("[CAN] Sniffer active @ 1000000 bps on G42(TX)/G43(RX)");
    Serial.println("[PDH] host commands: PDH_SWITCH 0|1, PDH_CLEAR_FAULTS, PDH_ID <0..63>, PDH_ID_AUTO");

    uiSetup();
    Serial.println("[UI] local screen active: BtnA=page, BtnB=id, BtnC(hold)=E-STOP");
    rs485Init(g_rs485Baud);   // RS485 (BMS) probe — passive RX logged; active via BMS/RS485TX cmds
    rcCfgBegin();
    tofBegin(&extI2C, []() { return selectChannel(kChTof); });
    g_mast.loadCfg();
    esp_task_wdt_init(kWdtTimeoutS, true);   // auto-reset the chip if the loop hangs (feed each loop)
    esp_task_wdt_add(NULL);                  // watch the loop task
    Serial.printf("[WDT] task watchdog %us (panic-reset on loop hang)\n", (unsigned)kWdtTimeoutS);

    // --- PoE W5500 Ethernet: shares the LCD/SD SPI bus (G7/G9/G8); leave RST untouched
    // (RST=G3 is also the LCD reset). Bounded DHCP so a missing cable can't hang setup(). ---
    SPI.begin(7, 9, 8, -1);              // SCK=G7, MISO=G9, MOSI=G8, no HW-SS
    Ethernet.init(11);                   // W5500 CS = G11
    uint64_t cid = ESP.getEfuseMac();
    eth_mac[0] = 0x02;                 eth_mac[1] = (uint8_t)(cid >> 32); eth_mac[2] = (uint8_t)(cid >> 24);
    eth_mac[3] = (uint8_t)(cid >> 16); eth_mac[4] = (uint8_t)(cid >> 8);  eth_mac[5] = (uint8_t)cid;
    Serial.printf("[NET] eth mac %02X:%02X:%02X:%02X:%02X:%02X, DHCP...\n",
                  eth_mac[0], eth_mac[1], eth_mac[2], eth_mac[3], eth_mac[4], eth_mac[5]);
    if (netBringUp(true)) {                               // DHCP acquire (short timeout); captures cfg for self-heal
        Serial.printf("[NET] eth UP ip=%s link=%d  udp->%s:%u\n",
                      eth_ip_str, (int)Ethernet.linkStatus(), eth_bcast.toString().c_str(), kNetPort);
    } else {
        Serial.printf("[NET] eth DHCP failed link=%d — netMaintain() will retry in the background\n",
                      (int)Ethernet.linkStatus());
    }

    // FIX: the internal I2C bus (I2C_NUM_0, SDA=G13/SCL=G15 — AW9523 relays/DIs + power/temp
    // sensors live here) ends up DEAD after our M5.begin(cfg)+M5StamPLC.begin()+ext-I2C/SPI/Ethernet
    // init (relays don't actuate, DIs read low, power/temp read 0). Re-assert the bus LAST (mirrors
    // M5StamPLC::i2c_init) and re-init IOExpander B on it so the relay/DI subsystem works.
    M5.In_I2C.release();
    M5.In_I2C.begin(I2C_NUM_0, 13, 15);
    {
        auto& b = M5StamPLC.getIOExpanderB();
        if (b.begin()) {
            b.configureDirection(0x0);
            b.openDrainPort0(false);
            for (int i = 0; i < 4; ++i) { b.pinMode(i, AW9523_Class::AW_OUTPUT); b.digitalWrite(i, false); }
            for (int i : {4, 5, 6, 7, 12, 13, 14, 15}) b.pinMode(i, AW9523_Class::AW_INPUT);
            Serial.println("[FIX] In_I2C re-asserted + AW9523 re-init OK");
        } else {
            Serial.println("[FIX] AW9523 re-begin FAILED (internal bus still dead)");
        }
    }
}

// Broadcast a compact JSON snapshot over UDP for Thor / the dashboard to consume.
static void netTelemetry()
{
    if (!eth_up) return;
    const uint8_t di = uiDi(), relay = uiRelay();
    const RcMode m = computeRcMode();
    const char* mode = (m == RCM_OFF) ? "OFF"
                     : (m == RCM_MANUAL_SAFE || m == RCM_MANUAL_LIVE) ? "MANUAL"
                     : (m == RCM_AUTO) ? "AUTO"
                     : (m == RCM_ESTOP) ? "ESTOP"
                     : (m == RCM_FAILSAFE) ? "FAILSAFE" : "NOLINK";
    const bool rc_live = (last_valid_frame_ms != 0) && (millis() - last_valid_frame_ms < kRcTimeoutMs);
    char buf[320];
    int n = snprintf(buf, sizeof(buf),
        "{\"seq\":%lu,\"up_s\":%lu,"
        "\"rc\":{\"live\":%s,\"mode\":\"%s\",\"dead\":%s,\"fr\":%u,\"rot\":%u,\"str\":%u,\"mast\":%u},"
        "\"io\":{\"di\":%u,\"relay\":%u,\"estop\":%s},"
        "\"pwr\":{\"busv\":%.2f,\"pdh_v\":%.2f,\"pdh_w\":%.1f,\"temp\":%.1f}}",
        (unsigned long)(++net_seq), (unsigned long)(millis() / 1000),
        rc_live ? "true" : "false", mode, (rc_live && rc_ch[5] > 1000) ? "true" : "false",
        rc_ch[2], rc_ch[0], rc_ch[3], rc_ch[1],
        (unsigned)di, (unsigned)relay, local_estop_active ? "true" : "false",
        M5StamPLC.getPowerVoltage(), pdh.v_bus, pdh.total_power, M5StamPLC.getTemp());
    if (n > 0) {
        eth_udp.beginPacket(eth_bcast, kNetPort);
        eth_udp.write((const uint8_t*)buf, n);
        eth_udp.endPacket();
    }
}

// Bring the W5500 up. dhcp=true acquires a lease (blocks up to the SHORT timeout — boot/retry only);
// dhcp=false re-asserts the last-acquired address STATICALLY and non-blocking — the self-heal path,
// so a mid-run re-init can't stall the 50 Hz control loop. Ethernet.begin() re-runs the W5500 SPI
// init either way, so this also recovers a chip that has wedged, not just a cable that dropped.
static bool netBringUp(bool dhcp)
{
    if (dhcp) {
        if (Ethernet.begin(eth_mac, 4000, 1500) != 1) { eth_up = false; return false; }
        eth_cfg_ip = Ethernet.localIP();  eth_cfg_dns = Ethernet.dnsServerIP();
        eth_cfg_gw = Ethernet.gatewayIP(); eth_cfg_subnet = Ethernet.subnetMask();
        eth_have_cfg = true;
    } else {
        if (!eth_have_cfg) return false;
        Ethernet.begin(eth_mac, eth_cfg_ip, eth_cfg_dns, eth_cfg_gw, eth_cfg_subnet);  // static, non-blocking
    }
    snprintf(eth_ip_str, sizeof(eth_ip_str), "%s", Ethernet.localIP().toString().c_str());
    eth_bcast = IPAddress(255, 255, 255, 255);
    eth_udp.begin(kNetPort);
    eth_up = true; eth_link_down_ticks = 0;
    return true;
}

// Keep Ethernet healthy at 1 Hz: retry if it never came up, watch the PHY link, self-heal on a drop
// with a fast static re-init, and renew the DHCP lease. This is exactly what the old code lacked —
// eth_up latched true at boot and nothing ever re-checked the link or re-inited the chip, so a single
// drop was permanent until a full reset.
static void netMaintain()
{
    if (millis() - last_eth_ms < 1000) return;   // 1 Hz
    last_eth_ms = millis();

    static unsigned long last_net_stat = 0;       // periodic health line (link / re-inits / heartbeat age)
    if (millis() - last_net_stat > 5000) {
        last_net_stat = millis();
        tprintf("[NET] link=%d ip=%s reinit=%u hb_age=%lu\n",
                (int)Ethernet.linkStatus(), eth_ip_str, eth_reinit_count,
                hb_seen ? (unsigned long)(millis() - last_hb_rx_ms) : 0UL);
    }

    if (!eth_up) {                                // never up, or torn down → retry (rate-limited)
        if (millis() - last_eth_reinit_ms > 5000) {
            last_eth_reinit_ms = millis();
            if (netBringUp(!eth_have_cfg)) Serial.printf("[NET] eth recovered ip=%s\n", eth_ip_str);
        }
        return;
    }

    if (Ethernet.linkStatus() == LinkOFF) {       // cable/PHY down (or a wedged chip reporting no link)
        if (eth_link_down_ticks < 255) eth_link_down_ticks++;
        if (eth_link_down_ticks >= 2 && millis() - last_eth_reinit_ms > 3000) {
            last_eth_reinit_ms = millis();
            eth_reinit_count++;
            Serial.printf("[NET] link down — self-heal re-init #%u\n", eth_reinit_count);
            netBringUp(false);                    // static, non-blocking
        }
        return;                                   // don't broadcast into a dead link
    }
    eth_link_down_ticks = 0;

    // Heartbeat watchdog: link READS up, but Thor's HB stopped arriving ⇒ the RX stack is wedged
    // (linkStatus() can't see that) ⇒ force a re-init. Only armed once we've heard at least one HB.
    if (hb_seen && (millis() - last_hb_rx_ms > kHbTimeoutMs) && (millis() - last_eth_reinit_ms > 3000)) {
        last_eth_reinit_ms = millis(); eth_reinit_count++;
        Serial.printf("[NET] heartbeat lost %lums — self-heal re-init #%u\n",
                      (unsigned long)(millis() - last_hb_rx_ms), eth_reinit_count);
        netBringUp(false);
        last_hb_rx_ms = millis();                 // grace window so it can't immediately re-trigger
    }

    int m = Ethernet.maintain();                  // DHCP renew; 1/3 == renew/rebind FAILED
    if (m == 1 || m == 3) { Serial.printf("[NET] DHCP maintain fail(%d) — re-assert static\n", m); netBringUp(false); }
}

// Send one already-formatted telemetry line to the Thor over UDP (broadcast). Called by the
// telemetry tee (temit/tprintf). No-op until the PoE link is up. Kept tiny + non-blocking.
void netSendLine(const char* buf, int n)
{
    if (!eth_up || n <= 0) return;
    eth_udp.beginPacket(eth_bcast, kNetPort);
    eth_udp.write((const uint8_t*)buf, (size_t)n);
    eth_udp.endPacket();
}

// Receive host commands over UDP (unicast from the Thor to this StampPLC's IP:kNetPort) and route
// them through the SAME dispatcher as the USB serial path, so LED_COLOR/PAGE/PDH_SWITCH/etc. keep
// working with USB unplugged. A datagram may carry one or more newline-separated command lines.
// Lines beginning with '[' are ignored — that's our own telemetry echoed back on a broadcast, not
// a command.
static void pollNetCommands()
{
    if (!eth_up) return;
    int psize;
    while ((psize = eth_udp.parsePacket()) > 0) {
        char cbuf[160];
        int len = eth_udp.read((uint8_t*)cbuf, sizeof(cbuf) - 1);
        if (len <= 0) continue;
        cbuf[len] = '\0';
        char* save = nullptr;
        for (char* line = strtok_r(cbuf, "\r\n", &save); line; line = strtok_r(nullptr, "\r\n", &save)) {
            if (line[0] == '\0' || line[0] == '[') continue;   // skip blanks / echoed telemetry
            if (line[0] == 'H' && line[1] == 'B' && line[2] == '\0') {   // Thor Ethernet heartbeat (not a command)
                last_hb_rx_ms = millis(); hb_seen = true; continue;
            }
            handleHostCommand(line);
            last_host_cmd_ms = millis();                       // host-link heartbeat for the local UI
        }
    }
}

// -------- Ethernet OTA ------------------------------------------------------------------------
// HTTP-GET a firmware .bin off the Thor and write it into the inactive OTA partition, then reboot
// into it. Triggered by `OTA http://<host>:<port>/<path>` over serial OR UDP — the whole point is
// reflashing with USB unplugged. Plain HTTP only (no TLS over the W5500). We drop the drive
// contactor first (no motion during a reflash), and on ANY failure we abort and stay on the
// current firmware. A truncated/corrupt image is caught by Update.end(); a complete-but-buggy
// image still needs USB recovery, so keep a USB port reachable. Needs a dual-OTA table (default.csv).
static void otaHttpPull(const char* url)
{
    if (!eth_up) { Serial.println("[OTA] abort: Ethernet down"); return; }

    // Safety: open R0 so the drive bus is dead while we reflash + across the reboot.
    M5StamPLC.writePlcRelay(0, false); local_estop_active = true;
    Serial.println("[OTA] R0 opened (drive bus down for reflash)");

    // Parse http://host[:port]/path (port defaults to 80).
    const char* pp = url;
    if (strncmp(pp, "http://", 7) == 0) pp += 7;
    char host[64]; char path[96]; int port = 80; int hi = 0;
    while (*pp && *pp != ':' && *pp != '/' && hi < (int)sizeof(host) - 1) host[hi++] = *pp++;
    host[hi] = '\0';
    if (*pp == ':') { port = atoi(pp + 1); while (*pp && *pp != '/') pp++; }
    snprintf(path, sizeof(path), "%s", (*pp == '/') ? pp : "/");
    if (hi == 0 || port <= 0) { Serial.println("[OTA] abort: bad URL (want http://host[:port]/path)"); return; }
    tprintf("[OTA] fetching http://%s:%d%s\n", host, port, path);

    EthernetClient client;
    client.setConnectionTimeout(5000);
    IPAddress ip; bool ok;
    if (ip.fromString(host)) ok = client.connect(ip, (uint16_t)port);   // dotted-quad: skip DNS
    else                     ok = client.connect(host, (uint16_t)port); // hostname: needs DNS
    if (!ok) { Serial.println("[OTA] abort: connect failed"); return; }

    char req[192];
    int rn = snprintf(req, sizeof(req),
        "GET %s HTTP/1.1\r\nHost: %s\r\nUser-Agent: opendubs-stamplc\r\nConnection: close\r\n\r\n",
        path, host);
    client.write((const uint8_t*)req, rn);

    // ---- status line + headers ----
    long content_len = -1; int status = 0;
    char line[160]; uint32_t t0 = millis(); bool first = true, headers_done = false;
    while (!headers_done) {
        int li = 0;
        for (;;) {
            int c = client.read();
            if (c < 0) {
                if (!client.connected() && client.available() == 0) { headers_done = true; break; }
                if (millis() - t0 > 8000) { client.stop(); Serial.println("[OTA] abort: header timeout"); return; }
                esp_task_wdt_reset(); delay(1); continue;
            }
            t0 = millis();
            if (c == '\r') continue;
            if (c == '\n') break;
            if (li < (int)sizeof(line) - 1) line[li++] = (char)c;
        }
        if (headers_done) break;
        line[li] = '\0';
        if (first) { first = false; sscanf(line, "HTTP/%*s %d", &status); }
        else if (li == 0) headers_done = true;
        else if (strncasecmp(line, "Content-Length:", 15) == 0) content_len = atol(line + 15);
    }
    if (status != 200) { client.stop(); Serial.printf("[OTA] abort: HTTP status %d\n", status); return; }
    if (content_len <= 0) { client.stop(); Serial.println("[OTA] abort: no/zero Content-Length"); return; }
    tprintf("[OTA] status=200 len=%ld — writing…\n", content_len);

    if (!Update.begin((size_t)content_len)) {
        Serial.printf("[OTA] abort: Update.begin failed: %s\n", Update.errorString());
        client.stop(); return;
    }

    // ---- stream body → flash ----
    size_t written = 0; uint8_t buf[1024]; uint32_t tlast = millis();
    while (written < (size_t)content_len) {
        int avail = client.available();
        if (avail <= 0) {
            if (!client.connected()) break;
            if (millis() - tlast > 8000) { Update.abort(); client.stop(); Serial.println("[OTA] abort: body timeout"); return; }
            esp_task_wdt_reset(); delay(1); continue;
        }
        int toread = avail > (int)sizeof(buf) ? (int)sizeof(buf) : avail;
        int n = client.read(buf, toread);
        if (n <= 0) continue;
        if (Update.write(buf, n) != (size_t)n) { Update.abort(); client.stop(); Serial.printf("[OTA] abort: flash write failed: %s\n", Update.errorString()); return; }
        written += n; tlast = millis();
        esp_task_wdt_reset();                                  // multi-second write — keep the WDT fed
        if ((written & 0x1FFFF) == 0) tprintf("[OTA] %u/%ld\n", (unsigned)written, content_len);
    }
    client.stop();

    if (written != (size_t)content_len) { Update.abort(); tprintf("[OTA] abort: short read %u/%ld\n", (unsigned)written, content_len); return; }
    if (!Update.end(true)) { Serial.printf("[OTA] abort: Update.end failed: %s\n", Update.errorString()); return; }

    tprintf("[OTA] OK %u bytes — rebooting into new firmware\n", (unsigned)written);
    delay(250);                                               // flush the final line over USB + UDP
    ESP.restart();
}

void loop()
{
    esp_task_wdt_reset();   // feed the task watchdog — a hang > kWdtTimeoutS resets the chip
    M5.update();
    M5StamPLC.update();

    while (rc.available() > 0) {
        const int b = rc.read();
        if (b < 0) {
            break;
        }
        total_bytes++;
        consumeByte(static_cast<uint8_t>(b));
    }

    pollHostCommands();
    pollNetCommands();   // same dispatcher, over Ethernet UDP (works with USB unplugged)
    // BMS poll is a NON-BLOCKING state machine (bmsStep) so it never stalls the control loop
    // (the old blocking read caused [RC] gaps → spurious dashboard NO-LINK). Absent-pack
    // backoff is handled inside bmsStep. Manual-probe mode just logs raw RS485 RX.
    if (g_bmsAuto) bmsStep();
    else           rs485Poll();

    if ((millis() - last_status_ms) > 1000) {
        tprintf("[SBUS] prof=%s bytes=%lu ok=%lu bad=%lu last_ok_ms=%lu\n",
                      kProfiles[0].name,
                      static_cast<unsigned long>(total_bytes),
                      static_cast<unsigned long>(valid_frames),
                      static_cast<unsigned long>(invalid_frames),
                      static_cast<unsigned long>(last_valid_frame_ms));
        last_status_ms = millis();
    }

    if ((millis() - last_sens_ms) > 1000) {
        const float power_v = M5StamPLC.getPowerVoltage();
        tprintf("[SENS] power=%.2fV\n", power_v);
        last_sens_ms = millis();
    }

    envStep();          // ENVIII (regen-resistor thermal) — non-blocking, self-throttled to ~1 Hz
    tofTick(millis());  // Z-height (VL53L1X on ch3) — one I2C transaction per call, at most
    static uint32_t lastTofPub = 0;
    if (millis() - lastTofPub > 250) { lastTofPub = millis(); tofPublish(); }
    prismOverlayStep();   // PRISM LAYER_1: Z-axis hazard over turn-signals, non-blocking flash

    if ((millis() - last_i2c_scan_ms) > 5000) {
        scanExternalBus();
        last_i2c_scan_ms = millis();
    }

    // Low-priority passive CAN sniff with per-loop budget so RC path stays responsive.
    twai_message_t can_msg;
    const uint32_t can_start_us = micros();
    uint8_t can_processed = 0;
    while (can_processed < kCanMaxMsgsPerLoop && (micros() - can_start_us) < kCanBudgetUs && twai_receive(&can_msg, 0) == ESP_OK) {
        can_processed++;
        can_rx_frames++;
        last_can_rx_ms = millis();

        if (can_msg.extd) {
            bool known = false;
            for (uint8_t i = 0; i < can_ext_seen_count; ++i) {
                if (can_ext_seen[i] == can_msg.identifier) {
                    known = true;
                    break;
                }
            }
            if (!known && can_ext_seen_count < 64) {
                can_ext_seen[can_ext_seen_count++] = can_msg.identifier;
                can_rx_ext_unique++;
                Serial.printf("[CAN] new ext id=0x%08lX dlc=%u\n",
                              static_cast<unsigned long>(can_msg.identifier),
                              static_cast<unsigned>(can_msg.data_length_code));
            }

            decodePdhStatusFrame(can_msg);
        } else {
            const uint16_t sid = static_cast<uint16_t>(can_msg.identifier & 0x7FF);
            if (!can_std_seen[sid]) {
                can_std_seen[sid] = true;
                can_rx_std_unique++;
                Serial.printf("[CAN] new std id=0x%03X dlc=%u\n",
                              static_cast<unsigned>(sid),
                              static_cast<unsigned>(can_msg.data_length_code));
            }
        }
    }

    if ((millis() - last_can_status_ms) > 1000) {
        tprintf("[CAN] rx=%lu uniq_std=%lu uniq_ext=%lu last_rx_ms=%lu\n",
                      static_cast<unsigned long>(can_rx_frames),
                      static_cast<unsigned long>(can_rx_std_unique),
                      static_cast<unsigned long>(can_rx_ext_unique),
                      static_cast<unsigned long>(last_can_rx_ms));
        last_can_status_ms = millis();
    }

    if ((millis() - last_pdh_pub_ms) > 1000) {
        publishPdhSummary();
        last_pdh_pub_ms = millis();
    }

    // [IO]/[ENC] at 10 Hz so DI/relay/safety/encoder telemetry is responsive
    // (the dashboard's fast status group pushes at ~10 Hz). Still cheap — a few
    // I2C reads + one printf.
    if ((millis() - last_io_pub_ms) > 100) {
        publishIo();
        pinpointPoll();        // goBILDA Pinpoint pose (I2C, PaHub ch4)
        publishOdo();
        last_io_pub_ms = millis();
    }

    // ---- Drive logic (50 Hz). driveTick() ALWAYS computes the gate/arm/mix so the
    // [DRIVE] telemetry is live for verification; only the physical PWM write inside
    // it is gated on DRIVE_ENABLE. So you can watch arming/protection work with no
    // signal reaching the ESCs until you flip the flag. ----
    if ((millis() - last_drive_ms) >= kDriveTickMs) {
        driveTick();
        rcCalTick(rc_ch);   // accumulate endpoints while an RCCAL SWEEP runs
        last_drive_ms = millis();
    }
    if ((millis() - last_drive_pub_ms) > 100) { publishDrive(); last_drive_pub_ms = millis(); }
    if ((millis() - last_rc_pub_ms) > 100)    { publishRc();    last_rc_pub_ms = millis(); }

    // ---- Ethernet (24V-powered board, wired LAN — NOT PoE): self-heal link watchdog + 5 Hz telemetry ----
    netMaintain();                                                  // link monitor + re-init + DHCP renew (1 Hz inside)
    if (eth_up && (millis() - last_net_tx) > 200) { netTelemetry(); last_net_tx = millis(); }

    // ---- Local UI: buttons every loop (catch edges), screen + LED at ~6 Hz ----
    uiButtons();
    if ((millis() - ui_last_draw_ms) > 150) {
        const uint8_t di = uiDi();
        uint32_t fault_mask;
        const UiState st = uiState(di, fault_mask);
        if (fault_mask & ~ui_last_fault_mask) M5StamPLC.tone(2200, 120);  // chirp on a NEW fault
        ui_last_fault_mask = fault_mask;
        uiLed(st);
        uiPrism(computeRcMode());   // mirror RC-derived state onto the PRISM (8Servo ch0 PWM)
        uiRender(di, st);
        ui_last_draw_ms = millis();
    }

    delay(1);
}
