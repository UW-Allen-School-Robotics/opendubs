# Regen Clamp — Placement & Brake-Resistor Sizing

!!! note "Source updated to the ECO-WORTHY baseline (analysis unchanged)"
    The upstream source is now a single **ECO-WORTHY ~52 V LiFePO4** battery feeding a **one-way
    52 → 24 V buck** (the DeWalt packs, switcher, and Victron DBC are retired). This *reinforces*
    the design below rather than changing it: **the one-way buck is precisely why the clamp sits
    downstream at the ESCs** — braking regen can't backfeed the pack through a one-way converter,
    so it's shunted at the bus. The sizing already works the **24 V bus** case, so the numbers
    stand; only the old "battery-direct ~21 V" column is retired.

Status: design worked June 2026. Sizes the ODrive Regen Clamp brake resistor(s) for the
drive motors and **revises the clamp placement** from the spine in `power-architecture.md`.
Companion to `power-architecture.md` (power spine, source-OR, e-stop) and the Figma board
(OpenDubsV2) which remains the authoritative schematic.

All math here is plain-text (no rendered LaTeX): `·` multiply, `²` square, `√` root,
`≈/≤/≥` comparisons, Greek `ω` for angular speed.

## 1. Placement decision — downstream of the PDH, one clamp per ESC

`power-architecture.md` currently lists a single clamp **upstream** of the contactor:

```
ECO-WORTHY 52 V → 52→24 DC/DC → [regen clamp] → e-stop contactor → REV PDH → drive ESCs
```

**Revised placement — clamp(s) downstream of the PDH, at the ESCs, one per ESC:**

```
ECO-WORTHY 52 V → 52→24 DC/DC → e-stop contactor → REV PDH → [clamp] → ESC-A (2 motors)
                                                            └────→ [clamp] → ESC-B (2 motors)
```

Rationale:

- **Keeps transient spikes downstream of the PDH.** The ODrive clamp contains a series
  ideal-diode pass element: it passes forward (motoring) current to the ESC and **blocks
  regen from backflowing up into the PDH**, shunting it to the resistor. Spikes never
  propagate onto the PDH bus or the rest of the electronics.
- **Fixes an e-stop coast hole.** When the contactor opens during a coast, the brushed
  ESCs keep braking (see §2) and push regen onto their own now-isolated input rail. With
  the clamp *upstream* of the open contactor, that regen has nowhere to go and the ESC
  input caps eat an overvoltage. With the clamp *downstream* (at the ESC), the local
  clamp+resistor still sink it — powered by the regen itself.
- **One clamp per ESC, not one shared clamp.** Two hard reasons:
  1. **80 A cap.** The clamp is rated ~80 A peak regen. A single shared clamp would carry
     all 4 channels' regen (worst case well over 80 A); per-ESC halves it.
  2. **Fuse independence.** The clamp's series pass element sits in the supply path, so a
     shared clamp would funnel both ESCs through one node — collapsing the two independent
     10 A PDH fuses into one. Per-ESC preserves each ESC on its own fuse (the low-fuse
     philosophy in `power-architecture.md`).

→ **Two clamps, one per dual-channel ESC (2 motors each).** If adopted, update the spine
in `power-architecture.md` and the Figma board accordingly.

## 2. The drive train sees *active* regen (not dissipative braking)

From the goBILDA 2×80A Motor Controller datasheet (SKU 3105-0202-0080):

- Neutral (1500 µs) / no-signal is **active brake mode** ("a motor will not freely spin"),
  the recommended config for mobile robots.
- "This type of control scheme **can feed voltage back to the supply**." → the bus/clamp
  genuinely sees regen energy; we are in the regen-to-bus regime, not winding-dissipation.
- "Normally not an issue with batteries" (a battery sinks the regen) — **but the one-way 52→24
  buck sits between the 24 V bus and the pack**, so our bus behaves like the
  "switching power supply" case the datasheet warns about, where "a shunt voltage
  suppressor is required." The ODrive clamp is that suppressor. This independently
  confirms `power-architecture.md`'s "clamp must absorb 100 % of braking regen."
