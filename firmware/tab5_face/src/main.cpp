// OpenDubs Tab5 — payload controller + face.
//
// Task layout (P4 is dual-core; rendering must never starve safety):
//   loop()      core 1  UI: touch, face<->controller mode, render (owns
//   M5.Display/Touch) controlTask core 0  lift model tick — ToF read, limit
//   detect, relay-pulse timing (50 Hz) safetyTask  core 0  HIGH PRIORITY — soft
//   e-stop enforcement + (TODO) hardware watchdog commsTask   core 0  WiFi(C6
//   ESP-Hosted) + ArduinoOTA over "Opendubs2"
// Face is home. Any touch reveals the lift controls; they auto-revert to the
// face after an idle timeout (unless the lift is moving) or when a software
// command drives a lift state change.
#include "can_protocol.h"
#include "face.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "hmi.h"
#include "prompt.h"
#include "lift.h"
#include "ui_shared.h"
#include <ArduinoOTA.h>
#include <HTTPClient.h>
#include <Update.h>
#include <M5Unified.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>


static const bool DEMO_CYCLE = false;  // true = auto-cycle robot states (face
                                       // demo). false = hold SAFE_IDLE + puns.
static const uint32_t IDLE_MS = 10000; // controller -> face auto-revert
static const uint32_t CONFIRM_MS = 2200; // e-stop release confirm window

// The head's position. The Tab5 OWNS it: manual sources send rates and this integrates
// them, so nothing has to be kept in sync across devices and no source can make the head
// jump by having drifted.
float   g_panDeg = 90.0f, g_tiltDeg = 90.0f;
int16_t g_targetPan = 90;
int16_t g_targetTilt = 90;

// Who is driving the head. Priority: the RC stick (a physical operator holding a deadman)
// beats the on-screen D-pad, which beats AI tracking. AI is suppressed entirely while
// either manual source is live — a policy being corrected should yield, not argue.
static float    g_rcPanDps = 0, g_rcTiltDps = 0;
static uint32_t g_rcCmdMs = 0;
static float    g_dpadPanDps = 0, g_dpadTiltDps = 0;
static uint32_t g_dpadMs = 0;
static constexpr uint32_t kManualHoldMs = 600;   // a manual source owns the head this long
static constexpr float    kDpadDps = 45.0f;      // on-screen D-pad slew
static inline bool rcOwnsHead()   { return millis() - g_rcCmdMs   < kManualHoldMs; }
static inline bool dpadOwnsHead() { return millis() - g_dpadMs    < kManualHoldMs; }

Preferences prefs;
int16_t g_tiltMin = 0;
int16_t g_tiltMax = 180;
bool g_isHoming = false;

// 8Servo I2C Constants
static constexpr uint8_t kServo8Addr = 0x25;
static constexpr uint8_t kSpkVolume  = 96;     // 0-255. Audible across a lab bay, not startling.
// Tones live between 2 and 4 kHz. Judged by ear on the robot with a sweep (400 Hz .. 4 kHz):
// everything below ~2 kHz was faint or inaudible across a bay. That is the usual story for a
// small sealed speaker, and it happens to be where hearing is most sensitive — so anything
// this robot ever needs a human to NOTICE belongs up here, not at a comfortable 880 Hz.

// Audio (ES8388 codec on I2S0). Started LATE and ONLY ONCE, from the network task, never from
// setup(): a first attempt that ran right after M5.begin() left the Tab5 dark — no display, no
// Wi-Fi, no way in but a cable — because a codec that does not come up takes the boot with it.
// Nothing above this line depends on audio, the state it reaches is shown on the face beside
// the IP, and the mic (ES7210) shares I2S0 so it stays off.
enum class SpkState : uint8_t { OFF = 0, STARTING, READY, FAILED };
static volatile SpkState g_spk = SpkState::OFF;

static const char *spkTag() {
  switch (g_spk) {
    case SpkState::STARTING: return " spk:init";
    case SpkState::READY:    return " spk:ok";
    case SpkState::FAILED:   return " spk:FAIL";
    default:                 return "";
  }
}

// True only when the codec is up. Callers must check: a BEEP before that is a no-op, not a hang.
bool spkReady() { return g_spk == SpkState::READY; }

