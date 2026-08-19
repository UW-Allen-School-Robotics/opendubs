// OpenDubs Tab5 — the expressive eyes-only face (home screen). Renders whatever robot state
// g_robotState names, morphing expression + gaze + blink, with the AUTONOMOUS radar, demo turn
// chevrons, and a SAFE_IDLE pun ticker along the bottom.
#pragma once

void faceBegin();        // allocate the eye + glow sprites (call once, from setup)
void faceInvalidate();   // force a full repaint (call when returning to the face from controls)
void faceRender();       // draw one frame of the face (owns M5.Display; call only from the UI task)
