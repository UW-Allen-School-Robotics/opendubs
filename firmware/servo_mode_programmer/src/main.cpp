// goBILDA / REV 2000-series dual-mode servo reprogrammer.
//
// These servos switch between POSITIONAL and CONTINUOUS rotation over a
// Feetech/Dynamixel-style half-duplex serial link on the SIGNAL wire — not over PWM:
//
//     FF FF <ID> <LEN> <INST> <params...> <checksum>     checksum = ~(sum of ID..params)
//     76800 baud, 8N1, single wire shared between TX and RX
//
// The only difference between the two modes is the 44-byte config block written at
// address 6; the unlock/lock either side is identical. So the change is reversible.
//
// Protocol reverse-engineered by Asaf / FRC 5951 (Chief Delphi); checksums independently
// re-derived here.
//
// WHY AN UNO. A first attempt driving this from a Tab5 GPIO at 3.3 V had no effect. The
// two candidate explanations were (a) the servo is not ID 1, so the packets were ignored,
// and (b) 3.3 V does not swing the line hard enough for a servo expecting a 5 V
// programmer. The Uno addresses (b) directly, and PING addresses (a).
//
// WIRING
//   servo SIGNAL (white/yellow) -> pin 3
//   servo GND    (black/brown)  -> GND        <- required; the link is single-ended
//   servo V+     (red)          -> 5V, or better an external 5-6 V supply with GND common
//
// The servo is never commanded to move here, so its draw stays low — but a torque servo
// on the Uno's regulator is still a poor idea if anything makes it twitch. External power
// is the safer choice if it's to hand.
//
// COMMANDS (USB serial, 115200)
//   PING          sweep IDs 0-253 and report which answer  <- run this FIRST
//   POS           write the positional-mode config
//   CONT          write the continuous-rotation config
//   SHOW          print the packets without sending anything
#include <Arduino.h>
#include <SoftwareSerial.h>

static const uint8_t PIN_SERVO = 3;
static SoftwareSerial ss(PIN_SERVO, PIN_SERVO);   // half-duplex: one wire, both directions

// ---- packets ---------------------------------------------------------------------
static const uint8_t kPing[]   PROGMEM = {255,255,254,2,1,254};
static const uint8_t kRead[]   PROGMEM = {255,255,1,4,2,21,1,226};
static const uint8_t kUnlock[] PROGMEM = {255,255,1,4,3,52,0,195};   // addr 52 = 0
static const uint8_t kLock[]   PROGMEM = {255,255,1,4,3,52,1,194};   // addr 52 = 1 (save)
static const uint8_t kCont[]   PROGMEM = {255,255,1,47,3,6,50,20,0,5,0,10,0,10,0,30,0,0,0,3,
    255,1,0,0,0,65,3,197,1,244,1,255,0,0,2,6,14,5,170,3,232,0,20,0,0,0,0,3,232,0,153};
static const uint8_t kPos[]    PROGMEM = {255,255,1,47,3,6,2,30,0,5,0,15,0,45,0,0,0,0,15,3,
    252,0,0,0,0,65,3,197,0,0,1,255,1,0,2,9,196,1,244,3,232,0,1,0,0,0,0,3,232,0,178};

static uint8_t chk(const uint8_t *b, uint8_t n)   // b at ID, n = ID..params
{
    uint16_t s = 0;
    for (uint8_t i = 0; i < n; ++i) s += b[i];
    return (uint8_t)(~s & 0xFF);
}

// Transmit from PROGMEM, then release the wire so the servo can answer on it.
// SoftwareSerial hears its own transmission on the shared pin, so the echo is discarded
// before listening for a real reply.
static uint8_t xfer(const uint8_t *pkt, uint8_t n, uint8_t *rx, uint8_t rxMax, uint16_t waitMs)
{
    pinMode(PIN_SERVO, OUTPUT);
    for (uint8_t i = 0; i < n; ++i) ss.write(pgm_read_byte(pkt + i));   // blocking, bit-banged
    pinMode(PIN_SERVO, INPUT_PULLUP);
    delayMicroseconds(150);
    while (ss.available()) ss.read();            // drop our own echo
    const uint32_t t0 = millis();
    uint8_t got = 0;
    while (millis() - t0 < waitMs) {
        while (ss.available() && got < rxMax) rx[got++] = ss.read();
    }
    return got;
}

static void sendOnly(const uint8_t *pkt, uint8_t n, uint16_t gapMs)
{
    uint8_t rx[8];
    xfer(pkt, n, rx, sizeof(rx), 0);
    delay(gapMs);
}

