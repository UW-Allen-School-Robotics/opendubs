# Workplan: perception (shared segmentation models)

**Status:** draft, 2026-10-22, on `archit/perception` only. Not merged, and no lane owns perception
yet (D3). The design is in `notes/design/perception-shared-models.md`.

**Goal:** one copy each of CLIPSeg, YOLO-World and NanoSAM on the Thor, in one container, serving
a navigation stream and manipulation queries. AnyGrasp (on a workstation, over a WebSocket) is the
first query consumer, in Phase 4.

## Where the work runs

All development and testing happens on **opendubs2, as the user `archit`**, a fresh account with
nothing installed. The user chose this on 2026-10-22: the account is isolated from the robot's own
services, which run under other users and under `/opt/opendubs`.

opendubs2 is the working robot, and the Thor stack plan says it is "never the test bed". This
workplan keeps that intent with the rules below: nothing on the host changes, and nothing here can
stop, slow for long, or reconfigure what the robot already runs. A test container under `archit`
is not a deploy of the robot's stack, so the orchestrator is told once Phase 0 has a result, not
before (the user, 2026-10-22).

`archit` runs Docker through `sudo docker`, which is root on the robot. Rule 2 matters all the
more for that.

### Rules for every session on `archit@opendubs2`

1. **No host changes.** No `apt`, no `pip` outside a container, no files under `/etc`, `/opt` or
   `/usr`, no systemd units, no udev rules, no `nvpmodel` or `jetson_clocks`. Everything lives in
   `~/opendubs-perception` (a clone of this branch) and `~/perception-data` (models, engines,
   samples, results), both in `archit`'s home.
2. **Docker is shared, so touch only our own objects.** Every image, container and volume is named
   `archit-perception…`. Never run `docker system prune`, `docker image prune`, `docker volume
   prune` or `docker rm`/`rmi` on anything else, and never restart or reconfigure the Docker
   daemon. The robot runs `openarm`, the voice service and `opendubs-llm` in this daemon.
3. **No `--restart`, no `--privileged`, no `/dev` passthrough** until Phase 3, and no cameras
   before then. The D415 is held by the face tracker (Thor stack T-4). Phase 0–2 use still images
   and recorded data.
4. **Keep test ROS traffic off the robot's graph.** The robot uses `ROS_DOMAIN_ID=0` with FastDDS.
   Test containers run with `ROS_DOMAIN_ID=77` and `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST`.
5. **GPU work is short and watched.** Engine builds and benches load the GPU for minutes, and the
   GPU is shared with the language model and the face tracker. Run them when nobody is using the
   robot, watch `tegrastats` in a second shell, and stop if the robot is needed.
6. **Downloads stay out of the clone.** Models and engines go to `~/perception-data`, never into
   `~/opendubs-perception` (CLAUDE.md: downloads never land in the working tree).

## Phase 0: feasibility on opendubs2

Nothing goes into the robot's stack. Each step records its output under
`~/perception-data/results/` and a one-line result in the table at the end of this phase.

