    /* REPLACES esp_lcd_touch_exit_sleep(tp) with the Arduino/M5GFX bring-up sequence -- see
     * od_gt911_m5gfx_bringup() for why, and for the three INT-pin differences it corrects.
     * Nothing is written to the chip's NVM and nothing here is one-way: a power cycle undoes it. */
    od_gt911_m5gfx_bringup(tp);
    (void)od_gt911_selftest(tp, "after m5gfx bringup");
