// THE NECK. An M5Dial (ESP32-S3) that stays with the robot and carries the pan/tilt, after the
// tablet took over the face. It replaces the Tab5 as the neck, and replaces the AtomS3R in the
// plan -- it is not a stand-in for either, so nothing here should read as temporary.
//
// WHAT IT ANSWERS, from head_protocol.py's own table: PING and REBOOT and OTA are `device` (both
// halves answer them); GAZE, NECK, PARK, TOUCH, TRACK, TOF, I2CSCAN, CHEST, HOLD and BADGE are `neck`.
//
// ITS ONE JOB FOR A PERSON IS HOLD (neck/hold.h): the knob turned one way holds the head where it is,
// the other way releases it, and the screen is a status badge (src/badge_screen.h). The button keeps
// its one job, confirming a tilt-limit change. The face half --
// EMOTE, SKIN, MOVE, CUE, the mouth, ARMED -- belongs to the tablet and is not here at all.
//
// THIS FILE IS STEP ONE: it joins Wi-Fi, answers PING as `kind=neck`, and answers NECK honestly.
// There is no pan/tilt wired yet, and `ERR neck no pan/tilt fitted` is a reply the protocol already
// defines for exactly that, so the conformance suite can be run against this before a single servo
// is connected. That is the point of doing it first: the protocol half is proven with no hardware
// risk at all, and when the servos arrive the only new thing is the servos.
//
// POWER: the Dial sits on the E-STOP SWITCHED DRIVE BRANCH, like the Tab5 before it. When the
// e-stop fires, all movement stops and this board loses power with it -- by design. It will stop
// answering PING, and nothing watching it should call that a fault: that is the robot stopping,
// which is what was asked for. It also means "alive but my servos are dead" cannot happen, so
// there is no limp-actuator state to handle.
//
// WI-FI CREDENTIALS ARE PER-ROBOT AND LIVE IN NVS. They are never compiled in and never committed;
// the same rule that kept the Tab5's PSK out of this repo. Set them once over USB with the serial
// `WIFI <ssid> <pass>` line below, which writes NVS and nothing else.

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <Preferences.h>
#include <Wire.h>
#include <HTTPClient.h>
#include <Update.h>
#include <driver/pulse_cnt.h>
#include <driver/gpio.h>
#include <esp_task_wdt.h>
#include <esp_system.h>
#include "badge_screen.h"
#include "ota_verify.h"      // signed OTA: the shared check (firmware/common/ota_verify, firmware-signing.md)
#include "ota_keys.h"        // the signing keys this build trusts (generated from that library's keys/)
#include "../neck/pantilt_core.h"
#include "../neck/hold.h"
#include "../neck/rc_frame.h"
#include "../neck/tilt_confirm.h"
#include "../neck/chest.h"

