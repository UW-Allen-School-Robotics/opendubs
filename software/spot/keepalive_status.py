"""Read-only: list Spot's Keepalive policies, to see which client is holding the motors off. Motors stay off.

    uv run --no-project --python 3.12 --with 'bosdyn-client==5.1.4' python software/spot/keepalive_status.py [SPOT_HOST]

Spot refuses to power on ("KeepaliveMotorsOffError: Cannot power on while Keepalive requests motors off")
while any client's Keepalive policy has fired a motors-off action: its client stopped checking in for
longer than the policy allows. The player registers no Keepalive policy, so the policy belongs to
another client, such as the tablet. This prints each policy: its client, its user, how long since it
last checked in, and its actions with their timeouts. It also prints the control actions now in force.
It changes nothing on Spot; the fix (bring that client back, or remove its policy) is a separate step.
"""
import sys
import time

import bosdyn.client
from bosdyn.api.keepalive import keepalive_pb2
from bosdyn.client.keepalive import KeepaliveClient
from bosdyn.util import duration_to_seconds, timestamp_to_sec

from spot_credentials import load

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.50.3"
robot = bosdyn.client.create_standard_sdk("opendubs-spot-keepalive").create_robot(host)
user, password = load()
robot.authenticate(user, password)
del password
robot.start_time_sync()
robot.time_sync.wait_for_sync()
now_robot = timestamp_to_sec(robot.time_sync.robot_timestamp_from_local_secs(time.time()))

status = robot.ensure_client(KeepaliveClient.default_service_name).get_status()
print(f"{len(status.status)} Keepalive policies")
for live in status.status:
    p = live.policy
    age = now_robot - timestamp_to_sec(live.last_checkin) if live.last_checkin.seconds else None
    print(f"- policy {live.policy_id} {p.name!r}: client {live.client_name!r}, user {p.user_id!r}, "
          f"last check-in {'never' if age is None else f'{age:.0f} s ago'}")
    for a in p.actions:
        print(f"    after {duration_to_seconds(a.after):.0f} s: {a.WhichOneof('action')}")
print("control actions in force now:", [keepalive_pb2.GetStatusResponse.PolicyControlAction.Name(c) for c in status.active_control_actions] or "none")
print("Done. Nothing was powered, leased or changed.")