// ---- OTA by HTTP pull -------------------------------------------------------------------
// ArduinoOTA is started below and has never once answered on this board: espota gets "no
// response" even seconds after a power cycle, with the head pingable at -40 dBm. Its listener
// goes through the ESP-Hosted co-processor and begin() returns void, so every failure is
// silent. The StampPLC solved the same problem by PULLING its firmware over HTTP on command,
// and that has worked all along — so the head now does the same. Serve the .bin from the Thor
// and send: OTA http://10.42.2.10:8000/firmware.bin
static void otaHttpPull(const char *url) {
  if (WiFi.status() != WL_CONNECTED) { Serial.println("[OTA] abort: no WiFi"); return; }
  Serial.printf("[OTA] fetching %s\n", url);
  setIpStr("OTA: connecting");
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(15000);
  if (!http.begin(url)) { Serial.println("[OTA] abort: bad URL"); setIpStr("OTA: bad URL"); return; }
  const int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("[OTA] abort: HTTP %d\n", code);
    char m[32]; snprintf(m, sizeof(m), "OTA: HTTP %d", code); setIpStr(m);
    http.end();
    return;
  }
  const int len = http.getSize();
  if (!Update.begin(len > 0 ? (size_t)len : UPDATE_SIZE_UNKNOWN)) {
    Serial.printf("[OTA] abort: Update.begin (%s)\n", Update.errorString());
    setIpStr("OTA: no space");
    http.end();
    return;
  }
  setIpStr("OTA: writing");
  // Read with a STALL DEADLINE instead of Update.writeStream(): that blocks for ever if the
  // source goes quiet, and it does not take much — killing the serving side mid-download left
  // this head wedged on "OTA: writing", network task and all, recoverable only by pulling
  // power. Nothing that writes firmware should be able to hang the device it runs on.
  static constexpr uint32_t kStallMs = 10000;
  WiFiClient *stream = http.getStreamPtr();
  uint8_t buf[1460];
  size_t written = 0;
  uint32_t last_rx = millis();
  bool stalled = false;
  while (len < 0 || written < (size_t)len) {
    const size_t avail = stream->available();
    if (avail) {
      const int n = stream->readBytes(buf, avail > sizeof(buf) ? sizeof(buf) : avail);
      if (n > 0) {
        if (Update.write(buf, (size_t)n) != (size_t)n) { stalled = true; break; }   // flash write failed
        written += (size_t)n;
        last_rx = millis();
        if (len > 0) {                       // progress on the face, so a slow link looks slow
          static int last_pct = -1;
          const int pct = (int)((written * 100ULL) / (size_t)len);
          if (pct != last_pct && pct % 10 == 0) {
            char m[24]; snprintf(m, sizeof(m), "OTA: %d%%", pct); setIpStr(m); last_pct = pct;
          }
        }
      }
    } else if (!stream->connected() && stream->available() == 0) {
      break;                                 // server closed: done if we have it all, short if not
    } else if (millis() - last_rx > kStallMs) {
      stalled = true;
      break;
    } else {
      delay(5);
    }
  }
  http.end();
  if (stalled || (len > 0 && written < (size_t)len)) {
    Update.abort();
    Serial.printf("[OTA] aborted after %u of %d bytes (%s)\n", (unsigned)written, len,
                  stalled ? "stalled" : "short read");
    setIpStr("OTA: aborted");
    return;                                  // the running image is untouched
  }
  if (!Update.end(true)) {
    Serial.printf("[OTA] FAILED after %u bytes (%s)\n", (unsigned)written, Update.errorString());
    setIpStr("OTA: FAILED");
    return;                                  // the running image is untouched
  }
  Serial.printf("[OTA] wrote %u bytes — rebooting\n", (unsigned)written);
  setIpStr("OTA: ok, rebooting");
  delay(400);
  ESP.restart();
}
static constexpr uint8_t kModeReg = 0x00;  // + pin
static constexpr uint8_t kPulseReg = 0x60; // + pin*2, 16-bit LE microseconds
static constexpr uint8_t kModeServo = 3;   // SERVO_CTL_MODE

static void applyLine(const char *line);

static float readServoCurrent() {
  uint8_t b[4];
  Wire.beginTransmission(kServo8Addr);
  Wire.write(0xA0); // kCurrentReg
  if (Wire.endTransmission(false) != 0)
    return 0.0f;
  if (Wire.requestFrom((int)kServo8Addr, 4) != 4)
    return 0.0f;
  for (int i = 0; i < 4; i++)
    b[i] = Wire.read();
  float out;
  memcpy(&out, b, 4);
  return out;
}

// ---- UI state (owned by loop) ----
static uint32_t idleLeft = 0;
static bool confirming = false;
static uint32_t confirmUntil = 0;
static uint32_t uiPrev = 0, demoMs = 0;
static uint32_t last_state_ms = 0;
static HmiHit g_activeHold = HmiHit::NONE;

