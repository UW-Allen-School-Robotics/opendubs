#!/usr/bin/env python3
"""OpenDubs StampPLC UDP -> ROS 2 bridge.

The StampPLC broadcasts a compact JSON status on UDP 255.255.255.255:5005 (~5 Hz).
This node listens for it and republishes as ROS topics + a diagnostic_msgs array,
so nothing on the robot needs a USB tether to the StampPLC. Replaces the old
serial/HTTP `stamplc-bridge`.

Topics published:
  /stamplc/json      std_msgs/String            raw JSON (anything can parse it)
  /stamplc/rc_mode   std_msgs/String            OFF|MANUAL|AUTO|ESTOP|FAILSAFE|NOLINK
  /stamplc/estopped  std_msgs/Bool              io.estop
  /stamplc/online    std_msgs/Bool              true while packets are arriving
  /diagnostics       diagnostic_msgs/DiagnosticArray
"""
import json
import socket

import rclpy
from rclpy.node import Node
from std_msgs.msg import String, Bool
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

UDP_PORT = 5005
STALE_NS = int(1.5e9)   # mark offline if no packet for 1.5 s


class StampPLCBridge(Node):
    def __init__(self):
        super().__init__('stamplc_udp_bridge')
        self.pub_json = self.create_publisher(String, 'stamplc/json', 10)
        self.pub_mode = self.create_publisher(String, 'stamplc/rc_mode', 10)
        self.pub_estop = self.create_publisher(Bool, 'stamplc/estopped', 10)
        self.pub_online = self.create_publisher(Bool, 'stamplc/online', 10)
        self.pub_diag = self.create_publisher(DiagnosticArray, 'diagnostics', 10)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setblocking(False)
        self.sock.bind(('', UDP_PORT))

        self.last_rx = None
        self.last = None
        self.create_timer(0.05, self._poll)             # drain socket at 20 Hz
        self.create_timer(0.5, self._publish_status)    # status + diagnostics at 2 Hz
        self.get_logger().info(f'listening for StampPLC JSON on UDP :{UDP_PORT}')

    def _poll(self):
        latest = None
        while True:
            try:
                data, _ = self.sock.recvfrom(2048)
                latest = data
            except (BlockingIOError, InterruptedError, OSError):
                break
        if latest is None:
            return
        text = latest.decode('utf-8', 'replace')
        try:
            j = json.loads(text)
        except ValueError:
            return
        self.last_rx = self.get_clock().now()
        self.last = j
        self.pub_json.publish(String(data=text))
        rc = j.get('rc', {})
        io = j.get('io', {})
        self.pub_mode.publish(String(data=str(rc.get('mode', '?'))))
        self.pub_estop.publish(Bool(data=bool(io.get('estop', False))))

    def _publish_status(self):
        now = self.get_clock().now()
        online = self.last_rx is not None and (now - self.last_rx).nanoseconds < STALE_NS
        self.pub_online.publish(Bool(data=online))

        st = DiagnosticStatus(name='StampPLC', hardware_id='stamplc-udp')
        if not online:
            st.level = DiagnosticStatus.STALE
            st.message = 'no UDP telemetry'
        else:
            j = self.last or {}
            rc, io, pwr = j.get('rc', {}), j.get('io', {}), j.get('pwr', {})
            if io.get('estop'):
                st.level, st.message = DiagnosticStatus.ERROR, 'E-STOP'
            elif rc.get('mode') in ('NOLINK', 'FAILSAFE'):
                st.level, st.message = DiagnosticStatus.WARN, 'RC ' + str(rc.get('mode'))
            else:
                st.level, st.message = DiagnosticStatus.OK, str(rc.get('mode', '?'))
            for k, v in (('mode', rc.get('mode')), ('rc_live', rc.get('live')), ('deadman', rc.get('dead')),
                         ('fwd_rev', rc.get('fr')), ('rotate', rc.get('rot')), ('strafe', rc.get('str')),
                         ('mast', rc.get('mast')), ('di', io.get('di')), ('relay', io.get('relay')),
                         ('estop', io.get('estop')), ('bus_v', pwr.get('busv')), ('pdh_v', pwr.get('pdh_v')),
                         ('pdh_w', pwr.get('pdh_w')), ('temp_c', pwr.get('temp')),
                         ('seq', j.get('seq')), ('up_s', j.get('up_s'))):
                st.values.append(KeyValue(key=str(k), value=str(v)))
        arr = DiagnosticArray()
        arr.header.stamp = now.to_msg()
        arr.status = [st]
        self.pub_diag.publish(arr)


def main():
    rclpy.init()
    node = StampPLCBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
