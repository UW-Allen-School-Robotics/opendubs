#include "app_startup_anim.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <lvgl.h>

// montserrat_40, not 48: the DEVICE build configures LVGL through sdkconfig (CONFIG_LV_CONF_SKIP=y)
// and CONFIG_LV_FONT_MONTSERRAT_48 is not enabled there. lv_conf.h governs only the desktop build.

using namespace mooncake;
using namespace smooth_ui_toolkit;

namespace {
lv_obj_t* g_name = nullptr;
lv_obj_t* g_sub  = nullptr;
// SAFE_IDLE's green, so the splash and the face it hands over to are the same robot.
constexpr uint32_t kAccent = 0x2ecc58;
}

AppStartupAnim::AppStartupAnim() { setAppInfo().name = "AppStartupAnim"; }

void AppStartupAnim::onCreate() { mclog::tagInfo(getAppInfo().name, "on create"); }

void AppStartupAnim::onOpen()
{
    mclog::tagInfo(getAppInfo().name, "on open");
    GetHAL()->lvglLock();

    // BLACK, matching the face. The vendor splash was white and the transition into the face was a
    // visible flash -- the kind of detail that reads as "unfinished" without anyone being able to
    // say why.
    lv_obj_t* scr = lv_screen_active();
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x000000), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    g_name = lv_label_create(scr);
    lv_obj_set_style_text_color(g_name, lv_color_hex(kAccent), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_name, &lv_font_montserrat_40, LV_PART_MAIN);
    lv_label_set_text(g_name, "OpenDubs");
    lv_obj_align(g_name, LV_ALIGN_CENTER, 0, -18);

    g_sub = lv_label_create(scr);
    lv_obj_set_style_text_color(g_sub, lv_color_hex(0x4a5a50), LV_PART_MAIN);
    lv_obj_set_style_text_font(g_sub, &lv_font_montserrat_24, LV_PART_MAIN);
    lv_label_set_text(g_sub, "UW Allen School Robotics");
    lv_obj_align(g_sub, LV_ALIGN_CENTER, 0, 42);

    GetHAL()->lvglUnlock();

    // Fade up and settle the letter spacing: cheap, and it reads as the robot coming to rather
    // than as a static image being shown.
    _opa.begin(); _gap.begin();
    _opa.animationType = animation_type::easing;
    _opa.easingOptions().duration = 0.55f;
    _opa.teleport(0.0f);  _opa = 255.0f;
    _gap.springOptions().visualDuration = 0.7f;
    _gap.teleport(26.0f); _gap = 2.0f;

    _t0 = GetHAL()->millis();
}

void AppStartupAnim::onRunning()
{
    const uint32_t now = GetHAL()->millis();

    GetHAL()->lvglLock();
    const int opa = (int)(float)_opa;
    lv_obj_set_style_text_opa(g_name, (lv_opa_t)opa, LV_PART_MAIN);
    lv_obj_set_style_text_opa(g_sub, (lv_opa_t)(opa > 160 ? 160 : opa), LV_PART_MAIN);
    lv_obj_set_style_text_letter_space(g_name, (int)(float)_gap, LV_PART_MAIN);
    GetHAL()->lvglUnlock();

    // An audible boot cue is worth keeping -- a robot that comes up silently looks dead for the
    // second before the face appears. This is still M5's startup sound; 1.3 replaces it with one
    // of ours, at which point the last of the vendor's identity goes.
    if (!_sfx && now - _t0 > 120) {
        GetHAL()->setSpeakerVolume(80);
        GetHAL()->playStartupSfx();
        _sfx = true;
    }

    // NO startWifiAp() here, deliberately -- see the header.
    if (now - _t0 > 1700) close();
}

void AppStartupAnim::onClose()
{
    mclog::tagInfo(getAppInfo().name, "on close");
    GetHAL()->lvglLock();
    if (g_name) { lv_obj_del(g_name); g_name = nullptr; }
    if (g_sub)  { lv_obj_del(g_sub);  g_sub  = nullptr; }
    GetHAL()->lvglUnlock();
}
