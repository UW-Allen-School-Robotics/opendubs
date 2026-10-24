# Perception: one set of segmentation models for navigation and manipulation

**Status:** built and tested on opendubs2 under `archit` (dummy camera), 2026-10-22, on
`archit/perception`. Nothing is deployed to the robot's stack. The phases, results, decisions and
test rules are in `notes/workplan-perception.md`; the code is in `software/perception/`.

## The goal

One copy each of three models runs on the Thor and serves two kinds of client:

- **Navigation (stream):** every frame from a navigation camera is segmented into traversable,
  non-traversable and people, and turned into something the navigation stack can use.
- **Manipulation (query):** a client asks for objects by name ("mug", "candy") and gets back a mask
  for every instance found, with the colour image, aligned depth and camera intrinsics of the
  frame used. The first consumer is AnyGrasp, which runs on a workstation on the same network and
  is called over a WebSocket. That link is a later phase.

The three models:

| Model | What it does here | Licence |
|---|---|---|
| **CLIPSeg** (`CIDAS/clipseg-rd16`) | Scores every pixel against text prompts. Gives a coarse label map. | Apache-2.0 |
| **YOLO-World** (`yolov8s-worldv2.pt`, Ultralytics) | Open-vocabulary detector: one box per object instance for any text label | **AGPL-3.0** (open decision D2) |
| **NanoSAM** (NVIDIA, distilled from MobileSAM) | Turns a box into a clean mask. Knows nothing about classes | Apache-2.0 |

## Where it comes from