- The suppressor's stated job is to "sense and suppress any voltage **spikes** … [which]
  scale with the size of the motor." That is **inductive (L·di/dt) transient clipping**,
  not bulk kinetic-energy absorption.

ESC ratings that bound the worst case: input 9–30 V; **80 A continuous / 160 A burst (5 s)
per channel**; current-limit/chop at **160 A per channel**; over-temp throttles 160→80 A
at ~80 °C PCB.

## 3. Hardware inputs

| Quantity | Value | Source |
|---|---|---|
| Drive motors | 4× goBILDA 5303 (RS-550/560 brushed, 99.5:1) | power-architecture.md |
| Per-motor current | 1.3 A no-load, 11 A rated, 60 A stall | power-architecture.md |
| Measured envelope | each ESC pair <10 A (10 A fuse never blows); base ~20–30 A | power-architecture.md |
| ESC | goBILDA 2×80A (3105-0202-0080), 2 ch, 160 A chop/ch, active brake | ESC datasheet |
| Bus voltage | 24 V (52→24 buck output) | — |
| Clamp voltage Vclamp | 26.5 V default auto-trip — must stay < Thor's 28 V ceiling | ODrive datasheet + power-architecture.md |
| Clamp ratings | resistor 0.1–10 Ω; 30 A cont / 60 A peak; 800 W; PT1000 OTP | ODrive datasheet |

Size at the **24 V-bus / 26.5 V-clamp** worst case — highest current, and the case where
the 28 V Thor ceiling binds.

## 4. Selection rule and worked numbers (per ESC = 2 channels)

ODrive brake-resistor rule:

```
Vclamp / Ibrake   ≤   Rbrake   ≤   Vclamp² / Pregen
```

- Lower bound (Rbrake ≥ Vclamp/Ibrake): keeps resistor current at the clamp voltage within
  the clamp's switch rating (protects the clamp MOSFET).
- Upper bound (Rbrake ≤ Vclamp²/Pregen): lets the resistor dissipate peak regen *while the
  clamp still holds the bus at Vclamp*. If R is larger, the bus floats up (V = √(Pregen·R))
  toward an overvoltage.
- Resistor power rating ≥ average regen power (thermal); pulse rating ≥ energy per stop.

Sizing R so the clamp sinks the per-clamp peak current at the clamp voltage,
R = Vclamp / Ipeak, with Vclamp = 26.5 V:

| Per-clamp regen peak (2 ch) | R = 26.5/I | P at 26.5 V | Status |
|---|---|---|---|
| 2×7.5 A = 15 A (documented envelope) | 1.77 Ω | 397 W | OK, comfortable |
| 2×10 A = 20 A | 1.33 Ω | 530 W | OK |
| 2×15 A = 30 A | 0.88 Ω | 795 W | OK, at 30 A continuous edge |
| 2×30 A = 60 A (extreme) | 0.44 Ω | 1590 W | peak-pulse only (>800 W, <60 A pk) |

**Caution on the stock 2 Ω:** at 2 Ω the resistor pulls only 26.5/2 = 13.3 A / 351 W at the
clamp voltage. If a clamp's two channels regen more than ~13 A combined, the bus floats up
(V = I·R): 20 A → 40 V, past Thor's 28 V. So stock 2 Ω is only safe per-ESC if measured
peak stays under ~13 A.

## 5. Why Kv is not the lead term — inductance is

A first-principles Kv calc would only pin the *steady (DC) regen component*, which is modest
here, and would miss the spike the clamp actually clips. The terms:

