/* ---------------------------------------------------------------------------------------------
 * OpenDubs: GT911 self-test and recovery.  PATCHED IN by firmware/tab5_idf/build.sh.
 * Source of truth: firmware/tab5_idf/patches/gt911_selftest.c.inc  --  do not edit in the demo
 * tree, it is overwritten every build.
 *
 * WHY THIS EXISTS.  The Tab5's touch dies intermittently: the GT911 keeps ACKing on I2C -- its
 * product-ID read passes at init, and a failure there would abort the boot outright because
 * CONFIG_BSP_ERROR_CHECK=y makes BSP_ERROR_CHECK_RETURN_NULL an ESP_ERROR_CHECK -- but its
 * firmware stops producing reports.  LVGL then sees RELEASED forever while the display is
 * completely unaffected, which is why this presents as "touch is broken" rather than as a fault.
 *
 * Four candidates were eliminated by measurement before this was written: poll sparsity (five
 * presses HELD a full second each registered nothing), a wrong I2C address after a chip reset
 * (would flood "GT911: I2C read error!" -- the console was silent, with the serial path proven
 * live by triggering an unrelated log line over UDP), out-of-range coordinates (would print
 * LVGL's hor-res warning), and the board's touch-controller revision (every boot log reports
 * Detected GT911 / TouchPad_ID 0x39,0x31,0x31).  Init ordering, reset timing and sleep were all
 * checked in source and are sound.
 *
 * SO THERE MAY BE NO BUG OF OURS TO FIND.  Goodix's own reference driver (gt9xx.c,
 * gtp_esd_check) ships a 2-second heartbeat watchdog that pulses RST, because GT9xx firmware is
 * known to hang in the field while still answering I2C.  The vendor's answer to this chip is a
 * watchdog, not a root cause -- so this file is a VERIFY-AND-RECOVER that prints its own verdict,
 * not a theory.
 *
 * It also removes a real hazard that was not today's fault: esp_lcd_touch_exit_sleep() left
 * GPIO23 (TP_INT) as OUTPUT_OD with pull-ups disabled, and INT level is what the GT911 latches
 * its I2C address from at every reset release.  Nothing here ever sleeps the chip, so that call
 * bought nothing and left the address to the flex's passives.
 * ------------------------------------------------------------------------------------------- */

/* A RUNNING GT911 firmware clears a 0xAA the host writes to 0x8040 and keeps 0xAA at 0x8041.
 * THESE SEMANTICS ARE FROM GOODIX'S REFERENCE DRIVER, NOT FROM ANY DATASHEET IN THIS TREE, and
 * they are UNCALIBRATED until a known-good unit has printed a PASS line.  Until then the boot
 * recovery below is safe (a false FAIL only costs three reset attempts and a log line) but the
 * runtime watchdog must stay off -- see OD_GT911_WATCHDOG. */
#define OD_GT911_CMD_REG 0x8040 /* host writes 0xAA; a running firmware clears it   */
#define OD_GT911_ESD_REG 0x8041 /* a running firmware keeps 0xAA here               */
#define OD_GT911_PID_REG 0x8140 /* reads "911"                                      */

/* OFF until the heartbeat above has been calibrated on a working unit. Flipping this to 1 is the
 * whole change; it is deliberately not wired to a build flag so that enabling it is a visible,
 * reviewed edit rather than an environment variable someone forgets they set. */
#define OD_GT911_WATCHDOG 0

/* INT must be an input with its pull-up on across any reset release: that is what selects the
 * 0x14 address the BSP expects (ESP_LCD_TOUCH_IO_I2C_GT911_ADDRESS_BACKUP). gpio_reset_pin()
 * gives exactly that. */
static void od_tp_int_pin_safe(void)
{
    gpio_reset_pin(GPIO_NUM_23);
}

/* TP_RST is PI4IOE1 P5. This pulses P5 ONLY.
 *
 * Deliberately NOT bsp_reset_tp(): that also drives LCD_RST (P4) low for 100 ms, which on a
 * running panel means a display that needs re-initialising. At boot that is harmless; at runtime
 * it is not, and this function is meant to be callable from both. */
static void od_tp_reset_only(void)
{
    uint8_t w[2] = {PI4IO_REG_OUT_SET, 0};
    uint8_t r[1] = {0};

    od_tp_int_pin_safe();

    /* READ-THEN-WRITE, AND REFUSE ON A FAILED READ. The vendor's own expander helpers
     * read-modify-write from a zeroed buffer without checking the read, so a timed-out read
     * writes 0x00 | bit and silently clears every other output on the expander -- including
     * TP_RST itself. Never copy that pattern. */
    if (i2c_master_transmit_receive(i2c_dev_handle_pi4ioe1, w, 1, r, 1, I2C_MASTER_TIMEOUT_MS) != ESP_OK) {
        ESP_LOGE(TAG, "GT911 recovery: expander read failed -- refusing to write a zeroed register");
        return;
    }

    w[1] = r[0];
    clrbit(w[1], 5);
    i2c_master_transmit(i2c_dev_handle_pi4ioe1, w, 2, I2C_MASTER_TIMEOUT_MS);
    vTaskDelay(pdMS_TO_TICKS(100));     /* datasheet minimum is ~100 us; 100 ms is the BSP's own figure */
    setbit(w[1], 5);
    i2c_master_transmit(i2c_dev_handle_pi4ioe1, w, 2, I2C_MASTER_TIMEOUT_MS);
    vTaskDelay(pdMS_TO_TICKS(100));     /* >= 50 ms required before the first I2C transaction */
}

