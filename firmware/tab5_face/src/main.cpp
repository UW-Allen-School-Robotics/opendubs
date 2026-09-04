// OpenDubs Tab5 — payload controller + face.
//
// Task layout (P4 is dual-core; rendering must never starve safety):
//   loop()      core 1  UI: touch, face<->controller mode, render (owns M5.Display/Touch)
//   controlTask core 0  lift model tick — ToF read, limit detect, relay-pulse timing (50 Hz)
//   safetyTask  core 0  HIGH PRIORITY — soft e-stop enforcement + (TODO) hardware watchdog
//   commsTask   core 0  WiFi(C6 ESP-Hosted) + ArduinoOTA over "Opendubs2"
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
#include <ArduinoOTA.h>

static const bool     DEMO_CYCLE = false;    // true = auto-cycle robot states (face demo). false = hold SAFE_IDLE + puns.
static const uint32_t IDLE_MS     = 10000;   // controller -> face auto-revert
static const uint32_t CONFIRM_MS  = 2200;    // e-stop release confirm window

static void applyLine(const char* line);

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
static WiFiUDP Udp;
static const uint16_t UDP_PORT = 4242;

static void commsTask(void*) {
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
      strncpy(g_ipStr, WiFi.localIP().toString().c_str(), sizeof(g_ipStr)-1);
      if (!ota_setup) {
        ArduinoOTA.setHostname("opendubs-tab5");
        ArduinoOTA.begin();
        ota_setup = true;
      }
      if (!udp_setup) {
        Udp.begin(UDP_PORT);
        udp_setup = true;
      }
      ArduinoOTA.handle();

      // Receive UDP packets for robot state feed
      int packetSize = Udp.parsePacket();
      if (packetSize) {
        char packetBuffer[64];
        int len = Udp.read(packetBuffer, sizeof(packetBuffer) - 1);
        if (len > 0) {
          packetBuffer[len] = 0;
          // Trim trailing newlines/carriage returns
          for(int i = len - 1; i >= 0; i--) {
            if(packetBuffer[i] == '\n' || packetBuffer[i] == '\r') {
               packetBuffer[i] = 0;
            } else {
               break;
            }
          }
          applyLine(packetBuffer);
        }
      }
    } else {
      if (!hosted_ok) {
        strncpy(g_ipStr, "ERR: ESP-Hosted Failed", sizeof(g_ipStr)-1);
      } else {
        snprintf(g_ipStr, sizeof(g_ipStr)-1, "Connecting... (%d)", (int)WiFi.status());
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
  
  M5.Imu.loadOffsetFromNVS();
  
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
