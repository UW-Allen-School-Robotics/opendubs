"""The ROS side of arm_service.py's slices 3 and 4: the actions the show runner sends each arm's routine and gripper cues to,
`/<side>_controller/follow_joint_trajectory` and `/<side>_gripper_controller/gripper_cmd`, and this arm's joints and the gripper's finger joints on `/joint_states`.

Only the adapters: the trajectory one checks the goal's joint names, turns the points into arrays and calls `ArmService.follow` (trajectory.py), which gates and plays it, a cancel
being `ArmService.hold`; the gripper one calls `ArmService.grip` (gripper.py): a close that stops on something aborts as stalled, the signal the runner reads as "something is in there".
Written against rclpy and control_msgs without a ROS here to run it: NEVER RUN, the first run is on a machine with ROS.
"""
from __future__ import annotations

import threading

import numpy as np


def joint_names(side: str) -> list:
    return [f"openarm_{side}_joint{j}" for j in range(1, 8)]    # the old stack's names (the runner's arm_output.joint_names)


def goal_arrays(side: str, trajectory):
    """(times, positions, velocities or None) of a trajectory_msgs/JointTrajectory, its columns in the arm's joint order; ValueError if its joints are not the arm's seven."""
    expected = joint_names(side)
    if sorted(trajectory.joint_names) != sorted(expected):
        raise ValueError(f"joints {list(trajectory.joint_names)}, not the {side} arm's {expected}")
    order = [list(trajectory.joint_names).index(name) for name in expected]
    times = np.array([p.time_from_start.sec + p.time_from_start.nanosec * 1e-9 for p in trajectory.points])
    positions = np.array([[p.positions[i] for i in order] for p in trajectory.points], dtype=float)
    velocities = None
    if all(len(p.velocities) == 7 for p in trajectory.points):
        velocities = np.array([[p.velocities[i] for i in order] for p in trajectory.points], dtype=float)
    return times, positions, velocities


def settle(handle, cancelled: bool, succeeded: bool) -> None:
    """End a goal in a state rclpy allows. A goal may be CANCELED only after a client asked for it (`is_cancel_requested`); one ended from the service's side, by `ctl hold`,
    was stopped and is ABORTED. (Calling `canceled()` on it raised "invalid transition from EXECUTING with CANCELED" on the Thor, 2026-10-26.)"""
    if cancelled:
        handle.canceled() if handle.is_cancel_requested else handle.abort()
    elif succeeded:
        handle.succeed()
    else:
        handle.abort()


def serve_trajectory(service):
    """Start the action server for `service`'s arm on its own node, executor and thread; returns the node."""
    import rclpy
    from control_msgs.action import FollowJointTrajectory
    from rclpy.action import ActionServer, CancelResponse, GoalResponse
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor

    side = service.args.side
    if not rclpy.ok():
        rclpy.init()
    node = rclpy.create_node(f"openarm_{side}_trajectory")

    def on_goal(goal):
        try:
            goal_arrays(side, goal.trajectory)
        except ValueError as error:
            node.get_logger().warn(f"rejected: {error}")
            return GoalResponse.REJECT
        if service.running:
            node.get_logger().warn(f"rejected: {service.running} is running")
            return GoalResponse.REJECT
        return GoalResponse.ACCEPT

    def on_cancel(_):
        service.hold()
        return CancelResponse.ACCEPT

    def execute(handle):
        result = FollowJointTrajectory.Result()
        times, positions, velocities = goal_arrays(side, handle.request.trajectory)
        outcome = service.follow(times, positions, velocities)
        result.error_code, result.error_string = outcome.code, outcome.message
        settle(handle, outcome.cancelled, outcome.code == 0)
        return result

    group = ReentrantCallbackGroup()
    serve_gripper(service, node)                 # one node, one executor: the gripper cues come while the routine plays
    serve_state(service, node, service.args.state_rate)
    ActionServer(node, FollowJointTrajectory, f"/{side}_controller/follow_joint_trajectory", execute, goal_callback=on_goal, cancel_callback=on_cancel,
                 callback_group=group)
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    threading.Thread(target=executor.spin, daemon=True).start()
    return node