// ---- operations ------------------------------------------------------------------
static void pingSweep()
{
    Serial.println(F("PING: sweeping IDs 0-253 ..."));
    uint8_t rx[16];
    uint8_t found = 0;
    for (uint16_t id = 0; id <= 253; ++id) {
        uint8_t body[3] = {(uint8_t)id, 2, 1};                 // ID, LEN=2, INST=PING
        uint8_t pkt[6]  = {0xFF, 0xFF, body[0], body[1], body[2], chk(body, 3)};
        pinMode(PIN_SERVO, OUTPUT);
        for (uint8_t i = 0; i < sizeof(pkt); ++i) ss.write(pkt[i]);
        pinMode(PIN_SERVO, INPUT_PULLUP);
        delayMicroseconds(150);
        while (ss.available()) ss.read();
        const uint32_t t0 = millis();
        uint8_t got = 0;
        while (millis() - t0 < 6) while (ss.available() && got < sizeof(rx)) rx[got++] = ss.read();
        if (got >= 4) {
            Serial.print(F("  ID ")); Serial.print(id); Serial.print(F(" replied:"));
            for (uint8_t i = 0; i < got; ++i) { Serial.print(' '); Serial.print(rx[i], HEX); }
            Serial.println();
            found++;
        }
    }
    Serial.print(F("PING: done — ")); Serial.print(found); Serial.println(F(" servo(s) answered"));
    if (!found) {
        Serial.println(F("  Nothing replied. Either the servo does not speak this protocol,"));
        Serial.println(F("  or the wiring/ground is wrong. Check GND before anything else."));
    }
}

static void program(bool positional)
{
    Serial.print(F("Writing ")); Serial.print(positional ? F("POSITIONAL") : F("CONTINUOUS"));
    Serial.println(F(" config ..."));
    sendOnly(kPing,   sizeof(kPing),   2);
    sendOnly(kRead,   sizeof(kRead),   2);
    sendOnly(kUnlock, sizeof(kUnlock), 3);
    if (positional) sendOnly(kPos,  sizeof(kPos),  6);
    else            sendOnly(kCont, sizeof(kCont), 6);
    sendOnly(kLock,   sizeof(kLock),   2);
    Serial.println(F("Sent. POWER-CYCLE THE SERVO, then re-test its mode."));
    Serial.println(F("  positional: commanded to a value it arrives and current drops to idle"));
    Serial.println(F("  continuous: it keeps turning and current never settles"));
}

static void dump(const __FlashStringHelper *label, const uint8_t *p, uint8_t n)
{
    Serial.print(label); Serial.print(F(": "));
    for (uint8_t i = 0; i < n; ++i) {
        const uint8_t b = pgm_read_byte(p + i);
        if (b < 16) Serial.print('0');
        Serial.print(b, HEX); Serial.print(' ');
    }
    Serial.println();
}

void setup()
{
    Serial.begin(115200);
    ss.begin(76800);
    pinMode(PIN_SERVO, INPUT_PULLUP);
    delay(300);
    Serial.println(F("\n=== dual-mode servo reprogrammer (5V) ==="));
    Serial.println(F("wiring: servo SIGNAL -> pin 3, GND -> GND, V+ -> 5V or external"));
    Serial.println(F("commands: PING | POS | CONT | SHOW"));
    Serial.println(F("run PING first — it proves the link before anything writes EEPROM"));
}

void loop()
{
    static char line[16];
    static uint8_t len = 0;
    while (Serial.available()) {
        const char c = Serial.read();
        if (c == '\n' || c == '\r') {
            line[len] = 0;
            if (len) {
                if      (!strcasecmp(line, "PING")) pingSweep();
                else if (!strcasecmp(line, "POS"))  program(true);
                else if (!strcasecmp(line, "CONT")) program(false);
                else if (!strcasecmp(line, "SHOW")) {
                    dump(F("ping  "), kPing,   sizeof(kPing));
                    dump(F("read  "), kRead,   sizeof(kRead));
                    dump(F("unlock"), kUnlock, sizeof(kUnlock));
                    dump(F("pos   "), kPos,    sizeof(kPos));
                    dump(F("cont  "), kCont,   sizeof(kCont));
                    dump(F("lock  "), kLock,   sizeof(kLock));
                } else Serial.println(F("? PING | POS | CONT | SHOW"));
            }
            len = 0;
        } else if (len < sizeof(line) - 1) line[len++] = c;
    }
}
