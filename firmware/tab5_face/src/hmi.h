// OpenDubs Tab5 — the lift-controller HMI (revealed by touch, reverts to the face on idle).
// Collapsed robot-state chip + Tab5 identity + relay/limit lamps on the left, the RAISE/height/
// LOWER stack in the center, and an oversized soft e-stop on the right.
#pragma once

enum class HmiHit { NONE, RAISE, LOWER, ESTOP, PAN_LEFT, PAN_RIGHT, TILT_UP, TILT_DOWN, MODE_TOGGLE };

struct HmiCtx {
  bool estopConfirm;   // release is armed (waiting for the 2nd tap)
  int  revertSecs;     // seconds until auto-revert to the face
  HmiHit activeHit;    // currently pressed button (for UI feedback)
};

void   hmiInvalidate();               // force a full chrome repaint (on entering the screen)
HmiHit hmiHitTest(int x, int y);      // which control a touch at (x,y) hits
void   hmiRender(const HmiCtx& ctx);  // draw one frame (owns M5.Display; UI task only)