- **Back-EMF / Kv.** E = Ke·ω, with Ke = 1/Kv. Peak DC regen current at top speed,
  Ibus ≈ Im·(1 − Im·Rm / Vbus). Our top speed exists *because* E ≈ Vbus there (no-load
  speed at bus voltage), so the boost ratio ≈ 1 and the DC regen is modest: with Im = 11 A,
  Rm ≈ 0.2 Ω, Vbus = 24 V → ~10 A/motor → ~20 A/ESC, ~480 W. That lands on the 20 A row
  above. As the robot slows, E = Ke·ω falls and Ibus tapers toward zero. Kv bounds *this*
  component only.
- **Inductance L (the actual clamp duty).** The datasheet's "voltage spikes … scale with
  the size of the motor" are L·di/dt transients from the brake-mode H-bridge switching,
  with energy ½·L·I² per event. This — not Kv — is what the shunt suppressor exists to
  clip. L and the ESC's internal motor-PWM frequency are **not published**, so the spike
  magnitude can't be hand-calculated.
- **Hard ceiling.** The ESC chops at 160 A/channel; nothing exceeds that regardless of Kv.

Implication: energy per stop (½·L·I² spikes + the small kinetic term — ~57 J total across
4 motors, 20 kg @ 0.9 m/s) is tiny, so the resistor's **power rating is non-binding**. The
binding requirement is **R low enough to clip the transient under 28 V** within the clamp's
30 A cont / 60 A peak.

## 6. Recommendation

- **Two ODrive Regen Clamps, one per ESC.**
- **Brake resistor: 1.5 Ω, 100 W wirewound per ESC.** Covers ~18 A/clamp regen (468 W) at
  the clamp voltage, draws well under 30 A continuous, holds the bus under 28 V, and sits
  in the window for everything but the extreme 60 A case. 100 W is margin, not necessity.
- Clamp auto-trip ≈ 26.5 V — **verify it lands below Thor's 28 V** on the 24 V shore bus.
- Use the clamp's **PT1000 RTD + OTP** for stuck-on / over-temp protection.

## 7. How to finalize (measure, don't hand-calc)

The spike that sets R depends on L and the internal PWM frequency, which aren't in the
datasheets — so the definitive step is a **measurement**, not a Kv calculation:

1. **Oscilloscope on the bus during a hard brake-from-speed** (per ESC). This captures the
   transient magnitude that sets how low R must be. Primary finalization.
2. **REV PDH per-channel telemetry** during the same stop for the DC/average regen current
   (telemetry is too slow for the µs spike, but pins the steady component and the fuse
   floor).

Decision from the data: two channels under ~13 A combined → stock **2 Ω/50 W** is fine,
change nothing; 15–30 A → **1.5 Ω** (down to ~1 Ω); only a genuine ~30 A *per channel*
needs a low-ohm pulse-rated resistor (~0.5 Ω) — and the kinetic-energy math says you won't
see that.

## 8. Open items / parameters to confirm

- Bare-motor **Kv** (or no-load rpm at a *stated* voltage — the goBILDA "180 rpm" needs its
  voltage; 12 V vs 24 V doubles Kv) and winding **Rm** — only needed to refine the DC
  component in §5; not required to pick the resistor.
- Robot **mass** (assumed 20 kg) and **top speed / wheel dia** (assumed 96 mm @ 180 rpm →
  0.9 m/s) — set energy-per-stop; both are non-binding given §5.
- Whether to also clamp the **mast ESC** (the 5th motor): lowering the mast regenerates;
  smaller energy, but the same diode-isolation argument applies.

## Sources

- ODrive Regen Clamp datasheet: https://docs.odriverobotics.com/v/latest/hardware/regen-clamp-datasheet.html
- goBILDA 2×80A Motor Controller datasheet (SKU 3105-0202-0080): https://www.servocity.com/content/user_manuals/3105-0202-0080_data-sheet.pdf
- goBILDA 5303 99.5:1 motor: https://www.servocity.com/5303-series-saturn-planetary-gear-motor-99-5-1-ratio-24mm-length-8mm-rex-shaft-180-rpm-3-3-5v-encoder/
