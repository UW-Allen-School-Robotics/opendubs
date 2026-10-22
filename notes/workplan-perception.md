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
| 0.0 | | |
| 0.2 | | |
| 0.3 | | |
| 0.5 | | |
| 0.6 | | |
| 0.7 | | |

## Later phases

| Phase | Work | Done when |
|---|---|---|
| **1. Server, navigation only** | The single-process server (`software/perception/`): ingress, scheduler, the three models, the navigation adapter. Fed from recorded images or a bag | A steady navigation rate on one camera, measured with the language model running, at 120 W. Target to confirm: ≥ 10 Hz |
| **2. Queries** | The `Segment` action and its interface package; per-instance NanoSAM decode; the per-request YOLO-World path; packaging the same frame's depth and `camera_info` | Queries are answered while the navigation stream runs, and the navigation rate still holds. Target to confirm: under 300 ms per query on the Thor |
| **3. Camera and Nav2** | The Orbbec driver (D1), its owner and udev rules (with the Thor stack lane); the obstacle cloud into Nav2's costmap (Thor stack lane) | Masks line up with depth; the costmap shows semantic obstacles; the LIDARs' obstacles are never cleared |
| **4. AnyGrasp** | A WebSocket client in the container: a query's masks and depth become a point cloud, sent to the workstation, which returns grasp poses | A "mug" query returns a grasp list |

## Decisions

| # | Decision | Owner | Status |
|---|---|---|---|
| D0 | Test on opendubs2 as `archit`, under the rules above | user | **Decided 2026-10-22** |
| D1 | Orbbec model; which camera serves navigation, and its mount and tilt | user | Open |
| D2 | YOLO-World (AGPL-3.0) or an Apache-licensed detector (NanoOWL / OWLv2) | user, with the Hygiene lane | Open |
| D3 | A perception lane owning `software/perception/`, this workplan and the design note; its CI workflow | orchestrator | Open |
| D4 | Base image: Isaac ROS 4.4 for r38.4 by digest (above). Tell the Thor stack lane, since it may answer their `ISAAC_ROS_BASE_IMAGE` | user, Thor stack lane | Proposed |

## Messages to send (after Phase 0)

- **Orchestrator:** perception lane request (D3), and the Phase 0 result from the test container
  run on opendubs2 under `archit`, with no host changes (D0).
- **Thor stack lane:** the Isaac ROS 4.4 r38.4 base digest, the finding that JetPack 7's runtime
  mounts only devices and drivers, and the host's CUDA/cuDNN/TensorRT versions (D4).
