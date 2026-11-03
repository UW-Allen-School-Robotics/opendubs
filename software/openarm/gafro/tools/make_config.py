#!/usr/bin/env python3
"""Make a COPY of the robot's hardware config with a few keys changed, for one experiment. The original is never written.

    python make_config.py --base $ARM_HW_CONFIG --out ~/cfg-torque-kd.yaml \
        --set right_arm.use_model_gains=false --set right_arm.torque_kd=0.5 --set 'right_arm.kd=[5,5,5,5,2,2,2]'

`--set group.key=value` takes a YAML value (a number, true/false, a list). The group must already exist in the base (`left_arm`, `right_arm`); the key must be one
the driver knows (OpenArm.cpp refuses an unknown key at start, so a typo here would only show up on the robot). It prints what changed, then the file's path.
Use the copy with `--hardware-config <out>`. Gains: `use_model_gains` defaults to true, which takes kp/kd from the model and ignores the YAML's, so set it false to
change them; kd is refused above 5 by the driver.
"""
from __future__ import annotations

import argparse
import os
import sys

import yaml

# The keys the openarm driver accepts: every `scalar(...)` / `array(...)` of its configuration parser (OpenArm.cpp, read 2026-10-23), plus driver and end_effector.
KNOWN = {
    "auto_reengage", "bus", "can_fd", "can_interface", "command_timeout", "direction", "disable_on_stop", "driver", "end_effector", "engage_ramp",
    "engage_start_fraction", "fake_gripper_position", "fake_initial_position", "fake_load", "gravity", "gravity_feedforward",
    "gripper_closed_position", "gripper_grasp_margin", "gripper_motor_type", "gripper_open_position", "gripper_recv_id", "gripper_send_id",
    "gripper_speed", "gripper_torque", "joint_limit_margin", "kd", "kp", "link_retry", "lower_limits", "max_velocity", "motor_types", "rate",
    "realtime_priority", "recv_ids", "reply_miss_warning", "reply_timeout", "require_permit", "send_ids", "start_attempts", "start_interval",
    "temperature_trip", "temperature_warning", "torque_fraction", "torque_kd", "torque_slew", "torque_trip", "torque_trip_time", "tracking_trip",
    "tracking_trip_time", "upper_limits", "use_gripper", "use_model_gains", "velocity_slew", "velocity_trip", "verify_motors", "verify_timeout",
    "zero_offset",
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", required=True, help="the robot's hardware config (never written)")
    p.add_argument("--out", required=True, help="where the copy goes (not the base, and not under /etc)")
    p.add_argument("--set", action="append", default=[], metavar="group.key=value")
    args = p.parse_args(argv)

    base, out = os.path.realpath(os.path.expanduser(args.base)), os.path.realpath(os.path.expanduser(args.out))
    if out == base or out.startswith("/etc/"):
        print("refused: the copy must not be the base file or live under /etc")
        return 1
    with open(base) as handle:
        config = yaml.safe_load(handle)

    changes = []
    for item in args.set:
        target, sep, raw = item.partition("=")
        group, dot, key = target.partition(".")
        if not sep or not dot:
            print(f"refused: {item!r} is not group.key=value")
            return 1
        if group not in config or not isinstance(config[group], dict):
            print(f"refused: the base has no group {group!r} (it has {sorted(k for k, v in config.items() if isinstance(v, dict))})")
            return 1
        if key not in KNOWN:
            print(f"refused: {key!r} is not a key the driver knows; the driver would refuse to start on it")
            return 1
        value = yaml.safe_load(raw)
        changes.append(f"  {group}.{key}: {config[group].get(key, '<unset>')!r} -> {value!r}")
        config[group][key] = value

    with open(out, "w") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    print(f"wrote {out} from {base}" + ("\n" + "\n".join(changes) if changes else " (no changes)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
