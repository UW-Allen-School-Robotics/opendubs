"""The geometry the AnyGrasp client gets right or the arm goes to the wrong place.

  python3 -m pytest software/perception/tests
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from perception_clients.anygrasp import crop_to_mask, erode, object_points, rotation_to_quaternion  # noqa: E402


def test_crop_keeps_every_point():
    """Cropping and shifting the principal point must not move a single 3D point."""
    rng = np.random.default_rng(0)
    h, w = 120, 160
    depth = rng.integers(300, 1400, (h, w)).astype(np.uint16)
    depth[rng.random((h, w)) < 0.1] = 0
    mask = np.zeros((h, w), np.uint8)
    mask[37:81, 101:150] = 255
    K = np.array([[200.0, 0, 77.3], [0, 210.0, 61.9], [0, 0, 1]])
    rgb = np.zeros((h, w, 3), np.uint8)
    _, d_c, m_c, K_c, _ = crop_to_mask(rgb, depth, mask, K, margin_px=5)
    full = object_points(depth, mask, K, 0.001)
    cropped = object_points(d_c, m_c, K_c, 0.001)
    assert m_c.shape == (44 + 10, 49 + 10)
    np.testing.assert_allclose(cropped, full)


def test_erode_matches_brute_force():
    rng = np.random.default_rng(1)
    m = rng.random((30, 40)) < 0.8
    m[:, :3] = True  # the image edge counts as outside
    for r in (1, 2, 3):
        pad = np.pad(m, r, constant_values=False)
        brute = np.ones_like(m)
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                brute &= pad[r + dy: r + dy + m.shape[0], r + dx: r + dx + m.shape[1]]
        np.testing.assert_array_equal(erode(m, r), brute)


def test_quaternion_round_trip():
    rng = np.random.default_rng(2)
    for _ in range(50):
        A = np.linalg.qr(rng.normal(size=(3, 3)))[0]
        R = A * np.sign(np.linalg.det(A))  # a proper rotation
        x, y, z, w = rotation_to_quaternion(R)
        back = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        np.testing.assert_allclose(back, R, atol=1e-9)
