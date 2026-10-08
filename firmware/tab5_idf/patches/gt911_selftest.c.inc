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
/* STAYS 0, and now for a second reason: the watchdog below ALSO writes 0xAA to the command
 * register, which is the very thing suspected of breaking touch. It must not be enabled until a
 * non-destructive liveness test exists. */
#define OD_GT911_WATCHDOG 0

/* INT must be an input with its pull-up on across any reset release: that is what selects the
 * 0x14 address the BSP expects (ESP_LCD_TOUCH_IO_I2C_GT911_ADDRESS_BACKUP). gpio_reset_pin()
 * gives exactly that. */
static void od_tp_int_pin_safe(void)
{
    gpio_reset_pin(GPIO_NUM_23);
}

/* THE KNOWN-GOOD BRING-UP, transcribed from the path that has never failed on this board.
 *
 * Sources, both in firmware/tab5_face/.pio/libdeps/tab5/M5GFX/src/:
 *   M5GFX.cpp:2687-2729       -- INT driven HIGH (push-pull) BEFORE and across the TP reset,
 *                                P5 low 10 ms, then INT to plain input and 100 ms. Its own
 *                                comments name bit5 as "GT911 TouchReset", which is what finally
 *                                confirms P5 is the touch reset rather than our inference.
 *   Touch_GT911.cpp:51-126    -- init() clears the buffer-status register 0x814E, then wakeup()
 *                                drives INT LOW 58 ms, HIGH 2 ms, and leaves it INPUT_PULLUP.
 *
 * WHY THIS AND NOT A CONFIG WRITE. M5GFX writes no configuration block either -- its only config
 * writers are never called -- and the chip reported Config_Version 0 on the two boots where touch
 * measured WORKING. So a zeroed config block is normal on this unit and was never the fault.
 * What differs between the working path and ours is the INT pin, in three ways: driven high vs a
 * weak internal pull-up across the reset release (INT level is what latches the I2C address);
 * esp_lcd_touch_exit_sleep() driving INT push-pull for 5 ms against a chip that is already
 * running and driving INT itself; and INT left floating open-drain afterwards instead of
 * input-with-pull-up.
 *
 * P5 ONLY, deliberately: the display panel is already initialised by the time this runs, so
 * LCD_RST (P4) must not move or the panel would need re-initialising. */
