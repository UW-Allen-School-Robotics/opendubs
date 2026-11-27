"""Read-only: ask Spot for its own joint limits and its sound-and-light hardware. Motors stay off.

    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' python software/spot/read_limits.py [SPOT_HOST]

- Spot's hardware configuration carries the robot's own URDF. This prints every joint's limits from it
  (position, velocity, effort) as JSON for ENCORE's Spot profile, and saves the URDF outside the repo,
  in ~/.opendubs-spot/limits/. It changes nothing on Spot.
- It also says whether Spot has the AudioVisual system (its LEDs and buzzer), and lists the
  directory's audio, light and Spot CAM services.
Body ranges (height, pitch, roll) and speed caps are not in the URDF: they come from Boston Dynamics'
mobility parameters and from caps we choose, as opendubs2's base cap is chosen.
"""
import json
import os
import sys
import xml.etree.ElementTree as ET

import bosdyn.client
from bosdyn.client.directory import DirectoryClient
from bosdyn.client.robot_state import RobotStateClient

from spot_credentials import load

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.50.3"
robot = bosdyn.client.create_standard_sdk("opendubs-spot-limits").create_robot(host)
serial = robot.get_id().serial_number
user, password = load()
robot.authenticate(user, password)
del password

hw = robot.ensure_client(RobotStateClient.default_service_name).get_robot_hardware_configuration()
urdf = hw.skeleton.urdf
out_dir = os.path.expanduser("~/.opendubs-spot/limits")
os.makedirs(out_dir, exist_ok=True)
path = os.path.join(out_dir, f"spot-{serial}.urdf")
with open(path, "w") as f:
    f.write(urdf)

joints = {}
for j in ET.fromstring(urdf).iter("joint") if urdf else []:
    lim = j.find("limit")
    if lim is not None and j.get("type") != "fixed":
        joints[j.get("name")] = {k: float(lim.get(k)) for k in ("lower", "upper", "velocity", "effort")
                                 if lim.get(k) is not None}
print(json.dumps({"serial": serial, "urdf_saved": path, "joints": joints}, indent=1))

print("has AudioVisual system (LEDs, buzzer):", hw.has_audio_visual_system)
for s in robot.ensure_client(DirectoryClient.default_service_name).list():
    if any(w in s.type.lower() or w in s.name.lower() for w in ("audio", "visual", "light", "led", "spot-cam")):
        print(f"service: {s.name} ({s.type})")
print("Done. Nothing was powered, leased or moved.")