namespace {

constexpr uint16_t kCmdPort = 4242;      // the head protocol's port, same as the Tab5's
constexpr char kHostName[] = "opendubs-neck";   // -> opendubs-neck.local
constexpr uint32_t kReportMs = 10000;    // telemetry cadence; see "report on a timer" below

// ---- the 8Servo on Grove Port A -------------------------------------------------------------
// PORT A IS `Wire`, NOT `Wire1`. That is a documented M5Dial trap and has cost a session before.
// Registers and the microsecond conversion are the Tab5's, carried over verbatim: the servo sees
// the same pulses it always has, so nothing about the mechanism changes with the controller.
constexpr uint8_t kServoAddr = 0x25;
constexpr uint8_t kModeReg   = 0x00;    // + channel
constexpr uint8_t kPulseReg  = 0x60;    // + channel*2, 16-bit LE microseconds
constexpr uint8_t kModeServo = 3;
constexpr uint8_t kChTilt    = 0;
constexpr uint8_t kChPan     = 1;
// The chest light (goBILDA 3118-0808-0002) behind the chest plate. Channel 2 keeps the driven
// channels contiguous, and the firmware only puts a channel into servo mode if it drives it.
//
// IT IS AN ORDINARY SERVO PULSE AND THE WIDTH IS THE COLOUR -- see neck/chest.h for the map. The
// colour is NOT decided here: the Thor maps the robot's winning state colour (the same one the
// PRISM shows) to a pulse and sends the pulse, so there is one map rather than two that can
// disagree about what red is.
constexpr uint8_t kChLight   = 2;
constexpr bool    kPanReverse = true, kTiltReverse = true;

neck::State  g_pt;
neck::Limits g_lim;                     // the narrow fallback until NVS says otherwise
bool g_servoPresent = false;
float g_gazePan = 90, g_gazeTilt = 90;
uint32_t g_gazeMs = 0, g_gazeLease = neck::kGazeHoldMs;
// RC, from the StamPLC's 0x107 rate frames. A RATE: the stick is spring-centred and the head owns
// the position. Silence is how the operator releases the head, so this expires on the control
// law's RC lease rather than waiting for a "stop" that is never sent.
float g_rcPan = 0, g_rcTilt = 0;
uint32_t g_rcMs = 0, g_rcFrames = 0;
// TOUCH, from the tablet (head protocol §4): HOLD keeps the head still while someone works at the
// screen, renewed by the dashboard while they do; RATE is the on-face aiming pad.
float g_touchPan = 0, g_touchTilt = 0;
uint32_t g_touchMs = 0, g_touchLease = neck::kTouchHoldMs;
constexpr float kTouchRateMax = 45.0f;       // head_protocol.TOUCH_RATE_MAX_DPS

// HOLD, and the knob that sets it (neck/hold.h). RAM only: a reboot always comes back released.
neck::Hold g_hold;
neck::KnobGesture g_knob;
// THE ENCODER, ON THE PCNT: a hardware counter, so no step is missed while the loop is busy with
// the servos or a draw, and no interrupt is spent on it. Pins from the vendor's board table.
constexpr int kEncA = 41, kEncB = 40;
pcnt_unit_handle_t g_enc = nullptr;
bool g_knobUp = false;

// THE BADGE, as the robot last described it (BADGE <STATE> <driver> <rrggbb>), and when. Old after
// kBadgeStaleMs: the dashboard re-sends every 5 s, so three missed sends mean it is not talking.
badge::View g_badge;
uint32_t g_badgeMs = 0;
constexpr uint32_t kBadgeStaleMs = 20000;
// The screen starts once the UDP socket is listening, or this long after boot without a network.
constexpr uint32_t kScreenLateMs = 30000;
const char* g_screenNote = "not started";

uint16_t degToUs(float deg, bool reverse) {
    if (deg < 0.0f) deg = 0.0f;
    if (deg > 180.0f) deg = 180.0f;
    const float d = reverse ? (180.0f - deg) : deg;
    return (uint16_t)(500.0f + d * (2000.0f / 180.0f));   // 0..180 -> 500..2500 us
}

bool servoWrite2(uint8_t reg, uint8_t v) {
    Wire.beginTransmission(kServoAddr); Wire.write(reg); Wire.write(v);
    return Wire.endTransmission() == 0;
}
bool servoPulse(uint8_t ch, uint16_t us) {
    Wire.beginTransmission(kServoAddr);
    Wire.write((uint8_t)(kPulseReg + ch * 2));
    Wire.write((uint8_t)(us & 0xff)); Wire.write((uint8_t)(us >> 8));
    return Wire.endTransmission() == 0;
}

WiFiUDP g_udp;
Preferences g_nvs;
char g_ssid[33] = {0};
uint32_t g_lastReport = 0;
uint32_t g_restartAt = 0;      // 0 = no restart pending; set by REBOOT and by the OTA handler

// SIGNED UPDATES (notes/design/firmware-signing.md; the policy table in firmware/common/ota_verify/
// README.md). An image is installed only if its signature, made on the Thor for board "neck", verifies
// against a key compiled in here; otherwise it is rejected and the running app stays the one that boots.
// TWO EXCEPTIONS, BOTH BECAUSE NO USB CABLE IS ROUTINELY FITTED ON THE ROBOT, so a check that strands
// the neck costs a visit to fit one:
//   * a REPORT-ONLY build (-DOTA_SIG_REPORT_ONLY, env neck_dial_report_only) checks, reports, and
//     installs anyway. It is the bootstrap image only: a signed round trip is proved on it first;
//   * a build whose own self-test FAILS (the verifier itself is broken on this board) behaves the same
//     and says otaverify=BROKEN in PING.
// Nothing sent over the network can choose either: one is the build, the other deterministic code.
constexpr char kOtaBoard[] = "neck";
#ifdef OTA_SIG_REPORT_ONLY
constexpr bool kOtaReportOnly = true;
#else
constexpr bool kOtaReportOnly = false;
static_assert(OTA_KEY_COUNT > 0, "an enforcing neck build must trust a key (firmware/common/ota_verify/keys/), "
                                 "or it would refuse every update and need a cable; build neck_dial_report_only to test");
#endif
bool g_otaSelftestOk = false;                       // set once in setup()
bool otaEnforcing() { return !kOtaReportOnly && g_otaSelftestOk; }

// `otasig=enforce|report otaverify=ok|BROKEN otakeys=<id>[,<id>]|none`, for PING and the report.
void otaFields(char* out, size_t n) {
    char keys[40] = "";
    for (size_t k = 0; k < OTA_KEY_COUNT; k++) {
        char id[17];
        otaKeyId(OTA_KEYS[k], id, sizeof(id));
        snprintf(keys + strlen(keys), sizeof(keys) - strlen(keys), "%s%s", k ? "," : "", id);
    }
    snprintf(out, n, "otasig=%s otaverify=%s otakeys=%s", otaEnforcing() ? "enforce" : "report",
             g_otaSelftestOk ? "ok" : "BROKEN", keys[0] ? keys : "none");
}

// THE TILT CONFIRM BUTTON. BtnA on the M5Dial is GPIO 42, ACTIVE LOW -- taken from M5Unified's own
// board table (board_M5Dial reads `!gpio_in(GPIO_NUM_42)`), not from memory, because a wrong pin
// here means the interlock can never be satisfied and commissioning becomes impossible on a board
// with no USB cable. The M5Dial library is deliberately NOT pulled in for this: it would start the
// display, touch and speaker, and contend for Port A's Wire with the 8Servo.
constexpr uint8_t kBtnPin = 42;
neck::Pending g_tiltPending;
// What the light was last told. Written to the 8Servo only on a CHANGE: an SOC-like value that
// moves rarely does not need a write every tick, and the Thor re-sends every 5 s anyway, so a
// rebooted neck recovers without the firmware polling anything.
uint16_t g_chestUs = neck::kChestOffUs;
bool buttonDown() { return digitalRead(kBtnPin) == LOW; }
uint32_t g_packets = 0;

// The firmware identity a PING reports. Filled by the build, not typed here, so an image can always
// be matched to a commit.
#ifndef NECK_FW_SHA
#define NECK_FW_SHA "0000000000000000"
#endif

// ---- serial ---------------------------------------------------------------------------------
// The rote procedure in CLAUDE.md, and the reasons are not optional:
//   * setTxTimeoutMs(0) or the first print blocks for ever when no host is draining the port;
//   * which makes writes lossy, so print LINE BY LINE with a gap, never a block;
//   * repeat every report on a timer, because attaching misses whatever was printed before;
//   * nothing here is triggered by a serial write from the host -- whether those arrive is
//     unverified, and a feature that needs one cannot be told apart from a broken feature.
// The one exception is WIFI below, which is a provisioning step a human runs at the bench and
// which prints its own verdict either way.
void line(const char* s) { Serial.println(s); delay(3); }

void report() {
    char b[160];
    snprintf(b, sizeof(b), "neck: fw=%s", NECK_FW_SHA);                       line(b);
    snprintf(b, sizeof(b), "wifi: ssid=%s state=%s ip=%s rssi=%d",
             g_ssid[0] ? g_ssid : "(unset)",
             WiFi.isConnected() ? "up" : "down",
             WiFi.isConnected() ? WiFi.localIP().toString().c_str() : "-",
             WiFi.isConnected() ? WiFi.RSSI() : 0);                           line(b);
    snprintf(b, sizeof(b), "udp:  port=%u packets=%lu rc_frames=%lu", kCmdPort,
             (unsigned long)g_packets, (unsigned long)g_rcFrames); line(b);
    if (g_servoPresent) {
        snprintf(b, sizeof(b), "pantilt: p%.0f t%.0f tilt[%.0f..%.0f]%s", g_pt.pan, g_pt.tilt,
                 g_lim.tiltMin, g_lim.tiltMax, g_lim.known ? "" : " FALLBACK");
        line(b);
        snprintf(b, sizeof(b), "chest: ch%u pulse=%uus%s", (unsigned)kChLight, (unsigned)g_chestUs,
                 g_chestUs == neck::kChestOffUs ? " (off)" : "");
        line(b);
    } else {
        line("pantilt: 8servo not answering at 0x25");
    }
    snprintf(b, sizeof(b), "hold: %s by=%s at p%.0f t%.0f  knob=%s  screen=%s",
             neck::holdPhaseName(g_hold), neck::holdByName(g_hold), g_hold.pan, g_hold.tilt,
             g_knobUp ? "up" : "FAILED", g_screenNote);
    line(b);
    char ota[96];
    otaFields(ota, sizeof(ota));
    snprintf(b, sizeof(b), "ota: %s", ota);
    line(b);
}

// ---- the head protocol ------------------------------------------------------------------------

// `PING` -> the neck's PONG, in the shape fake_head.py models and the conformance suite matches.
//
// IT USED TO REPORT pan=0.0 tilt=0.0 AND owner=none, HARD-CODED. That was honest when it was
// written -- no servos were wired, and inventing a position would have been worse than admitting
// to none. The servos have been fitted since, and nobody came back to this: during the tilt
// commissioning NECK reported pan=61.2 tilt=95.4 while PING, one line later, said 0.0/0.0. A
// field that was once "we do not know" had quietly become "we do know, and this is wrong".
const char* ownerName() {
    switch (g_pt.owner) {
        case neck::Owner::Gaze:  return "gaze";
        case neck::Owner::Rc:    return "rc";
        case neck::Owner::Touch: return "touch";
        case neck::Owner::Track: return "track";
        case neck::Owner::Hold:  return "hold";
        default:                 return "none";
    }
}

void replyPing(char* out, size_t n) {
    if (!g_servoPresent) {
        char ota[96];
        otaFields(ota, sizeof(ota));
        snprintf(out, n, "PONG kind=neck fw=%s state=VALID plc=none owner=none pan=0.0 tilt=0.0 %s",
                 NECK_FW_SHA, ota);
        return;
    }
    const char* owner = ownerName();
    char ota[96];
    otaFields(ota, sizeof(ota));
    snprintf(out, n, "PONG kind=neck fw=%s state=VALID plc=none owner=%s pan=%.1f tilt=%.1f %s",
             NECK_FW_SHA, owner, g_pt.pan, g_pt.tilt, ota);
}

// `NECK` -> the status the suite parses. With nothing fitted, the protocol's own
// `ERR neck no pan/tilt fitted` is the honest answer and is already an accepted reply, so this
// does not have to pretend to a position it has no way to know.
void replyNeck(char* out, size_t n) {
    if (!g_servoPresent) { snprintf(out, n, "ERR neck no pan/tilt fitted"); return; }
    const char* owner = ownerName();
    // tilt_band AND commissioned, both of them, because the band alone cannot be checked: the
    // FALLBACK is a legitimate-looking 80..100 and a commissioned robot could in principle be
    // measured to the same pair. Commissioning this neck is exactly the job that needs to be able
    // to tell "measured" from "never measured", and the reference neck already reports a band, so
    // this closes a gap rather than inventing a field.
    char pend[48] = "tilt_pending=none";
    if (g_tiltPending.waiting) {
        snprintf(pend, sizeof(pend), "tilt_pending=%d,%d in=%lus",
                 g_tiltPending.lo, g_tiltPending.hi,
                 (unsigned long)neck::secondsLeft(g_tiltPending, millis()));
    }
    // chest= so a dark plate can be told apart from a light that was never told anything. Without
    // it the only way to ask is to look at the robot, and the whole point of the part is to be
    // read from across a room by somebody who cannot.
    // hold= and hold_by= so the dashboard can show a held head and ANNOUNCE a release (hold.h): it
    // reads `hold=releasing` here for the whole announcement, before the head may move.
    snprintf(out, n, "OK neck pan=%.1f tilt=%.1f target=%.1f,%.1f moving=%d owner=%s tilt_band=%.0f,%.0f commissioned=%d %s chest=%u hold=%s hold_by=%s",
             g_pt.pan, g_pt.tilt, g_pt.panTgt, g_pt.tiltTgt, g_pt.moving ? 1 : 0, owner,
             g_lim.tiltMin, g_lim.tiltMax, g_lim.known ? 1 : 0, pend, (unsigned)g_chestUs,
             neck::holdPhaseName(g_hold), neck::holdByName(g_hold));
}

// TILT <min> <max> -- the per-robot tilt limits, measured ON the robot and written once.
//
// ONE IMPLEMENTATION, TWO TRANSPORTS. It began as a Serial-only provisioning line, which was right
// while the Dial was on a bench with a USB cable. It is now on the robot, powered from the 24 V bus
// with no USB, so the only way to commission it was a cable onto a live board -- and CLAUDE.md is
// explicit that host->device serial writes here are unverified, so that path cannot even tell a
// refused command from a broken one. Hence the same parser, reachable over UDP as well.
//
// The validation is unchanged and is the safety: 0 <= min < max <= 180, nothing else accepted. The
// band is what the control law clamps every target to, so widening it is the one remote command
// that can let the head reach somewhere new -- which is why it refuses rather than clamps, and why
// the reply states what was actually stored.
// By value, for the button path: the numbers were validated when the request was armed.
void applyTilt2(int lo, int hi, char* out, size_t n) {
    g_nvs.begin("opendubs", false);
    g_nvs.putInt("tiltMin", lo); g_nvs.putInt("tiltMax", hi);
    g_nvs.end();
    g_lim = neck::limitsFrom(true, lo, true, hi);      // live at once: no reboot to commission
    snprintf(out, n, "OK tilt %d %d", lo, hi);
}

bool applyTilt(const char* args, char* out, size_t n) {
    int lo = 0, hi = 0;
    if (sscanf(args, "%d %d", &lo, &hi) != 2 || lo >= hi || lo < 0 || hi > 180) {
        snprintf(out, n, "ERR tilt <min> <max>, 0 <= min < max <= 180");
        return false;
    }
    g_nvs.begin("opendubs", false);
    g_nvs.putInt("tiltMin", lo); g_nvs.putInt("tiltMax", hi);
    g_nvs.end();
    g_lim = neck::limitsFrom(true, lo, true, hi);      // live at once: no reboot to commission
    snprintf(out, n, "OK tilt %d %d", lo, hi);
    return true;
}

// OTA, the same shape as the Tab5's: the head PULLS its image over HTTP from a URL we are told,
// because a push needs a listener that has never worked reliably here. It is implemented EARLY --
// out of the planned order -- because the Dial is about to lose its USB cable, and a board with no
// OTA and no USB cannot be fixed at all.
// The detached signature, `<url>.sig`, into `buf`. False if it is missing, empty or too long.
bool fetchSig(const char* url, uint8_t* buf, size_t& len) {
    len = 0;
    char u[300];
    if (snprintf(u, sizeof(u), "%s.sig", url) >= (int)sizeof(u)) return false;
    HTTPClient h;
    if (!h.begin(u)) return false;
    const int code = h.GET();
    const int size = h.getSize();
    if (code != 200 || size <= 0 || size > OTA_SIG_MAX) { h.end(); return false; }
    WiFiClient* st = h.getStreamPtr();
    const uint32_t t0 = millis();
    while (len < (size_t)size && millis() - t0 < 5000) {
        const int a = st->available();
        const size_t left = (size_t)size - len;
        if (a > 0) len += st->read(buf + len, (size_t)a < left ? (size_t)a : left);
        else delay(1);
    }
    h.end();
    return len == (size_t)size;
}

bool otaPull(const char* url, char* out, size_t n) {
    if (!WiFi.isConnected()) { snprintf(out, n, "ERR ota no network"); return false; }
    // The signature first: it is small, and an enforcing neck without one need not fetch the image.
    static uint8_t sig[OTA_SIG_MAX];
    size_t sigLen = 0;
    const bool haveSig = fetchSig(url, sig, sigLen);
    if (!haveSig && otaEnforcing()) { snprintf(out, n, "ERR ota no signature"); return false; }

    HTTPClient http;
    if (!http.begin(url)) { snprintf(out, n, "ERR ota bad url"); return false; }
    const int code = http.GET();
    if (code != 200) { snprintf(out, n, "ERR ota http %d", code); http.end(); return false; }
    const int len = http.getSize();
    if (len <= 0) { snprintf(out, n, "ERR ota no length"); http.end(); return false; }
    if (!Update.begin(len)) { snprintf(out, n, "ERR ota no room"); http.end(); return false; }
    OtaVerify v;
    if (!otaVerifyBegin(v, kOtaBoard)) { Update.abort(); http.end(); snprintf(out, n, "ERR ota cannot hash"); return false; }

    // EVERY BYTE THROUGH THE HASH, so the stream is read here rather than by Update.writeStream().
    WiFiClient* st = http.getStreamPtr();
    static uint8_t buf[2048];
    size_t wrote = 0;
    uint32_t last = millis();
    while (wrote < (size_t)len) {
        const int a = st->available();
        if (a <= 0) {
            if (millis() - last > 8000) {
                Update.abort(); otaVerifyAbort(v); http.end();
                snprintf(out, n, "ERR ota stalled at %u/%d", (unsigned)wrote, len);
                return false;
            }
            delay(1);
            continue;
        }
        size_t want = (size_t)len - wrote;
        if (want > sizeof(buf)) want = sizeof(buf);
        if ((size_t)a < want) want = (size_t)a;
        const int got = st->read(buf, want);
        if (got <= 0) continue;
        if (Update.write(buf, got) != (size_t)got) {
            Update.abort(); otaVerifyAbort(v); http.end();
            snprintf(out, n, "ERR ota write %u/%d", (unsigned)wrote, len);
            return false;
        }
        otaVerifyUpdate(v, buf, got);
        wrote += got;
        last = millis();
    }
    http.end();

    // THE CHECK, BEFORE Update.end(true): that call is what makes the new image the one that boots.
    int which = -1;
    OtaVerifyResult r = OTA_SIG_MISSING;
    if (haveSig) r = otaVerifyFinish(v, sig, sigLen, OTA_KEYS, OTA_KEY_COUNT, &which);
    else otaVerifyAbort(v);
    if (r != OTA_SIG_OK && otaEnforcing()) {
        Update.abort();                                     // the running app stays the one that boots
        snprintf(out, n, "ERR ota rejected: %s", otaVerifyReason(r));
        line(out);
        return false;
    }
    if (!Update.end(true) || !Update.isFinished()) { snprintf(out, n, "ERR ota finish"); return false; }
    if (r == OTA_SIG_OK) {
        char id[17];
        otaKeyId(OTA_KEYS[which], id, sizeof(id));
        snprintf(out, n, "OK ota %d bytes, rebooting key=%s", len, id);
    } else {
        // Report-only, or a broken self-test: installed, and said loudly in the same reply.
        snprintf(out, n, "OK ota %d bytes, rebooting WARN would reject: %s", len, otaVerifyReason(r));
    }
    return true;
}

void handle(const char* cmd, char* out, size_t n) {
    if (!strncmp(cmd, "PING", 4))      { replyPing(out, n); return; }
    if (!strncmp(cmd, "NECK", 4))      { replyNeck(out, n); return; }
    // REBOOT restarts, as OTA does: the reply goes out first, the restart follows in the loop. It used to
    // answer "OK reboot" and do nothing (found 2026-10-26, rotating the robot's Wi-Fi).
    if (!strncmp(cmd, "REBOOT", 6))    { snprintf(out, n, "OK reboot"); g_restartAt = millis() + 400; return; }

    // GAZE <pan> <tilt> [lease_ms] -- an absolute target in degrees. The control law owns what
    // happens next: the slew limit, the bounds and the arbitration are in pantilt_core, not here.
    if (!strncmp(cmd, "GAZE", 4)) {
        float p, t; unsigned lease = 0;
        if (!strncmp(cmd + 4, " OFF", 4)) { g_gazeMs = 0; snprintf(out, n, "OK gaze off"); return; }
        const int got = sscanf(cmd + 4, " %f %f %u", &p, &t, &lease);
        if (got < 2) { snprintf(out, n, "ERR gaze <pan> <tilt> [lease_ms] | OFF"); return; }
        if (!g_servoPresent) { snprintf(out, n, "ERR gaze no pan/tilt fitted"); return; }
        g_gazePan = p; g_gazeTilt = t;
        g_gazeLease = got >= 3 && lease ? lease : neck::kGazeHoldMs;
        g_gazeMs = millis();
        // THE REPLY IS THE CLAMPED ANGLES, ROUNDED -- head-protocol.md: "the clamped angles
        // (rounded)... The reply shows what will actually run, so a sender can see a limit bite."
        // This echoed the REQUEST, unrounded ("OK gaze 120.0 95.0" where the reference neck says
        // "OK gaze 120 95"). Both are wrong and both matter: a sender could not see a limit bite at
        // all, and a reply carrying decimals is not what a consumer parsing the reference's format
        // expects. A live run raised a gaze_warning against this head; these were the two ways this
        // side departed from the spec.
        const float cp = p < neck::kRcPanMin ? neck::kRcPanMin : (p > neck::kRcPanMax ? neck::kRcPanMax : p);
        const float ct = t < g_lim.tiltMin ? g_lim.tiltMin : (t > g_lim.tiltMax ? g_lim.tiltMax : t);
        snprintf(out, n, "OK gaze %d %d", (int)lroundf(cp), (int)lroundf(ct));
        return;
    }

    // HOLD | HOLD HERE | HOLD <pan> <tilt> | HOLD OFF -- head protocol v9 (neck/hold.h). The same hold
    // the knob sets. `HOLD <pan> <tilt>` is for software that aims the head-top camera: the head slews
    // there at its own rate and stays, clamped like a GAZE to pan 0-180 and this robot's tilt band.
    // The reply is the clamped angles, rounded, so a sender sees a limit bite. `HOLD OFF` starts the
    // announced release; NECK says `hold=releasing` until the head is free.
    if (!strncmp(cmd, "HOLD", 4) && (cmd[4] == 0 || cmd[4] == ' ')) {
        if (!g_servoPresent) { snprintf(out, n, "ERR hold no pan/tilt fitted"); return; }
        const char* a = cmd + 4;
        while (*a == ' ') a++;
        if (!strcmp(a, "OFF")) {
            if (neck::holdRelease(g_hold, millis())) line("hold: released over the network, announced");
            snprintf(out, n, "OK hold off");
            return;
        }
        float p = g_pt.pan, t = g_pt.tilt;
        char extra = 0;
        if (*a && strcmp(a, "HERE") && sscanf(a, "%f %f %c", &p, &t, &extra) != 2) {
            snprintf(out, n, "ERR hold [HERE | <pan> <tilt> | OFF]");
            return;
        }
        p = p < neck::kRcPanMin ? neck::kRcPanMin : (p > neck::kRcPanMax ? neck::kRcPanMax : p);
        t = t < g_lim.tiltMin ? g_lim.tiltMin : (t > g_lim.tiltMax ? g_lim.tiltMax : t);
        neck::holdAt(g_hold, p, t, neck::HoldBy::Remote);
        snprintf(out, n, "OK hold %d %d", (int)lroundf(p), (int)lroundf(t));
        line(out);
        return;
    }

    // TOUCH HOLD [lease_ms] | TOUCH RATE <pan_dps> <tilt_dps> [lease_ms] | TOUCH OFF -- agreed in v1,
    // built now: the tablet's touch pauses the head while someone works at the screen (the dashboard
    // renews HOLD while they do, and it lapses shortly after the last touch). Leases cap at 1000 ms.
    if (!strncmp(cmd, "TOUCH", 5) && (cmd[5] == 0 || cmd[5] == ' ')) {
        if (!g_servoPresent) { snprintf(out, n, "ERR touch no pan/tilt fitted"); return; }
        const char* a = cmd + 5;
        while (*a == ' ') a++;
        unsigned lease = 0;
        float rp = 0, rt = 0;
        if (!strcmp(a, "OFF")) { g_touchMs = 0; snprintf(out, n, "OK touch off"); return; }
        if (!strncmp(a, "HOLD", 4) && (a[4] == 0 || (a[4] == ' ' && sscanf(a + 4, " %u", &lease) == 1))) {
            g_touchPan = g_touchTilt = 0;
        } else if (!strncmp(a, "RATE ", 5) && sscanf(a + 5, "%f %f %u", &rp, &rt, &lease) >= 2) {
            g_touchPan  = rp < -kTouchRateMax ? -kTouchRateMax : (rp > kTouchRateMax ? kTouchRateMax : rp);
            g_touchTilt = rt < -kTouchRateMax ? -kTouchRateMax : (rt > kTouchRateMax ? kTouchRateMax : rt);
        } else {
            snprintf(out, n, "ERR touch HOLD [lease_ms] | RATE <pan_dps> <tilt_dps> [lease_ms] | OFF");
            return;
        }
        g_touchLease = lease ? lease : neck::kTouchHoldMs;
        g_touchMs = millis() | 1;            // never 0, which reads as "never touched"
        if (a[0] == 'H') snprintf(out, n, "OK touch hold");
        else snprintf(out, n, "OK touch rate %.0f %.0f", g_touchPan, g_touchTilt);
        return;
    }

    // BADGE <STATE> <driver> <rrggbb> | BADGE OFF -- head protocol v9: what the screen shows. The
    // colour crosses the wire, as the chest light's does, so there is one map of state to colour
    // (the dashboard's) and not a second copy here to disagree with it. No lease: held, as CHEST is,
    // re-sent every 5 s; the screen greys the ring if it stops.
    if (!strncmp(cmd, "BADGE", 5) && (cmd[5] == 0 || cmd[5] == ' ')) {
        const char* a = cmd + 5;
        while (*a == ' ') a++;
        if (!strcmp(a, "OFF")) { g_badge.told = false; snprintf(out, n, "OK badge off"); return; }
        static const char* const kStates[] = {"INIT", "SAFE_IDLE", "MANUAL", "AUTONOMOUS", "DEGRADED", "E_STOP", "CHARGING"};
        char st[16] = "", dr[12] = "", hex[8] = "", extra = 0;
        bool good = sscanf(a, "%15s %11s %7s %c", st, dr, hex, &extra) == 3 && strlen(hex) == 6;
        bool known = false;
        for (const char* k : kStates) known = known || !strcmp(st, k);
        for (const char* c = dr; good && *c; ++c) good = (*c >= 'a' && *c <= 'z');
        for (const char* c = hex; good && *c; ++c) good = isxdigit((unsigned char)*c);
        if (!good || !known) { snprintf(out, n, "ERR badge <STATE> <driver> <rrggbb> | OFF"); return; }
        snprintf(g_badge.state, sizeof(g_badge.state), "%s", st);
        snprintf(g_badge.driver, sizeof(g_badge.driver), "%s", dr);
        g_badge.rgb = strtoul(hex, nullptr, 16);
        g_badge.told = true;
        g_badgeMs = millis();
        snprintf(out, n, "OK badge %s %s %s", st, dr, hex);
        return;
    }

    // TOF: SUPPRESSED on this neck, by decision rather than by omission. The sensor is awkward to
    // move and has had no use yet, so it is not fitted -- and saying so is not the same as the
    // message being unknown. A caller can tell "this neck has no ToF" from "this neck is too old
    // to have heard of ToF", which it could not if this fell through to ERR unknown.
    if (!strncmp(cmd, "TOF", 3)) { snprintf(out, n, "ERR tof not fitted"); return; }

    // CHEST <us> | OFF -- the chest light's pulse, which IS its colour (head protocol v7, agreed
    // with the Tablet lane). No lease: it holds what it was last told, as the PRISM does.
    if (!strncmp(cmd, "CHEST", 5)) {
        // No 8Servo, no light: the unit drives it, so this is the one case the firmware CAN tell.
        // It cannot tell whether anything is plugged into the channel, and does not pretend to.
        if (!g_servoPresent) { snprintf(out, n, "ERR chest not fitted"); return; }
        uint16_t us = 0;
        bool off = false;
        if (!neck::parseChest(cmd + 5, &us, &off)) {
            snprintf(out, n, "ERR chest <us 500-2500> | OFF");
            return;
        }
        if (us != g_chestUs) { servoPulse(kChLight, us); g_chestUs = us; }
        // The reply echoes the CALLER'S FORM, not the pulse's meaning: `CHEST 500` and `CHEST OFF`
        // apply the same pulse, and a backend that sent a number should not read back a word.
        if (off) snprintf(out, n, "OK chest off");
        else     snprintf(out, n, "OK chest %u", (unsigned)us);
        return;
    }

    // TILT <min> <max>: commissioning, over the wire -- ARMED HERE, APPLIED BY THE BUTTON.
    //
    // The maintainers chose this: the band is the fence every target is clamped to, and a band
    // widened remotely followed by a GAZE is two remote commands where the first removes the limit
    // the second would have hit. So a network TILT only ever ARMS a request; somebody at the head
    // presses the button to apply it. The reply says so, and NECK carries the pending state,
    // because the press happens after this reply has gone and there is nowhere else to report it.
    if (!strncmp(cmd, "TILT ", 5)) {
        int lo = 0, hi = 0;
        if (sscanf(cmd + 5, "%d %d", &lo, &hi) != 2
            || !neck::request(g_tiltPending, lo, hi, millis(), buttonDown())) {
            snprintf(out, n, "ERR tilt <min> <max>, 0 <= min < max <= 180");
            return;
        }
        snprintf(out, n, "OK tilt pending %d %d, press the button within %lus",
                 lo, hi, (unsigned long)(neck::kTiltConfirmMs / 1000));
        line(out);
        return;
    }

    if (!strncmp(cmd, "OTA ", 4)) {
        char reply[160];
        const bool ok = otaPull(cmd + 4, reply, sizeof(reply));
        snprintf(out, n, "%s", reply);
        // RESTART LATER, NOT HERE. Restarting inside the handler kills the board before the caller
        // has sent this reply, so a SUCCESSFUL update looks exactly like a dead board -- which is
        // what the first end-to-end test showed: the image downloaded, the firmware changed, and
        // the flashing tool saw NO REPLY. A tool that cannot tell success from bricked is worse
        // than one that is merely slow.
        if (ok) { line(reply); g_restartAt = millis() + 400; }
        return;
    }
    // Everything else the neck owes -- GAZE, PARK, TOUCH, TRACK, TOF, I2CSCAN, OTA -- arrives in
    // the steps that fit the hardware for it. Until then say so, rather than claiming OK for
    // something that will not happen: a show that gets OK and no movement is harder to diagnose
    // than one that is refused.
    snprintf(out, n, "ERR unknown");
}

// The knob's quadrature on a PCNT unit, both edges of both phases: four counts to a detent. ESP-IDF
// 5's pulse_cnt driver, as in its own rotary-encoder example, and NOT the legacy driver/pcnt.h,
// which aborts at boot if anything else in the image links the new one: a crash loop, on a board
// that normally runs with no cable fitted.
bool knobBegin() {
    pcnt_unit_config_t uc = {};
    uc.high_limit = 1000; uc.low_limit = -1000;      // read and cleared every loop: never near either
    if (pcnt_new_unit(&uc, &g_enc) != ESP_OK) return false;
    pcnt_glitch_filter_config_t fc = {};
    fc.max_glitch_ns = 1000;                         // contact bounce, not a detent
    pcnt_unit_set_glitch_filter(g_enc, &fc);
    pcnt_chan_config_t a = {}, b = {};
    a.edge_gpio_num = kEncA; a.level_gpio_num = kEncB;
    b.edge_gpio_num = kEncB; b.level_gpio_num = kEncA;
    pcnt_channel_handle_t ca = nullptr, cb = nullptr;
    if (pcnt_new_channel(g_enc, &a, &ca) != ESP_OK || pcnt_new_channel(g_enc, &b, &cb) != ESP_OK) return false;
    pcnt_channel_set_edge_action(ca, PCNT_CHANNEL_EDGE_ACTION_DECREASE, PCNT_CHANNEL_EDGE_ACTION_INCREASE);
    pcnt_channel_set_level_action(ca, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    pcnt_channel_set_edge_action(cb, PCNT_CHANNEL_EDGE_ACTION_INCREASE, PCNT_CHANNEL_EDGE_ACTION_DECREASE);
    pcnt_channel_set_level_action(cb, PCNT_CHANNEL_LEVEL_ACTION_KEEP, PCNT_CHANNEL_LEVEL_ACTION_INVERSE);
    gpio_pullup_en((gpio_num_t)kEncA); gpio_pullup_en((gpio_num_t)kEncB);
    return pcnt_unit_enable(g_enc) == ESP_OK && pcnt_unit_clear_count(g_enc) == ESP_OK
        && pcnt_unit_start(g_enc) == ESP_OK;
}

int knobRead() {
    int v = 0;
    if (pcnt_unit_get_count(g_enc, &v) == ESP_OK && v) pcnt_unit_clear_count(g_enc);
    return v;
}

// THE SCREEN'S START, guarded (badge_screen.h): not at all after a crash or a watchdog reset, so a
// screen that faults costs the screen and never the board; and under the task watchdog, set to
// reset, so a panel that hangs its start resets the board, which then comes back without it.
void screenStart() {
    const esp_reset_reason_t r = esp_reset_reason();
    if (r == ESP_RST_PANIC || r == ESP_RST_INT_WDT || r == ESP_RST_TASK_WDT || r == ESP_RST_WDT) {
        g_screenNote = "off this boot: the last reset was a crash or a watchdog";
        line("screen: OFF this boot -- the last reset was a crash or a watchdog");
        return;
    }
    // The core's own watchdog settings, put back afterwards (ESP-IDF 5's API).
    esp_task_wdt_config_t guard = {};
    guard.timeout_ms = 8000; guard.idle_core_mask = 0; guard.trigger_panic = true;
    esp_task_wdt_config_t back = {};
    back.timeout_ms = CONFIG_ESP_TASK_WDT_TIMEOUT_S * 1000;
#ifdef CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU0
    back.idle_core_mask |= 1u << 0;
#endif
#ifdef CONFIG_ESP_TASK_WDT_CHECK_IDLE_TASK_CPU1
    back.idle_core_mask |= 1u << 1;
#endif
#ifdef CONFIG_ESP_TASK_WDT_PANIC
    back.trigger_panic = true;
#endif
    const bool wasUp = esp_task_wdt_reconfigure(&guard) == ESP_OK;
    if (!wasUp) esp_task_wdt_init(&guard);
    esp_task_wdt_add(nullptr);
    const bool ok = badge::begin();
    esp_task_wdt_delete(nullptr);
    if (wasUp) esp_task_wdt_reconfigure(&back); else esp_task_wdt_deinit();
    g_screenNote = ok ? "on" : "failed to start, left off";
    line(ok ? "screen: on" : "screen: FAILED to start, left off");
}

void screenUpdate() {
    badge::View v = g_badge;
    v.stale = g_badge.told && millis() - g_badgeMs > kBadgeStaleMs;
    v.wifi = WiFi.isConnected();
    const char* local = g_hold.phase == neck::HoldPhase::On ? "hold"
                      : g_hold.phase == neck::HoldPhase::Releasing ? "releasing"
                      : g_pt.owner == neck::Owner::Rc ? "rc"
                      : g_pt.owner == neck::Owner::Touch ? "touch" : "";
    snprintf(v.local, sizeof(v.local), "%s", local);
    v.tiltLo = g_tiltPending.waiting ? g_tiltPending.lo : -1;
    v.tiltHi = g_tiltPending.waiting ? g_tiltPending.hi : -1;
    badge::show(v);
}

}  // namespace

void setup() {
    Serial.begin(115200);
    Serial.setTxTimeoutMs(0);       // MANDATORY; see above
    delay(200);
    line("");
    line("neck_dial starting");

    g_nvs.begin("neck", true);
    String ssid = g_nvs.getString("ssid", "");
    String pass = g_nvs.getString("pass", "");
    g_nvs.end();
    strncpy(g_ssid, ssid.c_str(), sizeof(g_ssid) - 1);

    // THE NETWORK STACK IS STARTED UNCONDITIONALLY, and that is not tidiness. An earlier version
    // only called WiFi.mode() when there were credentials, so a board with none never initialised
    // lwIP -- and the g_udp.begin() below then asserted inside FreeRTOS
    // (`xQueueSemaphoreTake queue.c:1709`) and the board crash-looped from its first boot. A fresh
    // board has no credentials BY DEFINITION, so that was the one path guaranteed to run first.
    WiFi.mode(WIFI_STA);
    pinMode(kBtnPin, INPUT_PULLUP);        // the TILT confirm button; active low
    WiFi.setHostname(kHostName);
    g_knobUp = knobBegin();                // a hardware counter: nothing here can block the network
    // The verifier's known-answer test, once. A failure makes this board report-only and says so
    // (otaverify=BROKEN), never a board that refuses its own fix.
    g_otaSelftestOk = otaVerifySelfTest();

    if (ssid.length()) {
        WiFi.begin(ssid.c_str(), pass.c_str());
        line("wifi: joining from NVS");
    } else {
        line("wifi: NO CREDENTIALS IN NVS -- send `WIFI <ssid> <pass>` over this port once");
    }

    // THE PER-ROBOT TILT LIMITS, from NVS, in the SAME namespace and keys the Tab5 used
    // ("opendubs" / tiltMin / tiltMax, int32 degrees) so the numbers are the robot's, not this
    // board's. Without them the control law keeps its deliberately narrow fallback band: being
    // wrong narrow costs range, being wrong wide drives the head into its own frame.
    {
        g_nvs.begin("opendubs", true);
        const bool haveLo = g_nvs.isKey("tiltMin"), haveHi = g_nvs.isKey("tiltMax");
        const long lo = g_nvs.getInt("tiltMin", 0), hi = g_nvs.getInt("tiltMax", 0);
        g_nvs.end();
        g_lim = neck::limitsFrom(haveLo, lo, haveHi, hi);
        char b[120];
        snprintf(b, sizeof(b), g_lim.known ? "tilt limits: %.0f..%.0f from NVS"
                                           : "tilt limits: NOT COMMISSIONED -- narrow fallback %.0f..%.0f",
                 g_lim.tiltMin, g_lim.tiltMax);
        line(b);
    }

    // Port A is `Wire`. The 8Servo is put into servo mode on both channels; if it does not answer,
    // g_servoPresent stays false and the neck says so rather than pretending to a position.
    Wire.begin();
    g_servoPresent = servoWrite2(kModeReg + kChTilt, kModeServo) && servoWrite2(kModeReg + kChPan, kModeServo);
    // The light, OFF at boot. Deliberately not the INIT colour: a chest plate that lights up
    // before the robot knows its own state is claiming something it has not been told, and the
    // Thor re-sends every 5 s, so the gap is short and honest rather than short and wrong.
    if (g_servoPresent) {
        servoWrite2(kModeReg + kChLight, kModeServo);
        servoPulse(kChLight, neck::kChestOffUs);
    }
    line(g_servoPresent ? "8servo: present at 0x25, channels in servo mode"
                        : "8servo: NO ANSWER at 0x25 -- neck reports no pan/tilt fitted");

    report();
}

void loop() {
    // The UDP socket is opened LAZILY, once there is a network to open it on. Binding a socket
    // before the interface exists is what crashed the first build; waiting costs nothing, because
    // nothing can reach us before we are on the network anyway.
    static bool udpUp = false;
    if (!udpUp && WiFi.isConnected()) { g_udp.begin(kCmdPort); udpUp = true; line("udp: listening"); }
    if (udpUp && !WiFi.isConnected()) { g_udp.stop(); udpUp = false; }

    static bool mdns = false;
    if (WiFi.isConnected() && !mdns) { mdns = MDNS.begin(kHostName); if (mdns) MDNS.addService("opendubs", "udp", kCmdPort); }
    if (!WiFi.isConnected()) mdns = false;

    int sz = udpUp ? g_udp.parsePacket() : 0;
    if (sz > 0) {
        char cmd[256] = {0};
        int got = g_udp.read(cmd, sizeof(cmd) - 1);
        if (got > 0) {
            // A RATE FRAME FIRST, before anything strips or parses it as text. It shares this port
            // with the text protocol, so the length/id/dlc triple is the only thing telling them
            // apart -- and it gets NO REPLY, because the StamPLC does not read one and sends these
            // at 20 Hz while an operator is aiming.
            float rp = 0, rt = 0;
            if (neck::parseRcFrame((const uint8_t*)cmd, (size_t)got, rp, rt)) {
                g_rcPan = rp; g_rcTilt = rt; g_rcMs = millis(); g_rcFrames++;
                // NOT `return`: that would skip this iteration's control-law tick and the pending
                // restart check, which lets a fast enough stream of rate frames starve the very
                // loop that acts on them. Fall through instead and do nothing else with it.
            } else {
                while (got > 0 && (cmd[got - 1] == '\n' || cmd[got - 1] == '\r')) cmd[--got] = 0;
                g_packets++;
                char out[256];
                handle(cmd, out, sizeof(out));
                g_udp.beginPacket(g_udp.remoteIP(), g_udp.remotePort());
                g_udp.write((const uint8_t*)out, strlen(out));
                g_udp.endPacket();
            }
        }
    }

    // Provisioning, the one host->device line here, and it prints its own verdict so the bench can
    // tell "it worked" from "nothing arrived".
    if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
        // TILT <min> <max>: the per-robot limits, measured on the robot and written once. The same
        // shape as WIFI above and for the same reason -- it is a property of this machine, so it
        // belongs in its NVS and never in the repo.
        if (s.startsWith("TILT ")) {
            char reply[96];
            applyTilt(s.c_str() + 5, reply, sizeof(reply));   // the same parser the UDP path uses
            line(reply);
        }
        if (s.startsWith("WIFI ")) {
            int sp = s.indexOf(' ', 5);
            if (sp > 5) {
                g_nvs.begin("neck", false);
                g_nvs.putString("ssid", s.substring(5, sp));
                g_nvs.putString("pass", s.substring(sp + 1));
                g_nvs.end();
                line("wifi: saved to NVS -- reboot to join");
            } else {
                line("wifi: usage WIFI <ssid> <pass>");
            }
        }
    }