| # | Step | Done when |
|---|---|---|
| 0.0 | **Preflight, read-only.** `id`; `sudo docker info --format '{{.DefaultRuntime}} {{json .Runtimes}}'`; `df -h /var/lib/docker ~`; `free -g`; `sudo docker ps --format '{{.Names}}'` (to know what not to touch) | Recorded |
| 0.1 | **Clone and data directory.** `git clone -b archit/perception … ~/opendubs-perception`; `mkdir -p ~/perception-data/{models,engines,samples,results}` | Both exist |
| 0.2 | **Write the image.** `software/perception/image/Dockerfile` (recipe below), built as `archit-perception:dev` | `docker build` succeeds; size recorded |
| 0.3 | **Smoke test inside the container.** `nvidia-smi`; `trtexec --version`; `python3 -c 'import tensorrt; print(tensorrt.__version__)'`; `python3 -c 'import torch; x=torch.ones(1024,1024,device="cuda"); print(torch.__version__, (x@x).sum().item())'` | TensorRT reports 10.13.3, and a matrix product runs on the GPU (R1) |
| 0.4 | **Model files.** CLIPSeg `CIDAS/clipseg-rd16` (Hugging Face); `resnet18_image_encoder.onnx` (NanoSAM README's Drive link); `mobile_sam.pt` (MobileSAM repository); `yolov8s-worldv2.pt` (Ultralytics release). All into `~/perception-data/models` | Files present, with checksums recorded |
| 0.5 | **Engines**, with HOUND's scripts inside the container: `DATA=/data/engines USE_DLA=0 IMAGE_BATCH=1 NANOSAM_CKPT=/data/models/mobile_sam.pt bash /opt/perception_models/scripts/build_all_engines.sh` (CLIPSeg image half and text half, NanoSAM 512 encoder and batched decoder). The script reads `resnet18_image_encoder.onnx` from `DATA`, so copy it there first | All four engines build and load through `torch2trt.TRTModule` (R2) |
| 0.6 | **Benchmarks, one at a time:** `bench_clipseg.py --trt-engine …`; a NanoSAM encode + decode bench (6 boxes and 16 boxes); `bench_yolo_world.py --models yolov8s-worldv2.pt --imgsz 640` | Milliseconds per stage recorded, with what else was running (`tegrastats`) |
| 0.7 | **YOLO-World per-request text.** One loaded model; alternate two label sets (`["person"]` and `["mug","bottle"]`) through `WorldModel.predict(x, txt_feats=…)`, never calling `set_classes` | Each call returns boxes only for its own labels, and the cached text features make the second call no slower (R3) |
| 0.8 | **Write up.** Results table below; update the design's risk list | The go/no-go for Phase 1 is clear |

### Image recipe (0.2)

```dockerfile
# Isaac ROS 4.4 for r38.4 (JetPack 7.1): Ubuntu 24.04 + ROS 2 Jazzy, no CUDA.
FROM nvcr.io/nvidia/isaac/ros@sha256:d7bca990d0d29eb38b47bb60ef6155fa361f2be29ad587b5f44ae7df5fee525f

# CUDA, cuDNN and TensorRT pinned to opendubs2's host versions, from the r38.4 repository the
# base already configures. JetPack 7 does not mount these from the host.
RUN apt-get update && apt-get install -y --no-install-recommends \
      cuda-toolkit-13-0=13.0.0-1 \
      libcudnn9-cuda-13=9.12.0.46-1 \
      tensorrt=10.13.3.9-1+cuda13.0 libnvinfer-bin=10.13.3.9-1+cuda13.0 \
      python3-libnvinfer=10.13.3.9-1+cuda13.0 \
      python3-pip git \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN pip install --no-cache-dir "numpy<2" torch==<the host's version> \
      transformers ultralytics opencv-python-headless onnx \
 && pip install --no-cache-dir git+https://github.com/NVIDIA-AI-IOT/torch2trt.git \
 && git clone https://github.com/NVIDIA-AI-IOT/nanosam.git /opt/nanosam \
 && pip install --no-cache-dir -e /opt/nanosam \
 && git clone https://github.com/sidtalia/perception_models.git /opt/perception_models \
 && git -C /opt/perception_models checkout 2cd9a68 \
 && pip install --no-cache-dir -e /opt/perception_models

ENV RMW_IMPLEMENTATION=rmw_fastrtps_cpp
```

Open points to settle while writing it: the exact `torch` version (read from the host with
`/opt/opendubs/venv/bin/python -c 'import torch; print(torch.__version__)'`, which reads only);
whether the full `cuda-toolkit-13-0` is needed or the runtime packages suffice (smaller image);
pinning `transformers` and `ultralytics`; and a fork of `perception_models` instead of the
upstream clone once we change it.

Run (Phase 0):

```sh
docker run --rm -it --name archit-perception-dev \
  --runtime nvidia --network host \
  -e ROS_DOMAIN_ID=77 -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
  -v ~/perception-data:/data -v ~/opendubs-perception:/work \
  archit-perception:dev bash
```

### Phase 0 results

| # | Result | Date |
|---|---|---|
| 0.0 | `archit` has passwordless `sudo docker`; 703 GB free; 87 GB of memory available; running: `opendubs-voice`, `opendubs-llm`, `opendubs-airplay`, `openarm`; host torch `2.14.0+cu130` | 2026-10-22 |
| 0.2 | `archit-perception:dev`, 18.4 GB. Three fixes: the Isaac ROS base does not set `NVIDIA_VISIBLE_DEVICES`, so the runtime injected no driver; ultralytics' `opencv-python` needs `libGL`; `nanosam/` has no `__init__.py`, so `pip install -e` maps nothing (now on `PYTHONPATH`). The generic arm64 CUDA repository offers TensorRT 11.4, so the r38.4 pin is required | 2026-10-22 |
| 0.3 | **Pass.** NVIDIA Thor, driver 580.00; torch `2.14.0+cu130` on `sm_110`; TensorRT 10.13.3.9 with fast FP16; `trtexec` v10.13.3; all imports, including `torch2trt`, NanoSAM, `perception_models` and `rclpy` | 2026-10-22 |
| 0.4 | CLIPSeg (1.2 GB Hugging Face cache), `mobile_sam.pt`, `yolov8s-worldv2.pt` downloaded, checksums in `SHA256SUMS`. `resnet18_image_encoder.onnx`: the Drive link refuses scripted downloads (the Hugging Face mirrors carry other encoders or `.ort`), so the user fetched it in a browser (SHA-256 `d266fcdc…`) | 2026-10-22 |
| 0.5 | **Pass, with one engine unusable.** `trtexec` 10.13.3 built: CLIPSeg image half (145 MB) and text half (1.3 MB), NanoSAM ResNet18 encoder at 512 (31 MB) and batched decoder (11 MB, ≤ 32 boxes), and the Drive-free fallback, MobileSAM's TinyViT encoder at 1024 (27 MB) and its decoder (12.5 MB). PyTorch 2.14 exports with dynamo by default, which failed on MobileSAM; the exports run on the classic exporter (`tools/onnx_export_classic.py`, `tools/classic_onnx/`). **The CLIPSeg text-half engine computes wrong results** (Phase 1, below) and is not used | 2026-10-22 |
| 0.6 | **CLIPSeg** (`bench_clipseg.py`): image half TensorRT **1.44 ms** (PyTorch eager 7.67 ms); text half, 9 prompts, PyTorch **2.49 ms**; TensorRT vs PyTorch features cosine 0.999995, FiLM probabilities within 0.0034. HOUND's Orin NX: ~6 ms and ~10 ms. The robot's GPU was otherwise idle (clock at 314 MHz outside the bench), so these are close to clean numbers. On the Thor, `tegrastats` reports the GPU clock (`GR3D_FREQ`), not a utilization percentage. NanoSAM and YOLO-World benches still to run | 2026-10-22 |
| 0.7 | **Pass.** One loaded YOLO-World answered three label sets through `WorldModel.predict(x, txt_feats=…)`: every box matched the official `set_classes` path (4/4, 5/5, 4/4 at IoU > 0.9), and the model's own text features were unchanged. Text: ~2.9 s for the first CLIP load, then ~9 ms for a new label set, ~0.1 ms cached. Detect + NMS ~15–20 ms per image (PyTorch FP16). **Two rules for Phase 2:** the head's class count (`head.nc`, `head.no`) must be set per call to the number of labels; never zero-pad text features, because a zero vector normalised in FP16 gives NaN scores and NMS silently drops every box | 2026-10-22 |

## Phases 1 and 2: the server, with both adapters

Built together on 2026-10-22 and run on opendubs2 with the dummy camera (`tools/dev.sh`). The
Femto Bolt is the manipulation camera once fitted; the navigation cameras are not chosen yet, so
both adapters run on the dummy camera for now (user, 2026-10-22).

| Piece | Where |
|---|---|
| Server: one worker thread makes every model call; a navigation frame when due, at most one query between two | `perception_server/server.py` |
| The three models, one copy each | `perception_server/models.py` |
| Navigation adapter | `perception_server/nav.py` |
| Query adapter | `perception_server/query.py` |
| `Segment` action, `Instance` message | `perception_msgs/` (built into the image) |
| Dummy RGB-D camera, query client, navigation snapshot, run helper | `tools/dummy_camera.py`, `tools/segment_client.py`, `tools/nav_snapshot.py`, `tools/dev.sh` |

### Results

| Check | Result |
|---|---|
| Queries during the navigation stream | All answered. `bus` → the bus (0.91); `bus person` → bus + 4 people; `bus person shoe` → 8 instances. Each person is its own mask, edges on the body (overlays checked by eye). `mug` on a scene without one → 0. Server 117–144 ms, round trip 160–189 ms (target < 300 ms) |
| CLIPSeg-only query (`source: clipseg`) | `ground` follows the pavement around people's feet; `bus` covers the bus |
| Navigation output | `bus.jpg`: 33 % free (the pavement), bus, building and people blocked; 35 k obstacle points at 1.5–4.0 m |
| Navigation rate | **6–8 Hz, limited by the dummy camera**, not the server: frames take 41–47 ms (image half ~7, text half ~6, SAM encode 10–17, SAM decode ~7 ms), and every frame that arrives is processed. The dummy camera's 3–4 MB frames reach the server at ~6 Hz. Re-measure with a real camera |
| Model memory | about 3 GB for the server process |

### What Phase 1 found

1. **TensorRT 10.13 computes CLIPSeg's text half (FiLM decoder) wrongly on the Thor.** On
   `bus.jpg`, "ground" over the pavement: PyTorch 0.74, ONNX Runtime 0.74 on the same ONNX file,
   TensorRT 0.01, in FP16, FP32 and FP32 without TF32, through `torch2trt` and through TensorRT's
   own API. The image half's engine is correct (cosine 0.999986). The text half now runs in
   PyTorch (~3–6 ms), as HOUND's design note ships it. Phase 0.6 missed this: its "FiLM
   probabilities" line compared PyTorch with PyTorch. **HOUND's SSoT points at a FiLM engine**
   (`clipseg_film_rd16_224_b3_p9.engine`); if their TensorRT does the same, their navigation
   segmentation is degraded. Worth telling them.
2. **SAM box prompts suit things, not stuff.** Given the box of a whole pavement, SAM returned a
   window above it. Free space now comes from CLIPSeg's traversable map; SAM only sharpens
   obstacles and people, joined with CLIPSeg's coarse obstacle map.
3. `docker exec` bypasses the image entrypoint, so `dev.sh` execs through it to source ROS.

### First run with the Femto Bolt (2026-10-23)

The Femto Bolt is fitted, and its driver runs on the robot's graph (domain 0, `/camera`, run by
another user for `hand3d`). `tools/camera_bridge.py` (`dev.sh bridge`) copies it one way into the
test domain as camera `femto`, so the server still publishes nothing on domain 0. The bridge needs
subnet discovery (with `LOCALHOST` it never finds the driver) and UDP only.

