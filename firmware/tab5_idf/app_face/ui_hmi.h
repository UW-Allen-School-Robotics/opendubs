// The lift-controller HMI: the screen the Arduino firmware showed when you touched the face.
//
// WHY THIS EXISTS AS A PORT, NOT AN INVENTION. firmware/tab5_face/src/hmi.cpp is 364 lines of
// M5GFX primitives that already settled every question of layout, palette, font floor and
// wording -- including the font floor's reason ("nothing meaningful below ~12pt: the panel is
// ~24in away at knee height") and the e-stop's exact caption. The IDF rewrite had reduced the
// touch UI to an audio/telemetry settings page, which is one row of a nine-row screen. This
// restores the rest. Layout constants, palette and copy are taken from hmi.cpp verbatim so the
// two look like the same product.
//
// TWO DELIBERATE DEVIATIONS FROM THE ARDUINO VERSION, both stated so neither looks like a bug:
//
//  1. The e-stop ring is drawn as concentric CIRCLES, not an octagon. LVGL has no octagon
//     primitive, and the alternatives (a canvas, or a generated image) cost memory and a build
//     step for a silhouette difference nobody reads at 24 inches. A round red mushroom is the
//     standard anyway.
//
//  2. EVERY COMMAND CONTROL IS DISABLED, VISIBLY AND ON PURPOSE. The Arduino HMI sent lift and
//     pan/tilt requests over CAN; the IDF firmware has no outbound link to the StamPLC yet --
//     only the inbound "STATE <NAME>" feed. A RAISE button that looks live and commands nothing
//     is worse than no button, and a soft e-stop that looks live and inhibits nothing is
//     dangerous. So they render greyed, with the reason on screen, and they do not respond. They
//     enable when the outbound link lands, which is a one-line change at kCommandLink.
#pragma once

#include <stdint.h>

// What the HMI needs from the face each tick. Passed in rather than reached for: the state table
// lives in app_face.cpp's anonymous namespace and should stay there, so the face owns the state
// and this file owns the pixels.
struct HmiFeed {
    const char* stateLabel;    // "SAFE IDLE"
    const char* stateDesc;     // "Motion disabled"
    uint32_t    stateColour;   // 0xRRGGBB, the chip's swatch
    bool        stateLinked;   // false when no STATE has arrived, or it has gone stale
    const char* net;           // netStatus(): "wifi: 192.168.68.119", "wifi: connecting", ...
};

// Builds the screen, hidden, plus the touch-anywhere reveal on the face. LVGL lock held.
void hmiBuild();

// True while the HMI is showing, so the face yields the glass exactly as it does for settings.
bool hmiOwnsPanel();

// One frame of dynamic values, plus the idle auto-revert countdown. LVGL lock held.
void hmiTick(uint32_t nowMs, const HmiFeed& feed);

// Touch counters for the UDP PING reply. Counted inside event callbacks only, so this costs the
// render loop nothing -- unlike the per-frame indev probe it replaces, which correlated with
// touch breaking for reasons never established.
const char* hmiDiag();
