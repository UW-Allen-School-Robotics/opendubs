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
#include <Preferences.h>
#include "ui_shared.h"
#include "face.h"
#include "hmi.h"
#include "lift.h"
#include "can_protocol.h"
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ArduinoOTA.h>

#if __has_include("esp_camera.h")
#include "esp_camera.h"
#define HAS_CAMERA 1
#else
#define HAS_CAMERA 0
#endif

static const bool     DEMO_CYCLE = false;    // true = auto-cycle robot states (face demo). false = hold SAFE_IDLE + puns.
static const uint32_t IDLE_MS     = 10000;   // controller -> face auto-revert
static const uint32_t CONFIRM_MS  = 2200;    // e-stop release confirm window

int16_t g_targetPan = 90;
int16_t g_targetTilt = 90;

Preferences prefs;
int16_t g_tiltMin = 0;
int16_t g_tiltMax = 180;
bool g_isHoming = false;

// 8Servo I2C Constants
static constexpr uint8_t kServo8Addr   = 0x25;
static constexpr uint8_t kModeReg      = 0x00;   // + pin
static constexpr uint8_t kPulseReg     = 0x60;   // + pin*2, 16-bit LE microseconds
static constexpr uint8_t kModeServo    = 3;      // SERVO_CTL_MODE

static void applyLine(const char* line);

static float readServoCurrent() {
  uint8_t b[4];
  Wire.beginTransmission(kServo8Addr);
  Wire.write(0xA0); // kCurrentReg
  if (Wire.endTransmission(false) != 0) return 0.0f;
  if (Wire.requestFrom((int)kServo8Addr, 4) != 4) return 0.0f;
  for (int i=0; i<4; i++) b[i] = Wire.read();
  float out;
  memcpy(&out, b, 4);
  return out;
}

// ---- UI state (owned by loop) ----
static uint32_t idleLeft = 0;
static bool     confirming = false;
static uint32_t confirmUntil = 0;
static uint32_t uiPrev = 0, demoMs = 0;
static uint32_t last_state_ms = 0;
static HmiHit   g_activeHold = HmiHit::NONE;