/* THE VERDICT, WITH NO HUMAN AT THE GLASS -- which is the point. A test that needs someone to
 * touch the panel is not a test: nobody is usually at the bench, and "no stimulus" reads exactly
 * like "broken hardware".
 *
 * Two layers: the chip must ANSWER (product ID), and its firmware must be EXECUTING (heartbeat).
 * The first alone is what init already proves and is not sufficient -- answering while hung is
 * precisely the failure mode. */
static esp_err_t od_gt911_selftest(esp_lcd_touch_handle_t tp, const char* when)
{
    uint8_t pid[3] = {0};
    uint8_t esd[2] = {0};
    uint8_t aa     = 0xAA;

    /* tp->io is public (esp_lcd_touch.h), and rx/tx_param with lcd_cmd_bits = 16 puts the 16-bit
     * register address on the wire exactly as the driver's own touch_gt911_i2c_read/write do. */
    esp_err_t e = esp_lcd_panel_io_rx_param(tp->io, OD_GT911_PID_REG, pid, 3);
    if (e != ESP_OK || pid[0] != '9' || pid[1] != '1' || pid[2] != '1') {
        ESP_LOGE(TAG, "GT911 SELFTEST FAIL (%s): chip not answering: pid %02x %02x %02x (%s)",
                 when, pid[0], pid[1], pid[2], esp_err_to_name(e));
        return ESP_FAIL;
    }

    esp_lcd_panel_io_tx_param(tp->io, OD_GT911_CMD_REG, &aa, 1);
    vTaskDelay(pdMS_TO_TICKS(100));
    e = esp_lcd_panel_io_rx_param(tp->io, OD_GT911_CMD_REG, esd, 2);   /* 0x8040 then 0x8041 */

    if (e != ESP_OK || esd[0] == 0xAA || esd[1] != 0xAA) {
        ESP_LOGE(TAG, "GT911 SELFTEST FAIL (%s): chip answers but firmware NOT running: "
                      "0x8040=%02x 0x8041=%02x (%s)",
                 when, esd[0], esd[1], esp_err_to_name(e));
        return ESP_FAIL;
    }

    /* CALIBRATION LINE. On a unit where touch is known to work this must print
     * 0x8040=00 0x8041=aa. Anything else means the heartbeat semantics above are wrong for this
     * chip's firmware and OD_GT911_WATCHDOG must stay 0 until they are corrected from the
     * reading. */
    ESP_LOGI(TAG, "GT911 SELFTEST PASS (%s): pid 911, 0x8040=%02x 0x8041=%02x", when, esd[0], esd[1]);
    return ESP_OK;
}

#if OD_GT911_WATCHDOG
/* Runs as an lv_timer, therefore in the LVGL task, therefore serialised against the driver's own
 * lvgl_read_cb -- no extra locking and no chance of a half-read.
 *
 * Two-phase (write on one tick, read on the next) so no vTaskDelay ever sits inside the LVGL
 * task. Two strikes before acting: one miss is noise, two is a verdict. Cost when healthy is two
 * I2C transactions every two seconds; cost of a recovery is a ~200 ms stall of the face. */
static void od_gt911_watchdog_cb(lv_timer_t* t)
{
    static bool armed   = false;
    static int  strikes = 0;
    uint8_t     aa      = 0xAA;
    uint8_t     esd[2]  = {0};

    (void)t;
    if (!_lcd_touch_handle) return;

    if (!armed) {
        esp_lcd_panel_io_tx_param(_lcd_touch_handle->io, OD_GT911_CMD_REG, &aa, 1);
        armed = true;
        return;
    }
    armed = false;

    esp_err_t e = esp_lcd_panel_io_rx_param(_lcd_touch_handle->io, OD_GT911_CMD_REG, esd, 2);
    if (e == ESP_OK && esd[0] != 0xAA && esd[1] == 0xAA) {
        strikes = 0;
        return;
    }

    ESP_LOGE(TAG, "GT911 watchdog strike %d: 0x8040=%02x 0x8041=%02x (%s)",
             strikes + 1, esd[0], esd[1], esp_err_to_name(e));
    if (++strikes < 2) return;

    /* The indev and the touch handle stay valid across this: the driver holds only io, config and
     * data, so the next read_data simply refreshes the point buffer. No re-creation needed. */
    od_tp_reset_only();
    if (od_gt911_selftest(_lcd_touch_handle, "after watchdog reset") != ESP_OK) {
        ESP_LOGE(TAG, "GT911 watchdog: recovery did NOT restore the heartbeat");
    }
    strikes = 0;
}
#endif /* OD_GT911_WATCHDOG */
