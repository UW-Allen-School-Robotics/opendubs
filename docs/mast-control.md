# Mast Control — Drivetrain, Homing & Position

The vertical lift. This is the prose/calculation companion; control lands as part of
the StampLC subsystem (see `control-architecture.md`, `state-machines.md` Mast FSM).

## Drivetrain

```
RS-560 brushed motor ─ 71.2:1 planetary ─ 1:1 belt ─ 8 mm-lead screw ─ carriage
                     └ quadrature encoder (Ch.A/Ch.B, 3.3–5 V) on the MOTOR shaft
                       └ M5 ExtEncoder Unit (I²C 0x59) decodes → StampLC reads `enc_count`
```

- **Motor:** goBILDA/ServoCity **5303 Saturn, 71.2:1, 260 rpm no-load, 24 V** (RS-560).
  (Swapped from the 99.5:1/180 rpm part — unavailable — for ~45 % more lift speed.)
- **Lead screw:** ServoCity **37-4, 8 mm lead** (2 mm pitch × 4 starts), 8 mm OD, **950 mm**
  mechanical travel.
- **Belt:** 1:1 motor-output → leadscrew.
- **Position feedback:** the motor's quadrature encoder → M5 **ExtEncoder (0x59)** → StampLC
  (`[ENC] mast=<int32>`). The encoder is **pre-gearbox**, so it reads through the 71.2:1
  planetary + belt (backlash is in the loop — fine for position-hold; if repeatability ever
  matters, move the encoder to the leadscrew side).

## Counts → height

goBILDA's spec for this encoder family is **28 counts/rev at the motor shaft** (CONFIRM — not
printed on the 5303-2402 sheet; standard for the 5203/5303 series). With 71.2:1 and the 1:1
belt:

```
counts per leadscrew rev = 28 × 71.2      = 1993.6
counts per mm            = 1993.6 / 8     = 249.2        (~4.0 µm / count)
full 950 mm travel       ≈ 236,740 counts                (int32 — fine)

height_mm = enc_count × 8 / 1993.6        (zero at the home reference)
```

**CALIBRATE, don't trust the constant.** The real `counts/mm` depends on the ExtEncoder's
quadrature multiplier (1×/2×/4×) and belt creep. Measure it: home, drive limit-to-limit,
`counts_per_mm = Δcounts / measured_travel`. Store the one measured value.

## Homing (DI2 = bottom limit, DI1 = top limit)

1. Jog **down** slowly until **DI2 (bottom)** triggers → mechanical zero → `ENC_RESET`.
2. `height = enc_count × mm_per_count`, positive up.
3. **Direction-clamp** at both limits (stop on DI1/DI2 trigger; the StampLC gates the mast ESC).
4. **Soft limits** bound the *OpenArm-collision-safe* window, NOT the screw's 950 mm: the arm
   shoulders collide with the base/top, so usable travel is **~2/3 ≈ 633 mm (provisional —
   set from final CAD)**. The DI limit switches stay at the physical ends as the homing
   reference + last-resort backstop; normal moves clamp to `[usable_min, usable_max]`.

## Speed

`260 rpm × 8 mm = 2080 mm/min = 34.7 mm/s` no-load → full 950 mm ≈ 27 s, usable ~633 mm ≈ 18 s
(slower under load). Resolution (~4 µm/count) is far finer than the achievable accuracy
(planetary + belt backlash dominates at ~tenths of a mm), so there's plenty of room to trade
resolution for more speed (higher-rpm motor / higher-lead screw) if needed.

## Open items

- **Confirm encoder CPR** (28/rev at motor assumed) — or just rely on the empirical calibration.
- **Final usable travel** (`usable_min`/`usable_max`) + zero end, from CAD.
- **Mast ESC + drive**: same ServoCity 2×80A ESC class as drive; PWM gated by the StampLC.
- Whether the 71.2:1/260 rpm speed is sufficient under load, or a further motor/gear change.
