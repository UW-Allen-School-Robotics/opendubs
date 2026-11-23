# Dora Evaluation (`modularization(4/5)`)

Isolated, opt-in evaluation of [dora-rs](https://dora-rs.ai/) on one concrete path
(`robot_state` + high-rate control/SysID loop latency), with a ROS 2 bridge so existing
ROS 2 consumers (`foxglove_bridge`, `rosbridge`, `nav2`, `slam_toolbox`) stay working.

Nothing here is started at boot or deployed by `provision-all.sh` by default; `demo`
and all existing ROS 2 services remain untouched.

## Setup (Thor or Dev Machine)

Because `ros2_bridge_node.py` imports both `dora`/`pyarrow` and ROS 2 (`rclpy`), use a `--system-site-packages` virtualenv on Ubuntu 24.04 (PEP 668) with ROS 2 sourced:

```bash
python3 -m venv --system-site-packages ~/.venvs/opendubs-dora
source ~/.venvs/opendubs-dora/bin/activate
pip install dora-rs dora-rs-cli pyarrow
```

## Files

- [`../../robots/profiles/dora-eval.yaml`](../../robots/profiles/dora-eval.yaml) — Opt-in robot profile (`OPENDUBS_PROFILE=dora-eval`) that keeps `opendubs-dash` running while stopping `opendubs-stamplc-bridge`, `opendubs-robot-state`, and tracker/autonomous stacks.
- [`dataflow.yml`](dataflow.yml) — Dora graph: `robot_state_source` → `ros2_bridge`.
- [`nodes/robot_state_node.py`](nodes/robot_state_node.py) — Reads `stamplc.reader` (or mock in bench mode) and reuses `software/ros/robot_state.build_robot_state()` to emit `/robot/state` JSON over Dora without duplicating safety logic.
- [`nodes/ros2_bridge_node.py`](nodes/ros2_bridge_node.py) — Subscribes to Dora `robot_state` and republishes `/robot/state`, `/robot/estopped`, `/robot/motion_enabled`, and `/robot/rc` onto ROS 2, while forwarding `/robot/estop` (`std_srvs/Trigger`) back to `robot_state_source/estop` so software e-stop keeps working in hybrid/Dora mode.
- [`bench_latency.py`](bench_latency.py) — Measures cross-process round-trip latency and jitter (`p50`, `p95`, `p99`, `max`, deadline misses at 200/500 Hz) via `--transport pipe` or `--transport ros2`.

## Running the Benchmark

```bash
# Baseline pipe/IPC and ROS 2 cross-process checks
python3 software/dora/bench_latency.py --transport pipe --hz 200 --samples 200
python3 software/dora/bench_latency.py --transport ros2 --hz 200 --samples 200

# Validate and run Dora dataflow locally (defaults to OPENDUBS_DORA_MOCK=1 so it won't conflict with opendubs-dash)
dora validate software/dora/dataflow.yml
dora run software/dora/dataflow.yml
```

> **Single-writer safety:** `dataflow.yml` sets `OPENDUBS_DORA_MOCK: "1"` by default so running it alongside `opendubs-dash` never causes a two-writer conflict on the StamPLC serial/UDP port. To test against live StamPLC hardware, stop `opendubs-dash`, `opendubs-stamplc-bridge`, and `opendubs-robot-state` first, then set `OPENDUBS_DORA_MOCK: "0"`.
