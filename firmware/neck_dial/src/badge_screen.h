// THE NECK'S SCREEN: A STATUS BADGE, nothing else (the user's design, 2026-10-22). A ring in the
// robot state's colour, the state's label, and who is driving the head; HOLD while held, "releasing
// hold" while a release is announced, and "press to confirm tilt …" while a tilt change waits for
// the button. No animation. Blank at boot until the robot says something.
//
// THE SCREEN IS NEVER ON THE WAY TO THE NETWORK. The neck has no USB cable, so OTA is the only way
// in, and a panel that hangs or faults before Wi-Fi and the UDP socket are up would leave the board
// unreachable. So (main.cpp): the screen starts LAST, after the socket is listening; its start is
// guarded by the task watchdog, so a hang resets the board; and a board whose last reset was a crash
// or a watchdog starts WITHOUT the screen, so a screen fault costs the screen, never the board.
//
// THE DRAW IS ON THE CONTROL LOOP'S THREAD, so it happens only on a CHANGE. A full frame is about
// 25 ms at 40 MHz: one 50 Hz tick late, a few times a minute at most, which the control law absorbs
// (it integrates by elapsed time).
//
// LovyanGFX with a hand-written configuration, and NOT M5Unified or M5GFX: those start the touch
// controller on I2C, which would contend for Port A's `Wire` with the 8Servo that drives the pan,
// the tilt and the chest light. Nothing here touches I2C. Pins from the vendor's own board table
// (notes/design/neck-dial-ui.md, Face & HRI's reading of M5GFX's board_M5Dial).

#pragma once
#include <cstdint>

namespace badge {

struct View {
    bool     told = false;        // a BADGE has arrived since boot
    bool     stale = false;       // ... but none for a while: the robot's word is old
    char     state[16] = "";      // the STATES name, e.g. SAFE_IDLE
    char     driver[12] = "";     // who drives the head, as the dashboard says: show, tracking, ...
    uint32_t rgb = 0;             // the state's colour, 0xRRGGBB, from the dashboard
    char     local[12] = "";      // what the NECK knows better: "hold", "releasing", "rc", "touch", or ""
    int      tiltLo = -1, tiltHi = -1;   // a tilt change waiting for the button, or -1
    bool     wifi = false;
};

// Start the panel. Returns false (and the screen stays off) if it cannot.
bool begin();
bool running();
// Draw `v` if it differs from what is on the screen. Cheap when nothing changed.
void show(const View& v);

}  // namespace badge
