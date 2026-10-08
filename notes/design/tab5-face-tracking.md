# Camera → detection → animation on the Tab5

Written 2026-10-05, after a long and badly-run debugging session. The design is at the bottom; the
top is what the pipeline actually is, and what I got wrong by not reading it first.

## 1. The pipeline, read from source

From `platforms/tab5/main/hal/components/hal_camera.cpp`, not inferred:

| stage | what it does |
|---|---|
| sensor | MIPI-CSI, opened via V4L2 (`app_video_open`). Pixel format is forced to **RGB565**; the **resolution is left at the sensor's default** (`default_format.fmt.pix.width/height` is read back and reused — only the format is changed). |
| PPA (`ppa_do_scale_rotate_mirror`) | in **1280×720 RGB565** → out **1280×720 RGB565**. `rotation_angle = 0`, `scale_x/y = 1`, **`mirror_x = true`**, `mirror_y = false`, `rgb_swap = false`, `byte_swap = false`. Blocking. |
| hand-off | `lv_canvas_set_buffer(camera_canvas, img_show->data, 1280, 720, LV_COLOR_FORMAT_RGB565)`, every frame, with the **same pointer** each time. |

So the buffer is **linear 1280×720 RGB565, little-endian, mirrored horizontally**. That is the whole
truth of it, and it took one careful read to establish.

### The trap that cost the most

The same function also contains:

```c
img_show->w = 720;
img_show->h = 1280;
img_show->pixfmt = PIXFORMAT_RGB565;
img_show->size = img_show->w * img_show->h * img_show->bpp;
```

**Those fields are dead.** They are `imlib`'s `image_t` metadata and nothing in the PPA path reads
them — PPA is told 1280×720 explicitly, and the canvas is told 1280×720 explicitly. Only
`img_show->data` is used. `img_show->size` is not even correct (`bpp` is never set).

I grepped, saw `720` and `1280` in the struct, and spent a flash-and-ask cycle testing whether the
frame was "really portrait". It never was. **The vendor file carries misleading leftovers, and a
grep cannot tell a live field from a dead one.** Read the function.

## 2. What I got wrong, in order

1. **Declared victory on the wrong evidence.** The built-in-image self-test passed at 0.835 and
   ~25 ms, and I reported "face detection WORKS". It proves the **model** runs. It exercises **no
   part of the camera**. The live path had never run once. This is the error that caused everything
   after it, and the team named it exactly: *a test without me in frame won't prove anything.*
2. **Ran 26 ms of inference while holding the LVGL lock.** This starved the render loop: the face
   animation froze while LVGL's own FPS overlay kept updating. That signature — *our* animation
   dead, *LVGL's* counter alive — means the render loop is being starved, not that LVGL has died.
   Worth remembering as a diagnostic in its own right.
3. **Two writers, one widget.** The probe wrote its status to `g_label`, which is the existing
   state/emote/heap HUD, rewritten on every state change. The status was clobbered within a frame.
   Worse, my change-guard compared against *what I had last written* rather than what was on the
   label, so once clobbered it was never rewritten at all. It now has its own label.
4. **A string replacement that matched the wrong occurrence** truncated `face_probe.cpp` from 171
   lines to 24, silently deleting the entire probe body. Recovered from git. Patching code by
   unique-ish string match is fine; patching by a string that appears in both arms of an `#ifdef`
   is not.
5. **Three guesses at a memory bug** (`MALLOC_CAP_INTERNAL`, then `MALLOC_CAP_DMA`, then a static
   task) before reading the 14 lines above the assert, which said plainly it was ESP-Hosted's task
   and not mine.

The pattern in all five: **acting on a plausible mechanism instead of reading the thing in front of
me.** Each one cost a flash and someone's attention.

## 3. The design

### Principle: the instrument must show the real path, not a proxy

A synthetic test that feeds the reference image through the live conversion code would validate my
glue and prove nothing about the camera — and I had already drawn false confidence from exactly that
shape of test. So the instrument is the **real** path, made visible.

### One pass, two outputs, from the same pixels

```
camera buffer (1280x720 RGB565, cached pointer, no lock needed)
        |
        |  single software pass, nearest-neighbour, 16:9 -> 16:9 (no distortion)
        v
   +---------------------------+
   |  prev565  320x180 RGB565  | --> a VISIBLE 320x180 canvas, bottom-left
   |  rgb888   320x180 RGB888  | --> HumanFaceDetect::run()
   +---------------------------+
```

**Both outputs come from the same loop over the same source pixels.** That identity is the point:
what is on the panel *is* what the detector saw. So the preview is not a nicety, it is the
measurement —

