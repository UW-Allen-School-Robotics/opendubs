"""Rollout step 1, read-only: log in to Spot and report what it says. Motors stay off.

It reads Spot's identity and software release, logs in with the Keychain login (macOS asks you to
allow the read), syncs clocks, then reads the power, battery and E-Stop state and who holds the lease.
It never powers on, never takes the lease, never registers an E-Stop and sends no motion.

    uv run --no-project --with 'bosdyn-client==5.1.4' python software/spot/check_login.py [SPOT_HOST]
    # SPOT_HOST defaults to 192.168.50.3 (the payload network). The SDK is pinned at or below Spot's
    # release (5.1.5 on 2026-11-19; PyPI has no 5.1.5, so 5.1.4).
"""
import importlib.metadata
import sys

import bosdyn.client
from bosdyn.client.estop import EstopClient
from bosdyn.client.lease import LeaseClient
from bosdyn.client.robot_state import RobotStateClient

from spot_credentials import load

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.50.3"
sdk = bosdyn.client.create_standard_sdk("opendubs-spot-check")
robot = sdk.create_robot(host)

rid = robot.get_id()                          # no login needed
sw = rid.software_release.version
print(f"Spot at {host}: {rid.nickname or rid.computer_serial_number}, release "
      f"{sw.major_version}.{sw.minor_version}.{sw.patch_level} ({rid.software_release.name})")
print(f"bosdyn-client here: {importlib.metadata.version('bosdyn-client')}")

user, password = load()
robot.authenticate(user, password)
del password
robot.time_sync.wait_for_sync()
print(f"logged in as {user!r}; clock offset known: {robot.time_sync.has_established_time_sync}")

state = robot.ensure_client(RobotStateClient.default_service_name).get_robot_state()
ps = state.power_state
print("motor power:", ps.MotorPowerState.Name(ps.motor_power_state))
for b in state.battery_states:
    print(f"battery {b.identifier}: {b.charge_percentage.value:.0f} %")
for e in state.estop_states:
    print(f"e-stop {e.name} ({e.Type.Name(e.type)}): {e.State.Name(e.state)}")

st = robot.ensure_client(EstopClient.default_service_name).get_status()
print(f"e-stop endpoints registered: {len(st.endpoints)}; stop level {st.stop_level}")
for r in robot.ensure_client(LeaseClient.default_service_name).list_leases():
    owner = r.lease_owner.client_name or "nobody"
    print(f"lease {r.resource}: held by {owner}")
print("Done. Nothing was powered, leased or moved.")
