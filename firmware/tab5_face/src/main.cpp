// OpenDubs Tab5 — payload controller + face.
//
// Task layout (P4 is dual-core; rendering must never starve safety):
//   loop()      core 1  UI: touch, face<->controller mode, render (owns M5.Display/Touch)
//   controlTask core 0  lift model tick — ToF read, limit detect, relay-pulse timing (50 Hz)
//   safetyTask  core 0  HIGH PRIORITY — soft e-stop enforcement + (TODO) hardware watchdog
//   commsTask   core 0  stub — WiFi(C6)/CAN state feed + authorize heartbeat (fills link/ip)
//
// Face is home. Any touch reveals the lift controls; they auto-revert to the face after an idle
// timeout (unless the lift is moving) or when a software command drives a lift state change.
#include <M5Unified.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ui_shared.h"
#include "face.h"
#include "hmi.h"
#include "lift.h"
#include <WiFi.h>
#include <WiFiUdp.h>
#include "opendubs_can.h"
#include "opendubs_can_udp.h"

#ifndef OPENDUBS_WIFI_SSID
#define OPENDUBS_WIFI_SSID "opendubs-local"   // override via platformio.ini build_flags; see firmware/tab5_face/README
#endif
#ifndef OPENDUBS_WIFI_PASS
#define OPENDUBS_WIFI_PASS "changeme"
#endif

static const bool     DEMO_CYCLE = false;    // true = auto-cycle robot states (face demo). false = hold SAFE_IDLE + puns.
static const uint32_t IDLE_MS     = 10000;   // controller -> face auto-revert
static const uint32_t CONFIRM_MS  = 2200;    // e-stop release confirm window

// ---- UI state (owned by loop) ----
static uint32_t idleLeft = 0;
static bool     confirming = false;
static uint32_t confirmUntil = 0;
static uint32_t uiPrev = 0, demoMs = 0;

// ================= background tasks =================
static void controlTask(void*) {
  for (;;) { Lift.tick(millis()); vTaskDelay(pdMS_TO_TICKS(20)); }
}
static void safetyTask(void*) {
  for (;;) { Lift.enforceSafe(millis());
    // TODO-HW: feed a hardware/task watchdog here; also drop authorize if the robot
    // heartbeat goes stale while under robot control (fail-safe stop).
    vTaskDelay(pdMS_TO_TICKS(20)); }
}
static opendubs_can::Fault deriveFault(const LiftController::Snapshot& s) {
  // STUCK_TIMEOUT / RELAY_FAULT: no sensor data to detect these yet (SIMULATE has no real
  // relay/motion feedback) — left NONE until the hardware path lands. [TODO-HW]
  if (s.atTop && s.atBot) return {opendubs_can::FaultCode::LIMIT_CONFLICT, 0};
  if (!s.tofOk)           return {opendubs_can::FaultCode::TOF_FAULT, 0};
  return {opendubs_can::FaultCode::NONE, 0};
}