| Check | Result |
|---|---|
| Feed | Colour 1280×720 `rgb8` ~19 Hz; `/camera/depth/image_raw` is aligned to colour, `16UC1`, ~20 Hz, stamps within 2 ms; 72 % of depth valid, median 1.7 m |
| `wall` | One mask over the whole wall, edges on the door frame (overlay checked by eye); median depth 1.26–1.28 m, steady across queries. Server 53–100 ms, round trip 83–140 ms |
| `whiteboard` | The board, and the far wall through the door (3.9 m) |
| `person` | 0 with nobody in view; correct |
| `door` | Weak: YOLO-World finds no door, and the CLIPSeg fallback gives up to 16 blobs at ~0.4, just over the 0.4 threshold, many on the whiteboard. Open: a higher fallback threshold, or keep only regions near the label's best |
| Depth with the result | 7 of 10 queries in steady state. **Large frames are lost over UDP on this host**: `net.core.rmem_max` is 212992 and the kernel counts ~440 k UDP receive-buffer errors; the bridge itself misses ~45 % of depth frames from the driver. The server now uses the newest colour frame that has its depth (`frames.py`). Raising `rmem_max` is a host change, for the Thor stack lane |

### Our own driver (2026-10-23, Phase 3 started)

The user stopped the other driver; ours runs in `archit-perception-camera` (`tools/camera.sh`,
image `image/camera.Dockerfile`, OrbbecSDK_ROS2 v2.10.6) and publishes `/camera/...` on the robot's
graph (domain 0), so other nodes read it too. The server reads it through a subscribe-only
domain-0 context (`domain:` in the camera's config) and shares the driver container's memory.

| Finding | Result |
|---|---|
| Depth 3-4 px off colour | The Femto Bolt has no hardware alignment; the SDK aligns in software. Colour was published **distorted** while aligned depth uses the pinhole model (its camera_info has D = 0). Undistorting the saved colour frames took the offset from 4 px to ~1 px. Fix: `enable_color_undistortion:=true`; colour and depth now share one K with D = 0. After: the horizontal offset is ≤ 1 px; a vertical 3-6 px reading rests on few horizontal edges (black hair returns no depth), and needs an object with straight edges about 0.5 m away to settle |
| Driver took ~9 cores | The Femto Bolt computes depth on the host in an OpenGL depth engine; in a container without the NVIDIA runtime it ran on Mesa's software renderer (`libgallium`). With `--runtime nvidia` and all driver capabilities: ~135 % CPU, aligned depth ~22 Hz, 94 % of colour frames paired with depth (55 % before) |
| Depth with every query | **10 of 10** (7 of 10 over the UDP bridge, now removed) |
| Other readers | Fast DDS shared memory does not cross users (its files are 0644), and a node on the same host receives **nothing** unless it sets `FASTDDS_BUILTIN_TRANSPORTS=UDPv4`. Over UDP a non-root reader gets ~10-18 Hz colour and ~12 Hz depth: this host's `rmem_max` (208 KB) still drops large frames |
| `--ipc host` | Tried and dropped: a participant that died holding a Fast DDS lock in the host's `/dev/shm` left every later root participant hanging at creation. Driver and server now share a private segment (`--ipc shareable` / `container:`) |
| Firmware | The camera has 1.0.9; v2.10.6 recommends 1.1.3. Not updated |
| Driver frame rate | Fast DDS's default 512 KB shared-memory segment let the server, as a second reader, cut every reader to ~7 Hz. `image/fastdds_camera.xml` (128 MB segment) and `--shm-size 1g`: **30 Hz** colour and depth with the server reading |
| Camera latency | Capture stamp to arrival in the server's container: **~94 ms** median, p90 ~98 ms (depth engine, alignment, undistortion, frame sync). Over UDP to another user: 26-28 Hz but **~450 ms**; not yet explained |

### Latency, paused 2026-10-23 (robot shut down)

Per query, the server logs the frame's age at start and each stage; the client records capture to
result. Warm `wall` queries: server 120-720 ms, capture to result 280-900 ms. The stages are each
5-30 ms when quiet (detect ~25, CLIPSeg image ~6, text ~5, SAM encode ~10-15, decode ~5-15), but
any one of them spikes to 100-550 ms at random: GPU time-sharing. First query of a new label set
also pays its text features (~450-760 ms detect). Suspect: the camera's depth engine (OpenGL on
the GPU, 30 Hz) or the language model. Test half done: `person` on the dummy camera's still image
with the Femto driver running, median 195 ms (77-573); **the driver-stopped half is still to run**.
Then: a fair breakdown (camera ~94 ms + frame wait + models + transfer), and the UDP 450 ms.

