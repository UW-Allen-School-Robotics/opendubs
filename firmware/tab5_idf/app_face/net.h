// Wi-Fi station bring-up. Stage 1 of the OTA chain (see notes/design/tab5-idf-ota.md).
//
// WHY THIS MATTERS MORE THAN IT LOOKS. The robot's head is flashed OVER THE AIR today, by
// firmware/tab5_face/ota-from-thor.sh. The ESP-IDF rewrite can only be flashed over USB -- and the
// Tab5 lives behind the robot's face on the e-stop-protected drive branch, so every USB flash
// means closing the contactor, finding a cable and reaching into the head. Shipping IDF without
// OTA is therefore a step BACKWARDS from what the robot runs now, and it makes every later stage
// of robot validation more expensive. This is the gap that gates the rest.
//
// The chain is: STA association (here) -> a UDP text-command listener on 4242 -> `OTA <url>`
// pulling over HTTP and self-flashing. The architecture transfers unchanged from the Arduino
// firmware, which never used ArduinoOTA either -- its listener sits behind the ESP-Hosted
// co-processor and fails silently.
//
// CREDENTIALS ARE NEVER COMPILED IN FROM THE REPO. Order of preference:
//   1. Whatever esp_wifi already has in NVS. `nvs` is pinned at 0x9000/0x5000 byte-for-byte
//      matching the Arduino default_16MB.csv, so if this board ever associated under the old
//      firmware the credentials are still there -- Arduino's WiFi library is a wrapper over
//      esp_wifi and persists to the same `nvs.net80211` namespace. Costs one call to find out.
//   2. Otherwise OPENDUBS_WIFI_SSID / OPENDUBS_WIFI_PASS passed to build.sh, which writes them
//      into a generated header in the EPHEMERAL build tree. They are never written to our repo.
// Once associated, esp_wifi persists them itself, so step 2 is needed once per board.
#pragma once

#include <stdint.h>

// Starts the station task. Safe to call unconditionally.
void netStart();

// Short human-readable state for the panel: "wifi: connecting", "wifi: 10.42.2.37",
// "wifi: no credentials", "wifi: auth failed (3)". Stable buffer, safe for
// lv_label_set_text_static.
const char* netStatus();

// True once we hold an IP. The OTA and UDP layers must not start before this.
bool netReady();