static void commsTask(void*) {
  // [TODO-HW] verify WiFi.begin() actually joins over the ESP32-C6/ESP-Hosted co-processor on
  // real hardware — this is the standard Arduino WiFi API, which pioarduino's ESP32-P4 Tab5
  // support initializes over ESP-Hosted transparently, but it's unverified until bench-tested.
  WiFi.mode(WIFI_STA);
  WiFi.begin(OPENDUBS_WIFI_SSID, OPENDUBS_WIFI_PASS);

  static WiFiUDP payload_udp;
  static const uint16_t kPayloadPort = 5006;   // matches the StampPLC's payload_udp port
  payload_udp.begin(kPayloadPort);
  static opendubs_can::UdpTransport tx(payload_udp, IPAddress(255, 255, 255, 255), kPayloadPort);

  unsigned long lastAuthorizeRxMs = 0;
  unsigned long lastStateTxMs = 0;
  uint8_t       lastFaultCode = static_cast<uint8_t>(opendubs_can::FaultCode::NONE);
  uint8_t       lastCmdSeq = 0;
  bool          haveCmdSeq = false;
  uint8_t       stateSeq = 0;

  for (;;) {
    const bool wifiUp = WiFi.status() == WL_CONNECTED;
    g_linkOnline = wifiUp;

    if (wifiUp) {
      IPAddress ip = WiFi.localIP();
      snprintf(g_ipStr, sizeof(g_ipStr), "%d.%d.%d.%d", ip[0], ip[1], ip[2], ip[3]);

      uint16_t rxId; uint8_t rxDlc; uint8_t rxBuf[8];
      while (tx.poll(rxId, rxDlc, rxBuf)) {
        if (rxId == opendubs_can::kIdAuthorize) {
          lastAuthorizeRxMs = millis();
        } else if (rxId == opendubs_can::kIdLiftCmd) {
          auto c = opendubs_can::decodeLiftCmd(rxBuf, rxDlc);
          if (!haveCmdSeq || c.seq != lastCmdSeq) {   // edge-detect: only act on a NEW seq
            lastCmdSeq = c.seq; haveCmdSeq = true;
            if      (c.cmd == opendubs_can::LiftCmd::UP)   Lift.commandUp();
            else if (c.cmd == opendubs_can::LiftCmd::DOWN) Lift.commandDown();
            else                                           Lift.commandStop();
          }
        }
      }
    } else {
      g_ipStr[0] = '\0';
    }

    // Fail-safe-by-omission must be evaluated regardless of link state: no fresh AUTHORIZE
    // (whether because the content is stale on a live link, or because there's no link at all
    // to carry it) means inhibited. lastAuthorizeRxMs simply stops advancing while WiFi is down,
    // so this naturally trips within kAuthorizeTimeoutMs of an outage starting, and is already
    // tripped at cold boot (lastAuthorizeRxMs's 0 default reads as stale) before any link forms.
    const bool authorized = !opendubs_can::isStale(millis(), lastAuthorizeRxMs,
                                                     opendubs_can::kAuthorizeTimeoutMs);
    Lift.setInhibit(!authorized);   // fail-safe-by-omission: no fresh AUTHORIZE -> inhibited

    if (wifiUp && millis() - lastStateTxMs >= opendubs_can::kLiftStatePeriodMs) {
      auto snap = Lift.snapshot();
      opendubs_can::LiftState st{};
      st.heightMm     = static_cast<uint16_t>(snap.heightMm < 0.0f ? 0.0f : snap.heightMm);
      st.inhibit      = snap.inhibit;
      st.atTop        = snap.atTop;
      st.atBot        = snap.atBot;
      st.tofOk        = snap.tofOk;
      st.relayUpPulse = snap.relayUpPulse;
      st.relayDnPulse = snap.relayDnPulse;
      st.motion       = snap.motion == Motion::UP   ? opendubs_can::LiftMotion::UP
                       : snap.motion == Motion::DOWN ? opendubs_can::LiftMotion::DOWN
                                                      : opendubs_can::LiftMotion::STOP;
      st.seq = stateSeq++;
      uint8_t buf[8]; uint8_t dlc = opendubs_can::encodeLiftState(st, buf);
      tx.send(opendubs_can::kIdLiftState, dlc, buf);
      lastStateTxMs = millis();

      auto fault = deriveFault(snap);
      if (static_cast<uint8_t>(fault.code) != lastFaultCode) {
        uint8_t fbuf[8]; uint8_t fdlc = opendubs_can::encodeFault(fault, fbuf);
        tx.send(opendubs_can::kIdLiftFault, fdlc, fbuf);
        lastFaultCode = static_cast<uint8_t>(fault.code);
      }
    }

    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// ================= UI helpers =================
static void enterController() { g_uiMode = UiMode::CONTROLLER; hmiInvalidate(); idleLeft = IDLE_MS; }
static void enterFace()       { g_uiMode = UiMode::FACE; faceInvalidate(); confirming = false; }

static void onEstopTap() {
  bool inhibited = Lift.snapshot().inhibit;
  if (!inhibited)        { Lift.setInhibit(true);  confirming = false; }
  else if (!confirming)  { confirming = true; confirmUntil = millis() + CONFIRM_MS; }
  else                   { Lift.setInhibit(false); confirming = false; }
}

static void handleTouch() {
  auto td = M5.Touch.getDetail();
  if (!td.wasPressed()) return;
  if (g_uiMode == UiMode::FACE) { enterController(); return; }   // first tap just reveals controls
  idleLeft = IDLE_MS;                                            // any touch keeps controls up
  switch (hmiHitTest(td.x, td.y)) {
    case HmiHit::RAISE: Lift.commandUp();   confirming = false; break;
    case HmiHit::LOWER: Lift.commandDown(); confirming = false; break;
    case HmiHit::ESTOP: onEstopTap(); break;
    default: break;
  }
}

// ================= robot-state feed (USB serial, pushed by the Thor) =================
// The Tab5 has no state inputs of its own — the Thor's FSM (robot_state) pushes "STATE <NAME>\n"
// over this CDC port. NAME matches STATES[].name (INIT/SAFE_IDLE/MANUAL/AUTONOMOUS/DEGRADED/
// E_STOP/CHARGING). Drives the same g_robotState the face + collapsed chip render.
static char     g_rx[64];
static int      g_rxlen = 0;
static uint32_t last_state_ms = 0;

static void applyLine(const char* line) {
  if (strncmp(line, "STATE ", 6) == 0) {
    const char* name = line + 6;
    for (int i = 0; i < NST; ++i)
      if (strcmp(STATES[i].name, name) == 0) {
        g_robotState = i; g_linkOnline = true; last_state_ms = millis(); return;
      }
  } else if (strncmp(line, "TURN ", 5) == 0) {
    char d = line[5];
    g_turnCmd = (d == 'R') ? 1 : (d == 'L') ? -1 : 0;
    g_linkOnline = true; last_state_ms = millis();
  }
}

static void pollStateSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { if (g_rxlen > 0) { g_rx[g_rxlen] = 0; applyLine(g_rx); g_rxlen = 0; } }
    else if (g_rxlen < (int)sizeof(g_rx) - 1) g_rx[g_rxlen++] = c;
  }
  if (g_linkOnline && last_state_ms != 0 && millis() - last_state_ms > 5000) g_linkOnline = false;
}

