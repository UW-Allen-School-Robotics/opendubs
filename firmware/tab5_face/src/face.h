// OpenDubs Tab5 — the expressive eyes-only face (home screen). Renders whatever robot state
// g_robotState names, morphing expression + gaze + blink, with the AUTONOMOUS radar, demo turn
// chevrons, and a SAFE_IDLE pun ticker along the bottom.
#pragma once
#include <cstdint>

void faceBegin();        // allocate the eye + glow sprites (call once, from setup)
void faceInvalidate();   // force a full repaint (call when returning to the face from controls)
void faceRender();       // draw one frame of the face (owns M5.Display; call only from the UI task)

// ---- gaze leads motion -------------------------------------------------------------------
// Look at a destination BEFORE moving there. An observer who sees the eyes go somewhere first can
// infer where the robot is about to go, which is the cheapest form of legible motion there is:
// it changes no trajectory, needs no planner, and costs nothing. (Legibility -- letting an
// observer infer the goal from motion in progress -- is distinct from predictability, which only
// matches expectation. Dragan & Srinivasa; see notes/design/hri-interaction-design.md.)
//
// ARGUMENTS ARE IN THE ROBOT'S OWN FRAME, because that is how every caller thinks: "I am about to
// move to my right." x = +1 the robot's right, y = +1 up. The viewer-frame mirror (the robot's
// right is the observer's left) is applied INSIDE, exactly as the existing TURN handling does.
//
// hold_ms is how long the gaze stays committed. The face CANNOT delay the robot, so a caller that
// genuinely wants the gaze to arrive first must call this, wait, and only then command motion --
// the lead time belongs to whoever is moving, not here.
void faceLookAt(float x, float y, uint32_t hold_ms);
