# Chest plate: design log

The chest plate is a 3D-printed badge on the flat front of the red neck/chest piece, just below the
robot's neck. It shows the OpenDubs logo (a cyber-husky head with a gear) and is **backlit across
the whole logo** by one goBILDA RGB Indicator Light behind it. The light shows the robot's state colour,
the same colour as the PRISM LED strip.

It is designed with the project team one step at a time; each decision is recorded with its reason.
Calls made by the team are marked "(team)".

## Step 0: brief (2026-10-16)

| Item | Choice | Why |
|---|---|---|
| Fabrication, first build | **3D-printed plate** (team) | Fastest to iterate |
| Fabrication, later | **Laser-cut acrylic logo layers on a 3D-printed backer** (team) | Crisper edges and better light. The printed design must be able to grow into this: the logo regions stay separate bodies with their own outlines. |
| Envelope | **75 × 75 mm max**, on the flat front of the red chest piece (team) | The flat area measured in the robot's Fusion model |
| Light | **goBILDA RGB Indicator Light (PWM Controlled), 3118-0808-0002**, one, behind the logo | Same colour language as the PRISM strip; PWM pulse width sets the colour |
| Mounting | **The plate fits over the light and uses the light's own mounting holes**, unless something better turns up (team) | No new holes in the chest piece beyond the light's |
| Wiring, firmware | **Not in this part.** Other work streams do them; the plate leaves a cable path | |

