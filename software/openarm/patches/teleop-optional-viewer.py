#!/usr/bin/env python3
"""Make the MuJoCo/viser viewer optional in the upstream WebXR teleop.

Profiled on opendubs2 2026-10-02: viser appeared in 67% of MainThread samples while the
teleop loop missed ~23% of its 150 Hz deadlines. The cost is NOT viewer.sync() (already
rate-limited to rendered_fps) but the TeleopPanels, which are registered into
PhysicsEventLoop and so update every tick. On real hardware the robot is the display.

Default behaviour is unchanged; OPENARM_TELEOP_VIEWER=0 runs headless.
"""
import pathlib
import sys

ROOT = pathlib.Path("/openarm_ws/src/openarm/src/openarm/webxr_teleop")


def patch(path, edits):
    t = path.read_text()
    for name, old, new in edits:
        if new in t and old not in t:
            print(f"  = {path.name}: {name} already applied")
            continue
        if t.count(old) != 1:
            sys.exit(f"  ! {path.name}: {name!r} matched {t.count(old)} times, expected 1")
        t = t.replace(old, new)
        print(f"  + {path.name}: {name}")
    path.write_text(t)


patch(ROOT / "teleop_config.py", [
    ("os import",
     "from dataclasses import dataclass, field\nfrom pathlib import Path",
     "import os\nfrom dataclasses import dataclass, field\nfrom pathlib import Path"),
    ("viewer flag",
     "    viewer_port: int = 8082\n    debug: bool = False",
     '''    viewer_port: int = 8082
    # The MuJoCo/viser viewer is the DOMINANT cost in the control loop on real
    # hardware. Profiled on opendubs2 2026-10-02: viser appeared in 67% of
    # MainThread samples while the loop missed ~23% of its 150 Hz deadlines
    # (worst overrun +9 ms against a 6.7 ms budget), and the loop itself warns
    # that "tracking is coarser/less responsive at this rate".
    #
    # The expensive part is NOT viewer.sync(), which is already rate-limited to
    # rendered_fps -- it is the TeleopPanels, which are registered into
    # PhysicsEventLoop and therefore update EVERY TICK.
    #
    # On real hardware the robot is the display, so the viewer is pure overhead.
    # Default stays ON so sim teleop is unchanged; OPENARM_TELEOP_VIEWER=0 runs
    # headless.
    viewer: bool = field(
        default_factory=lambda: os.environ.get("OPENARM_TELEOP_VIEWER", "1") != "0"
    )
    debug: bool = False'''),
])

patch(ROOT / "webxr_teleop.py", [
    ("signal/threading imports",
     "from mj_viser import MujocoViewer, TeleopPanel",
     "import signal\nimport threading\n\nfrom mj_viser import MujocoViewer, TeleopPanel"),

    ("conditional panels + viewer",
     """        self.panels = {
            side: TeleopPanel(
                arm=self.arms[side],
                controller=self.controllers[side],
                model=robot.model,
                data=robot.data,
                gripper_body_prefix=f"openarm_{side}_",
                arm_label=f"{side.title()} Arm",
            )
            for side in SIDES
        }

        for side in SIDES:
            self.loop.register_teleop(self.controllers[side], self.panels[side])

        self.viewer = MujocoViewer(robot.model, robot.data, port=config.viewer_port)
        for panel in self.panels.values():
            self.viewer.add_panel(panel)
""",
     """        # Panels are viser objects and are stepped by PhysicsEventLoop EVERY TICK, so
        # they are the viewer's real cost -- not viewer.sync(), which is rate-limited to
        # rendered_fps. Build neither when running headless. register_teleop()'s panel
        # argument is already optional, and the loop guards `if panel is not None`.
        if config.viewer:
            self.panels = {
                side: TeleopPanel(
                    arm=self.arms[side],
                    controller=self.controllers[side],
                    model=robot.model,
                    data=robot.data,
                    gripper_body_prefix=f"openarm_{side}_",
                    arm_label=f"{side.title()} Arm",
                )
                for side in SIDES
            }
        else:
            self.panels = {}

        for side in SIDES:
            self.loop.register_teleop(self.controllers[side], self.panels.get(side))

        if config.viewer:
            self.viewer = MujocoViewer(robot.model, robot.data, port=config.viewer_port)
            for panel in self.panels.values():
                self.viewer.add_panel(panel)
        else:
            self.viewer = None

        # Headless, the viewer window can no longer be what ends the session, so the
        # session needs its own stop condition. Without this the `finally:` cleanup
        # (xrt.close()) would be skipped on SIGTERM and the bridge's sockets would be
        # left to the process teardown.
        self._stop = threading.Event()

    def request_stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        if self.viewer is not None:
            return self.viewer.is_running()
        return not self._stop.is_set()
"""),

    ("guard launch_passive",
     "    rig.viewer.launch_passive()\n",
     """    if rig.viewer is not None:
        rig.viewer.launch_passive()
    else:
        # SIGTERM would otherwise kill the process outright and skip the cleanup below.
        # SIGINT already raises KeyboardInterrupt, which the loop catches.
        signal.signal(signal.SIGTERM, lambda *_: rig.request_stop())
        logger.info("running HEADLESS (OPENARM_TELEOP_VIEWER=0) -- no viser viewer or panels")
"""),

    ("loop condition",
     "        while rig.viewer.is_running():",
     "        while rig.is_running():"),

    ("guard sync",
     """            if now - last_render > 1 / config.timing.rendered_fps:
                rig.viewer.sync()
                last_render = now""",
     """            if rig.viewer is not None and now - last_render > 1 / config.timing.rendered_fps:
                rig.viewer.sync()
                last_render = now"""),

    ("guard close",
     "        xrt.close()\n        rig.viewer.close()",
     "        xrt.close()\n        if rig.viewer is not None:\n            rig.viewer.close()"),
])
print("  done")