- preview looks like a room → the camera streams and my conversion is correct
- preview is black → the camera is not producing pixels (or the lens is covered)
- preview is sheared/striped → my conversion or the stride is wrong
- preview looks right but no detection → the model or the face size is the problem

Four distinguishable outcomes, one glance, no inference. RGB888 because that is the **only format
the model is proven on** — the self-test decodes its JPEG to RGB888.

### Locking

- **Reading the camera buffer needs no lock.** The camera task writes it via PPA *without* holding
  the LVGL lock, so the lock protects nothing there. Holding it only blocks rendering. Tearing
  across a frame boundary is harmless for detection.
- The source pointer is **cached once** — `lv_canvas_set_buffer` is called with the same
  `img_show->data` every frame — so the hot loop takes no lock to read pixels.
- **Detection runs with no lock held.**
- The lock is taken only at the end, briefly, to invalidate the preview and move the marker.
- **The tracker never calls LVGL from its own task otherwise.** It publishes a gaze target in
  atomics; the face's own render loop consumes it. AUDIT.md invariant 1 satisfied structurally.

### No scaling in LVGL

The preview canvas is 320×180 pointed at a 320×180 buffer. The downscale is the software pass we
already do. Scaling an image object in LVGL induces a **layer** — a per-draw buffer the size of the
bounding box — which is what killed the rotated brow outright. A scaled 1280×720 would be far worse.

### The marker is a child of the preview

In preview pixel coordinates, so it cannot drift relative to the image and needs no translation.
Hollow, so it never hides what it marks. Created once, then only moved or hidden.

### Gaze

The detection centre is normalized to ±1, mirrored to undo PPA's `mirror_x`, and compressed
vertically by 0.55 to match the envelope `newSaccade` already uses — so a tracked glance moves in
the same range and with the same main-sequence timing as a spontaneous one, rather than looking
like a different mechanism. A tracked face suppresses the random saccade for 1.2 s; losing the face
for 900 ms releases gaze back to wandering.

## 4. Status: the live path is NOT working, and the cause is not yet known

Measured on device:

| | |
|---|---|
| model, static reference image | **PASS**, score 0.835, ~25 ms, deterministic across 8 runs |
| camera reports capturing | yes (but `is_camera_capturing` is set *before* the task runs, so this is weak evidence) |
| frame buffer content | non-zero (mean ~34 of ~93) — something is writing real-looking data |
| detection on live frames | **zero faces**, with a person in frame, at 22–26 ms per attempt |

The honest state: the model works, pixels appear to be arriving, and nothing is detected. The
preview instrument above exists to tell us *which* of those four outcomes we are actually in, and
it has not yet been looked at.

### Suspect eliminated by reading, not by flashing

My leading candidate was that `app_video_open` keeps the sensor's **default** resolution while the
PPA is hard-coded to a 1280×720 input. **That is not the problem.** The sensor is an **SC202CS**
(`CONFIG_CAMERA_SC202CS=y`), its format table's entry 0 is
`MIPI_1lane_24Minput_RAW8_1280x720_30fps`, and the build selects exactly that:

```
CONFIG_CAMERA_SC202CS_MIPI_IF_FORMAT_INDEX_DAFAULT=0
```

So: sensor 1280×720 RAW8 → ISP interpolates to RGB565 → PPA 1280×720 → buffer 1280×720. **The whole
geometry chain is consistent, and no stride or shape in it is wrong.** Confirmed from the Kconfig
value the build actually used, so it cost nothing and needed no one at the bench.

### What that leaves, in the order the preview discriminates them

1. **The camera is not seeing the person** — not pointed at them, covered, or the room is too dark
   for the ISP. The preview answers this instantly, and the new status line reports
   `luma lo/avg/hi` so a black or washed-out frame is explicit rather than inferred.
2. **The face is too small in the detector's input.** This is now my leading candidate on
   arithmetic. The reference face that scores 0.835 is **96 px wide in a 320-wide image — 30% of
   frame width.** A person sitting at arm's length from the panel is maybe 200 px wide in a
   1280-wide frame, which after downscaling to 320 is **~50 px, about 15%** — half the relative
   size, and the whole frame is 180 tall rather than 240. If the preview looks like a normal room,
   this is where to go next, and the fix is cheap and principled: **detect on a centre crop rather
   than the full frame** — take the middle 640×360 and scale 2:1 to 320×180, which doubles apparent
   face size for the same detector cost. A person addressing the panel is centred by construction,
   so a centre crop loses nothing that matters.
3. Pixel order or ISP output convention differing from the PPA's declared `rgb_swap = false` /
   `byte_swap = false`. A sheared or false-coloured preview would show it.

**Deliberately not flashed again while nobody is there to look.** The build on the device now
reports the preview and the luma range; the next move is to read it, not to guess.
