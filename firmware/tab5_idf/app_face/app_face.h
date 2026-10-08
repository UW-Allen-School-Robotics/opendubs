/*
 * OpenDubs face — Phase 1.1 gate demonstration.
 *
 * The question this answers: does LVGL plus smooth_ui_toolkit give us materially better eye
 * ANIMATION OPTIONS than the bespoke M5GFX renderer? That is the reason for the ESP-IDF switch;
 * face detection and the camera are upside on top of it.
 *
 * What it shows that the bespoke renderer could not do:
 *   1. UNBOUNDED GAZE. The pupil is its own LVGL object inside the eye body, so its travel is
 *      (body - pupil), not the 11 px that was left over inside a sprite the eye nearly filled.
 *   2. TWO MOTION MODELS AT ONCE. Gaze uses EASING with a short, amplitude-scaled duration -- a
 *      ballistic saccade, per Lee/Badler "Eyes Alive". Shape and emote use SPRINGS, which settle
 *      with overshoot and are what makes a morph read as physical. The old renderer had exactly
 *      one exponential lerp for everything, which is why gaze drifted and never fixated.
 *   3. INDEPENDENT LAYERS. Body, pupil, highlight and eyelid are separate objects, so they can be
 *      animated against each other instead of being re-rasterised together each frame.
 *   4. DECLARATIVE ANIMATION. `value = target;` and the library owns the interpolation, with
 *      delay, repeat and completion callbacks available.
 */
#pragma once
#include <mooncake.h>

class AppFace : public mooncake::AppAbility {
public:
    AppFace();
    void onCreate() override;
    void onOpen() override;
    void onRunning() override;
    void onClose() override;
};
