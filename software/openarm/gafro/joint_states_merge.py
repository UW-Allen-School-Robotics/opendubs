"""`/joint_states` for the whole robot, from the arm services' own `/left/joint_states` and `/right/joint_states`.

The arms run as one process each, and each publishes only its own joints (a fake bus for the other arm would otherwise overwrite that arm's real joints in every consumer that keeps
a dict by name). The old stack's single broadcaster put both arms in one message, and the runner and the dashboard read `/joint_states` as that. This node is that broadcaster
again: it keeps the newest value of every joint, from either arm and from the grippers' finger joints, and publishes them together. When the two arms share one process, this node
goes and the process publishes `/joint_states` itself.

A joint not heard from for `max_age` seconds is left out, so that a dead arm reads as missing (the runner refuses a run that needs it) and not as a live arm at its last angle.
Velocity and effort are NaN for a joint that has none (the fingers), because a message carries either all of them or none.

    python joint_states_merge.py [--rate 100] [--max-age 1.0] [--sides left right]
"""
import argparse
import math

NAN = float("nan")


class Merger:
    """The newest (time, position, velocity, effort) of each joint name. Pure: the node feeds it and reads it, so it is tested without ROS."""

    def __init__(self, max_age: float = 1.0):
        self.max_age = max_age
        self.joints: dict[str, tuple[float, float, float, float]] = {}

    def update(self, now: float, names, position, velocity=(), effort=()):
        """One message. Velocity and effort are used only when they are as long as the names, as the runner reads them."""
        has_v, has_e = len(velocity) == len(names), len(effort) == len(names)
        for index, name in enumerate(names):
            self.joints[name] = (now, position[index], velocity[index] if has_v else NAN, effort[index] if has_e else NAN)

    def snapshot(self, now: float):
        """(names, position, velocity, effort) of the joints heard within max_age, in a stable order; None when there are none."""
        fresh = sorted(name for name, (at, *_rest) in self.joints.items() if now - at <= self.max_age)
        if not fresh:
            return None
        return (fresh, [self.joints[n][1] for n in fresh],
                [self.joints[n][2] for n in fresh] if not all(math.isnan(self.joints[n][2]) for n in fresh) else [],
                [self.joints[n][3] for n in fresh] if not all(math.isnan(self.joints[n][3]) for n in fresh) else [])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--rate", type=float, default=100.0, help="Hz of the merged /joint_states")
    parser.add_argument("--max-age", type=float, default=1.0, help="s after which a joint not heard from is left out")
    parser.add_argument("--sides", nargs="+", default=["left", "right"], choices=["left", "right"])
    args = parser.parse_args(argv)

    import rclpy
    from sensor_msgs.msg import JointState

    rclpy.init()
    node = rclpy.create_node("openarm_joint_states")
    clock = node.get_clock()
    merger = Merger(args.max_age)

    def now():
        return clock.now().nanoseconds * 1e-9

    def receive(message):
        merger.update(now(), list(message.name), list(message.position), list(message.velocity), list(message.effort))

    for side in args.sides:
        node.create_subscription(JointState, f"/{side}/joint_states", receive, 10)
    publisher = node.create_publisher(JointState, "/joint_states", 10)

    def publish():
        snapshot = merger.snapshot(now())
        if snapshot is None:
            return
        message = JointState()
        message.header.stamp = clock.now().to_msg()
        message.name, message.position, message.velocity, message.effort = snapshot
        publisher.publish(message)

    node.create_timer(1.0 / args.rate, publish)
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
