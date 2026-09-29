#!/usr/bin/env python3
"""StamPLC → ROS 2 bridge.

The kiosk dashboard is the SINGLE owner of the StamPLC serial link. This node makes
the StamPLC's sensed values + controls available in ROS/Foxglove WITHOUT a serial
conflict: it polls the dashboard's lightweight `/api/stamplc` endpoint and publishes
ROS topics, and advertises services that POST to the dashboard's command endpoints
(which already enforce the safety rules — e.g. only relays 2/3 are free toggles).

Topics published:
  /stamplc/diagnostics      diagnostic_msgs/DiagnosticArray  (labeled DI/relay/safety/PDH/encoder)
  /stamplc/bus_voltage      std_msgs/Float32   (logic-bus volts)
  /stamplc/temperature      std_msgs/Float32   (board LM75B °C)
  /stamplc/mast_encoder     std_msgs/Int32     (ExtEncoder count)
  /stamplc/estopped         std_msgs/Bool
  /stamplc/motion_enabled   std_msgs/Bool
  /stamplc/pdh/{voltage,current,power}  std_msgs/Float32

Services advertised (std_srvs):
  /stamplc/estop            Trigger  → POST /api/estop          (software e-stop, SAFE direction)
  /stamplc/estop_clear      Trigger  → POST /api/estop/clear    (re-arm, guarded by dashboard)
  /stamplc/enc_reset        Trigger  → POST /api/enc/reset      (home/zero mast encoder)
  /stamplc/i2c_scan         Trigger  → POST /api/i2c/scan
  /stamplc/relay2           SetBool  → POST /api/relay/2/{on,off}
  /stamplc/relay3           SetBool  → POST /api/relay/3/{on,off}
  /stamplc/pdh_switch       SetBool  → POST /api/pdh/{on,off}

Interim bridge — the long-term clean path is micro-ROS on the ESP32-S3 (see
docs/hardening-roadmap.md). This gets the StamPLC into ROS now with NO firmware
change and NO dashboard rework, and keeps the dashboard as the only serial owner.
"""
import json
import os
import urllib.request

import rclpy
from rclpy.node import Node
from std_msgs.msg import Bool, Float32, Int32
from std_srvs.srv import Trigger, SetBool
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue

BASE = os.environ.get("OPENDUBS_DASH_URL", "http://127.0.0.1:8080")
POLL_HZ = float(os.environ.get("OPENDUBS_STAMPLC_HZ", "5"))
OK, WARN, ERROR = DiagnosticStatus.OK, DiagnosticStatus.WARN, DiagnosticStatus.ERROR


def _get(path, timeout=2.0):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return json.load(r)


