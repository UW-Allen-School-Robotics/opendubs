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

namespace {

constexpr uint16_t kCmdPort = 4242;      // the head protocol's port, same as the Tab5's
constexpr char kHostName[] = "opendubs-neck";   // -> opendubs-neck.local
constexpr uint32_t kReportMs = 10000;    // telemetry cadence; see "report on a timer" below

WiFiUDP g_udp;
Preferences g_nvs;
char g_ssid[33] = {0};
uint32_t g_lastReport = 0;
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
    snprintf(b, sizeof(b), "udp:  port=%u packets=%lu", kCmdPort, (unsigned long)g_packets); line(b);
    line("pantilt: not fitted yet (step one: protocol only)");
}

// ---- the head protocol ------------------------------------------------------------------------

// `PING` -> the neck's PONG, in the shape fake_head.py models and the conformance suite matches.
// pan/tilt report 0 and owner `none` until the servos are wired; they are not invented.
void replyPing(char* out, size_t n) {
    snprintf(out, n, "PONG kind=neck fw=%s state=VALID plc=none owner=none pan=0.0 tilt=0.0",
             NECK_FW_SHA);
}

// `NECK` -> the status the suite parses. With nothing fitted, the protocol's own
// `ERR neck no pan/tilt fitted` is the honest answer and is already an accepted reply, so this
// does not have to pretend to a position it has no way to know.
void replyNeck(char* out, size_t n) {
    snprintf(out, n, "ERR neck no pan/tilt fitted");
}

void handle(const char* cmd, char* out, size_t n) {
    if (!strncmp(cmd, "PING", 4))      { replyPing(out, n); return; }
    if (!strncmp(cmd, "NECK", 4))      { replyNeck(out, n); return; }
    if (!strncmp(cmd, "REBOOT", 6))    { snprintf(out, n, "OK reboot"); return; }
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

    if (ssid.length()) {
        WiFi.mode(WIFI_STA);
        WiFi.setHostname(kHostName);
        WiFi.begin(ssid.c_str(), pass.c_str());
        line("wifi: joining from NVS");
    } else {
        line("wifi: NO CREDENTIALS IN NVS -- send `WIFI <ssid> <pass>` over this port once");
    }

    g_udp.begin(kCmdPort);
    report();
}

void loop() {
    static bool mdns = false;
    if (WiFi.isConnected() && !mdns) { mdns = MDNS.begin(kHostName); if (mdns) MDNS.addService("opendubs", "udp", kCmdPort); }
    if (!WiFi.isConnected()) mdns = false;

    int sz = g_udp.parsePacket();
    if (sz > 0) {
        char cmd[256] = {0};
        int got = g_udp.read(cmd, sizeof(cmd) - 1);
        if (got > 0) {
            while (got > 0 && (cmd[got - 1] == '\n' || cmd[got - 1] == '\r')) cmd[--got] = 0;
            g_packets++;
            char out[256];
            handle(cmd, out, sizeof(out));
            g_udp.beginPacket(g_udp.remoteIP(), g_udp.remotePort());
            g_udp.write((const uint8_t*)out, strlen(out));
            g_udp.endPacket();
        }
    }

    // Provisioning, the one host->device line here, and it prints its own verdict so the bench can
    // tell "it worked" from "nothing arrived".
    if (Serial.available()) {
        String s = Serial.readStringUntil('\n');
        s.trim();
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

    if (millis() - g_lastReport > kReportMs) { g_lastReport = millis(); report(); }
    delay(5);
}