// ================= background tasks =================
static void controlTask(void*) {
  for (;;) { 
    Lift.tick(millis()); 
    
    // Convert angles to microseconds for the 8Servo unit
    uint16_t tiltUs = map(g_targetTilt, 0, 180, 500, 2500);
    uint16_t panUs = map(g_targetPan, 0, 180, 500, 2500);

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
static void safetyTask(void*) {
  for (;;) { Lift.enforceSafe(millis());
    // TODO-HW: feed a hardware/task watchdog here; also drop authorize if the robot
    // heartbeat goes stale while under robot control (fail-safe stop).
    vTaskDelay(pdMS_TO_TICKS(20)); }
}

static void streamerTask(void*) {
  while (WiFi.status() != WL_CONNECTED) {
    vTaskDelay(pdMS_TO_TICKS(500));
  }
  
  WiFiServer server(80);
  server.begin();
  for (;;) {
    WiFiClient client = server.accept();
    if (client) {
      client.println("HTTP/1.1 200 OK");
      client.println("Content-Type: multipart/x-mixed-replace; boundary=frame");
      client.println();
      while (client.connected()) {
#if HAS_CAMERA
        camera_fb_t * fb = esp_camera_fb_get();
        if (fb) {
          client.println("--frame");
          client.println("Content-Type: image/jpeg");
          client.printf("Content-Length: %u\n\n", fb->len);
          client.write(fb->buf, fb->len);
          client.println();
          esp_camera_fb_return(fb);
        }
#endif
        vTaskDelay(pdMS_TO_TICKS(100)); // 10 FPS
      }
      client.stop();
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
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
        if (len >= sizeof(can_frame_udp)) {
          can_frame_udp* frame = (can_frame_udp*)packetBuffer;
          if (frame->can_id == CAN_ID_ROBOT_STATE) {
            CAN_Robot_State* state = (CAN_Robot_State*)frame->data;
            if (state->state_id < NST) {
              g_robotState = state->state_id;
              last_state_ms = millis();
              extern volatile bool g_linkOnline; // ensure we can set it
              g_linkOnline = true;
            }
          } else if (frame->can_id == CAN_ID_PAN_TILT_CMD) {
            CAN_Pan_Tilt_Command* pt = (CAN_Pan_Tilt_Command*)frame->data;
            g_targetPan = pt->pan_angle;
            g_targetTilt = pt->tilt_angle;
            // TODO: Actually drive the PWM servos with these values (e.g., via PbHub or direct PWM)
            // Serial.printf("Received Pan: %d, Tilt: %d\n", g_targetPan, g_targetTilt);
          }
        } else if (len > 0) {
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

      // Periodically broadcast ToF data
      static uint32_t last_tof_send = 0;
      if (millis() - last_tof_send > 50) {
        last_tof_send = millis();
        auto s = Lift.snapshot();
        can_frame_udp frame = {0};
        frame.can_id = CAN_ID_TOF_FRONT;
        frame.can_dlc = sizeof(CAN_ToF_Distance);
        CAN_ToF_Distance* tof = (CAN_ToF_Distance*)frame.data;
        tof->distance_mm = (uint16_t)s.tofMm;
        tof->status = s.tofOk ? 1 : 0;
        
        Udp.beginPacket(IPAddress(255, 255, 255, 255), 5005);
        Udp.write((const uint8_t*)&frame, sizeof(frame));
        Udp.endPacket();
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

static void sendLiftCommandUDP(uint8_t cmd) {
  if (WiFi.status() == WL_CONNECTED) {
    can_frame_udp frame = {0};
    frame.can_id = CAN_ID_LIFT_CMD;
    frame.can_dlc = sizeof(CAN_Lift_Command);
    CAN_Lift_Command* lift_cmd = (CAN_Lift_Command*)frame.data;
    lift_cmd->command = cmd;
    Udp.beginPacket(IPAddress(255, 255, 255, 255), 5005);
    Udp.write((const uint8_t*)&frame, sizeof(frame));
    Udp.endPacket();
  }
}

static void handleTouch() {
  auto td = M5.Touch.getDetail();

  if (g_uiMode == UiMode::FACE) {
    if (td.wasPressed()) { enterController(); }
    return;
  }

  if (td.isPressed()) {
    idleLeft = IDLE_MS;                                            // any touch keeps controls up
    HmiHit hit = hmiHitTest(td.x, td.y);
    if (td.wasPressed() && hit == HmiHit::ESTOP) {
      onEstopTap();
    } else if (hit == HmiHit::RAISE || hit == HmiHit::LOWER ||
               hit == HmiHit::PAN_LEFT || hit == HmiHit::PAN_RIGHT ||
               hit == HmiHit::TILT_UP || hit == HmiHit::TILT_DOWN) {
      if (g_activeHold != hit) {
        if (g_activeHold == HmiHit::RAISE || g_activeHold == HmiHit::LOWER) { 
            Lift.commandStop(); sendLiftCommandUDP(0); 
        }
        if (hit == HmiHit::RAISE) { Lift.commandUp(); sendLiftCommandUDP(1); }
        if (hit == HmiHit::LOWER) { Lift.commandDown(); sendLiftCommandUDP(2); }
        confirming = false;
        g_activeHold = hit;
      }
    } else if (g_activeHold != HmiHit::NONE) {
      if (g_activeHold == HmiHit::RAISE || g_activeHold == HmiHit::LOWER) {
        Lift.commandStop();
        sendLiftCommandUDP(0);
      }
      g_activeHold = HmiHit::NONE;
    }
  } else if (td.wasReleased() || (!td.isPressed() && g_activeHold != HmiHit::NONE)) {
    if (g_activeHold != HmiHit::NONE) {
      if (g_activeHold == HmiHit::RAISE || g_activeHold == HmiHit::LOWER) {
        Lift.commandStop();
        sendLiftCommandUDP(0);
      }
      g_activeHold = HmiHit::NONE;
    }
  }
}

// ================= robot-state feed (USB serial, pushed by the Thor) =================
// The Tab5 has no state inputs of its own — the Thor's FSM (robot_state) pushes "STATE <NAME>\n"
// over this CDC port. NAME matches STATES[].name (INIT/SAFE_IDLE/MANUAL/AUTONOMOUS/DEGRADED/
// E_STOP/CHARGING). Drives the same g_robotState the face + collapsed chip render.
static char     g_rx[64];
static int      g_rxlen = 0;

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
  
  M5.Display.setRotation(3);
  Serial.begin(115200); Serial.setTxTimeoutMs(0);   // non-blocking HWCDC (see tab5-face memory)

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
    HmiCtx ctx; ctx.activeHit = HmiHit::NONE; ctx.estopConfirm = false; ctx.revertSecs = 0;
    auto tickUI = [&]() { M5.update(); hmiRender(ctx); };
    
    tickUI();
    
    // 1. Center servo
    uint16_t us = map(90, 0, 180, 500, 2500);
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 0 * 2);
    Wire.write(us & 0xFF);
    Wire.write(us >> 8);
    Wire.endTransmission();
    
    for (int i=0; i<20; i++) { delay(50); tickUI(); } // wait 1 sec
    
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
      delay(40); tickUI();
      float c = readServoCurrent();
      if (c > idleCurrent + 1.5f) { // Stall detected
        foundMin = i + 5; // back off 5 degrees
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
    for (int i=0; i<20; i++) { delay(50); tickUI(); } // wait 1 sec
    
    // 4. Sweep up to find MAX
    int16_t foundMax = 180;
    for (int i = 90; i <= 180; i++) {
      us = map(i, 0, 180, 500, 2500);
      Wire.beginTransmission(kServo8Addr);
      Wire.write(kPulseReg + 0 * 2);
      Wire.write(us & 0xFF);
      Wire.write(us >> 8);
      Wire.endTransmission();
      delay(40); tickUI();
      float c = readServoCurrent();
      if (c > idleCurrent + 1.5f) { // Stall detected
        foundMax = i - 5; // back off 5 degrees
        break;
      }
    }
    
    // 5. Save and apply
    g_tiltMin = foundMin;
    g_tiltMax = foundMax;
    prefs.putInt("tiltMin", g_tiltMin);
    prefs.putInt("tiltMax", g_tiltMax);
    g_isHoming = false;
    
    // Move to safe center (90, or the midpoint of the bounds if 90 is outside bounds)
    g_targetTilt = 90;
    if (g_targetTilt < g_tiltMin) g_targetTilt = g_tiltMin;
    if (g_targetTilt > g_tiltMax) g_targetTilt = g_tiltMax;
    us = map(g_targetTilt, 0, 180, 500, 2500);
    Wire.beginTransmission(kServo8Addr);
    Wire.write(kPulseReg + 0 * 2);
    Wire.write(us & 0xFF);
    Wire.write(us >> 8);
    Wire.endTransmission();
    tickUI();
  }

#if HAS_CAMERA
  // Note: Tab5 ESP32-P4 requires MIPI CSI camera initialization.
  // This is a placeholder for the actual MIPI config.
  camera_config_t config = {0};
  // config.pin_... = ...;
  if (ESP_FAIL == ESP_OK) { // esp_camera_init(&config)
    Serial.println("Camera initialized.");
  } else {
    Serial.println("Camera init failed or missing config.");
  }
#endif

  xTaskCreatePinnedToCore(controlTask, "control", 4096, nullptr, 3, nullptr, 0);
  xTaskCreatePinnedToCore(safetyTask,  "safety",  3072, nullptr, 6, nullptr, 0);
  xTaskCreatePinnedToCore(commsTask,   "comms",   4096, nullptr, 1, nullptr, 0);
  xTaskCreatePinnedToCore(streamerTask,"streamer",8192, nullptr, 1, nullptr, 1);

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

    // manual pan/tilt accumulation
    static float panAcc = 0, tiltAcc = 0;
    float rate = 45.0f / 1000.0f; // 45 degrees per second
    if (g_activeHold == HmiHit::PAN_LEFT) panAcc += rate * dtms;
    else if (g_activeHold == HmiHit::PAN_RIGHT) panAcc -= rate * dtms;
    else if (g_activeHold == HmiHit::TILT_UP) tiltAcc -= rate * dtms;
    else if (g_activeHold == HmiHit::TILT_DOWN) tiltAcc += rate * dtms;
    
    if (abs(panAcc) >= 1.0f) {
      g_targetPan += (int)panAcc;
      panAcc -= (int)panAcc;
      if (g_targetPan < 0) g_targetPan = 0;
      if (g_targetPan > 180) g_targetPan = 180;
    }
    if (abs(tiltAcc) >= 1.0f) {
      g_targetTilt += (int)tiltAcc;
      tiltAcc -= (int)tiltAcc;
      if (g_targetTilt < g_tiltMin) g_targetTilt = g_tiltMin;
      if (g_targetTilt > g_tiltMax) g_targetTilt = g_tiltMax;
    }
  }

  if (g_uiMode == UiMode::FACE) {
    faceRender();
  } else {
    HmiCtx ctx{ confirming, (int)((idleLeft + 999) / 1000), g_activeHold };
    hmiRender(ctx);
  }
}
