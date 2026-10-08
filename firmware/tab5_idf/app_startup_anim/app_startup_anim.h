/*
 * OpenDubs boot splash. Replaces the M5Stack vendor splash: the robot should not boot into a dev
 * board's branding.
 *
 * Also fixes two things about the original beyond the logo:
 *   - It was WHITE, and the face is black, so booting flashed white then snapped to black.
 *   - It called GetHAL()->startWifiAp(), which stands up an access point. This robot joins an
 *     existing network; an unexpected open AP on a machine students use is not wanted.
 */
#pragma once
#include <mooncake.h>
#include <smooth_ui_toolkit.h>
#include <stdint.h>

class AppStartupAnim : public mooncake::AppAbility {
public:
    AppStartupAnim();
    void onCreate() override;
    void onOpen() override;
    void onRunning() override;
    void onClose() override;

private:
    uint32_t _t0 = 0;
    bool     _sfx = false;
    smooth_ui_toolkit::AnimateValue _opa, _gap;
};
