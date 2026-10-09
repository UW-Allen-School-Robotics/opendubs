# Lower Frame Assembly

Steps **1–13** all build up the **lower frame** — the rolling chassis that carries power,
drive electronics, compute, and the battery. Build them roughly in order; two items float:
the **[OTOS module](otos.md)** can be assembled any time but can't be *installed* until the
wheels are on, and the **[Pinpoint odometry](pinpoint.md)** pod goes in near the end. Once
the lower frame is done, the next phase is the **[upper frame](upper-frame.md)**.

## The assembly map

Everything in steps 1–13 lands somewhere on this render. **Click it to orbit the live Fusion
model** — isolate, zoom, and explode any component to see exactly how it fits.

<div class="embed-facade amap" markdown="1" style="--embed-aspect: 66.7%" data-embed="https://mycs440.autodesk360.com/g/shares/SH90d2dQT28d5b602811027a67a5ebc30391?mode=embed" data-title="Lower frame assembly — Fusion 3D viewer">
--8<-- "amap-map.html"
<span class="embed-facade__play">▶ Open the interactive 3D model</span>
</div>

Each step page below repeats this map with **its** component highlighted — a quick "you are
here" so you can see where the part you're about to install belongs.

## The steps

1. **[Base Frame](base-frame.md)** — the goBILDA chassis; defines the coordinate origin.
2. **[Rivnut Installation](rivnuts.md)** — set M10 riv-nuts into the bare frame (far easier now than later).
3. **[Thor Install](thor-install.md)** — mount the NVIDIA Jetson AGX Thor to the frame in its printed cradle.
4. **[Electronics Bay](electronics-bay.md)** — power distribution, drive electronics, I²C hub.
5. **[Mini Power Module](mini-power-module.md)** — the 24 V logic bus; six fused branches out of the electronics bay.
6. **[Power Bay](power-bay.md)** — drive-bus distribution behind the e-stop contactor.
7. **[Charger Port Integration](charger-port.md)** — the external charge inlet + drive inhibit.
8. **[Cooling Fan Integration](cooling-fan.md)** — the electronics-bay exhaust fan panel.
9. **[Compute & USB Installation](compute-usb.md)** — the Thor's USB fan-out + compute bring-up.
10. **[Wheel Install](wheel-install.md)** — mecanum wheels on the drive hubs.
11. **[OTOS Module](otos.md)** — optical tracking odometry sensor *(build any time; install after wheels)*.
12. **[Battery Install](battery-install.md)** — the LiFePO₄ pack *(the heaviest single part — see safety)*.
13. **[Base Frame Final Assembly + Wiring](base-frame-final.md)** — tie the buses together and verify.

*Then:* **[Upper Frame Assembly →](upper-frame.md)** — in development: its pages are outlined,
not yet complete, and the same holds for the payload modules (the scissor lift and the pan/tilt
head).
