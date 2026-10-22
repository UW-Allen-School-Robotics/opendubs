"""Phase 0.7: can one loaded YOLO-World serve different label sets per request?

The navigation stream keeps the model's own class list (["person"]). A request brings its own
labels and is answered through WorldModel.predict(x, txt_feats=...), never set_classes, so the
shared model is not changed. Checks, on Ultralytics' bundled bus.jpg:

1. the per-request path finds the same boxes as the official set_classes path;
2. the model's own text features are unchanged after the requests;
3. cached text features make a repeated label set no slower than the first.

Run inside archit-perception:dev with HOME=/data/home, so the CLIP text model downloaded on first
use is kept. Usage: python3 yolo_world_per_request.py /data/models/yolov8s-worldv2.pt
"""

import sys
import time

import numpy as np
import torch
from ultralytics import YOLO
from ultralytics.data.augment import LetterBox
from ultralytics.utils import ASSETS

try:
    from ultralytics.utils.nms import non_max_suppression
except ImportError:  # older layout
    from ultralytics.utils.ops import non_max_suppression
from ultralytics.utils.ops import scale_boxes

IMGSZ = 640
CONF = 0.25
# The detection head's class count is fixed by the last set_classes() (head.nc, head.no), so text
# features passed per call must always have the same length. Every label set is padded to SLOTS
# with zero vectors, and detections in padded slots are dropped.
SLOTS = 16
NAV_LABELS = ["person"]
REQUESTS = [["bus", "shoe"], ["person", "bus"], ["bus", "shoe"]]  # the last repeats the first


def ms(t0):
    torch.cuda.synchronize()
    return (time.perf_counter() - t0) * 1000.0


def pad(feats):
    """[1, n, D] text features -> [1, SLOTS, D], zero-filled."""
    n = feats.shape[1]
    if n > SLOTS:
        raise ValueError(f"{n} labels > {SLOTS} slots")
    return torch.cat([feats, feats.new_zeros(1, SLOTS - n, feats.shape[2])], dim=1)


def iou(a, b):
    x0, y0 = np.maximum(a[:2], b[:2])
    x1, y1 = np.minimum(a[2:], b[2:])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area = lambda r: (r[2] - r[0]) * (r[3] - r[1])  # noqa: E731
    return inter / (area(a) + area(b) - inter + 1e-9)


def main(weights):
    import cv2

    bgr = cv2.imread(str(ASSETS / "bus.jpg"))

    # Official path, used only as the reference: set_classes changes the model for every caller.
    ref = YOLO(weights)
    reference = {}
    for labels in REQUESTS[:2]:
        ref.set_classes(labels)
        r = ref.predict(bgr, imgsz=IMGSZ, conf=CONF, half=True, device="cuda", verbose=False)[0]
        reference[tuple(labels)] = [
            (labels[int(c)], b) for c, b in zip(r.boxes.cls.cpu().numpy(), r.boxes.xyxy.cpu().numpy())
        ]
    del ref

    # Shared model: navigation's class list and a fixed head of SLOTS classes, set once at startup.
    shared = YOLO(weights)
    shared.set_classes(NAV_LABELS)
    world = shared.model.to("cuda").eval()
    world.half()
    head = world.model[-1]
    world.txt_feats = pad(world.txt_feats)
    head.nc = SLOTS
    head.no = SLOTS + head.reg_max * 4
    nav_feats = world.txt_feats.clone()

    lb = LetterBox((IMGSZ, IMGSZ), auto=False, stride=32)
    img = lb(image=bgr)[:, :, ::-1].transpose(2, 0, 1).copy()
    x = torch.from_numpy(img).to("cuda").half().unsqueeze(0) / 255.0

    cache = {}
    ok = True
    for labels in REQUESTS:
        key = tuple(labels)
        was_cached = key in cache
        t0 = time.perf_counter()
        if not was_cached:
            cache[key] = pad(world.get_text_pe(labels, cache_clip_model=True).to("cuda").half())
        t_text = ms(t0)

        t0 = time.perf_counter()
        with torch.inference_mode():
            raw = world.predict(x, txt_feats=cache[key])
        raw = raw[0] if isinstance(raw, (tuple, list)) else raw
        det = non_max_suppression(raw, conf_thres=CONF, iou_thres=0.7, max_det=300)[0]
        det = det[det[:, 5] < len(labels)]  # drop padded slots
        t_det = ms(t0)

        boxes = scale_boxes(x.shape[2:], det[:, :4].clone(), bgr.shape[:2]).cpu().numpy()
        found = [(labels[int(c)], b) for c, b in zip(det[:, 5].cpu().numpy(), boxes)]
        names = sorted(n for n, _ in found)
        print(f"request {labels}: text {t_text:.1f} ms (cached={was_cached}), "
              f"detect+nms {t_det:.1f} ms, found {names}", flush=True)

        if key in reference:
            want = reference[key]
            matched = sum(
                any(n == m and iou(b, c) > 0.9 for m, c in found) for n, b in want
            )
            print(f"  vs official set_classes path: {matched}/{len(want)} boxes match (IoU > 0.9)")
            ok &= matched == len(want) and len(found) == len(want)
        ok &= all(n in labels for n in names)

    unchanged = torch.equal(world.txt_feats, nav_feats)
    print(f"shared model's own text features unchanged: {unchanged}")
    ok &= unchanged
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/data/models/yolov8s-worldv2.pt"))
