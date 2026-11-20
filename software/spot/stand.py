"""Rollout step 2: power Spot on and stand it, hold the stand, then sit and power off. One GO per run.

    uv run --no-project --with 'bosdyn-client==5.1.4' python software/spot/stand.py [SPOT_HOST]

It acquires the lease only if nobody holds it (never takes it from the tablet) and refuses unless a
person's E-Stop is registered and released. Spot stands for as long as this program holds the lease.
Ctrl-C or SIGTERM sits Spot and powers it off. Spot 5.1.5 sat and powered off by itself a second or
two after the lease was returned with its motors on (2026-11-19), so if this program dies Spot sits;
the person's E-Stop stays the backstop.
"""
import signal
import sys

import bosdyn.client
from bosdyn.client.estop import EstopClient
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive
from bosdyn.client.robot_command import RobotCommandClient, blocking_sit, blocking_stand

from spot_credentials import load

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.50.3"
robot = bosdyn.client.create_standard_sdk("opendubs-spot-stand").create_robot(host)
user, password = load()
robot.authenticate(user, password)
del password
robot.time_sync.wait_for_sync()

if not robot.ensure_client(EstopClient.default_service_name).get_status().endpoints:
    sys.exit("refused: no E-Stop endpoint. Start estop.py in the separate Terminal app first.")
if robot.is_estopped():
    sys.exit("refused: Spot is E-Stopped. Press r in estop.py to release it, then run again.")


def to_interrupt(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, to_interrupt)
lease_client = robot.ensure_client(LeaseClient.default_service_name)
with LeaseKeepAlive(lease_client, must_acquire=True, return_at_exit=True):
    cmd = robot.ensure_client(RobotCommandClient.default_service_name)
    try:
        robot.power_on(timeout_sec=20)
        blocking_stand(cmd, timeout_sec=10)
        print("Spot is standing, motors on, lease held. Ctrl-C or SIGTERM to sit.", flush=True)
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        print("Sitting and powering off.", flush=True)
        blocking_sit(cmd, timeout_sec=10)
        robot.power_off(cut_immediately=False, timeout_sec=20)
        print("Spot is sitting, motors off.", flush=True)
