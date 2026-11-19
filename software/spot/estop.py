"""A person's E-Stop for Spot, on the Mac's keyboard. Run it in the separate Terminal app and keep it
in front of you; it is the stop for everything the driver does.

    uv run --no-project --with 'bosdyn-client==5.1.4' python software/spot/estop.py [SPOT_HOST]

It registers as Spot's only E-Stop endpoint and checks in every second or so. It starts STOPPED.
  r       release: allow Spot to be powered
  any other key, or Ctrl-C:   STOP (Spot sits, then power is cut)
If this program dies, the Mac sleeps or the cable drops, the check-ins stop and Spot stops within the
timeout, as a lost tablet would. Leave it running until Spot is sitting with its motors off.
"""
import sys
import termios
import time
import tty

import bosdyn.client
from bosdyn.client.estop import EstopClient, EstopEndpoint, EstopKeepAlive

from spot_credentials import load

TIMEOUT_S = 9.0

host = sys.argv[1] if len(sys.argv) > 1 else "192.168.50.3"
robot = bosdyn.client.create_standard_sdk("opendubs-person-estop").create_robot(host)
user, password = load()
robot.authenticate(user, password)
del password
robot.time_sync.wait_for_sync()

client = robot.ensure_client(EstopClient.default_service_name)
endpoint = EstopEndpoint(client, name="opendubs-person-estop", estop_timeout=TIMEOUT_S)
endpoint.force_simple_setup()          # this endpoint becomes Spot's only E-Stop
keep = EstopKeepAlive(endpoint)
keep.settle_then_cut()                 # start stopped
print("E-Stop registered, STOPPED. Press r to release, any other key to STOP.\r")

fd = sys.stdin.fileno()
saved = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    while True:
        key = sys.stdin.read(1)
        if key == "r":
            keep.allow()
            print(f"{time.strftime('%H:%M:%S')} RELEASED: Spot may be powered. Any key to STOP.\r")
        else:
            keep.settle_then_cut()
            print(f"{time.strftime('%H:%M:%S')} STOPPED (sit, then cut). r to release.\r")
            if key in ("\x03", "\x04"):     # Ctrl-C / Ctrl-D: stop, then quit
                break
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    keep.settle_then_cut()
    print("Stopped. Leaving the E-Stop registered and stopped; check-ins end now.")
    keep.shutdown()
