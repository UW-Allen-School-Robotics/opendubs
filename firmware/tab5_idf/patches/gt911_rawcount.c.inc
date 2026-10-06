/* OpenDubs: RAW TOUCH INSTRUMENT. Counts what the GT911 DRIVER delivers to LVGL's read callback,
 * BEFORE any hit-testing, z-order or event routing. Patched in by firmware/tab5_idf/build.sh.
 *
 * This is the layer nobody has observed. Every verdict so far was taken either at LVGL's far end
 * (the veil press counter) or at the register end (the self-test) -- so "touch is dead" has never
 * distinguished "the chip reports nothing" from "the chip reports and LVGL loses it". These
 * counters sit exactly in between. Read over UDP in the PING reply and echoed on serial. */
volatile uint32_t od_tp_polls = 0, od_tp_presses = 0, od_tp_errs = 0, od_tp_lastx = 0, od_tp_lasty = 0;
