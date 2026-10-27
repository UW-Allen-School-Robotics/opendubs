#!/usr/bin/env python3
"""floorplan / floor_frames / slam.launch: the fit's pose algebra, room labels, and the SLAM mode switch.
Pure Python (pyyaml for the mode switch); no ROS. Run: python3 software/ros/tests/test_floorplan.py"""
import math
import pathlib
import sys
import tempfile
import types
import unittest

HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import floor_frames as ff  # noqa: E402
import floorplan as fp  # noqa: E402


class PoseTest(unittest.TestCase):
    def test_map_in_floor_places_the_start_where_the_fit_put_it(self):
        th, tx, ty, sx, sy = math.radians(82.3), 52.26, -51.17, -5.13, 0.14
        x, y, yaw = fp.map_in_floor(th, tx, ty, sx, sy)
        # the start, a map-frame point, carried into the floor frame by the published transform
        c, s = math.cos(yaw), math.sin(yaw)
        self.assertAlmostEqual(c * sx - s * sy + x, tx, places=9)
        self.assertAlmostEqual(s * sx + c * sy + y, ty, places=9)

    def test_yaw_quaternion(self):
        qx, qy, qz, qw = ff.yaw_quaternion(math.pi)
        self.assertAlmostEqual(abs(qz), 1.0); self.assertAlmostEqual(qw, 0.0)


class RoomTest(unittest.TestCase):
    def test_room_labels(self):
        for w in ("269", "200K", "2ST1", "250-1", "200A-1", "G00A"):
            self.assertTrue(fp.is_room(w), w)
        for w in ("Feet", "100Feet", "Gates", "2/20/2021", "GGG"):
            self.assertFalse(fp.is_room(w), w)


class PlanFileTest(unittest.TestCase):
    """`floorplan.py plan`: the open plan file (encore.plan/1), built without a PDF library."""
    P = staticmethod(lambda x, y: types.SimpleNamespace(x=x, y=y))

    def test_strokes_read_lines_curves_and_rectangles(self):
        P = self.P
        rect = types.SimpleNamespace(tl=P(0, 0), tr=P(4, 0), br=P(4, 2), bl=P(0, 2))
        segs = fp.strokes([("l", P(1, 1), P(2, 1)), ("c", P(0, 0), P(9, 9), P(9, 9), P(3, 0)), ("re", rect, 0)])
        self.assertEqual(segs[0], ((1, 1), (2, 1)))
        self.assertEqual(segs[1], ((0, 0), (3, 0)))                    # a curve as its chord p1 -> p4
        self.assertEqual(segs[2:], [((0, 0), (4, 0)), ((4, 0), (4, 2)), ((4, 2), (0, 2)), ((0, 2), (0, 0))])

    def test_walls_are_clipped_to_the_drawing_and_y_flips(self):
        box = fp.drawing_area(200, 150)                                # x 10..190, y 40..140 (page points)
        segs = [((0, 100), (100, 100)),                                # crosses the left margin: clipped at x 10
                ((50, 5), (60, 20)),                                   # in the header: dropped
                ((80, 90), (80, 90))]                                  # zero length: dropped
        d = fp.plan_doc(segs, 0.5, {"269": [1.0, -2.0]}, box, "f2", {"file": "x.pdf"}, {"name": "t"}, 100)
        self.assertEqual(d["walls"], [[[5.0, -50.0], [50.0, -50.0]]])  # metres, y negated
        self.assertEqual(d["format"], "encore.plan/1")
        self.assertEqual((d["frame"], d["units"], d["rooms"]), ("f2", "m", {"269": [1.0, -2.0]}))
        self.assertEqual(d["scale"]["m_per_unit"], 0.5)

    def test_clip_keeps_the_inside_and_rejects_the_outside(self):
        box = (0, 10, 0, 10)
        self.assertEqual(fp.clip((5, 5), (15, 5), box), ((5, 5), (10, 5)))
        self.assertIsNone(fp.clip((11, 0), (20, 0), box))


class ModeTest(unittest.TestCase):
    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("pyyaml not installed")
        # slam.launch imports ROS launch modules at the top; stand them in, only slam_params is tested
        for name in ("ament_index_python", "ament_index_python.packages", "launch", "launch.actions",
                     "launch.launch_description_sources"):
            mod = sys.modules.setdefault(name, types.ModuleType(name))
            for attr in ("get_package_share_directory", "LaunchDescription", "ExecuteProcess",
                         "IncludeLaunchDescription", "LogInfo", "PythonLaunchDescriptionSource"):
                setattr(mod, attr, getattr(mod, attr, None))
        import importlib.util
        spec = importlib.util.spec_from_file_location("slam_launch", HERE / "slam.launch.py")
        self.sl = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.sl)

    def test_no_site_file_maps(self):
        mode, p = self.sl.slam_params(site_path="/nonexistent/site.yaml")
        self.assertEqual(mode, "mapping")
        self.assertEqual(p["slam_toolbox"]["ros__parameters"]["mode"], "mapping")

    def test_a_saved_map_localizes_from_its_start(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "m1.posegraph").write_bytes(b"")
            pathlib.Path(d, "site.yaml").write_text("map: m1\nstart_pose: [-5.13, 0.14, -0.09]\n")
            mode, p = self.sl.slam_params(site_path=f"{d}/site.yaml")
            r = p["slam_toolbox"]["ros__parameters"]
            self.assertEqual((mode, r["mode"], r["map_file_name"]), ("localization", "localization", f"{d}/m1"))
            self.assertEqual(r["map_start_pose"], [-5.13, 0.14, -0.09])

    def test_a_named_map_that_is_missing_maps(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d, "site.yaml").write_text("map: gone\n")
            self.assertEqual(self.sl.slam_params(site_path=f"{d}/site.yaml")[0], "mapping")


if __name__ == "__main__":
    unittest.main()
