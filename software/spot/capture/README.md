# Recording a Spot walk for HallPass

These scripts run on the Jetson Orin carried by Spot. They only read and record. They send Spot no
commands, and none of them asks for a password. Spot is walked by a person with Spot's own controller,
and the arm stays stowed, carrying the Ouster.

1. **`c1-check.sh`** prints what the Orin has: its ROS 2 and recording tools, any earlier Spot or Ouster
   setup, free disk, the topics and frames, the Ouster's mode, and PTP. It changes nothing. Send its
   output to the Spot lane, which replies with the exact `record-walk.sh` line for that day.
2. **`record-walk.sh DATA_ROOT CAPTURE_ID`** records the walk into `DATA_ROOT/CAPTURE_ID/bag` (MCAP), with
   spot_ros2 and the Ouster driver already running. Ctrl-C stops it and writes `SHA256SUMS`.
3. **`mark.sh DATA_ROOT/CAPTURE_ID "roof: at the door"`** adds a mark, optionally. Marks can also be
   added afterwards, from photo times.

Afterwards the capture is copied off the Orin by wire, never over Wi-Fi, and split into HallPass's
`light/`, `lidar/` and `heavy/` parts with its `capture.yaml` (the contract `opendubs.capture/1`).
At full camera rate a walk takes about 50 GB per half hour.