// ================= background tasks =================
static void controlTask(void *) {
  uint32_t prevMs = millis();
  for (;;) {
    Lift.tick(millis());

    // Integrate whichever manual source owns the head. AI writes g_targetPan/Tilt directly
    // and is gated at the receiving end, so it never reaches here.
    {
      const uint32_t now = millis();
      float dt = (now - prevMs) / 1000.0f;
      prevMs = now;
      if (dt < 0) dt = 0; else if (dt > 0.1f) dt = 0.1f;
      float pDps = 0, tDps = 0;
      if (rcOwnsHead())        { pDps = g_rcPanDps;   tDps = g_rcTiltDps; }
      else if (dpadOwnsHead()) { pDps = g_dpadPanDps; tDps = g_dpadTiltDps; }
      if (pDps != 0 || tDps != 0) {
        g_panDeg  += pDps * dt;
        g_tiltDeg += tDps * dt;
        if (g_panDeg  < 0)   g_panDeg  = 0;
        if (g_panDeg  > 180) g_panDeg  = 180;
        if (g_tiltDeg < g_tiltMin) g_tiltDeg = g_tiltMin;
        if (g_tiltDeg > g_tiltMax) g_tiltDeg = g_tiltMax;
        g_targetPan  = (int16_t)lroundf(g_panDeg);
        g_targetTilt = (int16_t)lroundf(g_tiltDeg);
      }
    }

    // Convert angles to microseconds for the 8Servo unit
    // Servo direction is a property of the MECHANISM — how the horns and the bracket
    // happened to go together — so it is corrected once here, at the write, rather than in
    // each input. Otherwise the RC stick, the D-pad and AI tracking each need their own
    // sign and they will not agree for long. Both axes are reversed on this head, observed
    // on the bench: the D-pad drove it mirrored horizontally and vertically.
    static constexpr bool kPanReverse  = true;
    static constexpr bool kTiltReverse = true;
    const int16_t panCmd  = kPanReverse  ? (int16_t)(180 - g_targetPan)  : g_targetPan;
    const int16_t tiltCmd = kTiltReverse ? (int16_t)(180 - g_targetTilt) : g_targetTilt;
    uint16_t tiltUs = map(tiltCmd, 0, 180, 500, 2500);
    uint16_t panUs = map(panCmd, 0, 180, 500, 2500);

    // Write Tilt to CH0
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 0 * 2);
    Wire.write(tiltUs & 0xFF);
    Wire.write(tiltUs >> 8);
    Wire.endTransmission();

    // Write Pan to CH1
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 1 * 2);
    Wire.write(panUs & 0xFF);
    Wire.write(panUs >> 8);
    Wire.endTransmission();

    vTaskDelay(pdMS_TO_TICKS(20));
  }
}
static void safetyTask(void *) {
  for (;;) {
    Lift.enforceSafe(millis());
    // TODO-HW: feed a hardware/task watchdog here; also drop authorize if the
    // robot heartbeat goes stale while under robot control (fail-safe stop).
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

static WiFiUDP Udp;
static const uint16_t UDP_PORT = 4242;          // we LISTEN here
// We used to transmit to 255.255.255.255:5005. Port 5005 is the StampPLC's telemetry port,
// and the Thor's recorder was ingesting these binary CAN frames into the robot's telemetry
// log — corrupting the log we diagnose the robot with. Transmit has its own port now, and
// goes to the subnet broadcast rather than the global one.
static const uint16_t TX_PORT  = 5006;

// Directed subnet broadcast: 255.255.255.255 is not routed and gets sprayed out of whichever
// interface the stack picks, which on a multi-homed robot is a coin flip.
static IPAddress txTarget()
{
    const IPAddress ip = WiFi.localIP(), mask = WiFi.subnetMask();
    return IPAddress((uint8_t)(ip[0] | ~mask[0]), (uint8_t)(ip[1] | ~mask[1]),
                     (uint8_t)(ip[2] | ~mask[2]), (uint8_t)(ip[3] | ~mask[3]));
}

// Only the robot's own controller may command this head. Without a source check any host on
// the network could drive the servos and hold the link "online". The Thor is whoever the
// robot-state feed comes from, so the first valid state frame pins it.
static IPAddress g_thorIp;
static bool      g_haveThor = false;
// The StampPLC commands the head directly (low latency, and it keeps working if the Thor
// stops), but only once the Thor has vouched for its address over the state channel — so
// the "no unauthenticated host moves the servos" property survives the second peer.
static IPAddress g_plcIp;
static bool      g_havePlc = false;

static void commsTask(void *) {
  // Bring up WiFi via the ESP32-C6 co-processor (ESP-Hosted)
  bool hosted_ok = false;
#if defined(ARDUINO_ARCH_ESP32) && defined(BOARD_HAS_PSRAM)
  // hostedSetPins(clk, cmd, d0, d1, d2, d3, rst) - M5Stack Tab5 pins
  hostedSetPins(12, 13, 11, 10, 9, 8, 15);
  hosted_ok = hostedInitWiFi();
#endif

  WiFi.begin("Opendubs2", "mush-cleaner-platoon");
  bool ota_setup = false;
  bool udp_setup = false;

  for (;;) {
    if (WiFi.status() == WL_CONNECTED) {
      setIpStr((WiFi.localIP().toString() + spkTag()).c_str());
      if (!ota_setup) {
        // Explicit port, and callbacks that SAY something. The unit on the bench was found
        // with tcp/3232 closed while WiFi was up and streaming, and nothing anywhere said
        // why — begin() returns void and every failure was silent. At minimum the log now
        // distinguishes "never started" from "started and then died".
        ArduinoOTA.setHostname("opendubs-tab5");
        ArduinoOTA.setPort(3232);
        ArduinoOTA.onStart([]() { Serial.println("[OTA] start"); });
        ArduinoOTA.onEnd([]() { Serial.println("[OTA] done — rebooting"); });
        ArduinoOTA.onError([](ota_error_t e) { Serial.printf("[OTA] error %u\n", (unsigned)e); });
        ArduinoOTA.begin();
        ota_setup = true;
        Serial.printf("[OTA] listening on %s:3232 as opendubs-tab5\n",
                      WiFi.localIP().toString().c_str());
      }
      if (!udp_setup) {
        Udp.begin(UDP_PORT);
        udp_setup = true;
      }
      if (g_spk == SpkState::OFF) {
        // The face already shows "spk:init" at this point, so a codec that hangs here is
        // visible on the screen instead of looking like a dead head.
        g_spk = SpkState::STARTING;
        setIpStr((WiFi.localIP().toString() + spkTag()).c_str());
        // FULL CODEC RESET BEFORE STARTING. The ES8388 has its OWN power rail, so it keeps
        // whatever state it was in across an ESP32 software reset — mid-DMA, PLL locked, DAC
        // powered with a stale configuration. M5Unified's init writes the chip-reset register
        // with no delay around it, which is not enough to shift it: measured here, sound worked
        // after a power cycle and never after an OTA, while begin() reported success both times.
        // Sequence and register values follow amcchord/M5Tab-Macintosh, which hit and fixed the
        // same thing on this board: quiesce the chip, THEN reset it, and hold the reset long
        // enough for an in-flight I2S frame and the DAC PLL to finish.
        // (The amp itself needs no help from us — M5Unified raises it, PI4IO1 0x43 reg 0x05
        // bit 1, inside its Tab5 speaker callback.)
        {
          constexpr uint8_t kEs = 0x10;              // ES8388
          M5.In_I2C.bitOff(0x43, 0x05, 0b00000010, 400000);       // amp off: no pops while we work
          delay(5);
          M5.In_I2C.writeRegister8(kEs, 0x19, 0x04, 400000);      // DAC mute
          M5.In_I2C.writeRegister8(kEs, 0x2E, 0x00, 400000);      // LOUT1 volume 0
          M5.In_I2C.writeRegister8(kEs, 0x2F, 0x00, 400000);      // ROUT1 volume 0
          M5.In_I2C.writeRegister8(kEs, 0x04, 0xC0, 400000);      // DAC L+R powered down
          M5.In_I2C.writeRegister8(kEs, 0x03, 0xFF, 400000);      // ADC powered down
          M5.In_I2C.writeRegister8(kEs, 0x02, 0xFF, 400000);      // chip power down
          delay(20);
          M5.In_I2C.writeRegister8(kEs, 0x00, 0x80, 400000);      // assert reset...
          delay(100);                                             // ...and HOLD it
          M5.In_I2C.writeRegister8(kEs, 0x00, 0x00, 400000);      // release
          delay(50);                                              // internal power-on reset
          const uint8_t ctrl1 = M5.In_I2C.readRegister8(kEs, 0x00, 400000);
          Serial.printf("[SPK] ES8388 reset -> ctrl1=0x%02X%s\n", ctrl1,
                        ctrl1 == 0xFF ? " (NOT RESPONDING)" : "");
        }
        M5.Speaker.setVolume(kSpkVolume);
        g_spk = M5.Speaker.begin() ? SpkState::READY : SpkState::FAILED;
        if (g_spk == SpkState::READY) {
          // Put the amp back. The reset above switched it off to avoid pops, and begin() only
          // raises it from M5Unified's enable callback — which evidently does not run in the
          // state a warm boot leaves things in, so it stayed muted: a codec reporting ready,
          // playing into a disabled amplifier. Same pin M5Unified uses (PI4IO1 0x43, reg 0x05,
          // bit 1); asserting it twice is harmless.
          const bool pa = M5.In_I2C.bitOn(0x43, 0x05, 0b00000010, 400000);
          Serial.printf("[SPK] amp enable: %s\n", pa ? "ok" : "I2C FAILED");
        }
        Serial.printf("[SPK] %s\n", g_spk == SpkState::READY ? "ready" : "FAILED to start");
      }
      ArduinoOTA.handle();

      // Receive UDP packets for robot state feed
      int packetSize = Udp.parsePacket();
      if (packetSize) {
        // Read into a union rather than casting a packed struct out of a char array: the
        // buffer is then correctly aligned for the fields, which RISC-V cares about more
        // than the Xtensa parts did.
        union { can_frame_udp frame; char text[64]; } pkt;
        const IPAddress from = Udp.remoteIP();
        int len = Udp.read((uint8_t *)&pkt, sizeof(pkt) - 1);
        char *packetBuffer = pkt.text;
        // Length alone does not tell a binary frame from a text line: "STATE SAFE_IDLE" is
        // 15 bytes and was being read as a CAN frame with a garbage id, then dropped. So the
        // id has to be one we know before this is treated as binary; anything else falls
        // through to the text parser.
        const uint32_t cid = (len >= (int)sizeof(can_frame_udp)) ? pkt.frame.can_id : 0;
        // Every id handled below MUST be listed here, or the frame falls through to the text
        // parser and is silently dropped. CAN_ID_PAN_TILT_RATE was added later and missed,
        // so the RC stick transmitted 20 times a second into a packet this line discarded —
        // the StampPLC counted 553 sends while the head never saw one.
        const bool isFrame = (cid == CAN_ID_ROBOT_STATE || cid == CAN_ID_PAN_TILT_CMD
                              || cid == CAN_ID_PAN_TILT_RATE || cid == CAN_ID_LIFT_STATE);
        if (isFrame) {
          can_frame_udp *frame = &pkt.frame;
          // can_dlc is checked against each payload before it is read — the length on the
          // wire is attacker-controlled, and a short frame would otherwise be read past.
          if (frame->can_id == CAN_ID_ROBOT_STATE
              && frame->can_dlc >= sizeof(CAN_Robot_State)) {
            CAN_Robot_State *state = (CAN_Robot_State *)frame->data;
            if (state->state_id < NST) {
              if (!g_haveThor) { g_thorIp = from; g_haveThor = true; }   // pin the controller
              g_robotState = state->state_id;
              last_state_ms = millis();
              extern volatile bool g_linkOnline; // ensure we can set it
              g_linkOnline = true;
            }
          } else if (frame->can_id == CAN_ID_LIFT_STATE
                     && frame->can_dlc >= sizeof(CAN_Lift_State)
                     && g_haveThor && from == g_thorIp) {
            // What the lift is ACTUALLY doing, measured on the StampPLC. The Tab5 used to
            // model this locally and show the model as if it were real.
            CAN_Lift_State *ls = (CAN_Lift_State *)frame->data;
            Lift.reportFromRobot(
                ls->height_mm == LIFT_HEIGHT_UNKNOWN ? -1.0f : (float)ls->height_mm,
                ls->motion == 1 ? Motion::UP : ls->motion == 2 ? Motion::DOWN : Motion::STOP,
                (ls->flags & LIFT_FLAG_AT_TOP) != 0, (ls->flags & LIFT_FLAG_AT_BOTTOM) != 0,
                (ls->flags & LIFT_FLAG_GUARD) != 0);
            g_linkOnline = true;
            last_state_ms = millis();
          } else if (frame->can_id == CAN_ID_PAN_TILT_RATE
                     && frame->can_dlc >= sizeof(CAN_Pan_Tilt_Rate)
                     && g_havePlc && from == g_plcIp) {
            // RC stick, as a rate. Arrives only while the operator holds the deadman with
            // CH7 in PAN-TILT, so its mere presence is the claim on the head.
            CAN_Pan_Tilt_Rate *r = (CAN_Pan_Tilt_Rate *)frame->data;
            g_rcPanDps  = (float)r->pan_dps;
            g_rcTiltDps = (float)r->tilt_dps;
            g_rcCmdMs   = millis();
            g_linkOnline = true;
            last_state_ms = millis();
          } else if (frame->can_id == CAN_ID_PAN_TILT_CMD
                     && frame->can_dlc >= sizeof(CAN_Pan_Tilt_Command)
                     && g_haveThor && from == g_thorIp) {
            CAN_Pan_Tilt_Command *pt = (CAN_Pan_Tilt_Command *)frame->data;

            // A packet from the pinned controller means the link is online
            g_linkOnline = true;
            last_state_ms = millis();

            // AI tracking yields to a human. A policy being corrected should give way.
            if (g_trackingMode == TrackingMode::AI && !rcOwnsHead() && !dpadOwnsHead()) {
              g_panDeg  = pt->pan_angle;      // keep the integrator in step with AI, so a
              g_tiltDeg = pt->tilt_angle;     // later manual nudge starts from here
              g_targetPan = pt->pan_angle;
              g_targetTilt = pt->tilt_angle;
              // Constrain to limits
              if (g_targetPan < 0) g_targetPan = 0;
              if (g_targetPan > 180) g_targetPan = 180;
              if (g_targetTilt < g_tiltMin) g_targetTilt = g_tiltMin;
              if (g_targetTilt > g_tiltMax) g_targetTilt = g_tiltMax;
            }
          }
        } else if (len > 0) {
          packetBuffer[len] = 0;
          // A text state line identifies the controller too, so pan/tilt commands from the
          // same host are accepted afterwards.
          if (strncmp(packetBuffer, "STATE ", 6) == 0 && !g_haveThor) {
            g_thorIp = from; g_haveThor = true;
          }
          // Trim trailing newlines/carriage returns
          for (int i = len - 1; i >= 0; i--) {
            if (packetBuffer[i] == '\n' || packetBuffer[i] == '\r') {
              packetBuffer[i] = 0;
            } else {
              break;
            }
          }
          applyLine(packetBuffer);
        }
      }

      // Periodically broadcast ToF data
      static uint32_t last_tof_send = 0;
      if (millis() - last_tof_send > 50) {
        last_tof_send = millis();
        auto s = Lift.snapshot();
        can_frame_udp frame = {0};
        frame.can_id = CAN_ID_TOF_FRONT;
        frame.can_dlc = sizeof(CAN_ToF_Distance);
        CAN_ToF_Distance *tof = (CAN_ToF_Distance *)frame.data;
        tof->distance_mm = s.tofOk ? (uint16_t)s.tofMm : 0;
        tof->status = s.tofOk ? 1 : 0;
        tof->part = s.tofPart;

        Udp.beginPacket(txTarget(), TX_PORT);
        Udp.write((const uint8_t *)&frame, sizeof(frame));
        Udp.endPacket();

        // Ask for what the operator pressed. The StampPLC decides whether to honour it —
        // deadman, authority, limits and the pinch guard all still apply there.
        can_frame_udp lf = {0};
        lf.can_id = CAN_ID_LIFT_CMD;
        lf.can_dlc = sizeof(CAN_Lift_Command);
        CAN_Lift_Command *lc = (CAN_Lift_Command *)lf.data;
        const Motion req = Lift.request();
        lc->command = (req == Motion::UP) ? 1 : (req == Motion::DOWN) ? 2 : 0;
        Udp.beginPacket(txTarget(), TX_PORT);
        Udp.write((const uint8_t *)&lf, sizeof(lf));
        Udp.endPacket();

        // What the head believes, so its state can be checked without a cable.
        can_frame_udp sf = {0};
        sf.can_id = CAN_ID_TAB5_STATUS;
        sf.can_dlc = sizeof(CAN_Tab5_Status);
        CAN_Tab5_Status *ts = (CAN_Tab5_Status *)sf.data;
        ts->robot_state = (uint8_t)g_robotState;
        ts->link_online = g_linkOnline ? 1 : 0;
        ts->tracking = (g_trackingMode == TrackingMode::AI && !rcOwnsHead() && !dpadOwnsHead()) ? 1 : 0;
        ts->ui_mode = (g_uiMode == UiMode::CONTROLLER) ? 1 : 0;
        ts->current_pan = g_targetPan;
        ts->current_tilt = g_targetTilt;
        Udp.beginPacket(txTarget(), TX_PORT);
        Udp.write((const uint8_t *)&sf, sizeof(sf));
        Udp.endPacket();
      }
    } else {
      if (!hosted_ok) {
        setIpStr("ERR: ESP-Hosted Failed");
      } else {
        char st[24];
        snprintf(st, sizeof(st), "Connecting... (%d)", (int)WiFi.status());
        setIpStr(st);
      }
      ota_setup = false;
      if (udp_setup) {
        Udp.stop();
        udp_setup = false;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10)); // Need fast polling for OTA and UDP
  }
}

// ================= UI helpers =================
static void enterController() {
  g_uiMode = UiMode::CONTROLLER;
  hmiInvalidate();
  idleLeft = IDLE_MS;
}
static void enterFace() {
  g_uiMode = UiMode::FACE;
  faceInvalidate();
  confirming = false;
}

static void onEstopTap() {
  bool inhibited = Lift.snapshot().inhibit;
  if (!inhibited) {
    Lift.setInhibit(true);
    confirming = false;
  } else if (!confirming) {
    confirming = true;
    confirmUntil = millis() + CONFIRM_MS;
  } else {
    Lift.setInhibit(false);
    confirming = false;
  }
}

// NOTE: there is no edge-triggered lift sender any more. The request is published every
// 50 ms from Lift.request() in commsTask, which is what gives it heartbeat semantics — the
// far end stops the lift when the stream stops. The old one also broadcast to
// 255.255.255.255:5005, the StampPLC's telemetry port.

static void handleTouch() {
  auto td = M5.Touch.getDetail();

  if (g_uiMode == UiMode::FACE) {
    if (td.wasPressed()) {
      enterController();
    }
    return;
  }

  if (td.isPressed()) {
    idleLeft = IDLE_MS; // any touch keeps controls up
    HmiHit hit = hmiHitTest(td.x, td.y);
    if (td.wasPressed() && hit == HmiHit::ESTOP) {
      onEstopTap();
    } else if (td.wasPressed() && hit == HmiHit::MODE_TOGGLE) {
      g_trackingMode = (g_trackingMode == TrackingMode::MANUAL) ? TrackingMode::AI : TrackingMode::MANUAL;
      hmiInvalidate();
    } else if (hit == HmiHit::RAISE || hit == HmiHit::LOWER ||
               hit == HmiHit::PAN_LEFT || hit == HmiHit::PAN_RIGHT ||
               hit == HmiHit::TILT_UP || hit == HmiHit::TILT_DOWN) {
      if (g_trackingMode == TrackingMode::AI && (hit == HmiHit::PAN_LEFT || hit == HmiHit::PAN_RIGHT || hit == HmiHit::TILT_UP || hit == HmiHit::TILT_DOWN)) {
        // Ignore manual pan/tilt in AI mode
      } else if (g_activeHold != hit) {
        if (g_activeHold == HmiHit::RAISE || g_activeHold == HmiHit::LOWER) {
          Lift.commandStop();
        }
        if (hit == HmiHit::RAISE) Lift.commandUp();
        if (hit == HmiHit::LOWER) Lift.commandDown();
        confirming = false;
        g_activeHold = hit;
      }
      // The D-pad drives the head. It used to register the touch and set g_activeHold and
      // nothing else — the arrows had never moved a servo.
      g_dpadPanDps  = (hit == HmiHit::PAN_RIGHT) ?  kDpadDps
                    : (hit == HmiHit::PAN_LEFT)  ? -kDpadDps : 0.0f;
      g_dpadTiltDps = (hit == HmiHit::TILT_UP)   ?  kDpadDps
                    : (hit == HmiHit::TILT_DOWN) ? -kDpadDps : 0.0f;
      if (g_dpadPanDps != 0 || g_dpadTiltDps != 0) g_dpadMs = millis();
    } else if (g_activeHold != HmiHit::NONE) {
      if (g_activeHold == HmiHit::RAISE || g_activeHold == HmiHit::LOWER) {
        Lift.commandStop();
      }
      g_dpadPanDps = g_dpadTiltDps = 0.0f;
      g_activeHold = HmiHit::NONE;
    }
  } else if (td.wasReleased() ||
             (!td.isPressed() && g_activeHold != HmiHit::NONE)) {
    if (g_activeHold != HmiHit::NONE) {
      if (g_activeHold == HmiHit::RAISE || g_activeHold == HmiHit::LOWER) {
        Lift.commandStop();
      }
      g_dpadPanDps = g_dpadTiltDps = 0.0f;
      g_activeHold = HmiHit::NONE;
    }
  }
}

// ================= robot-state feed (USB serial, pushed by the Thor)
// ================= The Tab5 has no state inputs of its own — the Thor's FSM
// (robot_state) pushes "STATE <NAME>\n" over this CDC port. NAME matches
// STATES[].name (INIT/SAFE_IDLE/MANUAL/AUTONOMOUS/DEGRADED/ E_STOP/CHARGING).
// Drives the same g_robotState the face + collapsed chip render.
static char g_rx[64];
static int g_rxlen = 0;

static void applyLine(const char *line) {
  if (strncmp(line, "STATE ", 6) == 0) {
    const char *name = line + 6;
    for (int i = 0; i < NST; ++i)
      if (strcmp(STATES[i].name, name) == 0) {
        g_robotState = i;
        g_linkOnline = true;
        last_state_ms = millis();
        return;
      }
  } else if (strncmp(line, "LIFT ", 5) == 0) {
    // "LIFT <height_mm|-1> <motion 0|1|2> <flags>" — the StampPLC's measured Z-axis state.
    // Text, like STATE, so neither side can drift on a numeric table the other reorders.
    int h = -1, m = 0, f = 0;
    if (sscanf(line + 5, "%d %d %d", &h, &m, &f) >= 2) {
      Lift.reportFromRobot((float)h,
                           m == 1 ? Motion::UP : m == 2 ? Motion::DOWN : Motion::STOP,
                           (f & LIFT_FLAG_AT_TOP) != 0, (f & LIFT_FLAG_AT_BOTTOM) != 0,
                           (f & LIFT_FLAG_GUARD) != 0);
      g_linkOnline = true;
      last_state_ms = millis();
    }
  } else if (strncmp(line, "OTA ", 4) == 0) {
    otaHttpPull(line + 4);
  } else if (strcmp(line, "REBOOT") == 0) {
    // Remote recovery. Without it, a head whose OTA listener has died needs a cable, which on
    // a robot means someone walking over to it.
    Serial.println("[CMD] REBOOT");
    delay(150);
    ESP.restart();
  } else if (strncmp(line, "BEEP", 4) == 0) {
    // "BEEP [freq_hz] [ms]" — one tone. A BEEP before the codec is up does nothing, and says so.
    if (!spkReady()) { Serial.println("[SPK] BEEP ignored: codec not ready"); return; }
    float hz = 2600.0f;                      // see kSpkVolume note: 2-4 kHz is what this speaker carries
    int ms = 120;
    sscanf(line + 4, "%f %d", &hz, &ms);
    if (hz < 50.0f) hz = 50.0f;
    if (hz > 8000.0f) hz = 8000.0f;
    if (ms < 10) ms = 10;
    if (ms > 3000) ms = 3000;              // a stuck caller must not own the speaker
    M5.Speaker.tone(hz, (uint32_t)ms);
  } else if (strcmp(line, "CHIME") == 0) {
    if (!spkReady()) { Serial.println("[SPK] CHIME ignored: codec not ready"); return; }
    M5.Speaker.tone(2000, 110);
    M5.Speaker.tone(2600, 110);
    M5.Speaker.tone(3200, 170);
  } else if (strncmp(line, "VOL ", 4) == 0) {
    if (!spkReady()) { Serial.println("[SPK] VOL ignored: codec not ready"); return; }
    int v = kSpkVolume;
    if (sscanf(line + 4, "%d", &v) == 1) {
      if (v < 0) v = 0;
      if (v > 255) v = 255;
      M5.Speaker.setVolume((uint8_t)v);
      M5.Speaker.tone(880, 80);            // so the new volume is audible immediately
    }
  } else if (strncmp(line, "PLCIP ", 6) == 0) {
    // "PLCIP a.b.c.d" — the Thor naming the StampPLC as an authorised head commander.
    int a, b, c, d;
    if (sscanf(line + 6, "%d.%d.%d.%d", &a, &b, &c, &d) == 4) {
      g_plcIp = IPAddress((uint8_t)a, (uint8_t)b, (uint8_t)c, (uint8_t)d);
      g_havePlc = true;
    }
  } else if (strncmp(line, "PMODE ", 6) == 0) {
    const char *m = line + 6;
    const int was = g_payloadMode;
    g_payloadMode = (strcmp(m, "PANTILT") == 0) ? PMODE_PANTILT
                  : (strcmp(m, "ZAXIS") == 0)   ? PMODE_ZAXIS : PMODE_OFF;
    if (g_payloadMode != was) faceInvalidate();
    g_linkOnline = true;
    last_state_ms = millis();
  } else if (strncmp(line, "TURN ", 5) == 0) {
    char d = line[5];
    g_turnCmd = (d == 'R') ? 1 : (d == 'L') ? -1 : 0;
    g_linkOnline = true;
    last_state_ms = millis();
  }
}

static void pollStateSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (g_rxlen > 0) {
        g_rx[g_rxlen] = 0;
        applyLine(g_rx);
        g_rxlen = 0;
      }
    } else if (g_rxlen < (int)sizeof(g_rx) - 1)
      g_rx[g_rxlen++] = c;
  }
  if (g_linkOnline && last_state_ms != 0 && millis() - last_state_ms > 5000)
    g_linkOnline = false;
}

