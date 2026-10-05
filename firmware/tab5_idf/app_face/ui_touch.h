/*
 * ui_touch — the LVGL input device, the entry gesture, and which screen owns the panel.
 *
 * notes/design/tab5-dashboard.md's last open item under "Still open" is the one this file
 * answers first: "Touch does not exist in the head firmware yet. [...] every grade above is a
 * design, not a change to something that works, and the first implementation question is the
 * LVGL input device rather than the policy."
 *
 * WHAT WAS FOUND, READING THE BUILD TREE RATHER THAN GUESSING AT IT:
 *
 *  1. THE INPUT DEVICE ALREADY EXISTS. `bsp_display_start_with_config()` — which
 *     `hal_esp32.cpp` calls — detects the panel type and registers one of two pointer indevs:
 *     `bsp_display_indev_init()` (GT911, a raw lv_indev_create + read callback) or
 *     `bsp_display_indev_init_to_st7123()` (ST712x, via esp_lvgl_port's lvgl_port_add_touch).
 *     Both end up as LV_INDEV_TYPE_POINTER on the same display. The HAL's own `lvTouchpad`
 *     member is commented out in that file, which is what makes it look as though nothing is
 *     wired; the BSP's `disp_indev` is the live one. So this file DISCOVERS the indev rather
 *     than creating one, and says loudly on the panel if it cannot find it.
 *
 *  2. TOUCH IS READ INSIDE THE LVGL TASK, so handlers are locked by construction.
 *     esp_lvgl_port's loop is `lock -> read every indev -> lv_timer_handler() -> unlock`
 *     (esp_lvgl_port.c, the LVGL_PORT_EVENT_TOUCH branch). An LVGL event callback therefore
 *     already holds the mutex. Nothing under ui_grade/ui_controls/ui_touch calls lvglLock() from
 *     a callback, and that is deliberate — see the call-graph table added to AUDIT.md.
 *
 *  3. THE ROTATION IS HANDLED, AND ITS HANDEDNESS IS UNVERIFIED. The panel is natively 720x1280
 *     and the HAL sets LV_DISPLAY_ROTATION_90; LVGL 9.2's `indev_pointer_proc()`
 *     (src/indev/lv_indev.c) transforms a 90-degree display's points as
 *     `x' = ver_res - y - 1; y' = x`. With x_max=720 / y_max=1280 in both BSP touch configs and
 *     no swap_xy / mirror flags set, that is DIMENSIONALLY consistent: a touch lands inside
 *     0..1279 x 0..719. Whether it lands on the thing the operator is looking at depends on the
 *     digitiser's physical orientation relative to the panel, and NOTHING IN THE SOURCE SETTLES
 *     THAT. It is the first bench observation to take, and OPENDUBS_TOUCH_DEBUG=1 draws a
 *     crosshair at the transformed point so one glance settles it — the same argument the face's
 *     tracking reticle already makes: "the eyes are always moving, so a wrong mapping reads as
 *     ordinary wandering."
 *
 * THE ENTRY GESTURE. A hold on an unlabelled central region of the face. Grade 1 by the
 * dashboard note's own table — a screen change moves nothing and is undone by its inverse — but
 * gated anyway, for a different reason than safety: the note requires the technician screen be
 * "reached by a deliberate gesture, not a stray touch", so that a bystander leaning on the glass
 * does not find the controls. The gate is about DISCOVERY, not authority, and those are worth
 * keeping apart because conflating them is how a jog ends up behind two confirmations and a
 * screen change ends up behind none.
 *
 * Which region, and the one thing that is a genuine guess: the note says "Not a corner tap,
 * which bystanders find. A specific unlabelled region plus a hold is the cheapest candidate;
 * whether it is discoverable enough for a student who was shown it once is a question for the
 * first person who has to use it in the field, and the evidence that would settle it is watching
 * someone try." This picks the gap BETWEEN THE EYES — central, so an edge brush misses it, and
 * memorable because it is a place on a face. The hold reveals a progress bar only after 400 ms,
 * which is a deliberate attempt at the open question: holding the wrong place does nothing and
 * teaches nothing, holding the right place shows you it is working and how much longer to wait.
 * It is a proposal. Watching someone try is still what settles it.
 */
#pragma once
#include <lvgl.h>
#include <cstdint>

enum class PanelScreen : uint8_t { Face = 0, Tech = 1, Fault = 2 };

// Build the hit region, the debug crosshair and both screens. CALLER HOLDS THE LVGL LOCK, and
// must call it after the face's own buildScene() so the layers sit above it.
void touchUiBuild(lv_obj_t* parent);

// Arbitrate, run the gesture timers and refresh what is visible.
// CALLER HOLDS THE LVGL LOCK. Call it once per face tick.
void touchUiTick(uint32_t nowMs);

// True while the technician or fault screen owns the panel. The face uses it to SKIP its own
// LVGL work — architecture note §1.7: "While the controls screen is shown the face timers are
// paused, so the two never compete." Read from the same task that writes it (the face's tick),
// so a plain bool is sufficient and no barrier is implied.
bool        touchUiOwnsPanel();
PanelScreen touchUiScreen();

// One line for the panel and for a future STATUS reply. Says whether an indev was found, which
// screen is up, and the grade pool's state. The architecture note §7.3's rule applies: a bench
// that reports only on the panel is not a bench, so everything here is a string a UDP reply can
// carry verbatim.
const char* touchUiStatus();
