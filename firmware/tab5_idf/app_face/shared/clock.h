// A millisecond clock for the perception layer that does NOT come from the HAL.
//
// WHY NOT GetHAL()->millis(). `app/hal/hal.h` includes <lvgl.h> -- its interface mentions
// lv_obj_t* and it hands out the LVGL lock. So a perception module that includes hal.h to get a
// clock has, transitively, included LVGL into a layer whose whole point is that it cannot touch
// LVGL. The grep in AUDIT.md would pass (no `#include <lvgl.h>` in the file) while the invariant
// it stands for had quietly stopped being structural. One #include for one function is not worth
// giving that up.
//
// CONSEQUENCE, and it is deliberate: percept's timestamps are NOT comparable with the face app's
// GetHAL()->millis(). So nothing outside percept ever compares them -- every staleness test on a
// published perception value is performed INSIDE percept, against percept's own clock, and the face
// app only ever asks "is there a target" and gets yes or no. A clock mismatch therefore cannot
// produce a wrong answer; it can only fail to compile.
//
// (On the ESP32-P4 esp_timer_get_time() is a 64-bit microsecond counter, so the 32-bit millisecond
// value here still wraps at ~49 days and every comparison still goes through due().)
#pragma once
#include <stdint.h>

#ifdef ESP_PLATFORM
#include <esp_timer.h>
inline uint32_t perceptNowMs()
{
    return (uint32_t)(esp_timer_get_time() / 1000);
}
#else
#include <chrono>
inline uint32_t perceptNowMs()
{
    using namespace std::chrono;
    return (uint32_t)duration_cast<milliseconds>(steady_clock::now().time_since_epoch()).count();
}
#endif
