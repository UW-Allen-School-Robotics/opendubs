"""Query adapter: labels -> one mask per object instance.

Boxes come from YOLO-World (one per instance). With SOURCE_AUTO, labels the detector finds nothing
for fall back to CLIPSeg regions: each blob of a label's probability map becomes a box, so touching
objects can merge into one. NanoSAM turns every box into its own mask.
"""

from __future__ import annotations

import cv2
import numpy as np
import torch

from .models import SAM_MAX_BOXES, SharedModels, upsample

SOURCE_AUTO, SOURCE_DETECTOR, SOURCE_CLIPSEG = 0, 1, 2


class QueryAdapter:
    def __init__(self, models: SharedModels, cfg: dict):
        self.m = models
        self.default_score = float(cfg.get("default_min_score", 0.25))
        self.max_instances = min(int(cfg.get("max_instances", 16)), SAM_MAX_BOXES)
        self.clip_threshold = float(cfg.get("clipseg_threshold", 0.4))
        self.min_area = float(cfg.get("clipseg_min_area", 0.001))

    @torch.inference_mode()
    def run(self, rgb: np.ndarray, labels: list[str], min_score: float, source: int):
        """-> list of dicts: label, score, box (x0, y0, x1, y1), mask (uint8 HxW, 255 = object)."""
        labels = [s.strip() for s in labels if s.strip()]
        if not labels:
            return []
        score = min_score if min_score > 0 else self.default_score
        found = []
        if source in (SOURCE_AUTO, SOURCE_DETECTOR):
            found = self.m.detect(rgb, labels, score)
        if source == SOURCE_CLIPSEG or (source == SOURCE_AUTO and not found):
            found += self._clipseg_regions(rgb, [s for s in labels if s not in {f[0] for f in found}])
        found.sort(key=lambda f: f[1], reverse=True)
        found = found[: self.max_instances]
        if not found:
            return []

        h, w = rgb.shape[:2]
        masks = self.m.sam_masks(self.m.sam_features(rgb), [f[2] for f in found], (h, w),
                                 per_instance=True)
        masks = (masks > 0).to(torch.uint8).mul_(255).cpu().numpy()
        return [{"label": lab, "score": sc, "box": box, "mask": masks[i]}
                for i, (lab, sc, box) in enumerate(found)]

    def _clipseg_regions(self, rgb, labels):
        if not labels:
            return []
        h, w = rgb.shape[:2]
        probs = self.m.clip_probs(self.m.clip_hidden(rgb), self.m.label_cond(labels))
        probs = upsample(probs, (h, w)).cpu().numpy()
        out = []
        for lab, p in zip(labels, probs):
            n, cc, stats, _ = cv2.connectedComponentsWithStats((p >= self.clip_threshold).astype(np.uint8), connectivity=8)
            for c in range(1, n):
                x, y, bw, bh, area = stats[c]
                if area < self.min_area * h * w:
                    continue
                out.append((lab, float(p[cc == c].mean()), (int(x), int(y), int(x + bw), int(y + bh))))
        return out
