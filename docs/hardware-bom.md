# Hardware BOM

Everything you buy to build the robot, generated from the project's parts list
(`hardware/parts.csv`). Each [build page](build/lower-frame.md) carries the same parts, grouped
by the step that uses them; this page is the shopping list.

!!! tip "Educational discounts available"
    goBILDA / ServoCity offers **educational pricing for K-12 and university groups**.
    If you're ordering for a school or university team, apply through
    [servocity.com/education](https://www.servocity.com/education) before you purchase.

## Components

Electronics and complete assemblies. The first rows are the parts with no build page of their
own (the computer, the battery, the arms); the rest name the build page that installs them. A
part used in two places is listed once, with the quantities added.

<!-- BOM-AUTO:purchase:components -->
| Part | SKU | Qty | Used in | Notes |
|---|---|---:|---|---|
| [NVIDIA Jetson AGX Thor Developer Kit](https://www.nvidia.com/en-us/autonomous-machines/embedded-systems/jetson-thor/) | — | 1 | — | The main computer: runs the dashboard, the ROS software and the arms. |
| [Samsung Galaxy Tab A11, 8.7-inch, Wi-Fi (operator interface)](https://www.samsung.com/latin_en/tablets/galaxy-tab-a/galaxy-tab-a11-gray-64gb-sm-x133nzaagto/) | `SM-X133` | 1 | — | The robot has no screen of its own: the dashboard runs full-screen on this tablet, locked in the project's kiosk app (software/tablet-kiosk). The kiosk app's setup guide is written for this model. |
| [ECO-WORTHY 51.2 V 50 Ah LiFePO4 server-rack battery](https://www.ecoworthy.com/) | — | 1 | — | 2.56 kWh, 16 cells in series. Its BMS reports charge and voltage to the StamPLC over RS-485. The heaviest single part. |
| [48 V LiFePO4 battery charger](https://a.co/d/0aP9q4YX) | — | 1 | — | Set it to 48 V; 8 A is the recommended current. |
| [GIGAVAC GX11 contactor, 24 V coil](https://www.sensata.com/products/gigavac-power-contactors-fuses/gx-contactors-800v/gx-normally-open-contactors) | `GX11CAB` | 1 | — | The e-stop contactor: sealed, normally open, with an auxiliary contact the StamPLC reads to detect a weld. |
| [RadioLink T8S transmitter + R8EF receiver](https://a.co/d/0cFzagC4) | — | 1 | — | Manual control; the receiver talks SBUS to the StamPLC. |
| [M5Stack Tab5 (ESP32-P4)](https://shop.m5stack.com/products/m5stack-tab5-iot-development-kit-esp32-p4) | — | 1 | — | The head: the face, front control panel and voice. |
| Scissor lift with its own up/down controller | — | 1 | — | The StamPLC presses the controller's up and down buttons through two relays. |
| [M5Stack ToF4M unit (VL53L1X)](https://docs.m5stack.com/en/unit/Unit-ToF4M) | — | 1 | — | Measures the lift's height for the pinch guard. |
| [OpenArm bimanual arm set (two 7-DOF arms with grippers)](https://shop.wowrobo.com/products/openarm-open-source-humanoid-robot-arm-by-enactic) | — | 1 | — | Left and right, from OpenArm's certified seller. Each arm has its own CAN-FD bus. The robot uses the follower set; leader arms are a separate set. |
| [PEAK PCAN-USB Pro FD](https://www.peak-system.com/PCAN-USB-Pro-FD.366.0.html) | — | 1 | — | Two CAN-FD channels, one per arm. |
| [MikroTik hAP ac3 router](https://mikrotik.com/product/hap_ac3) | — | 1 | — | The robot's own network: the computer, the StamPLC, the head and the tablet. |
| [RealSense D415 depth camera](https://www.realsenseai.com/products/stereo-depth-camera-d415/) | — | 1 | — | Depth camera; the face tracker uses it. |
| [SparkFun Optical Tracking Odometry Sensor (OTOS)](https://www.sparkfun.com/sparkfun-optical-tracking-odometry-sensor-paa5160e1-qwiic.html) | — | 1 | [OTOS Module](build/otos.md) | PAA5160E1, Qwiic; 3.3 V — needs level-shifting to the 5 V M5 I2C bus |
| [Adafruit logic level converter](https://www.adafruit.com/product/5637) | `Adafruit 5637` | 2 | [OTOS Module](build/otos.md), [Electronics Bay](build/electronics-bay.md) | Shifts OTOS 3.3 V I2C up to the 5 V M5 bus |
| [4-Bar Odometry Pack — Pinpoint computer + 2 dead-wheel pods](https://www.servocity.com/4-bar-odometry-pack-2-pods-1-pinpoint-computer/) | `3203-3110-0002` | 1 | [Pinpoint Odometry](build/pinpoint.md) | Pinpoint (IMU + odometry) + 2 pods; I2C at 3.3 V — the step-down lives in the electronics bay, NOT this module. Same pack on the Core BOM. |
| [REV Mini Power Module (24 V logic bus)](https://www.revrobotics.com/rev-11-1956/) | `REV-11-1956` | 1 | [Electronics Bay](build/electronics-bay.md) | 6 fused branch circuits — feeds Thor / StamPLC / router / USB hub(s) / E-stop signal (outside the bay) and the PRISM (inside) |
| [goBILDA 2x80A ESC](https://www.servocity.com/content/user_manuals/3105-0202-0080_data-sheet.pdf) | `3105-0202-0080` | 2 | [Electronics Bay](build/electronics-bay.md) | Drive ESCs (datasheet at the link). NOTE: 2x80A is likely OVER-SPEC for this drivetrain — a lower-amperage ESC would work and cost less; we used these because we had them on hand. |
| [M5 8Servo unit](https://docs.m5stack.com/en/unit/8Servos%20Unit) | — | 1 | [Electronics Bay](build/electronics-bay.md) | PWM source to the ESCs; I2C 0x25 (same as Core BOM) |
| [ODrive Regen Clamp](https://shop.odriverobotics.com/products/odrive-regeneration-clamp) | — | 1 | [Electronics Bay](build/electronics-bay.md) | Absorbs braking regen; the resistor lead requires SOLDERING |
| [M5Stack PaHub (I2C hub)](https://docs.m5stack.com/en/unit/pahub) | — | 1 | [Electronics Bay](build/electronics-bay.md) | I2C hub for the M5 bus |
| [M5 ENV III sensor](https://docs.m5stack.com/en/unit/env-iii) | — | 1 | [Electronics Bay](build/electronics-bay.md) | Thermal probe under the regen clamp resistor |
| [goBILDA PRISM light controller](https://www.gobilda.com/prism-rgb-led-driver-6-30v-input-i2c-pwm-control/) | — | 1 | [Electronics Bay](build/electronics-bay.md) | In the bay; fed by the REV Mini Power Module; goBILDA I2C |
| [Fuse block (blade fuses)](https://www.amazon.com/dp/B0FLJV9BDD) | — | 1 | [Power Bay](build/power-bay.md) | RECOMMENDED for the 24 V drive bus. Simple, cheap, correctly rated. CHOOSE this OR the REV PDH. |
| 24 V 60 A DC/DC converter | — | 1 | [Power Bay](build/power-bay.md) | Mounted OUTSIDE the bay, feeds in; the bay/bus sits behind the e-stop contactor |
| [Slamtec RPLIDAR A1](https://www.adafruit.com/product/4010) | — | 2 | [Lidar — Front & Rear](build/lidar.md) | One front, one rear; the A1 typically ships with its USB adapter + cable |
| RPLIDAR USB adapter (daughter board) | — | 2 | [Lidar — Front & Rear](build/lidar.md) | Comes with the RPLIDAR A1 kit. |
| USB cable (A1 to hub) | — | 2 | [Lidar — Front & Rear](build/lidar.md) | Usually included with the A1 |
| [Holybro M9N GPS / compass](https://docs.holybro.com/gps-and-rtk-system/m8n-m9n-m10-gps/standard-m10-m9n-m8n-gps/overview) | — | 1 | [Lidar — Front & Rear](build/lidar.md) | FRONT lidar only |
| [M5Stack StamPLC controller](https://www.digikey.com/en/products/detail/m5stack-technology-co-ltd/K141/26459381) | `K141` | 1 | [Right Upper Electronics Bay](build/upper-right-electronics-bay.md) | The mobile base's edge controller. Available from many sources; DigiKey (shown) stocks well for US-based teams. |
| [M5Stack StamPLC PoE module](https://www.digikey.com/en/products/detail/m5stack-technology-co-ltd/A165/28968801) | `A165` | 1 | [Right Upper Electronics Bay](build/upper-right-electronics-bay.md) | Adds PoE Ethernet to the StamPLC — the StamPLC<->Thor transport rides this. Many sources; DigiKey stocks well for US teams. |
| [Micro-Fit 3.0 4-pin cable (Thor DC input)](https://a.co/d/06nenwIA) | `B07Z6BJCVH` | 1 | [Mini Power Module (logic bus)](build/mini-power-module.md) | Sold as an ATX 4-pin 12 V cable — the housing fits Thor, the PIN ASSIGNMENT DOES NOT. Verify against Thor's pinout and re-pin before first power-on; do not trust wire colors. |
| [goBILDA 1205-series dual block mount (2-pack)](https://www.servocity.com/1205-series-dual-block-mount-1-5-2-pack/) | — | 1 | [Thor Install](build/thor-install.md) | The 2-pack gives the two dual blocks that clamp the Thor cradle to the rail. |
<!-- /BOM-AUTO:purchase:components -->

## Structure

The goBILDA frame parts, quantities totalled across the build.

<!-- BOM-AUTO:purchase:structure -->
| Part | SKU | Qty | Source |
|---|---|---:|---|
| goBILDA grid plate, 17x29-hole, 136x232 mm | `1116-0136-0232` | 1 | [ServoCity](https://www.servocity.com/1116-series-grid-plate-17-x-29-hole-136-x-232mm/) |
| 1118 Series Open goRAIL, 192 mm | `1118-0024-0192` | 4 | [ServoCity](https://www.servocity.com/1118-series-open-gorail-192mm-length/) |
| U-channel, 17-hole, 432 mm | `1120-0017-0432` | 3 | [goBILDA](https://www.gobilda.com/1120-series-u-channel-17-hole-432mm-length/) |
| U-channel, 25-hole, 624 mm | `1120-0025-0624` | 2 | [goBILDA](https://www.gobilda.com/1120-series-u-channel-25-hole-624mm-length/) |
| Quad block mount | `1201-0043-0002` | 10 | [ServoCity](https://www.servocity.com/1201-series-quad-block-pattern-mount-43-2/) |
| Spacer, 9 mm | `1522-0010-0090` | 4 | [goBILDA](https://www.gobilda.com/1522-series-8mm-id-spacer-10mm-od-9mm-length-4-pack/) |
| Bearing, 6 mm ID | `1611-0514-0006` | 4 | [goBILDA](https://www.gobilda.com/1611-series-flanged-ball-bearing-6mm-id-x-14mm-od-5mm-thickness-2-pack/) |
| Bearing, 8 mm | `1611-0514-0008` | 4 | [goBILDA](https://www.gobilda.com/1611-series-flanged-ball-bearing-8mm-id-x-14mm-od-5mm-thickness-2-pack/) |
| Hub shaft | `2110-0608-0002` | 4 | [goBILDA](https://www.gobilda.com/shouldered-8mm-rex-hub-shaft-20mm-hub-offset/) |
| Miter gear, 8 mm REX | `2320-4008-0024` | 8 | [goBILDA](https://www.gobilda.com/clamping-steel-miter-gear-8mm-rex-bore-24-tooth-mod-1/) |
| Washer, 4 mm | `2801-0004-0008` | 4 | [goBILDA](https://www.gobilda.com/2801-series-zinc-plated-steel-washer-4mm-id-x-8mm-od/) |
| Shim, 6 mm ID | `2807-0609-0250` | 4 | [goBILDA](https://www.gobilda.com/2807-series-stainless-steel-shim-6mm-id-x-9mm-od-0-25mm-thickness-12-pack/) |
| Shim, 8 mm ID | `2807-0811-0500` | 4 | [goBILDA](https://www.gobilda.com/2807-series-stainless-steel-shim-8mm-id-x-11mm-od-0-50mm-thickness-12-pack/) |
| Mecanum wheel, LEFT slant | `3213-3606-0003` | 2 | [goBILDA](https://www.gobilda.com/140mm-mecanum-wheel-set-70a-durometer-bearing-supported-rollers/) |
| Mecanum wheel, RIGHT slant | `3213-3606-0003` | 2 | [goBILDA](https://www.gobilda.com/140mm-mecanum-wheel-set-70a-durometer-bearing-supported-rollers/) |
| goBILDA 5303-series motor, 99.5:1, 24 mm | `5303-series (99.5:1 / 24 mm)` | 4 | [ServoCity](https://www.servocity.com/5303-series-saturn-planetary-gear-motor-99-5-1-ratio-24mm-length-8mm-rex-shaft-180-rpm-3-3-5v-encoder/) |
<!-- /BOM-AUTO:purchase:structure -->

## Cost

What one robot costs to buy, generated from the same parts list.

<!-- BOM-AUTO:purchase:cost -->
Prices read 2026-10-09, in US dollars, before tax and shipping. Each line is priced from the listing named beside it: the maker's or vendor's own store where it sells direct, a reseller where not. 3D-printed parts and optional parts are not included.

| | Total (USD) | Lines without a price |
|---|---:|---:|
| **One robot, all parts** | **$14,871.71** | 8 |
| One robot, without the Thor and the arms | $3,972.71 | 8 |
| Tools, bought once | $188.53 | 4 |

*Parts without a price, not in the totals:* Samsung Galaxy Tab A11, 8.7-inch, Wi-Fi (operator interface); ECO-WORTHY 51.2 V 50 Ah LiFePO4 server-rack battery; Scissor lift with its own up/down controller; 24 V 60 A DC/DC converter; USB cable (A1 to hub); ATM (mini) blade fuse, 5 A; ATM (mini) blade fuse, 10 A; XT30 connector pair (PRISM branch).

*Tools without a price, not in the totals:* Safety glasses (PPE); Work gloves (PPE); 2.5 mm hex wrench / bit; Soldering iron + solder.

| Part | Buy | Unit price | Line total | Price from |
|---|---:|---:|---:|---|
| NVIDIA Jetson AGX Thor Developer Kit | 1 | $5,499.00 | $5,499.00 | [developer.nvidia.com](https://developer.nvidia.com/embedded/faq), 2026-10-09 |
| Samsung Galaxy Tab A11, 8.7-inch, Wi-Fi (operator interface) | 1 | — | — | no price yet |
| ECO-WORTHY 51.2 V 50 Ah LiFePO4 server-rack battery | 1 | — | — | no price yet |
| 48 V LiFePO4 battery charger | 1 | $98.00 | $98.00 | [amazon.com](https://www.amazon.com/dp/B0G8JYWGBP), 2026-10-09 |
| GIGAVAC GX11 contactor, 24 V coil | 1 | $194.91 | $194.91 | [digikey.com](https://www.digikey.com/en/products/detail/sensata-gigavac-clean-energy/GX11CAB/17867704), 2026-10-09 |
| RadioLink T8S transmitter + R8EF receiver | 1 | $51.99 | $51.99 | [amazon.com](https://www.amazon.com/dp/B09BTSJN7P), 2026-10-09 |
| M5Stack Tab5 (ESP32-P4) | 1 | $60.00 | $60.00 | [shop.m5stack.com](https://shop.m5stack.com/products/m5stack-tab5-iot-development-kit-esp32-p4), 2026-10-09 |
| Scissor lift with its own up/down controller | 1 | — | — | no price yet |
| M5Stack ToF4M unit (VL53L1X) | 1 | $8.95 | $8.95 | [shop.m5stack.com](https://shop.m5stack.com/products/time-of-flight-distance-unit-vl53l1x), 2026-10-09 |
| OpenArm bimanual arm set (two 7-DOF arms with grippers) | 1 | $5,400.00 | $5,400.00 | [shop.wowrobo.com](https://shop.wowrobo.com/products/openarm-open-source-humanoid-robot-arm-by-enactic), 2026-10-09 |
| PEAK PCAN-USB Pro FD | 1 | $751.00 | $751.00 | [gridconnect.com](https://www.gridconnect.com/products/can-usb-pro-fd-adapter-2-channels-can-lin), 2026-10-09 |
| MikroTik hAP ac3 router | 1 | $109.00 | $109.00 | [mikrotik.com](https://mikrotik.com/product/hap_ac3), 2026-10-09 |
| RealSense D415 depth camera | 1 | $325.00 | $325.00 | [store.realsenseai.com](https://store.realsenseai.com/buy-realsense-depth-camera-d415.html), 2026-10-09 |
| U-channel, 25-hole, 624 mm | 2 | $28.99 | $57.98 | [gobilda.com](https://www.gobilda.com/1120-series-u-channel-25-hole-624mm-length/), 2026-10-09 |
| U-channel, 17-hole, 432 mm | 3 | $20.99 | $62.97 | [gobilda.com](https://www.gobilda.com/1120-series-u-channel-17-hole-432mm-length/), 2026-10-09 |
| Quad block mount | 10 | $6.99 | $69.90 | [gobilda.com](https://www.gobilda.com/1201-series-quad-block-pattern-mount-43-2/), 2026-10-09 |
| 1118 Series Open goRAIL, 192 mm | 4 | $7.79 | $31.16 | [gobilda.com](https://www.gobilda.com/1118-series-open-gorail-192mm-length/), 2026-10-09 |
| Hub shaft | 4 | $12.99 | $51.96 | [gobilda.com](https://www.gobilda.com/shouldered-8mm-rex-hub-shaft-20mm-hub-offset/), 2026-10-09 |
| Bearing, 6 mm ID | 2 | $3.99 | $7.98 | [gobilda.com](https://www.gobilda.com/1611-series-flanged-ball-bearing-6mm-id-x-14mm-od-5mm-thickness-2-pack/), 2026-10-09 |
| Bearing, 8 mm | 2 | $3.99 | $7.98 | [gobilda.com](https://www.gobilda.com/1611-series-flanged-ball-bearing-8mm-id-x-14mm-od-5mm-thickness-2-pack/), 2026-10-09 |
| Miter gear, 8 mm REX | 8 | $15.99 | $127.92 | [gobilda.com](https://www.gobilda.com/clamping-steel-miter-gear-8mm-rex-bore-24-tooth-mod-1/), 2026-10-09 |
| Spacer, 9 mm | 1 | $4.24 | $4.24 | [gobilda.com](https://www.gobilda.com/1522-series-8mm-id-spacer-10mm-od-9mm-length-4-pack/), 2026-10-09 |
| Shim, 6 mm ID | 1 | $2.49 | $2.49 | [gobilda.com](https://www.gobilda.com/2807-series-stainless-steel-shim-6mm-id-x-9mm-od-0-25mm-thickness-12-pack/), 2026-10-09 |
| Shim, 8 mm ID | 1 | $2.69 | $2.69 | [gobilda.com](https://www.gobilda.com/2807-series-stainless-steel-shim-8mm-id-x-11mm-od-0-50mm-thickness-12-pack/), 2026-10-09 |
| Washer, 4 mm | 1 | $1.99 | $1.99 | [gobilda.com](https://www.gobilda.com/2801-series-zinc-plated-steel-washer-4mm-id-x-8mm-od/), 2026-10-09 |
| goBILDA 5303-series motor, 99.5:1, 24 mm | 4 | $59.99 | $239.96 | [servocity.com](https://www.servocity.com/5303-series-saturn-planetary-gear-motor-99-5-1-ratio-24mm-length-8mm-rex-shaft-180-rpm-3-3-5v-encoder/), 2026-10-09 |
| Mecanum wheel, LEFT slant + Mecanum wheel, RIGHT slant | 1 | $299.99 | $299.99 | [gobilda.com](https://www.gobilda.com/140mm-mecanum-wheel-set-70a-durometer-bearing-supported-rollers/), 2026-10-09 |
| M4 x 8 mm socket-head screw (bulk) | 3 | $7.99 | $23.97 | [amazon.com](https://www.amazon.com/dp/B0CZP521J7), 2026-10-09 |
| M4 x 10 mm socket-head screw (bulk) | 3 | $7.99 | $23.97 | [amazon.com](https://www.amazon.com/dp/B0CZP5LXWV), 2026-10-09 |
| Safety glasses (PPE) | 1 | — | — | no price yet |
| Work gloves (PPE) | 1 | — | — | no price yet |
| 3 mm hex wrench (4-pack) | 1 | $6.59 | $6.59 | [amazon.com](https://www.amazon.com/dp/B0CZRRWF71), 2026-10-09 |
| Rechargeable hex driver (incl. 3 mm bit) | 1 | $69.99 | $69.99 | [amazon.com](https://www.amazon.com/dp/B0G3TVJS2K), 2026-10-09 |
| 2.5 mm hex wrench / bit | 1 | — | — | no price yet |
| Blue removable threadlocker | 1 | $9.99 | $9.99 | [amazon.com](https://www.amazon.com/dp/B0CHM5QS3N), 2026-10-09 |
| SparkFun Optical Tracking Odometry Sensor (OTOS) | 1 | $84.95 | $84.95 | [sparkfun.com](https://www.sparkfun.com/sparkfun-optical-tracking-odometry-sensor-paa5160e1-qwiic.html), 2026-10-09 |
| Adafruit logic level converter | 2 | $2.95 | $5.90 | [adafruit.com](https://www.adafruit.com/product/5637), 2026-10-09 |
| M4 x 55 mm socket-head screw | 1 | $8.86 | $8.86 | [amazon.com](https://www.amazon.com/dp/B089NGGLZQ), 2026-10-09 |
| WAGO lever connectors | 1 | $23.99 | $23.99 | [amazon.com](https://www.amazon.com/dp/B0D456KL7K), 2026-10-09 |
| 4-Bar Odometry Pack — Pinpoint computer + 2 dead-wheel pods | 1 | $279.99 | $279.99 | [gobilda.com](https://www.gobilda.com/4-bar-odometry-pack-2-pods-1-pinpoint-computer/), 2026-10-09 |
| M10 riv-nut | 1 | $9.99 | $9.99 | [amazon.com](https://www.amazon.com/dp/B0D5D1HPNY), 2026-10-09 |
| Riv-nut tool (manual) | 1 | $43.99 | $43.99 | [amazon.com](https://www.amazon.com/dp/B0CBKFV533), 2026-10-09 |
| REV Mini Power Module (24 V logic bus) | 1 | $38.00 | $38.00 | [revrobotics.com](https://www.revrobotics.com/rev-11-1956/), 2026-10-09 |
| goBILDA 2x80A ESC | 2 | $159.99 | $319.98 | [servocity.com](https://www.servocity.com/2x80a-motor-controller/), 2026-10-09 |
| M5 8Servo unit | 1 | $6.95 | $6.95 | [shop.m5stack.com](https://shop.m5stack.com/products/8-channel-servo-driver-unit-stm32f030), 2026-10-09 |
| ODrive Regen Clamp | 1 | $89.00 | $89.00 | [shop.odriverobotics.com](https://shop.odriverobotics.com/products/odrive-regeneration-clamp), 2026-10-09 |
| M5Stack PaHub (I2C hub) | 1 | $5.95 | $5.95 | [shop.m5stack.com](https://shop.m5stack.com/products/i2c-hub-1-to-6-expansion-unit-v2-1-with-dip-switch-pca9548a), 2026-10-09 |
| M5 ENV III sensor | 1 | $6.95 | $6.95 | [shop.m5stack.com](https://shop.m5stack.com/products/env-iii-unit-with-temperature-humidity-air-pressure-sensor-sht30-qmp6988), 2026-10-09 |
| goBILDA PRISM light controller | 1 | $39.99 | $39.99 | [gobilda.com](https://www.gobilda.com/prism-rgb-led-driver-6-30v-input-i2c-pwm-control/), 2026-10-09 |
| goBILDA grid plate, 17x29-hole, 136x232 mm | 1 | $13.99 | $13.99 | [gobilda.com](https://www.gobilda.com/1116-series-grid-plate-17-x-29-hole-136-x-232mm/), 2026-10-09 |
| Soldering iron + solder | 1 | — | — | no price yet |
| Wire cutter | 1 | $5.99 | $5.99 | [amazon.com](https://www.amazon.com/dp/B0G6JKGQPD), 2026-10-09 |
| Wire stripper | 1 | $12.99 | $12.99 | [amazon.com](https://www.amazon.com/dp/B08YDCTHMS), 2026-10-09 |
| Heat gun | 1 | $24.99 | $24.99 | [amazon.com](https://www.amazon.com/dp/B00004TUCV), 2026-10-09 |
| Heat-shrink assortment / labels | 1 | $7.99 | $7.99 | [amazon.com](https://www.amazon.com/dp/B01MFA3OFA), 2026-10-09 |
| Fuse block (blade fuses) | 1 | $29.99 | $29.99 | [amazon.com](https://www.amazon.com/dp/B0FLJV9BDD), 2026-10-09 |
| 24 V 60 A DC/DC converter | 1 | — | — | no price yet |
| Wire crimper | 1 | $23.99 | $23.99 | [amazon.com](https://www.amazon.com/dp/B0978S514B), 2026-10-09 |
| Crimp connector assortment (butt joints + ring terminals) | 1 | $9.99 | $9.99 | [amazon.com](https://www.amazon.com/dp/B086Z2Y1D6), 2026-10-09 |
| Slamtec RPLIDAR A1 | 2 | $99.95 | $199.90 | [adafruit.com](https://www.adafruit.com/product/4010), 2026-10-09 |
| USB cable (A1 to hub) | 2 | — | — | no price yet |
| Holybro M9N GPS / compass | 1 | $54.99 | $54.99 | [holybro.com](https://holybro.com/products/m9n-gps), 2026-10-09 |
| M5Stack StamPLC controller | 1 | $42.90 | $42.90 | [shop.m5stack.com](https://shop.m5stack.com/products/m5stamp-plc-controller-with-m5stamps3), 2026-10-09 |
| M5Stack StamPLC PoE module | 1 | $18.50 | $18.50 | [digikey.com](https://www.digikey.com/en/products/detail/m5stack-technology-co-ltd/A165/28968801), 2026-10-09 |
| WAGO DIN-rail assortment (rail + rail-mount connectors) | 1 | $33.99 | $33.99 | [amazon.com](https://www.amazon.com/dp/B0FV7YXJ8X), 2026-10-09 |
| Micro-Fit 3.0 4-pin cable (Thor DC input) | 1 | $6.99 | $6.99 | [amazon.com](https://www.amazon.com/dp/B07Z6BJCVH), 2026-10-09 |
| ATM (mini) blade fuse, 5 A | 4 | — | — | no price yet |
| ATM (mini) blade fuse, 10 A | 1 | — | — | no price yet |
| XT30 connector pair (PRISM branch) | 1 | — | — | no price yet |
| goBILDA 1205-series dual block mount (2-pack) | 1 | $6.99 | $6.99 | [servocity.com](https://www.servocity.com/1205-series-dual-block-mount-1-5-2-pack/), 2026-10-09 |
<!-- /BOM-AUTO:purchase:cost -->

## Tools & consumables

Shared across the whole build (buy once, not per module). goBILDA hardware is **metric**;
structural screws take a **3 mm hex**. **Generated from `hardware/parts.csv`** — third-party
tool links are examples, any equivalent works.

### Fasteners

<!-- BOM-AUTO:purchase:fasteners -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| M4 x 8 mm socket-head screw (bulk) | recommended | 2-3 packs | [buy](https://a.co/d/06F4f8am) | 105-count pack; the primary structural fastener; DEGREASE before use |
| M4 x 10 mm socket-head screw (bulk) | recommended | 2-3 packs | [buy](https://a.co/d/07bUYr2o) | 105-count pack; DEGREASE before use |
| M4 cap-head screw assortment | optional | 1 assortment | — | Some parts use cap-head M4 (installed with a 2.5 mm hex); buy an assortment |
| M3 socket-head screw assortment | optional | 1 assortment | — | For non-goBILDA parts; not used in the base frame |
| M4 x 55 mm socket-head screw | — | 4 | [buy](https://www.amazon.com/iExcell-Stainless-Socket-Screws-Wrench/dp/B089NGGLZQ) | Long standoff screws for the 10 mm sensor mount; confirm qty |
| M10 riv-nut | — | 2 | [buy](https://a.co/d/07JbuGAl) | Set into the rail's 14 mm holes; rivnut install is a FOLLOW-ON base-frame step; confirm qty |
| M4 x 55 mm socket-head screws | — | 2 | [buy](https://www.amazon.com/gp/product/B0DFYK39N6) | Run up through the dual blocks to receive the cradle. The 50-pack bought for the OTOS module covers these two. |
<!-- /BOM-AUTO:purchase:fasteners -->

### Tools

<!-- BOM-AUTO:purchase:tools -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| Safety glasses (PPE) | required | 1 | — | Wear during ALL assembly — cutting, wiring, battery handling |
| Work gloves (PPE) | required | 1 | — | Protect hands during assembly, wiring, and battery handling |
| 3 mm hex wrench (4-pack) | required | 1 | [buy](https://a.co/d/028aG2GM) | Minimum tool; builds the frame by hand (~$7) |
| Rechargeable hex driver (incl. 3 mm bit) | recommended | 1 | [buy](https://a.co/d/06aqx7Jc) | Much faster for the bulk of the screws |
| 2.5 mm hex wrench / bit | recommended | 1 | — | For M4 cap-head screws |
| 7 mm ratcheting wrench | optional | 1 | [buy](https://a.co/d/03lJ5n8P) | For nuts |
| Long 3 mm hex bits | optional | 1 | [buy](https://a.co/d/05i2Vbj9) | Reach screws in tight spots |
| Riv-nut tool (manual) | — | 1 | [buy](https://a.co/d/0am8KbEV) | Sets the M10 riv-nuts — CHOOSE this OR the drill attachment |
| Riv-nut drill attachment | — | 1 | [buy](https://a.co/d/09zmlaXE) | Drill-driven alternative to the manual riv-nut tool |
| Soldering iron + solder | required | 1 | — | Required to build the regen clamp resistor lead |
| Wire cutter | recommended | 1 | [buy](https://a.co/d/04pImvE0) | Or similar |
| Wire stripper | recommended | 1 | [buy](https://a.co/d/05DNUngu) | Or similar |
| Heat gun | recommended | 1 | [buy](https://a.co/d/00PNwYUp) | For heat-shrink; or similar |
| Wire crimper | recommended | 1 | [buy](https://a.co/d/0dhX4RmB) | For battery connections; also if you crimp instead of WAGO/solder |
<!-- /BOM-AUTO:purchase:tools -->

### Consumables

<!-- BOM-AUTO:purchase:consumables -->
| Item | Need | Qty | Source | Notes |
|---|---|---|---|---|
| Blue removable threadlocker | recommended | 1 | [buy](https://a.co/d/0igs0Bmw) | Apply to structural screws; BLUE (removable), not red |
| WAGO lever connectors | — | a few | [buy](https://a.co/d/09VO6gwP) | Qwiic<->Grove and I2C/power joins — or solder instead |
| WAGO lever connectors | — | a few | [buy](https://a.co/d/09VO6gwP) | PaHub<->PRISM (goBILDA I2C) and PaHub<->LLC<->Pinpoint joins |
| Heat-shrink assortment / labels | recommended | 1 | [buy](https://a.co/d/04p7f5Vs) | Heat-shrink tube assortment or heat-shrink labels |
| Crimp connector assortment (butt joints + ring terminals) | recommended | 1 | [buy](https://a.co/d/08GtC4Ql) | Butt joints + ring connectors |
| WAGO DIN-rail assortment (rail + rail-mount connectors) | — | 1 | [buy](https://a.co/d/07yKPglw) | Includes the DIN rail AND the WAGO connectors — the bay's field-wiring junction. |
| ATM (mini) blade fuse, 5 A | — | 4 | — | Positions 1/2/3/6 — StamPLC, E-stop, accessory bus, PRISM. Leave the spare bay unfused until it is used. |
| ATM (mini) blade fuse, 10 A | — | 1 | — | Position 5 — Thor only. Every other branch is 5 A. |
| XT30 connector pair (PRISM branch) | — | 1 | — | Position 6 lands on the PRISM light controller through an XT30; see the wiring board |
<!-- /BOM-AUTO:purchase:consumables -->