    // THE KNOB: turned one way, deliberately, it HOLDS the head where it is; the other way RELEASES
    // it, announced (neck/hold.h). Nothing to hold without a pan/tilt.
    if (g_knobUp && g_servoPresent) {
        switch (neck::knobFeed(g_knob, knobRead(), millis())) {
            case neck::Knob::Hold:
                neck::holdAt(g_hold, g_pt.pan, g_pt.tilt, neck::HoldBy::Knob);
                line("hold: by the knob");
                break;
            case neck::Knob::Release:
                if (neck::holdRelease(g_hold, millis())) line("hold: released by the knob, announced");
                break;
            case neck::Knob::None:
                break;
        }
    }
    if (neck::holdTick(g_hold, millis())) line("hold: released, the head is free");

    // The control law, every loop. It gates itself to its own 50 Hz period.
    if (g_servoPresent) {
        neck::In in;
        in.nowMs = millis();
        // Motion is allowed while we have a network: with no link we do not know the robot's state,
        // and the safe reading of "unknown" is "do not move".
        in.motionAllowed = WiFi.isConnected();
        in.gazePan = g_gazePan; in.gazeTilt = g_gazeTilt;
        in.gazeMs = g_gazeMs; in.gazeLease = g_gazeLease;
        // RC outranks a show, in the control law, not here.
        in.rcPan = g_rcPan; in.rcTilt = g_rcTilt; in.rcMs = g_rcMs;
        in.touchPan = g_touchPan; in.touchTilt = g_touchTilt; in.touchMs = g_touchMs; in.touchLease = g_touchLease;
        // A hold outranks all of them, in the control law.
        in.holdOn = neck::holding(g_hold); in.holdPan = g_hold.pan; in.holdTilt = g_hold.tilt;
        if (neck::tick(g_pt, in, g_lim)) {
            servoPulse(kChTilt, degToUs(g_pt.tilt, kTiltReverse));
            servoPulse(kChPan,  degToUs(g_pt.pan,  kPanReverse));
        }
    }

    // A pending restart, far enough after the reply that it has certainly gone out.
    // The TILT confirm. Polled here rather than on an interrupt: the window is 15 s and the loop
    // runs every few ms, so an edge cannot be missed, and an ISR writing NVS would be worse.
    switch (neck::poll(g_tiltPending, millis(), buttonDown())) {
        case neck::Confirm::Applied: {
            char reply[96];
            applyTilt2(g_tiltPending.lo, g_tiltPending.hi, reply, sizeof(reply));
            line(reply);
            break;
        }
        case neck::Confirm::Expired:
            line("tilt: pending request expired, nothing changed");
            break;
        case neck::Confirm::None:
            break;
    }

    // THE SCREEN, LAST: started once the socket is listening (or late, with no network), and drawn
    // only when what it shows changes.
    static bool screenTried = false;
    if (!screenTried && (udpUp || millis() > kScreenLateMs)) { screenTried = true; screenStart(); }
    if (badge::running()) screenUpdate();

    if (g_restartAt && (int32_t)(millis() - g_restartAt) >= 0) { delay(50); ESP.restart(); }

    if (millis() - g_lastReport > kReportMs) { g_lastReport = millis(); report(); }
    delay(5);
}
