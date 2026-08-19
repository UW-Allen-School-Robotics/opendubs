#!/usr/bin/env python3
"""RPLIDAR liveness watchdog + (optional) motor-enable owner.

The A1 can hit a serial RESULT_OPERATION_TIMEOUT (USB/serial glitch, unplug) OR
stall at the SDK handshake when two units start at once on a marginal hub — either
way it publishes nothing while the driver process stays alive, so systemd
Restart=always never fires. This watchdog restarts the driver when its scan topic
goes stale.

ENV-DRIVEN so ONE script runs per lidar:
  OPENDUBS_WD_TOPIC       scan topic to watch          (default /scan)
  OPENDUBS_WD_SERVICE     systemd unit to restart      (default opendubs-rplidar)
  OPENDUBS_WD_NODE        rclpy node name (unique!)    (default scan_watchdog)
  OPENDUBS_WD_OWN_ENABLE  1 = own /lidar/enable + motor stop (dashboard STOP SPIN);
                          0 = restart-on-stale only    (default 1)

A stale scan is ambiguous with an intentional motor STOP, so when it owns the
enable it only restarts while the motor is *supposed* to be running (desired_on).
Runs as the driver's user (emmar) so DDS matches; restart needs the sudoers rule.
"""
import os
import subprocess
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import LaserScan
from std_srvs.srv import Empty, SetBool

SERVICE = os.environ.get("OPENDUBS_WD_SERVICE", "opendubs-rplidar")
TOPIC = os.environ.get("OPENDUBS_WD_TOPIC", "/scan")
NODE = os.environ.get("OPENDUBS_WD_NODE", "scan_watchdog")
OWN_ENABLE = os.environ.get("OPENDUBS_WD_OWN_ENABLE", "1") == "1"
STALE_S = float(os.environ.get("OPENDUBS_WD_STALE_S", "5.0"))   # scan dead after this many s w/o a message
CHECK_S = 2.0     # evaluation period
# Quiet window after a restart/motor (re)start. MUST exceed the unit's ExecStartPre stagger +
# SDK init or the watchdog re-fires as the driver starts publishing → restart loop. The REAR
# unit staggers 15s, so its watchdog sets OPENDUBS_WD_GRACE_S=40 (see its service Environment).
GRACE_S = float(os.environ.get("OPENDUBS_WD_GRACE_S", "20.0"))


class ScanWatchdog(Node):
    def __init__(self):
        super().__init__(NODE)
        now = time.monotonic()
        self.last = now
        self.muted_until = now + GRACE_S
        self.desired_on = True   # motor should be running unless told otherwise

        self.create_subscription(LaserScan, TOPIC, self._on_scan, qos_profile_sensor_data)
        self.create_timer(CHECK_S, self._check)

        self._stop_cli = None
        if OWN_ENABLE:
            # Own the motor enable/disable so we know the desired state (dashboard STOP SPIN).
            self.create_service(SetBool, "/lidar/enable", self._on_enable)
            self._stop_cli = self.create_client(Empty, "/stop_motor")

        self.get_logger().info(
            f"watchdog[{NODE}]: restart {SERVICE} if {TOPIC} stale >{STALE_S:.0f}s"
            f"{' (owns /lidar/enable)' if OWN_ENABLE else ' (restart-only)'}")

    def _on_scan(self, _msg):
        self.last = time.monotonic()

    def _call(self, cli):
        if cli and cli.wait_for_service(timeout_sec=2.0):
            cli.call_async(Empty.Request())

    def _restart(self):
        subprocess.run(["sudo", "/usr/bin/systemctl", "restart", SERVICE], check=False)

    def _on_enable(self, req, resp):
        self.desired_on = bool(req.data)
        now = time.monotonic()
        if self.desired_on:
            self._restart()   # /start_motor doesn't reliably resume; a driver relaunch does
            self.muted_until = now + GRACE_S
            self.last = now
        else:
            self._call(self._stop_cli)
        self.get_logger().info(f"/lidar/enable -> {'ON' if self.desired_on else 'OFF'}")
        resp.success = True
        resp.message = "on" if self.desired_on else "off"
        return resp

    def _check(self):
        now = time.monotonic()
        age = now - self.last
        if OWN_ENABLE and not self.desired_on:
            if age < STALE_S:
                self._call(self._stop_cli)   # re-assert an intentional stop; never restart
            self.last = now
            return
        if now < self.muted_until:
            return
        if age > STALE_S:
            self.get_logger().warn(f"{TOPIC} stale {age:.1f}s — restarting {SERVICE}")
            self._restart()
            self.muted_until = now + GRACE_S
            self.last = now


def main():
    rclpy.init()
    node = ScanWatchdog()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
