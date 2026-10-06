    /* exit_sleep RESTORED, AND THAT IS THE POINT OF THIS REVISION.
     *
     * An earlier version of this patch DELETED esp_lcd_touch_exit_sleep(tp) on the reasoning that
     * nothing in this firmware ever sleeps the GT911, so the call bought nothing and only left
     * GPIO23 driven. That was an inference, not a measurement, and it looks wrong: for the GT911
     * this call IS the wake sequence -- it drives INT high for 5 ms (esp_lcd_touch_gt911.c). If
     * the chip comes up in a low-power state, that pulse is what starts it scanning, and without
     * it you get a chip that ACKs I2C but never reports a touch, which is exactly the symptom.
     *
     * Evidence: before the patch touch worked intermittently; with the call removed it failed on
     * BOTH following boots -- one flash reset and one cold power pull -- with byte-identical
     * register readings. So the vendor behaviour is restored verbatim and the pin is deliberately
     * NOT touched afterwards, because what state INT must be left in is not established.
     *
     * The self-test below is now INSTRUMENTATION ONLY. Its pass criterion is known to be wrong:
     * it required 0x8041 == 0xAA while nothing in this stack ever writes 0xAA to 0x8041, and per
     * the GT911 programming guide that register is the ESD_Check register which the HOST arms
     * once and which resets to 0 on initialisation. It therefore reported FAIL on every boot,
     * including boots indistinguishable from working ones. Driving recovery from a test that
     * cannot pass was costing ~600 ms and nine expander writes per boot to no purpose, so the
     * retry loop is gone until a detector exists that has been validated against a known-good
     * AND a known-bad boot. We can now produce both, which is what makes that possible. */
    esp_lcd_touch_exit_sleep(tp);
    (void)od_gt911_selftest(tp, "boot");