**The light, from the vendor page** (https://www.servocity.com/rgb-indicator-light-pwm-controlled/):
- 4 × M4 thru-holes on goBILDA's 32 mm square pattern, with hex counterbores for hex-head screws and
  locknuts. Hole diameter not stated.
- 5–15 V supply; PWM signal 3.3–5 V, 500–2500 µs.
- Two TJC8 ports and two 3-pin JST PH ports; the JST ports are reachable through a 14 mm hole when the
  light sits over one.
- Outline, thickness and lens size: **not listed**. To be read from the vendor STEP model.

**The logo:** traced from a 476 px raster into one layer per colour: a white backing silhouette, purple
linework, a gold gear and a gold eye. The layers stack (each sits on the white silhouette); they don't
tile. At ~60 mm across, the finest circuit lines are near or below what a 0.4 mm nozzle can print.

## Step 1: interfaces (2026-10-16)

**The light, read from the vendor STEP model** (`3118-0808-0002.step`, from
https://www.servocity.com/content/step_files/3118-0808-0002.zip; kept outside the repo). Frame of the
model: the PCB's back face is z = 0, the lens faces +z, and the holes are symmetric about x = y = 0.

| Feature | Value (from the STEP) |
|---|---|
| Case outline | **40.0 × 42.5 mm** (x × y) |
| Thickness | **17.8 mm**: back case 8.0 mm (z −8 … 0), front cover 9.8 mm (z 0 … 9.8) |
| Front cover | A hollow cover, flat front face at z ≈ 9.6, ~36 × 40 mm, 1.5 mm thick, with chamfered sides and the corners stepped down to clear the mounting ears. It is the lens; it sits ~5 mm in front of the LEDs. |
| Mounting holes | **4 × ø4.0 thru on a 32 × 32 mm square** (±16, ±16), through corner ears. The ears run from the back face (z −8) to z 3.4: **11.4 mm of case per hole**. The model shows a round ø7 relief on the back case around each hole, not a hex. |
| LEDs | **20 RGB LEDs, 5 × 5 mm**, over a **~36 × 37 mm** area: a ring of 12 (three per side) plus two inner rows of four. **So the light is an area source, not a point source.** That makes even lighting much easier. |
| JST PH ports (2) | Vertical, on the back, at the centre (x ±0.4 … 5.1, y ±4), 6 mm deep. They are reached through the opening in the back case (the 14 mm hole on the vendor page). |
| TJC8 ports (2) | Right-angle headers pointing **sideways (±x)**, through ~10.7 mm wide slots in the left and right walls, z −5.5 … −0.25 |
| Case screws | 4 × self-tapping, at (±10.75, ±18.75), from the back; not used by us |

**The logo, re-traced from the team's original** (2026-10-16):
- The original is a 1024 × 559 px JPEG, which includes text. **No text goes on the plate (team).**
- Cropped to the husky and gear, the artwork is **298 × 317 px**: about the same resolution as the earlier
  trace. **There is no higher-resolution original (team).** So the fine lines are fixed in CAD, not by
  re-tracing.
- Traced with `raster-to-vector`: `--palette 2e1060,c8a867,e3e3e3,f8d070 --backing ffffff --min-width 3
  --epsilon 1.0`, with no `--width-mm`. The DXF stays in pixels, and the CAD scales it by the artwork's
  own bounding box.
- The source image and the trace stay outside git.

**Decisions:**

| Item | Choice | Why |
|---|---|---|
| Where the light sits | **Inside or partly inside the red chest piece**, behind the logo (team) | The chest piece's depth becomes the light box, so the badge doesn't stand ~35–40 mm proud |
| Chest piece | **Provisional; the team adapts it to this design's depth** (team) | This design states the depth and the cut-out it needs, and the chest piece follows |
| Logo text | **None (team)** | Husky and gear only |

## Step 2: which regions glow (2026-10-16)

Four options were mocked up with a stand-in state colour: (A) the face glows; (B) the face and the gear
glow; (C) the linework glows; (D) an opaque logo with a halo behind it.

| Item | Choice | Why |
|---|---|---|
| Glow regions | **A: the white face panels and the eye glow; the purple linework and the gold gear stay opaque** (team) | The biggest lit area, and the logo still reads as the logo. With the light white or off, it looks like the normal logo. It also maps directly onto the later acrylic build: an opaque purple layer with windows over a frosted layer. |
| Ears | **Both ears glow** (team) | In the artwork only the far ear (the husky's right) has a white interior. The near ear (its left) is drawn as a solid purple wedge with a thin white sliver. For the plate, the near ear gets a glow window too: the ear's triangle, inset by the same stroke width as the far ear's frame. |
| Eye | **A true circle**, glowing | The trace gives a crude octagon |
| White cut lines and circuit traces | **Glow**, as slots through the opaque layer | They're white in the artwork. At plate size they're ~0.6–0.9 mm wide; as slots cut *through* an opaque layer, rather than printed ribs, that is printable. The slot width gets a floor in `P`. |
| Naming | **Husky**, not wolf (team) | The trace's file names still say `wolf`; the plate's files say `husky` |

## Step 3: light box and diffuser (2026-10-16)

**Material:** the team has **transparent PLA** for the diffuser (team).

**What the published practice says** (tested results marked; everything else is assertion):

| Practice | Source | Evidence |
|---|---|---|
| Clear or natural PLA diffuses poorly when thin. It needs "several millimetres" to match 0.4 mm of a milky or silver PLA, and trapped air between layers makes it uneven. | https://zodiusinfuser.uk/blog/2018/06/08/experimenting-with-3d-printed-light-diffusers/ | Tested by eye, WS2812s |
| An **opaque, reflective** housing behind the diffuser (silver beat black) evens out each lit area and stops light bleeding sideways | same | Tested by eye |
| **100 % infill** gives the most even result; 20 % infill casts the infill pattern as shadows | https://hackaday.com/2022/12/22/3d-printed-light-pipe-turns-overly-bright-led-into-design-harmony/ | Tested |
| Thicker clear PLA over an LED gives a **circular gradient** (bright centre); corners of a guide come out dark | same | Tested, 7–11 mm |
| More perimeters or denser structure → a softer, frosted look; print near the top of the temperature range (190–225 °C) so the layers fuse; dry the filament (4–6 h at 40–50 °C); **PLA softens at ~60 °C** | https://www.formfutura.com/blog/blogs-1/transparent-pla-the-complete-guide-to-clear-light-diffusing-3d-printing-98 | Vendor guide, asserted |
| Sanding a clear lens with 800+ grit evens out the light | Hackaday comments | Anecdotal |
| Backlit signs: keep the LED pitch to the LED-to-face distance at ≤ 2 : 1; a stronger diffuser shortens the distance needed | https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/6416201, https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/9165487 | Industry rule of thumb |
| Wide-angle and dimmed LEDs diffuse better than bright, narrow ones | https://blueriverdigital.com/blog/5-ways-to-improve-diffusion-in-an-led-lightbox | Vendor, asserted |

**What that means here:**
- **The light already meets the sign rule by itself.** Its LEDs are ~6 mm apart and ~5 mm behind its own lens. The problem is not hot spots between LEDs; it is **reach**: the logo is ~64 × 68 mm and the light's lit area is ~36 × 37 mm, so the ears, nose and neck sit 14–30 mm outside it.
- So the light box must **carry light outward** (reflective walls, depth), and the diffuser must **hold back the centre** (thicker there) more than it blurs.

**Decisions:**

| Item | Choice | Why |
|---|---|---|
| Diffuser material | **Transparent PLA** (team), at **100 % infill**: all solid layers, 0.2 mm, printed hot (~215–220 °C) and dry | 100 % infill avoids infill shadows. Each layer interface scatters, so the frost comes from the layer count and the surface. |
| Diffuser thickness | **Graded: thicker over the light, thinner at the edges.** *Changed in step 5: a flat 1.6 mm plate plus a separate 1.2 mm clear "centre mask" glued behind it over the light, so every part prints flat.* | Clear PLA attenuates with thickness, so the extra over the light cuts the central hot spot. The edges stay thin to pass what reaches them. |
| Diffuser surface | **Front face printed on the bed. Sand it with 400 → 800 grit if the layer lines or the LEDs show**, or print it on a textured PEI sheet if one is to hand | Gives a matte, scattering face. Both are cheap and need no coupon. |
| Inlay | The diffuser carries **raised plugs** that fill the face's windows flush from behind | Two single-colour prints, no multi-material printer needed. The plugs register the parts, hold any purple islands, and give the flush look the acrylic version will have. |
| Light box | An **opaque white (or silver) PLA shell** with reflective walls (*step 5: straight walls, as in a sign cabinet, not flared*); **15 mm** from the light's lens to the back of the diffuser (`P["box_gap"]`) | Reflective walls carry light to the edges (Zodius). 15 mm is a starting value: deeper reaches the edges better, shallower makes a smaller pocket in the chest piece. |
| Face | **Opaque, ≥ 1.6 mm** (*step 5: a 2.0 mm black base with 0.8 mm purple linework on top*); the plugs come through flush with the base | Thin purple PLA glows faintly when lit from behind. The white box behind it and ≥ 1.6 mm keep it reading as solid linework. |
| Heat | No concern expected. PLA softens at ~60 °C, and the light's LEDs sit 15 mm from the diffuser behind their own lens, with an air gap. Check by hand after an hour at full white. | |

**Check on the bench, no print needed:** hold a scrap of the transparent PLA ~15 mm in front of the lit light at full white, and look at how far the glow spreads.

## Step 4: colours, badge shape and mounting (2026-10-16)

**Filament on hand (team):** PLA in most colours, including white, purple, black, grey and yellow/gold,
plus transparent PLA.

| Part | Filament | Why |
|---|---|---|
| Face (linework) | **Purple PLA**, opaque | The logo's purple |
| Gear | **Gold/yellow PLA**, an inlay in a pocket in the face | Opaque in option A, and keeps the brand gold |
| Diffuser and window plugs | **Transparent PLA** | Step 3 |
| Light box | **White PLA** inside | The reflective interior from step 3. Silver would do too. |
| Face background | **Black PLA** (decided in step 5) | The square badge needs a background that isn't purple, or the husky's purple outline vanishes into it. Black is also the most light-tight. |

| Item | Choice | Why |
|---|---|---|
| Badge shape | **Rounded square, 75 × 75 mm, r 8** (team) | A near-full-size husky (66 mm), a simple pocket in the chest piece, and room in the corners for fixings. A silhouette badge (71 mm husky) and a ø75 round badge (63 mm husky) were mocked up and not chosen. |
| Mounting | **Front-serviceable: four M4 screws from the front through the light's own holes** (team) | The face lifts off to reach the screws; nothing needs access inside the chest piece |

## Step 5: CAD (2026-10-16)

**Script:** `cad/chest_plate_cad.py` (CadQuery 2.8 + shapely in `~/.venvs/cad`). Every dimension is in its
`P` dict. Frame: X right as you face the chest, Y up, Z out of the chest; the origin is the centre of the
flat area, on the chest piece's front face.

    ~/.venvs/cad/bin/python cad/chest_plate_cad.py --light <3118-0808-0002.step> --out cad/out
    ~/.venvs/cad/bin/python cad/chest_plate_cad.py --out cad/out          # without the vendor reference body
    ~/.venvs/cad/bin/python cad/chest_plate_cad.py --out cad/out --2d     # logo layers only, seconds
    ~/.venvs/mujoco/bin/python cad/render_check.py --parts cad/out

- **Inputs:**
  - **`cad/husky_trace.dxf`, the logo trace, in the repo (team).** With it, the CAD regenerates from the
    repo alone. Provenance: traced with the `raster-to-vector` skill from the team's own logo image (step
    1), cropped to the husky and gear with no text. Command: `vectorize.py <crop> --palette
    2e1060,c8a867,e3e3e3,f8d070 --backing ffffff --min-width 3 --epsilon 1.0`. Pixel units, y up, one layer
    per colour. SHA-256 `e721221c…54d2`. The source image itself stays outside git.
  - **The vendor STEP stays outside git.** It is optional: it's used for the assembly and the interference
    check against the light. Every dimension the parts take from it is copied into `P` and `LIGHT_COVER`.
- **Outputs** go in `cad/out/` (git-ignored here):
  - one STEP + STL per part, in its print orientation;
  - `parts.json`;
  - `chest_plate_assembly.step` (~60 MB with the vendor model in it);
  - **`chest_cutout.step`, the body to subtract from the red chest piece in Fusion**;
  - `face_layers.dxf` (the 2D face layers in mm, for the later acrylic version);
  - `logo_check.png` and `assembly.png`.

### The stack, front to back (chest frame, z in mm)

| z | What |
|---|---|
| +2.8 … +2.0 | **Purple linework**, in relief (`relief_t` 0.8) |
| +2.0 … 0 | **Black face base** (`base_t` 2.0), with the glow windows through it. The gold gear is inlaid 1.0 mm deep, its top flush with the purple. |
| 0 | The chest piece's front face, and the light box's rim |
| 0 … −1.6 | Diffuser plate (`dif_t`). Its plugs fill the windows up to +2.0. |
| −1.6 … −2.8 | Centre mask, over the light only |
| −16.6 | The light's lens (`box_gap` 15 behind the diffuser) |
| −19.9 … −22.9 | The box floor, clamped on the light's mounting ears (ear tops at the light's z 3.5) |
| −34.4 | The back of the light; the chest pocket's floor |
| −43.4 | The bottom of the M4 insert holes |

**Depth the chest piece must give:** 22.9 mm for the box pocket (73.6 × 73.6, r 7.3), then the light
pocket (40.6 × 43.1) down to 34.4 mm, then ~9 mm of solid material under it for the inserts. The face
stands **2.8 mm proud**. A ø14 cable hole behind the light's JST ports runs into the chest's interior.
All of this is in `chest_cutout.step`.

### What changed while building it, and why

| Change | Why |
|---|---|
| **The glow regions come from the purple layer, not from the traced white backing.** Glow = the holes in the purple shapes, plus the white cut lines that reach the outside, closed off where narrower than 2.4 mm (`close_r`). | The backing's drop-shadow overshoot put a thin glowing band round the whole outline. The artwork's white circuit traces near the ear bases connect to the outside, so without the closing they printed as black slots. |
| **Opaque islands become glow.** One was found: the purple ring of the eyelet on the far ear's base. It's now a plain glowing oval. Anything bigger than 12 mm² stops the script. | A piece of the face that a window cuts loose can't be printed. The acrylic version can keep the ring (glued to the frosted layer). |
| **Near ear:** a window inset 1.6 mm from the ear's triangle; the artwork's thin white sliver inside the ear becomes purple frame | One clean window with the same frame as the far ear |
| **Thin features are widened by 0.25 mm per side:** glow slots narrower than 0.9 mm, and purple lines narrower than 0.8 mm. ~3 mm² of each is left below the limits, almost all of it corner tips (red/orange in `logo_check.png`). One real slot stays narrow: inside the far ear, ~0.7 mm, so its plug prints as a single line. | The source is ~300 px across the husky (0.21 mm/px). The finest white lines came out ~0.6 mm. |
| **Eye:** a ø2.2 true circle, the plug relieved there, so the light passes through 1.6 mm of clear PLA at the eye instead of 3.6 mm | The eye sits in the glowing almond. Less material makes it a brighter dot, with no floating island. |
| **The graded diffuser became a flat diffuser plus a separate centre mask** (1.2 mm clear, the light's outline + 3 mm, glued behind the diffuser) | A stepped back can't print flat with plugs on the front. Two flat parts give the same gradient. Leave the mask off if the centre isn't hot. |
| **The light box has straight walls** | A sign cabinet's straight white box spreads light as well as a flare in this depth, and it's far simpler. The white floor around the light is part of the reflector. |
| **The light sits under the centroid of the glowing area**, at (−1.5, −2.5) | Even reach to the ears, the nose and the neck |
| **Face held by 4 pairs of 6 × 2 mm magnets** in the corners: one in a 1.6 mm pocket in the face's back (standing 0.4 proud, which locates it), one 0.4 mm deep in a boss in the box | Front-serviceable without visible fixings. The script checks they stay clear of the windows and the gear. |
| **M4 socket head cap screws (ISO 4762, head ø7.0)**, not button heads | The light's cover has notches round its holes that leave exactly ø7 for a head. A ø7.6 button head would hit the cover. |
| The box floor rests on the **light's ears at z 3.5**, not 3.4 | Measured in the STEP; the first pass overlapped 0.1 mm |

### Checks (every run)

- **Interference:** none unintended. Intended: each ø7.0 screw head touches the cover's notch (0.14 mm³),
  which is the vendor's own clearance for that head.
- **Glow inside the diffuser:** 0 mm² outside. **Magnets clear of glow and gear:** 0 / 0 mm².
- **Envelope:** the face is 75.0 × 75.0 mm, the limit. The box and its pocket are 73 / 73.6 mm, so the
  face overlaps the chest's flat area by 0.7 mm all round and hides the pocket's edge.
- **Render:** `assembly.png`, with front, three-quarter and exploded views.

### Parts

| Part | Qty | Filament | Solid mass | Print notes |
|---|---|---|---|---|
| `face` | 1 | **Black, then purple**: pause/filament change at **z = 2.0 mm** | 12.3 g | Back face down, as exported. 0.2 mm layers. The windows and the gear pocket are open from the top; the magnet pockets bridge 6.2 mm. |
| `gear` | 1 | Gold | 0.4 g | Flat. Glue into the face's pocket. |
| `diffuser` | 1 | **Transparent**, 100 % infill | 10.7 g | Back face down, plugs up. 0.2 mm layers, ~215–220 °C, dry filament. Sand the plug tops 400 → 800 if the LEDs show. |
| `centre_mask` | 1 | Transparent, 100 % | 3.3 g | Flat. Glue behind the diffuser over the light (optional, see above). |
| `box` | 1 | **White** | 32.5 g | Floor down. 3 perimeters, 20 % infill. |

**Printed total ≈ 59 g.**

**Assembly:**
1. Glue the gear into the face.
2. Press the face magnets into the face's pockets and the box magnets into the bosses, **poles
   attracting**. Glue them with CA.
3. Fit the diffuser's plugs into the face's windows and tack the diffuser's rim to the face with a few dots
   of CA, outside the windows. Add the centre mask if used.
4. Set the light in its pocket in the chest piece, cable through the ø14 hole. Lay the box in its pocket
   over it and fit the four screws through the box floor and the light into the inserts. **Snug only:**
   the head bears on a ~1 mm ring of PLA floor.
5. Put the face on; the magnets locate it.

### Hardware

- 4 × **M4 × 20 socket head cap screws** (ISO 4762). Stack: 3.0 mm floor + 11.5 mm of light, so 5.5 mm
  of thread in the insert.
- 4 × **M4 heat-set inserts** in the chest piece, ~5.6 mm hole × 8.1 mm long (a common size; check yours
  and set `insert_hole_d` / `insert_len`).
- 8 × **6 × 2 mm disc magnets** (N52 or similar).
- CA glue.

**Off-the-shelf:** 1 × goBILDA RGB Indicator Light (PWM Controlled), 3118-0808-0002. Its BOM row goes
through the Docs & BOM lane.

### Still to confirm before printing

- **The chest piece can give ~43 mm of depth** behind the badge. If it can't, cut `box_gap` (15): every
  mm comes off the depth, at some cost to how far the light reaches.
- The light's real dimensions against the STEP: the case, the hole positions and the ear height.
- That the light's lens is diffuse (the STEP doesn't say), and how even the glow is, using the bench check
  in step 3.
- That the screw heads clear the cover's notches on the real part.
- The insert size you have.
- The magnets' pull: 4 pairs should hold the 26 g face-and-diffuser easily (not measured).
- Which port the wiring uses. The plan is a **JST PH through the ø14 hole at the back**. The side TJC8
  ports are boxed in by the pocket, so wiring that needs them requires side slots in `chest_cutout`.
