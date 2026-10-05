/*
 * Cost gate — measures what a face configuration would cost the DEVICE, on the desktop.
 *
 * This works because of one property that is easy to miss: DIRTY AREA PER FRAME IS GEOMETRIC, NOT
 * A CPU PROPERTY. The desktop has far more CPU than the Tab5, so it cannot tell you whether
 * something runs there — but the number of pixels a configuration invalidates per frame is
 * identical on both machines. Measured against the device's benched blit rate, that is enough to
 * reject a configuration that would freeze the robot, without ever putting it on the robot.
 *
 * It exists because the alternative is what actually happened while building this face: flash,
 * watch, freeze, guess, repeat. A gate reporting "this needs 1.4 screens per frame" would have
 * caught both the 120-segment band and the oversized eye before either reached hardware.
 *
 * Desktop only. Every function is a no-op on the device, so the shipped firmware carries nothing.
 */
#pragma once
#include <cstdint>

void costGateBegin();                 // hook the display; no-op on ESP_PLATFORM
void costGateReport(bool final_);     // print the verdict (periodically, and once at the end)