static void od_gt911_m5gfx_bringup(esp_lcd_touch_handle_t tp)
{
    uint8_t w[2] = {PI4IO_REG_OUT_SET, 0};
    uint8_t r[1] = {0};
    uint8_t zero = 0;
    gpio_config_t out  = {.pin_bit_mask = BIT64(GPIO_NUM_23), .mode = GPIO_MODE_OUTPUT};
    gpio_config_t in   = {.pin_bit_mask = BIT64(GPIO_NUM_23), .mode = GPIO_MODE_INPUT};
    gpio_config_t inpu = {.pin_bit_mask = BIT64(GPIO_NUM_23), .mode = GPIO_MODE_INPUT,
                          .pull_up_en = GPIO_PULLUP_ENABLE};

    /* 1. INT driven HIGH, push-pull, before and across the reset: the address is selected by a
     *    DRIVEN level here, not by a weak pull-up as the IDF path does it. */
    gpio_config(&out);
    gpio_set_level(GPIO_NUM_23, 1);

    /* 2. TP_RST (PI4IOE1 P5): low 10 ms, then high. Read-then-write with the read CHECKED -- the
     *    vendor helpers write 0x00|bit on a failed read, which would clear every other output. */
    if (i2c_master_transmit_receive(i2c_dev_handle_pi4ioe1, w, 1, r, 1, I2C_MASTER_TIMEOUT_MS) != ESP_OK) {
        ESP_LOGE(TAG, "m5gfx bringup: expander read failed -- aborting, nothing written");
        return;
    }
    w[1] = r[0];
    clrbit(w[1], 5);
    i2c_master_transmit(i2c_dev_handle_pi4ioe1, w, 2, I2C_MASTER_TIMEOUT_MS);
    vTaskDelay(pdMS_TO_TICKS(10));
    setbit(w[1], 5);
    i2c_master_transmit(i2c_dev_handle_pi4ioe1, w, 2, I2C_MASTER_TIMEOUT_MS);

    /* 3. INT to plain input, then 100 ms (M5GFX.cpp:2727-2729). */
    gpio_config(&in);
    vTaskDelay(pdMS_TO_TICKS(100));

    /* 4. Touch_GT911::init(): clear the buffer-status register. */
    esp_lcd_panel_io_tx_param(tp->io, 0x814E, &zero, 1);

    /* 5. Touch_GT911::wakeup(): INT low 58 ms, high 2 ms, then input WITH PULL-UP -- and it stays
     *    there, unlike the IDF path which leaves it floating. */
    gpio_config(&out);
    gpio_set_level(GPIO_NUM_23, 0);
    vTaskDelay(pdMS_TO_TICKS(58));
    gpio_set_level(GPIO_NUM_23, 1);
    vTaskDelay(pdMS_TO_TICKS(2));
    gpio_config(&inpu);

    ESP_LOGI(TAG, "GT911: M5GFX bring-up applied (reset w/ INT driven, 0x814E cleared, "
                  "wake pulse, INT input+pullup)");
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

/* READ-ONLY. NO WRITES TO THE CHIP, AND THAT IS THE WHOLE POINT OF THIS REVISION.
 *
 * The previous version wrote 0xAA to 0x8040 as a liveness probe. 0x8040 is the GT911's COMMAND
 * register: 0x00 means "read coordinate state", and per the GT911 programming guide the host is
 * expected to return it to 0x00 after issuing any command. This test wrote 0xAA and never
 * restored it, so it left the chip parked on a command instead of reporting coordinates.
 *
 * That is almost certainly why touch stopped: it worked before this patch and then failed on
 * EVERY boot afterwards -- a flash reset, a cold power pull, and a reflash with the wake call
 * restored -- because the write happens at every boot, which is exactly why power-cycling could
 * not help. And the 0x8040=aa we kept reading back was never a hang signature; it was our own
 * value sitting where we put it.
 *
 * So: the product ID only. It proves the I2C path and that the chip answers, which is all a
 * read-only test can prove. It CANNOT distinguish a healthy chip from one that answers while
 * hung -- that was what the heartbeat was for, and until a non-destructive liveness test has been
 * validated against a known-good AND a known-bad boot, not having one is better than having one
 * that writes to a command register. An instrument that changes what it measures is worse than
 * no instrument. */
static esp_err_t od_gt911_selftest(esp_lcd_touch_handle_t tp, const char* when)
{
    uint8_t pid[3] = {0};
    uint8_t cfg    = 0;

    esp_err_t e = esp_lcd_panel_io_rx_param(tp->io, OD_GT911_PID_REG, pid, 3);
    if (e != ESP_OK || pid[0] != '9' || pid[1] != '1' || pid[2] != '1') {
        ESP_LOGE(TAG, "GT911 SELFTEST FAIL (%s): chip not answering: pid %02x %02x %02x (%s)",
                 when, pid[0], pid[1], pid[2], esp_err_to_name(e));
        return ESP_FAIL;
    }

    /* 0x8040 is READ, never written: if it is non-zero the chip is parked on a command rather
     * than reporting coordinates, and whoever left it there is the bug. Reported, not corrected,
     * because silently writing 0x00 here would hide exactly that. */
    esp_lcd_panel_io_rx_param(tp->io, OD_GT911_CMD_REG, &cfg, 1);

    /* THE CHIP'S OWN CONFIGURATION, ALL READS.
     *
     * The ESP-IDF GT911 driver writes NO configuration -- nothing in esp_lcd_touch_gt911.c
     * touches 0x8047..0x8100 -- so the chip scans with whatever block lives in its own flash.
     * M5GFX/LovyanGFX's driver, which is what the Arduino firmware uses and which has never
     * failed on this board, DOES write a config at init. If the stored block is corrupt, or its
     * coordinate maxima or touch count are zero, the chip will ACK I2C forever and never report a
     * coordinate -- which is precisely the measured state: ~3200 error-free reads, zero presses.
     *
     * Register map from the GT911 programming guide: 0x8140 info block (pid[4] fw[2] xres[2]
     * yres[2] vendor), 0x8047 config block (ver, xmax[2], ymax[2], touches, modsw1, ...,
     * refresh at +15), 0x80FF checksum, 0x8100 config_fresh, 0x814E buffer status. 0x814E is
     * safe to read without clearing -- the driver clears it on its next poll. */
    uint8_t info[11] = {0}, cfgb[16] = {0}, chk[2] = {0}, st = 0;
    esp_lcd_panel_io_rx_param(tp->io, 0x8140, info, 11);
    esp_lcd_panel_io_rx_param(tp->io, 0x8047, cfgb, 16);
    esp_lcd_panel_io_rx_param(tp->io, 0x80FF, chk, 2);
    esp_lcd_panel_io_rx_param(tp->io, 0x814E, &st, 1);
    ESP_LOGI(TAG, "GT911 INFO (%s): pid %c%c%c fw %02x%02x xres %u yres %u vendor %02x", when,
             info[0], info[1], info[2], info[5], info[4],
             (unsigned)(info[6] | info[7] << 8), (unsigned)(info[8] | info[9] << 8), info[10]);
    ESP_LOGI(TAG, "GT911 CFG  (%s): ver %02x xmax %u ymax %u touches %u modsw1 %02x refresh %02x "
                  "chksum %02x fresh %02x status814E %02x",
             when, cfgb[0], (unsigned)(cfgb[1] | cfgb[2] << 8), (unsigned)(cfgb[3] | cfgb[4] << 8),
             cfgb[5], cfgb[6], cfgb[15], chk[0], chk[1], st);

    ESP_LOGI(TAG, "GT911 SELFTEST PASS (%s): pid 911, command reg 0x8040=%02x%s",
             when, cfg, cfg ? "  <-- NOT ZERO: chip is not in coordinate-read mode" : "");
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