// ================= setup / loop =================
void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  M5.Imu.loadOffsetFromNVS();

  M5.Display.setRotation(3);
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0); // non-blocking HWCDC (see tab5-face memory)

  faceBegin();
  Lift.begin();

  // Initialize 8Servo channels
  Wire.beginTransmission(kServo8Addr);
  Wire.write(kModeReg + 0); // CH0 (Tilt) mode
  Wire.write(kModeServo);
  Wire.endTransmission();

  Wire.beginTransmission(kServo8Addr);
  Wire.write(kModeReg + 1); // CH1 (Pan) mode
  Wire.write(kModeServo);
  Wire.endTransmission();

  // Load or Calibrate Limits
  prefs.begin("opendubs", false);
  g_tiltMin = prefs.getInt("tiltMin", -1);
  g_tiltMax = prefs.getInt("tiltMax", -1);

  if (g_tiltMin == -1 || g_tiltMax == -1) {
    g_isHoming = true;
    HmiCtx ctx;
    ctx.activeHit = HmiHit::NONE;
    ctx.estopConfirm = false;
    ctx.revertSecs = 0;
    auto tickUI = [&]() {
      M5.update();
      hmiRender(ctx);
    };

    tickUI();

    // 1. Center servo
    uint16_t us = map(90, 0, 180, 500, 2500);
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 0 * 2);
    Wire.write(us & 0xFF);
    Wire.write(us >> 8);
    Wire.endTransmission();

    for (int i = 0; i < 20; i++) {
      delay(50);
      tickUI();
    } // wait 1 sec

    float idleCurrent = readServoCurrent();

    // 2. Sweep down to find MIN
    int16_t foundMin = 0;
    for (int i = 90; i >= 0; i--) {
      us = map(i, 0, 180, 500, 2500);
      Wire.beginTransmission(kServo8Addr);
      Wire.write(kPulseReg + 0 * 2);
      Wire.write(us & 0xFF);
      Wire.write(us >> 8);
      Wire.endTransmission();
      delay(40);
      tickUI();
      float c = readServoCurrent();
      if (c > idleCurrent + 1.5f) { // Stall detected
        foundMin = i + 5;           // back off 5 degrees
        break;
      }
    }

    // 3. Return to center
    us = map(90, 0, 180, 500, 2500);
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 0 * 2);
    Wire.write(us & 0xFF);
    Wire.write(us >> 8);
    Wire.endTransmission();
    for (int i = 0; i < 20; i++) {
      delay(50);
      tickUI();
    } // wait 1 sec

    // 4. Sweep up to find MAX
    int16_t foundMax = 180;
    for (int i = 90; i <= 180; i++) {
      us = map(i, 0, 180, 500, 2500);
      Wire.beginTransmission(kServo8Addr);
      Wire.write(kPulseReg + 0 * 2);
      Wire.write(us & 0xFF);
      Wire.write(us >> 8);
      Wire.endTransmission();
      delay(40);
      tickUI();
      float c = readServoCurrent();
      if (c > idleCurrent + 1.5f) { // Stall detected
        foundMax = i - 5;           // back off 5 degrees
        break;
      }
    }

    // 5. Save and apply
    g_tiltMin = foundMin;
    g_tiltMax = foundMax;
    prefs.putInt("tiltMin", g_tiltMin);
    prefs.putInt("tiltMax", g_tiltMax);
    g_isHoming = false;

    // Move to safe center (90, or the midpoint of the bounds if 90 is outside
    // bounds)
    g_targetTilt = 90;
    if (g_targetTilt < g_tiltMin)
      g_targetTilt = g_tiltMin;
    if (g_targetTilt > g_tiltMax)
      g_targetTilt = g_tiltMax;
    us = map(g_targetTilt, 0, 180, 500, 2500);
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 0 * 2);
    Wire.write(us & 0xFF);
    Wire.write(us >> 8);
    Wire.endTransmission();
    tickUI();
  }

  xTaskCreatePinnedToCore(controlTask, "control", 4096, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(safetyTask, "safety", 3072, nullptr, 6, nullptr, 0);
  xTaskCreatePinnedToCore(commsTask, "comms", 4096, nullptr, 1, nullptr, 0);

  g_robotState = 1; // SAFE_IDLE
  g_uiMode = UiMode::FACE;
  uiPrev = demoMs = millis();
  Serial.printf(
      "[TAB5] controller+face up  %dx%d  freeInt=%uKB freePSRAM=%uKB\n",
      (int)M5.Display.width(), (int)M5.Display.height(),
      (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
      (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));
}

void loop() {
  M5.update();
  pollStateSerial(); // consume "STATE <NAME>" pushed by the Thor
  uint32_t now = millis();
  uint32_t dtms = now - uiPrev;
  uiPrev = now;

  handleTouch();

  // optional robot-state demo cycle (the real value arrives from commsTask's
  // state feed)
  if (DEMO_CYCLE && now - demoMs > 3500) {
    demoMs = now;
    g_robotState = (g_robotState + 1) % NST;
  }

  // e-stop release confirm window
  if (confirming && now > confirmUntil)
    confirming = false;

  // controller idle -> face auto-revert (never desert an active move / inhibit
  // / confirm)
  if (g_uiMode == UiMode::CONTROLLER) {
    if (idleLeft > dtms)
      idleLeft -= dtms;
    else
      idleLeft = 0;
    LiftController::Snapshot s = Lift.snapshot();
    bool busy = (s.motion != Motion::STOP) || s.inhibit || confirming;
    if (idleLeft == 0 && !busy)
      enterFace();

    // manual pan/tilt accumulation
    if (g_trackingMode == TrackingMode::MANUAL) {
      static float panAcc = 0, tiltAcc = 0;
      float rate = 45.0f / 1000.0f; // 45 degrees per second
      if (g_activeHold == HmiHit::PAN_LEFT)
        panAcc += rate * dtms;
      else if (g_activeHold == HmiHit::PAN_RIGHT)
        panAcc -= rate * dtms;
      else if (g_activeHold == HmiHit::TILT_UP)
        tiltAcc -= rate * dtms;
      else if (g_activeHold == HmiHit::TILT_DOWN)
        tiltAcc += rate * dtms;
  
      if (abs(panAcc) >= 1.0f) {
        g_targetPan += (int)panAcc;
        panAcc -= (int)panAcc;
        if (g_targetPan < 0)
          g_targetPan = 0;
        if (g_targetPan > 180)
          g_targetPan = 180;
      }
      if (abs(tiltAcc) >= 1.0f) {
        g_targetTilt += (int)tiltAcc;
        tiltAcc -= (int)tiltAcc;
        if (g_targetTilt < g_tiltMin)
          g_targetTilt = g_tiltMin;
        if (g_targetTilt > g_tiltMax)
          g_targetTilt = g_tiltMax;
      }
    }
  }

  if (g_uiMode == UiMode::FACE) {
    faceRender();
  } else {
    HmiCtx ctx{confirming, (int)((idleLeft + 999) / 1000), g_activeHold};
    hmiRender(ctx);
  }
  // The pinch-guard prompt sits on top of whichever screen is showing — the operator needs it
  // on the controls as much as on the face, and it owns no state of its own.
  const uint32_t nowUi = millis();
  promptTick(nowUi);
  promptRender(nowUi);
}
