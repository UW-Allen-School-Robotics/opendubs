"""Rollout step 2: power Spot on and stand it, or sit it and power off. One GO per run.

    uv run --no-project --with 'bosdyn-client==5.1.4' python software/spot/stand.py [--sit] [SPOT_HOST]

It acquires the lease only if nobody holds it (never takes it from the tablet), refuses unless a
person's E-Stop is registered and released, and returns the lease when it exits. Standing, Spot stays
standing with its motors on; the person's E-Stop, or `stand.py --sit`, ends that.
"""
import sys

import bosdyn.client
from bosdyn.client.estop import EstopClient
from bosdyn.client.lease import LeaseClient, LeaseKeepAlive
from bosdyn.client.robot_command import RobotCommandClient, blocking_sit, blocking_stand

from spot_credentials import load

args = [a for a in sys.argv[1:] if not a.startswith("--")]
sit = "--sit" in sys.argv
host = args[0] if args else "192.168.50.3"

robot = bosdyn.client.create_standard_sdk("opendubs-spot-stand").create_robot(host)
user, password = load()
robot.authenticate(user, password)
del password
robot.time_sync.wait_for_sync()

status = robot.ensure_client(EstopClient.default_service_name).get_status()
if not status.endpoints:
    sys.exit("refused: no E-Stop endpoint. Start estop.py in the separate Terminal app first.")
if robot.is_estopped():
    sys.exit("refused: Spot is E-Stopped. Press r in estop.py to release it, then run again.")

lease_client = robot.ensure_client(LeaseClient.default_service_name)
with LeaseKeepAlive(lease_client, must_acquire=True, return_at_exit=True):
    cmd = robot.ensure_client(RobotCommandClient.default_service_name)
    if sit:
        blocking_sit(cmd, timeout_sec=10)
        robot.power_off(cut_immediately=False, timeout_sec=20)
        print("Spot is sitting, motors off.")
    else:
        robot.power_on(timeout_sec=20)
        blocking_stand(cmd, timeout_sec=10)
        print("Spot is standing, motors on. Lease returned on exit; it keeps standing.")