def _post(path, timeout=3.0):
    req = urllib.request.Request(BASE + path, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status < 300


class StamPLCBridge(Node):
    def __init__(self):
        super().__init__("stamplc_bridge")
        self.diag = self.create_publisher(DiagnosticArray, "/stamplc/diagnostics", 10)
        self.p_busv = self.create_publisher(Float32, "/stamplc/bus_voltage", 10)
        self.p_temp = self.create_publisher(Float32, "/stamplc/temperature", 10)
        self.p_enc = self.create_publisher(Int32, "/stamplc/mast_encoder", 10)
        self.p_estop = self.create_publisher(Bool, "/stamplc/estopped", 10)
        self.p_motion = self.create_publisher(Bool, "/stamplc/motion_enabled", 10)
        self.p_pv = self.create_publisher(Float32, "/stamplc/pdh/voltage", 10)
        self.p_pi = self.create_publisher(Float32, "/stamplc/pdh/current", 10)
        self.p_pp = self.create_publisher(Float32, "/stamplc/pdh/power", 10)

        # Command services → POST to the dashboard's existing endpoints. The
        # dashboard enforces the safety policy (relay 0/1 are NOT exposed here;
        # estop_clear is guarded there) so this bridge can't widen the attack surface.
        self.create_service(Trigger, "/stamplc/estop", self._mk_trigger("/api/estop"))
        self.create_service(Trigger, "/stamplc/estop_clear", self._mk_trigger("/api/estop/clear"))
        self.create_service(Trigger, "/stamplc/enc_reset", self._mk_trigger("/api/enc/reset"))
        self.create_service(Trigger, "/stamplc/i2c_scan", self._mk_trigger("/api/i2c/scan"))
        self.create_service(SetBool, "/stamplc/relay2", self._mk_relay(2))
        self.create_service(SetBool, "/stamplc/relay3", self._mk_relay(3))
        self.create_service(SetBool, "/stamplc/pdh_switch", self._mk_setbool("/api/pdh/on", "/api/pdh/off"))

        self.create_timer(1.0 / POLL_HZ, self._poll)
        self._warned = False
        self.get_logger().info("StamPLC bridge polling %s/api/stamplc @ %.0f Hz" % (BASE, POLL_HZ))

    # ---- service factories -------------------------------------------------
    def _mk_trigger(self, path):
        def cb(req, resp):
            try:
                resp.success = _post(path)
                resp.message = path
            except Exception as e:
                resp.success, resp.message = False, str(e)
            return resp
        return cb

    def _mk_relay(self, ch):
        def cb(req, resp):
            state = "on" if req.data else "off"
            try:
                resp.success = _post("/api/relay/%d/%s" % (ch, state))
                resp.message = "relay %d %s" % (ch, state)
            except Exception as e:
                resp.success, resp.message = False, str(e)
            return resp
        return cb

    def _mk_setbool(self, on_path, off_path):
        def cb(req, resp):
            path = on_path if req.data else off_path
            try:
                resp.success = _post(path)
                resp.message = path
            except Exception as e:
                resp.success, resp.message = False, str(e)
            return resp
        return cb

    # ---- telemetry poll ----------------------------------------------------
    def _poll(self):
        try:
            d = _get("/api/stamplc")
            self._warned = False
        except Exception as e:
            if not self._warned:   # warn once, not every tick, while the dashboard is down
                self.get_logger().warn("dashboard unreachable: %s" % e)
                self._warned = True
            return

        io = d.get("io") or {}
        saf = d.get("safety") or {}
        enc = d.get("encoder") or {}
        power = d.get("power") or {}
        pdh = power.get("pdh") or {}

        if io.get("bus_v") is not None:
            self.p_busv.publish(Float32(data=float(io["bus_v"])))
        if io.get("temp_c") is not None:
            self.p_temp.publish(Float32(data=float(io["temp_c"])))
        if enc.get("count") is not None:
            self.p_enc.publish(Int32(data=int(enc["count"])))
        self.p_estop.publish(Bool(data=bool(saf.get("estopped"))))
        self.p_motion.publish(Bool(data=bool(saf.get("motion_enabled"))))
        if pdh.get("v") is not None:
            self.p_pv.publish(Float32(data=float(pdh["v"])))
        if pdh.get("i") is not None:
            self.p_pi.publish(Float32(data=float(pdh["i"])))
        if pdh.get("p") is not None:
            self.p_pp.publish(Float32(data=float(pdh["p"])))

        arr = DiagnosticArray()
        arr.header.stamp = self.get_clock().now().to_msg()
        arr.status = [self._safety_status(saf), self._di_status(io),
                      self._relay_status(io), self._pdh_status(power), self._enc_status(enc)]
        self.diag.publish(arr)

    # ---- diagnostics builders ---------------------------------------------
    @staticmethod
    def _kv(k, v):
        return KeyValue(key=str(k), value=str(v))

    def _safety_status(self, saf):
        st = DiagnosticStatus(name="StamPLC: Safety", hardware_id="stamplc")
        if (not saf.get("fresh", True)) or saf.get("welded") or saf.get("estopped"):
            st.level = ERROR
        elif not saf.get("motion_enabled"):
            st.level = WARN
        else:
            st.level = OK
        st.message = saf.get("source") or ("MOTION ENABLED" if saf.get("motion_enabled") else "disarmed")
        if saf.get("welded"):
            st.message += "  ⚠ CONTACTOR WELDED"
        st.values = [self._kv("estopped", saf.get("estopped")),
                     self._kv("sources", saf.get("sources")),
                     self._kv("contactor", saf.get("contactor")),
                     self._kv("motion_enabled", saf.get("motion_enabled")),
                     self._kv("welded", saf.get("welded")),
                     self._kv("stale", saf.get("stale"))]
        return st

    def _di_status(self, io):
        st = DiagnosticStatus(name="StamPLC: Digital Inputs", level=OK, hardware_id="stamplc")
        di = io.get("di") or []
        labels = io.get("di_labels") or []
        st.message = "%d inputs" % len(di)
        st.values = [self._kv("DI%d %s" % (i, labels[i] if i < len(labels) else ""),
                              "HIGH" if di[i] else "low") for i in range(len(di))]
        return st

    def _relay_status(self, io):
        st = DiagnosticStatus(name="StamPLC: Relays", level=OK, hardware_id="stamplc")
        relay = io.get("relay") or []
        labels = io.get("relay_labels") or []
        st.message = "%d relays" % len(relay)
        st.values = [self._kv("R%d %s" % (i, labels[i] if i < len(labels) else ""),
                              "closed" if relay[i] else "open") for i in range(len(relay))]
        return st

    def _pdh_status(self, power):
        st = DiagnosticStatus(name="StamPLC: REV PDH", hardware_id="pdh")
        pdh = power.get("pdh") or {}
        faults = power.get("pdh_fault") or []
        nf = sum(1 for x in faults if x)
        if pdh.get("brn") or pdh.get("hw"):
            st.level = ERROR
        elif nf:
            st.level = WARN
        else:
            st.level = OK
        st.message = ("%.1f V  %.1f A  %.1f W" % (pdh.get("v", 0.0), pdh.get("i", 0.0), pdh.get("p", 0.0))
                      if pdh else "no PDH data")
        st.values = [self._kv("voltage_V", pdh.get("v")), self._kv("current_A", pdh.get("i")),
                     self._kv("power_W", pdh.get("p")), self._kv("brownout", pdh.get("brn")),
                     self._kv("hw_fault", pdh.get("hw")), self._kv("channel_faults", nf)]
        return st

    def _enc_status(self, enc):
        st = DiagnosticStatus(name="StamPLC: Mast Encoder", level=OK, hardware_id="extencoder")
        st.message = "present" if enc.get("present") else "no encoder"
        st.values = [self._kv("count", enc.get("count")), self._kv("present", enc.get("present"))]
        return st


def main():
    rclpy.init()
    node = StamPLCBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