// ================= setup / loop =================
void setup() {
  auto cfg = M5.config(); M5.begin(cfg);
  M5.Display.setRotation(1);
  Serial.begin(115200); Serial.setTxTimeoutMs(0);   // non-blocking HWCDC (see tab5-face memory)

  faceBegin();
  Lift.begin();

  xTaskCreatePinnedToCore(controlTask, "control", 4096, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(safetyTask,  "safety",  3072, nullptr, 6, nullptr, 0);
  xTaskCreatePinnedToCore(commsTask,   "comms",   4096, nullptr, 1, nullptr, 0);

  g_robotState = 1;                 // SAFE_IDLE
  g_uiMode = UiMode::FACE;
  uiPrev = demoMs = millis();
  Serial.printf("[TAB5] controller+face up  %dx%d  freeInt=%uKB freePSRAM=%uKB\n",
                (int)M5.Display.width(), (int)M5.Display.height(),
                (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024),
                (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024));
}

void loop() {
  M5.update();
  pollStateSerial();                 // consume "STATE <NAME>" pushed by the Thor
  uint32_t now = millis();
  uint32_t dtms = now - uiPrev; uiPrev = now;

  handleTouch();

  // optional robot-state demo cycle (the real value arrives from commsTask's state feed)
  if (DEMO_CYCLE && now - demoMs > 3500) { demoMs = now; g_robotState = (g_robotState + 1) % NST; }

  // e-stop release confirm window
  if (confirming && now > confirmUntil) confirming = false;

  // controller idle -> face auto-revert (never desert an active move / inhibit / confirm)
  if (g_uiMode == UiMode::CONTROLLER) {
    if (idleLeft > dtms) idleLeft -= dtms; else idleLeft = 0;
    LiftController::Snapshot s = Lift.snapshot();
    bool busy = (s.motion != Motion::STOP) || s.inhibit || confirming;
    if (idleLeft == 0 && !busy) enterFace();
  }

  if (g_uiMode == UiMode::FACE) {
    faceRender();
  } else {
    HmiCtx ctx{ confirming, (int)((idleLeft + 999) / 1000) };
    hmiRender(ctx);
  }
}
