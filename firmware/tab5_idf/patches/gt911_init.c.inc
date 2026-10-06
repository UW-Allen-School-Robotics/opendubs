    /* OpenDubs: replaces esp_lcd_touch_exit_sleep(tp).
     *
     * That call was pointless here -- nothing in this firmware ever sleeps the GT911 -- and it
     * left GPIO23 (TP_INT) as OUTPUT_OD with pull-ups disabled, i.e. floating. INT level is what
     * the chip latches its I2C address from at every reset release, so leaving it floating hands
     * the address to the flex's passive network. od_tp_int_pin_safe() keeps it input-pull-up.
     *
     * Then VERIFY, and RECOVER IF NEEDED, with the verdict in the boot log. Three attempts, which
     * also adjudicates the one thing that could not be settled from source -- whether a P5 pulse
     * actually resets this chip (no Tab5 schematic is in the tree):
     *
     *   PASS (boot)                        -> healthy, nothing was needed
     *   FAIL (boot) -> PASS (after ...)    -> the P5 pulse DOES reset the chip, and the fault is
     *                                         something transient at bring-up
     *   FAIL x3                            -> P5 does not reset this chip and only a power cycle
     *                                         does; the lever would then be a TP power-enable
     *                                         pin, which is not identifiable from source
     *
     * See firmware/tab5_idf/patches/gt911_selftest.c.inc for why this is a watchdog rather than a
     * root cause. */
    od_tp_int_pin_safe();
    for (int attempt = 0; attempt < 3; ++attempt) {
        if (od_gt911_selftest(tp, attempt == 0 ? "boot" : "after tp reset") == ESP_OK) break;
        if (attempt == 2) {
            ESP_LOGE(TAG, "GT911 UNRECOVERABLE after 3 attempts -- touch will not work this boot");
            break;
        }
        od_tp_reset_only();
    }

#if OD_GT911_WATCHDOG
    /* Created here rather than next to the indev so this patch needs only ONE anchor in the
     * vendor file. LVGL is already initialised at this point (lvgl_port_init runs earlier in
     * bsp_display_start_with_config), and the lock is taken because this is not called from the
     * LVGL task. */
    lvgl_port_lock(0);
    lv_timer_create(od_gt911_watchdog_cb, 2000, NULL);
    lvgl_port_unlock();
#endif
