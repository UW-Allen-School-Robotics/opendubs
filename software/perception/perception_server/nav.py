"""Navigation adapter: one camera frame -> traversability image and obstacle points.

The same steps as HOUND's segmentation: CLIPSeg scores navigation's prompts, each group's blobs
become boxes, NanoSAM refines them, and masks of one group are merged. Unlike HOUND, the output is
for OpenDubs' navigation stack: a mono8 traversability image (for nvblox later) and the obstacle
pixels projected through depth into 3D points (for a Nav2 obstacle layer).

This adapter only ever adds obstacles. It never reports free space that the LIDARs see as blocked.
"""

from __future__ import annotations

import numpy as np
import torch

from perception_models.seg_ops import labels_to_boxes

from .models import SharedModels, upsample


class NavAdapter:
    def __init__(self, models: SharedModels, cfg: dict):
        self.m = models
        self.threshold = float(cfg.get("threshold", 0.3))
        self.max_boxes = int(cfg.get("sam_max_boxes", 6))
        self.min_area = float(cfg.get("sam_min_area", 0.002))
        self.stride = int(cfg.get("cloud_stride", 4))
        self.max_depth = float(cfg.get("max_depth_m", 5.0))
        names = models.nav_layout.group_names
        self.trav = names.index("traversable")
        self.blocked = [names.index(g) for g in ("non_traversable", "people") if g in names]

    @torch.inference_mode()
    def run(self, rgb: np.ndarray, depth_m: np.ndarray | None, K: np.ndarray | None):
        """-> (traversability uint8 HxW: 255 free, 0 blocked or unknown; obstacle points Nx3 float32)."""
        h, w = rgb.shape[:2]
        groups = self.m.nav_groups(self.m.clip_probs(self.m.clip_hidden(rgb), self.m.clip.cond))
        maxv, arg = groups.max(dim=0)
        labels = torch.where(maxv >= self.threshold, arg + 1, torch.zeros_like(arg)).to(torch.uint8)
        cands = labels_to_boxes(labels.cpu().numpy(), groups.shape[0], self.min_area, self.max_boxes)

        if cands:
            sy, sx = h / labels.shape[0], w / labels.shape[1]
            boxes = [(x0 * sx, y0 * sy, x1 * sx, y1 * sy) for _, _, (x0, y0, x1, y1) in cands]
            masks = self.m.sam_masks(self.m.sam_features(rgb), boxes, (h, w),
                                     classes=[g for g, _, _ in cands], per_instance=False)
            refined = torch.zeros((groups.shape[0], h, w), device=masks.device)
            refined[: masks.shape[0]] = masks
        else:  # nothing above threshold: fall back to the coarse maps
            refined = (upsample(groups, (h, w)) >= self.threshold).float()

        blocked = refined[self.blocked].amax(dim=0) > 0 if self.blocked else torch.zeros((h, w), dtype=torch.bool, device=refined.device)
        free = (refined[self.trav] > 0) & ~blocked
        trav = (free.to(torch.uint8) * 255).cpu().numpy()

        points = np.zeros((0, 3), dtype=np.float32)
        if depth_m is not None and K is not None:
            points = self._project(blocked.cpu().numpy(), depth_m, K)
        return trav, points

    def _project(self, mask: np.ndarray, depth_m: np.ndarray, K: np.ndarray) -> np.ndarray:
        """Blocked pixels with valid depth -> camera-frame points (x right, y down, z forward)."""
        s = self.stride
        m = mask[::s, ::s]
        z = depth_m[::s, ::s]
        keep = m & (z > 0.05) & (z < self.max_depth) & np.isfinite(z)
        if not keep.any():
            return np.zeros((0, 3), dtype=np.float32)
        v, u = np.nonzero(keep)
        z = z[keep]
        u = u * s
        v = v * s
        x = (u - K[0, 2]) * z / K[0, 0]
        y = (v - K[1, 2]) * z / K[1, 1]
        return np.stack([x, y, z], axis=1).astype(np.float32)
