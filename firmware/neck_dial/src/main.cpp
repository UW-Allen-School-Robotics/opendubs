// THE NECK. An M5Dial (ESP32-S3) that stays with the robot and carries the pan/tilt, after the
// tablet took over the face. It replaces the Tab5 as the neck, and replaces the AtomS3R in the
// plan -- it is not a stand-in for either, so nothing here should read as temporary.
//
// WHAT IT ANSWERS, from head_protocol.py's own table: PING and REBOOT and OTA are `device` (both
// halves answer them); GAZE, NECK, PARK, TOUCH, TRACK, TOF and I2CSCAN are `neck`. The face half --
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
#include "../neck/pantilt_core.h"
#include "../neck/rc_frame.h"
#include "../neck/tilt_confirm.h"

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
uint32_t g_restartAt = 0;      // 0 = no restart pending; see the OTA handler

// THE TILT CONFIRM BUTTON. BtnA on the M5Dial is GPIO 42, ACTIVE LOW -- taken from M5Unified's own
// board table (board_M5Dial reads `!gpio_in(GPIO_NUM_42)`), not from memory, because a wrong pin
// here means the interlock can never be satisfied and commissioning becomes impossible on a board
// with no USB cable. The M5Dial library is deliberately NOT pulled in for this: it would start the
// display, touch and speaker, and contend for Port A's Wire with the 8Servo.
constexpr uint8_t kBtnPin = 42;
neck::Pending g_tiltPending;
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
    } else {
        line("pantilt: 8servo not answering at 0x25");
    }
}

// ---- the head protocol ------------------------------------------------------------------------

// `PING` -> the neck's PONG, in the shape fake_head.py models and the conformance suite matches.
//
// IT USED TO REPORT pan=0.0 tilt=0.0 AND owner=none, HARD-CODED. That was honest when it was
// written -- no servos were wired, and inventing a position would have been worse than admitting
// to none. The servos have been fitted since, and nobody came back to this: during the tilt
// commissioning NECK reported pan=61.2 tilt=95.4 while PING, one line later, said 0.0/0.0. A
// field that was once "we do not know" had quietly become "we do know, and this is wrong".
void replyPing(char* out, size_t n) {
    if (!g_servoPresent) {
        snprintf(out, n, "PONG kind=neck fw=%s state=VALID plc=none owner=none pan=0.0 tilt=0.0",
                 NECK_FW_SHA);
        return;
    }
    const char* owner = g_pt.owner == neck::Owner::Gaze ? "gaze"
                      : g_pt.owner == neck::Owner::Rc   ? "rc"
                      : g_pt.owner == neck::Owner::Touch ? "touch"
                      : g_pt.owner == neck::Owner::Track ? "track" : "none";
    snprintf(out, n, "PONG kind=neck fw=%s state=VALID plc=none owner=%s pan=%.1f tilt=%.1f",
             NECK_FW_SHA, owner, g_pt.pan, g_pt.tilt);
}

// `NECK` -> the status the suite parses. With nothing fitted, the protocol's own
// `ERR neck no pan/tilt fitted` is the honest answer and is already an accepted reply, so this
// does not have to pretend to a position it has no way to know.
void replyNeck(char* out, size_t n) {
    if (!g_servoPresent) { snprintf(out, n, "ERR neck no pan/tilt fitted"); return; }
    const char* owner = g_pt.owner == neck::Owner::Gaze ? "gaze"
                      : g_pt.owner == neck::Owner::Rc   ? "rc"
                      : g_pt.owner == neck::Owner::Touch ? "touch"
                      : g_pt.owner == neck::Owner::Track ? "track" : "none";
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
    snprintf(out, n, "OK neck pan=%.1f tilt=%.1f target=%.1f,%.1f moving=%d owner=%s tilt_band=%.0f,%.0f commissioned=%d %s",
             g_pt.pan, g_pt.tilt, g_pt.panTgt, g_pt.tiltTgt, g_pt.moving ? 1 : 0, owner,
             g_lim.tiltMin, g_lim.tiltMax, g_lim.known ? 1 : 0, pend);
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
bool otaPull(const char* url, char* out, size_t n) {
    if (!WiFi.isConnected()) { snprintf(out, n, "ERR ota no network"); return false; }
    HTTPClient http;
    if (!http.begin(url)) { snprintf(out, n, "ERR ota bad url"); return false; }
    const int code = http.GET();
    if (code != 200) { snprintf(out, n, "ERR ota http %d", code); http.end(); return false; }
    const int len = http.getSize();
    if (len <= 0) { snprintf(out, n, "ERR ota no length"); http.end(); return false; }
    if (!Update.begin(len)) { snprintf(out, n, "ERR ota no room"); http.end(); return false; }
    const size_t wrote = Update.writeStream(http.getStream());
    http.end();
    if (wrote != (size_t)len || !Update.end(true) || !Update.isFinished()) {
        snprintf(out, n, "ERR ota write %u/%d", (unsigned)wrote, len);
        return false;
    }
    snprintf(out, n, "OK ota %d bytes, rebooting", len);
    return true;
}

void handle(const char* cmd, char* out, size_t n) {
    if (!strncmp(cmd, "PING", 4))      { replyPing(out, n); return; }
    if (!strncmp(cmd, "NECK", 4))      { replyNeck(out, n); return; }
    if (!strncmp(cmd, "REBOOT", 6))    { snprintf(out, n, "OK reboot"); return; }

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

    // TOF: SUPPRESSED on this neck, by decision rather than by omission. The sensor is awkward to
    // move and has had no use yet, so it is not fitted -- and saying so is not the same as the
    // message being unknown. A caller can tell "this neck has no ToF" from "this neck is too old
    // to have heard of ToF", which it could not if this fell through to ERR unknown.
    if (!strncmp(cmd, "TOF", 3)) { snprintf(out, n, "ERR tof not fitted"); return; }

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
        char reply[128];
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

    if (g_restartAt && (int32_t)(millis() - g_restartAt) >= 0) { delay(50); ESP.restart(); }

    if (millis() - g_lastReport > kReportMs) { g_lastReport = millis(); report(); }
    delay(5);
}
