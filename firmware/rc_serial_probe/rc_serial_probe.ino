#include <Arduino.h>
#include <M5StamPLC.h>

// From M5StamPLC/src/pin_config.h:
// STAMPLC_PIN_GROVE_BLUE_SDA = 5, STAMPLC_PIN_GROVE_BLUE_SCL = 4
static constexpr int kBluePortRxPin = 5;
static constexpr int kBluePortTxPin = 4;

struct UartProfile {
    const char* name;
    uint32_t baud;
    uint32_t config;
    bool inverted;
};

static const UartProfile kProfiles[] = {
    {"iBUS", 115200, SERIAL_8N1, false},
    {"CRSF", 420000, SERIAL_8N1, false},
    {"SBUS (non-inverted)", 100000, SERIAL_8E2, false},
    {"SBUS (inverted)", 100000, SERIAL_8E2, true},
};

HardwareSerial& rc = Serial2;
int active_profile = 0;
unsigned long profile_started_ms = 0;
unsigned long last_rx_ms = 0;
size_t bytes_in_profile = 0;
size_t line_col = 0;

void beginProfile(int idx)
{
    const auto& p = kProfiles[idx];
    rc.end();
    delay(20);
    rc.begin(p.baud, p.config, kBluePortRxPin, kBluePortTxPin, p.inverted);

    bytes_in_profile = 0;
    line_col         = 0;
    profile_started_ms = millis();

    Serial.println();
    Serial.printf("[RC] Profile: %s | baud=%lu | cfg=0x%lX | inverted=%s | RX=G%d TX=G%d\n", p.name,
                  static_cast<unsigned long>(p.baud), static_cast<unsigned long>(p.config),
                  p.inverted ? "yes" : "no", kBluePortRxPin, kBluePortTxPin);
}

void setup()
{
    auto cfg            = M5.config();
    cfg.serial_baudrate = 115200;
    M5.begin(cfg);
    M5StamPLC.begin();

    Serial.println("\nM5StamPLC RC Serial Probe");
    Serial.println("Receiver TX should go to blue-port G5 (RX).");
    Serial.println("Cycling iBUS -> CRSF -> SBUS until data is seen.");

    beginProfile(active_profile);
}

void loop()
{
    M5.update();
    M5StamPLC.update();

    while (rc.available() > 0) {
        const int b = rc.read();
        if (b < 0) {
            break;
        }

        if (line_col == 0) {
            Serial.printf("[RC] ");
        }

        Serial.printf("%02X ", static_cast<uint8_t>(b));
        line_col++;
        bytes_in_profile++;
        last_rx_ms = millis();

        if (line_col >= 24) {
            Serial.println();
            line_col = 0;
        }
    }

    // If no data arrives on this profile for 3 seconds, try the next one.
    if (bytes_in_profile == 0 && (millis() - profile_started_ms) > 3000) {
        active_profile = (active_profile + 1) % (sizeof(kProfiles) / sizeof(kProfiles[0]));
        beginProfile(active_profile);
    }

    // Print a short heartbeat once data is flowing.
    if (bytes_in_profile > 0 && (millis() - last_rx_ms) > 1000) {
        Serial.printf("\n[RC] Seen %u bytes on profile %s\n", static_cast<unsigned>(bytes_in_profile),
                      kProfiles[active_profile].name);
        last_rx_ms = millis();
    }

    delay(2);
}
