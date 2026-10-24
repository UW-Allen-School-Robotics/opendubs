# AnyGrasp server (for the GPU workstation)

AnyGrasp predicts parallel-jaw grasps from an object's point cloud. It needs an NVIDIA GPU, the
AnyGrasp SDK (its `gsnet` module) and a licence from its authors, so it runs on a workstation, not
on the robot. The robot reaches it over a WebSocket from `perception_tasks` (action
`grasp_object`): the perception server segments the object, and this server returns grasps in the
camera's optical frame.

`server.py` was written from the lab's original AnyGrasp server and takes the same request, so the
robot's client (`perception_clients/anygrasp.py`) works with either. This one runs one request at a
time, returns real error messages, accepts messages up to 64 MB, returns candidates and how the
best was chosen, and has a `/health` route. Its docstring lists the request and reply in full.

## Run

In the environment where the AnyGrasp SDK already runs (`gsnet` importable, licence in place), with
`aiohttp` and `numpy` (and `matplotlib` for `--save_vis`):

```sh
python server.py --checkpoint_path log/checkpoint_detection.tar --port 8768
curl http://localhost:8768/health
```

It can run beside the original server (port 8767): each loads its own copy of the model.

On the robot: `ANYGRASP_URL=ws://HOST:8768/grasp tools/dev.sh up`, then
`tools/dev.sh grasp mug --camera femto`.

Without the robot, from any machine with `numpy` and `websockets`:

```sh
python3 tools/anygrasp_probe.py --url ws://HOST:8768/grasp   # a synthetic mug on a table
```

## How the best grasp is chosen

AnyGrasp's top 20 grasps (after non-maximum suppression) are candidates.

- **Up direction known** (the request's `options.up_cam` or `cam_to_base_quat`, or
  `--camera_tilt_rad`): `combined = score × upright²`, among grasps with a score of at least 0.01
  and a width of at least 5 cm. `upright` is 1 when the fingers close horizontally on a side grasp.
- **Up direction unknown:** the best raw score. A tilt of 0 would treat the camera's optical axis
  as up, which is wrong for a camera looking forward, so no upright or tilt is reported.

`flip` rotates the chosen grasp 180° about its approach axis, which swaps the jaws and moves only
the wrist: `away` (binormal away from the camera, the default for the raw mode), `right`, `none`.

## Limits

- **Workspace:** points must lie within 1.5 m of the camera along its axis and 1 m sideways
  (`options.lims` changes it). The client checks this first and says how far the object is.
- **At least 50 object points with depth.** Dark, shiny or transparent objects often return no
  depth from a time-of-flight camera.