The models, wrappers and engine scripts come from HOUND's perception pipeline,
[`sidtalia/perception_models`](https://github.com/sidtalia/perception_models), pinned at
`2cd9a68` (2026-10-16, the merge of `feature/multicam-batch`). How it works today:

1. CLIPSeg scores each image against 9 prompts in three groups (traversable: floor, ground, path,
   dirt; non-traversable: furniture, wall, pillar, car; people). Each pixel takes the strongest
   group, or no label below a threshold of 0.3.
2. Connected blobs of each label become bounding boxes: the largest 6, ignoring blobs under 0.2 %
   of the image.
3. NanoSAM decodes all boxes in one batch. Masks of the same label are merged.
4. The result is painted white (traversable) or black (everything else) and painted into the
   map by HOUND's own mapper.

What we keep, change and drop:

| Part | Decision | Reason |
|---|---|---|
| `clipseg/` | Keep | The text embedding is an input to the decoder (`dec(hs, cond)`), so a request can bring its own labels |
| `nanosam_trt/` | Keep; add a "no merge" option | Box-agnostic and batched. Without the per-label merge (`refiner.py:451`) each box keeps its own mask, which a query needs |
| `yolo_world/` | Keep; add a per-request path | See "Per-request labels" below |
| `seg_ops.py`, `sam_box_merge.py` | Keep | Pure helpers |
| `scripts/` (export, engine builds, benches) | Keep; build for one camera, DLA off | The Thor has no DLA cores |
| `dora_*.py`, `cuda_ipc.py`, Arrow parts of `multicam_rgb_batch.py` | Drop | The Dora process split is replaced by one process (below) |

Two things in HOUND's documentation are not true of its code, so neither is relied on here:
`YOLO_WORLD.md` marks the YOLO-box "manip decode" as done, but `dora_seg_refine.py` never reads
YOLO boxes (only helpers and tests exist); and `sam_max_boxes_manip` is passed by the launch file
and never read.

## Architecture: one process

```
                      perception container: one Python process
 ROS in ──────┐   ┌───────────────────────────────────────────────────────────────┐
 nav camera   │   │ ingress: latest frame per camera, with depth of the same stamp│
 Segment      │   │ scheduler: a nav frame every period; at most one query between│
 action goals ┘   │ models (one copy each):                                       │
                  │   CLIPSeg    image half TensorRT, text half PyTorch           │
                  │   YOLO-World torch FP16, text features per call               │
                  │   NanoSAM    encoder TensorRT, batched decoder TensorRT       │
                  │ adapters:                                                     │
                  │   navigation → CLIPSeg free space, SAM-sharpened obstacles    │
                  │   query      → per-instance masks + rgb + depth + camera_info │
                  └───────────────────────────────────────────────────────────────┘
```

**Why one process instead of HOUND's Dora graph.** HOUND runs the models in separate processes
connected by Dora. That design was built for one streaming client, and three parts of it break
under a second, request-driven client:

- Every Dora connection keeps only the newest message, and `seg_refine` pairs images and features
  by arrival order, not by an ID. A request frame would be overwritten or mismatched.
- A request needs a reply path back to the caller, which makes the graph a loop. Whether Dora
  supports that is unverified.
- HOUND's own notes record that splitting models across processes does not run them in parallel
  on one GPU ("One Orin GPU ≈ sum of heavy kernels; process splits need MPS"). In one process,
  CUDA streams can overlap, and the float16 copies between processes go away.

HOUND required `dora-rs>=0.3.0`; PyPI is now at 1.1.0, so its node code may not run unchanged
anyway. The cost of dropping Dora is diverging from HOUND's glue code; the model code stays
shared.

**Scheduling.** Navigation runs at a fixed rate on the latest frame only. Queries wait in a FIFO
with a request ID, and at most one runs between two navigation frames, so the navigation rate
holds. When a query names the navigation camera and the latest frame is fresh enough, its NanoSAM
image encoding is reused.

**Per-request labels without changing the shared models.**

- *CLIPSeg:* the request's text embedding is computed once (cached per label) and passed to the
  PyTorch FiLM decoder, which takes any number of prompts. **The text half never runs in
  TensorRT:** TensorRT 10.13 on the Thor computes it wrongly (on `bus.jpg`, "ground" over the
  pavement scored 0.01 against 0.74 in PyTorch and in ONNX Runtime on the same ONNX file, in FP16
  and FP32 alike). The image half's engine is correct and does the expensive work.
- *YOLO-World:* HOUND changes the class list with `set_classes`, which modifies the model for
  every caller. Instead, call `WorldModel.predict(x, txt_feats=…)` with text features from
  `get_text_pe(…, cache_clip_model=True)`, with Ultralytics' letterbox and NMS around it. The
  detection head's class count (`head.nc`, `head.no`) must be set to the number of labels on each
  call; that is safe because calls are serialised. **Never zero-pad text features** to a fixed
  count: a zero vector normalised in FP16 gives NaN scores, and NMS silently drops every box
  (Phase 0.7). Matched the official path box for box (4/4, 5/5, 4/4).
- *NanoSAM:* query boxes run as their own decode, never batched with navigation frames:
  `decode_multi` requires every image in a batch to be the same size, and a query camera may
  differ.

## Interfaces (draft)

**Query: a ROS 2 action, `Segment`** (in a small interface package built in the container):

```
# goal
string[] labels          # e.g. ["mug", "candy"]
string   camera          # camera namespace, e.g. "top"
float32  min_score
uint8    source          # AUTO=0 (detector, then CLIPSeg fallback), DETECTOR=1, CLIPSEG=2
---
# result
std_msgs/Header        header      # stamp and frame of the frame used
sensor_msgs/Image      rgb
sensor_msgs/Image      depth       # aligned to rgb
sensor_msgs/CameraInfo camera_info
Instance[]             instances   # label, score, box (x0,y0,x1,y1), mask (mono8)
---
# feedback
string stage
```

"All of them" depends on the detector: CLIPSeg alone gives regions, not separate instances.

**Navigation adapter.** CLIPSeg's traversable map is the free space; obstacle and people blobs
become boxes for SAM, whose masks are joined with CLIPSeg's coarse obstacle map; free = traversable
minus blocked. SAM is not used for the floor: it segments the dominant *object* in a box, and given
the box of a whole pavement it returned a window above it.

**Navigation stream, per camera:**

- `/perception/<camera>/obstacles` — `PointCloud2` of non-traversable and people pixels projected
  through depth, for Nav2's obstacle or voxel layer.
- `/perception/<camera>/traversability` — mono8 traversability image with its `camera_info`, for
  nvblox if the Thor stack's Phase 5 adopts it.

**Safety rule:** the navigation adapter only adds obstacles. It never clears what the LIDARs see.

**Grasp: a ROS 2 action, `GraspObject`,** served by `perception_tasks`, a second process:

```
# goal
string label  ·  string camera  ·  float32 min_score  ·  uint8 source
geometry_msgs/Vector3 up_cam     # world up in the camera frame if known; zero = unknown
---
# result
bool success  ·  string message
std_msgs/Header header           # the camera's optical frame and stamp
Instance instance  ·  int32 instances_found
Grasp grasp  ·  Grasp[] candidates   # pose (x approach, y closing: GraspNet), width, scores
string mode                      # how the workstation chose the best
sensor_msgs/Image rgb  ·  sensor_msgs/CameraInfo camera_info
float32 segment_ms  ·  float32 grasp_ms
```

It calls `segment`, then AnyGrasp on a workstation over a WebSocket (`perception_clients/`). Two
layering rules. **Models that run off the robot get a plain-Python client** in
`perception_clients/`, with no ROS, so it can be tested anywhere. **Tasks that combine services run
apart from `perception_server`**: its one worker thread makes every model call, and a network round
trip of seconds there would hold up the navigation stream. A workstation that is down or slow
fails only the task. The request is cropped to the mask's box, with the principal point shifted:
the workstation builds its cloud only from masked pixels, so nothing is lost, and a whole frame
exceeds its server's 4 MB message limit. Details and results: the workplan, Phase 4.

## The container

Built only for this work. Verified against opendubs2 (R38.4, JetPack 7.1) on 2026-10-22.

**Base:** NVIDIA's public Isaac ROS 4.4 image for r38.4,
`nvcr.io/nvidia/isaac/ros@sha256:d7bca990d0d29eb38b47bb60ef6155fa361f2be29ad587b5f44ae7df5fee525f`
(tag `isaac_ros_28556f8bc78a98822bd08b2d7c6fcf9b-arm64-jetpack`, created 2026-04-30). It is
Ubuntu 24.04 with ROS 2 Jazzy, and its apt sources already point at Isaac ROS `release-4.4` and
`repo.download.nvidia.com/jetson/common r38.4`. It contains no CUDA. HOUND's `mushr-jazzy`
image is built on the same base.

**CUDA and TensorRT go inside the image.** On JetPack 7 the NVIDIA runtime mounts only the
devices and the driver into a container (`/etc/nvidia-container-runtime/host-files-for-container.d/`
holds just `devices.csv` and `drivers.csv` on opendubs2). The `mushr` README's statement that CUDA
and TensorRT "live on the host … exposed via `--runtime=nvidia`" is not true on this JetPack. The
`r38.4` repository has the host's exact versions, so the image pins those:

| Component | opendubs2 host (`dpkg -l`) | Image (apt, `jetson/common r38.4`) |
|---|---|---|
| CUDA toolkit | `cuda-toolkit-13-0 13.0.0-1` | same |
| cuDNN | `libcudnn9-cuda-13 9.12.0.46-1` | same |
| TensorRT | `tensorrt 10.13.3.9-1+cuda13.0` | same, plus `libnvinfer-bin` (`trtexec`) and `python3-libnvinfer` |

**Python:** PyTorch from PyPI (the `cu130` build the host already runs on the GPU),
`numpy<2` (as HOUND pins it, for the apt-built ROS Python packages), `torch2trt` from git (its
`TRTModule` has a TensorRT 10 path: `execute_async_v3`), `nanosam` from git, `transformers`,
`ultralytics`, `opencv`. No `cv_bridge`: images convert with numpy.

**Model files** live outside git and outside the image, in a data directory mounted into the
container: CLIPSeg from Hugging Face, the NanoSAM encoder ONNX (NVIDIA's Google Drive link in the
NanoSAM README, which refuses scripted downloads), the MobileSAM checkpoint, and the YOLO-World
weights. TensorRT engines are built on the Thor into the same directory.

**SAM engines.** The default is NanoSAM's ResNet18 encoder at 512 with HOUND's batched decoder, as
HOUND ships. Without the Drive file, MobileSAM's own TinyViT encoder (the model NanoSAM distils)
exports from `mobile_sam.pt` and runs at 1024 (`tools/build_mobilesam_engines.sh`). PyTorch 2.14
exports ONNX with dynamo by default, which fails on MobileSAM; exports run on the classic exporter.

**Run:** `--runtime nvidia --network host`, `RMW_IMPLEMENTATION=rmw_fastrtps_cpp` (the robot uses
FastDDS; HOUND's image ships a CycloneDDS config), data directory mounted.

Not used: HOUND's `dbzfan2012/mushr-jazzy:aarch64` image. It exists only as a 28.7 GB tarball on
Google Drive (the Docker Hub fallback in its `context.md` returns 404), it was saved by hand ("Do
not rebuild the image"), its PyTorch source is unknown, and it carries the whole HOUND stack.

## Navigation on OpenDubs

HOUND's navigation output, a white/black image painted into its own TSDF mapper, has no consumer
on OpenDubs. OpenDubs plans Nav2 with two 2D LIDARs, with cuVSLAM and nvblox as a later Thor stack
phase. The obstacle cloud fits Nav2 directly; the traversability image is kept for nvblox. Wiring
either into the navigation stack is the Thor stack lane's work.

## Open decisions and known risks

Tracked with owners in `notes/workplan-perception.md`. In short:

- **D1** The Orbbec model, and which camera serves navigation (it must see the floor; the head
  D415 moves with the neck and is held by the face tracker).
- **D2** YOLO-World (AGPL-3.0) or an Apache-licensed detector (NanoOWL / OWLv2).
- **D3** The perception lane, its paths, and its CI workflow (the orchestrator).
- ~~**R1** PyTorch kernels inside the container~~: work (Phase 0.3).
- ~~**R2** `torch2trt` on TensorRT 10~~: works for the engines we use.
- **R3** YOLO-World has no TensorRT path (`adaptive_max_pool2d` fails ONNX export), so it runs in
  PyTorch FP16 (~15–20 ms per image). The per-request path matches the official one.
- **R7** TensorRT 10.13 miscomputes CLIPSeg's FiLM decoder (above). Any future TensorRT engine is
  checked against PyTorch on a real image before use; a synthetic one hid this.
- **R4** The GPU is shared with the voice lane's language model (capped at 20 % of memory) and
  the face tracker; compute is shared by time-slicing.
- **R5** Moving images between host and container under FastDDS (UDP only) costs CPU; unmeasured.
- **R6** The Orbbec ROS 2 driver (`OrbbecSDK_ROS2` `v2-main`) lists Jazzy but not aarch64 or Thor.

## Sources

- HOUND: [`perception_models`](https://github.com/sidtalia/perception_models) (`dora/SEGMENTATION.md`,
  `dora/YOLO_WORLD.md`), [`hound_core` `docs/context.md`](https://github.com/prl-mushr/hound_core/blob/feature/multicam-batch/docs/context.md),
  [`mushr` install](https://github.com/prl-mushr/mushr/tree/jazzy/mushr_utils/install)
- [NanoSAM](https://github.com/NVIDIA-AI-IOT/nanosam), [torch2trt](https://github.com/NVIDIA-AI-IOT/torch2trt),
  [NanoOWL](https://github.com/NVIDIA-AI-IOT/nanoowl), [OrbbecSDK_ROS2](https://github.com/orbbec/OrbbecSDK_ROS2)
- [JetPack 7.1](https://developer.nvidia.com/embedded/jetpack/downloads/archive-7.1.md),
  [NVIDIA forum: Docker image for JetPack 7.1](https://forums.developer.nvidia.com/t/docker-image-for-jetpack-7-1/356743)
- `notes/workplan-thor-stack.md` (T-1, T-2, T-4, the opendubs2 audit), `software/stack/versions.env`