def serve_gripper(service, node=None, finger_rate: float = 20.0):
    """The gripper's action server on `/<side>_gripper_controller/gripper_cmd`, and its finger joints (`openarm_<side>_finger_joint1`, `..2`, metres) on `/joint_states`,
    which is where the runner reads where the gripper really is. Returns the node. The finger joints go out as a message of their own, beside the arm's: the runner and the dashboard
    keep a dict by name. If the bridge's own `/joint_states` ever carries them too, drop one."""
    import rclpy
    from control_msgs.action import GripperCommand
    from rclpy.action import ActionServer, CancelResponse, GoalResponse
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from sensor_msgs.msg import JointState

    import gripper

    side = service.args.side
    if node is None:
        if not rclpy.ok():
            rclpy.init()
        node = rclpy.create_node(f"openarm_{side}_gripper")

    def on_goal(goal):
        return GoalResponse.ACCEPT

    def on_cancel(_):
        service.grip_cancel.set()
        return CancelResponse.ACCEPT

    def execute(handle):
        outcome = service.grip(float(handle.request.command.position))
        result = GripperCommand.Result()
        result.position, result.effort, result.stalled, result.reached_goal = outcome.position, outcome.effort, outcome.stalled, outcome.reached
        if not (outcome.cancelled or outcome.reached):
            node.get_logger().warn(f"gripper goal ended: {outcome.message}")
        settle(handle, outcome.cancelled, outcome.reached)
        return result

    group = ReentrantCallbackGroup()
    ActionServer(node, GripperCommand, f"/{side}_gripper_controller/gripper_cmd", execute, goal_callback=on_goal, cancel_callback=on_cancel, callback_group=group)

    publisher = node.create_publisher(JointState, "/joint_states", 10)
    names = [f"openarm_{side}_finger_joint1", f"openarm_{side}_finger_joint2"]
    state = {}

    def publish():
        try:
            if "sub" not in state:
                state["sub"] = service.robot.subscribe_gripper(gripper.name_of(service.robot, side))
            reading = state["sub"].read() or state.get("last")
        except Exception:  # noqa: BLE001 -- no gripper (not calibrated): nothing to publish
            return
        if reading is None:
            return
        state["last"] = reading
        message = JointState()
        message.header.stamp = node.get_clock().now().to_msg()
        message.name = names
        message.position = [gripper.position_of(reading)] * 2
        publisher.publish(message)

    node.create_timer(1.0 / finger_rate, publish)
    return node


def arm_joint_names(robot, side: str) -> list:
    """The model's names of `side`'s seven joints, in the order of the task space's joint indices (the order of the state vector)."""
    system = robot.get_system()
    names = [actuator.joint_name for actuator in system.get_actuators()]
    return [names[i] for i in system.get_task_space(side).get_joint_indices()]


def serve_state(service, node, rate: float = 100.0):
    """`/joint_states` for this arm ONLY (the model's `openarm_<side>_joint1..7`: the old stack's names), from the service's own RobotInterface.

    Not the bridge's `state` channel, which publishes the whole system: with one service per arm the other arm is a fake bus, and its joints would overwrite the other
    service's real ones in every consumer that keeps a dict by name (the runner, the dashboard)."""
    from sensor_msgs.msg import JointState

    robot, side = service.robot, service.args.side
    indices = np.asarray(robot.get_system().get_task_space(side).get_joint_indices())
    names = arm_joint_names(robot, side)
    publisher = node.create_publisher(JointState, "/joint_states", 10)

    def publish():
        state = robot.get_state()
        message = JointState()
        message.header.stamp = node.get_clock().now().to_msg()
        message.name = names
        message.position = [float(v) for v in np.asarray(state.get_position(), dtype=float)[indices]]
        message.velocity = [float(v) for v in np.asarray(state.get_velocity(), dtype=float)[indices]]
        message.effort = [float(v) for v in np.asarray(state.get_torque(), dtype=float)[indices]]
        publisher.publish(message)

    node.create_timer(1.0 / rate, publish)
