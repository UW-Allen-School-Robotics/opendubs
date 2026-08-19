#!/usr/bin/env python3
"""Headless Quest-teleop SIMULATOR.

Publishes the /quest/* topic contract to rosbridge so the robot side can be
verified WITHOUT the headset — a smoke test before building the Unity app, and a
standing test client afterward. Speaks the same rosbridge path the real Unity
ros-sharp client uses, so "topics show up correctly here" proves the robot half.

Needs roslibpy (`pip install roslibpy`). Usage:
    python3 sim_quest_client.py [host] [port] [seconds]
    (defaults: localhost 9090 6)
"""
import math
import sys
import time

import roslibpy

host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 9090
dur = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0

client = roslibpy.Ros(host=host, port=port)
client.run()

hmd = roslibpy.Topic(client, "/quest/hmd/pose", "geometry_msgs/PoseStamped")
joy = roslibpy.Topic(client, "/quest/controller/right/joy", "sensor_msgs/Joy")
hmd.advertise()
joy.advertise()

t0 = time.time()
i = 0
while time.time() - t0 < dur and client.is_connected:
    t = time.time() - t0
    stamp = {"sec": int(time.time()), "nanosec": 0}
    hmd.publish(roslibpy.Message({
        "header": {"stamp": stamp, "frame_id": "quest_hmd"},
        "pose": {"position": {"x": math.sin(t), "y": 1.3, "z": math.cos(t)},
                 "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}}}))
    joy.publish(roslibpy.Message({
        "header": {"stamp": stamp, "frame_id": "quest_controller_right"},
        "axes": [0.0, 0.0, 0.0, 0.0], "buttons": [i % 2, 0, 0, 0]}))
    i += 1
    time.sleep(0.1)

hmd.unadvertise()
joy.unadvertise()
client.terminate()
print(f"published {i} frames to {host}:{port}")