## Phase 4: AnyGrasp (built 2026-10-24, robot not yet)

A label in, a grasp in the camera's optical frame out: action `grasp_object`
(`perception_msgs/action/GraspObject`), served by `perception_tasks`, a separate process from
`perception_server` (D5). It calls `segment`, takes the highest-scoring instance, and sends its
mask with the frame's colour, raw `16UC1` depth and K to AnyGrasp on the workstation through
`perception_clients/anygrasp.py`. `anygrasp_server/` is a server for the workstation that runs
beside the lab's original and takes the same request.

| Piece | Where |
|---|---|
| Client: erode the mask 2 px (time-of-flight edge pixels), check the workspace, crop to the mask's box and shift K, send, parse | `perception_clients/anygrasp.py` |
| The action and its node | `perception_tasks/`, `perception_msgs/action/GraspObject.action`, `msg/Grasp.msg` |
| Workstation server | `anygrasp_server/server.py`, `README.md` |
| Checks without the robot: a synthetic mug, or a saved query | `tools/anygrasp_probe.py` |
| On the robot | `ANYGRASP_URL=ws://HOST:8768/grasp dev.sh up`, then `dev.sh grasp mug --camera femto` (`tools/grasp_client.py`: overlay with the grasp's axes, `result.json`) |
| Geometry tests (crop keeps every point, erosion, quaternion) | `tests/test_anygrasp_client.py` |

Findings against the lab's original server (port 8767), from a laptop on the workstation's network:

| Check | Result |
|---|---|
| A full 1280x720 frame (7.4 MB as base64) | **Refused**, WebSocket close 1009 "message too big": aiohttp's default limit is 4 MB. Cropped to the mask's box: 301 kB, accepted |
| Synthetic mug 0.72 m away, 640x480 and 1280x720 | A grasp on the mug, width 0.10 m; round trip 553 ms cold, 150-190 ms warm |
| Mug at 1.6 m | Stopped by the client: "the object is 1.54 m away; AnyGrasp's workspace ends at 1.5 m" |
| Up direction | Without one, the original takes tilt 0, which treats the optical axis as up: it reported 63.7 deg of tilt for an approach about 9 deg from horizontal. Upright and tilt from it are wrong for a camera looking forward. The new server picks by raw score when it knows no up direction, and says so in `mode` |
| `perception_tasks` logic, with ROS stubbed | Segment-shaped messages (`bgr8`, `16UC1`, `mono8`) through `execute`: the same grasp as the probe; "nothing found" and "no depth" abort with their reason |

### On the robot (opendubs2, 2026-10-24)

The image was rebuilt (`websockets` in its own layer, the new `perception_msgs`), the Femto Bolt
driver and the server started with `ANYGRASP_URL` set; the workstation ran `anygrasp_server/` with
`--use_raw_best --top_down_grasp false`.

| Check | Result |
|---|---|
| Thor to the workstation | Port 8768 reachable; the probe from inside the image: synthetic mug grasped, 376 ms round trip |
| `dev.sh grasp chair --camera femto` | **The full chain from a name**: chair 0.50 (1 of 2), grasp on the chair back's lower-left edge at (-0.046, 0.344, 0.904) m, width 0.045 m. Segment 371 ms + AnyGrasp 163 ms; round trip 563 ms. Overlay checked by eye |
| A black mesh pen stand at 0.90 m | **Not detected** by YOLO-World under any name tried (mug, cup, pen holder, pencil cup, black cup, basket, trash can, bucket; also at score 0.05) nor by CLIPSeg ("black pen holder"); the chair's mask swallows it. Depth is fine (93 % valid at 0.90 m), but through the mesh its top reads 1.75 m, the wall behind. With a mask cut from depth by hand, AnyGrasp grasped it at (-0.098, 0.206, 0.891) m, on the stand, horizontal closing, 100 ms |
| Objects across the room | Everything else in view was 1.5-2 m away, beyond AnyGrasp's 1.5 m workspace; the client says so |
| Two bugs found and fixed | rclpy refuses a logging call site that changes severity (a success after a failure crashed before its result was set); `grasp_client.py` could not write the ROS `int32` box to JSON |

Still to run: a solid mug (not black, not mesh) 0.5-1 m away, on its own.

`anygrasp_server/server.py` was run here with a stand-in `gsnet` (the protocol, the lock, the up
logic, errors, a 7.4 MB request): with up known it chose the side grasp over a higher-scoring
top-down one. On the workstation with the real model it returns the original's grasp for the same
input, with candidates, and accepts the 7.4 MB frame.

### Next

| Phase | Work | Done when |
|---|---|---|
| **2b. Under load** | Run the server while the robot is in use (the language model answering) | Navigation and query numbers with the robot busy |
| **3. Camera and Nav2** | The Orbbec driver (D1), its owner and udev rules (with the Thor stack lane); the obstacle cloud into Nav2's costmap (Thor stack lane) | Masks line up with depth; the costmap shows semantic obstacles; the LIDARs' obstacles are never cleared |
| **4. AnyGrasp** | Built and run on the robot (above). Left: a solid mug 0.5-1 m away, on its own | The overlay's axes sit on the mug, and the pose's depth matches the mask's median depth |

## Decisions

| # | Decision | Owner | Status |
|---|---|---|---|
| D0 | Test on opendubs2 as `archit`, under the rules above | user | **Decided 2026-10-22** |
| D1 | Manipulation camera: **Orbbec Femto Bolt** (OrbbecSDK_ROS2 `v2-main` lists it). Navigation cameras: not fitted yet; the dummy camera stands in | user | **Decided 2026-10-22** |
| D2 | YOLO-World (AGPL-3.0) or an Apache-licensed detector: "whatever suits best and works". YOLO-World works and stays; the licence goes to the Hygiene lane before merging | user | **Decided 2026-10-22** |
| D3 | A perception lane owning `software/perception/`, this workplan and the design note. **Not announced until everything is built and validated on `archit@opendubs2`** | user | **Decided 2026-10-22** |
| D4 | Base image: Isaac ROS 4.4 for r38.4 by digest (above). Tell the Thor stack lane, since it may answer their `ISAAC_ROS_BASE_IMAGE` | user, Thor stack lane | Proposed |
| D5 | Off-robot models get plain-Python clients in `perception_clients/`; tasks that combine services are actions in `perception_tasks/`, a process apart from `perception_server`, which keeps only the GPU models on the Thor | user | **Decided 2026-10-24** |
| D6 | Grasps are returned in the camera's optical frame; no TF to the robot's base for now. The goal's optional `up_cam` lets an accelerometer improve the choice later | user | **Decided 2026-10-24** |

## Messages to send (only once everything is validated, D3)

- **Orchestrator:** perception lane request (D3), and the results from the test containers run on
  opendubs2 under `archit`, with no host changes (D0).
- **HOUND's maintainers:** the CLIPSeg FiLM TensorRT finding (Phase 1, finding 1).
- **Thor stack lane:** the Isaac ROS 4.4 r38.4 base digest, the finding that JetPack 7's runtime
  mounts only devices and drivers, and the host's CUDA/cuDNN/TensorRT versions (D4).
