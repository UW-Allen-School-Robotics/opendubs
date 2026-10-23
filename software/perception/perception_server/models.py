"""One copy each of CLIPSeg, NanoSAM and YOLO-World, shared by the navigation and query adapters.

Not thread-safe: the server's scheduler makes one call at a time. Every method takes and returns
plain arrays or CUDA tensors, so the adapters never touch the models' internals.
"""

from __future__ import annotations

import time

import numpy as np
import torch
import torch.nn.functional as F

from perception_models.clipseg.shared import ClipSegParts, PromptLayout
from perception_models.clipseg.trt import load_clipseg_vision_engine, trt_encode_hs
from perception_models.nanosam_trt import NanoSamDecoder, NanoSamEncoder

# NanoSAM's batched decoder engine is built for at most this many boxes per call.
SAM_MAX_BOXES = 32


class _Log:
    """The logger interface perception_models' NanoSAM wrappers expect."""

    def __init__(self, fn):
        self.info = fn
        self.warning = fn
        self.warn = fn


class SharedModels:
    def __init__(self, cfg: dict, nav_prompts: list[str], nav_group_ids: list[int],
                 nav_group_names: list[str], log=print):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available in this container (is --runtime nvidia set?)")
        torch.set_float32_matmul_precision("high")
        self.log = log

        # CLIPSeg. The layout holds navigation's prompts; queries bring their own text.
        self.nav_layout = PromptLayout(
            subprompts=nav_prompts, group_names=nav_group_names,
            group_ids=nav_group_ids, suppress_prompts=[],
        )
        self.clip = ClipSegParts(
            cfg["clipseg_model"], self.nav_layout, input_res=224, compile_mode="none",
            device="cuda", logger=log,
        )
        # The image half runs in TensorRT. The text half (FiLM decoder) runs in PyTorch: TensorRT
        # 10.13 on the Thor computes it wrongly (FP16 and FP32 alike, while ONNX Runtime runs the
        # same ONNX correctly), so on bus.jpg "ground" scored 0.01 on the pavement instead of 0.74.
        # HOUND's design note also ships FiLM in PyTorch. Never route it through a FiLM engine.
        self.clip_vision = load_clipseg_vision_engine(cfg["clipseg_vision_engine"])
        self.film = self.clip.build_decoder()  # any prompt count
        self._cond_cache: dict[str, torch.Tensor] = {}

        # NanoSAM (or MobileSAM's encoder: the same embedding).
        size = int(cfg["sam_image_size"])
        self.sam_enc = NanoSamEncoder(cfg["sam_image_encoder"], image_encoder_size=size,
                                      device="cuda", logger=_Log(log))
        self.sam_dec = NanoSamDecoder(cfg["sam_mask_decoder"], orig_image_encoder_size=size,
                                      device="cuda", logger=_Log(log), max_batch_k=SAM_MAX_BOXES)

        # YOLO-World. Its own class list stays navigation's; queries pass text features per call.
        from ultralytics import YOLO

        yolo = YOLO(cfg["yolo_world"])
        yolo.set_classes(list(cfg.get("yolo_nav_labels", ["person"])))
        self.world = yolo.model.to("cuda").eval().half()
        self.yolo_imgsz = int(cfg.get("yolo_imgsz", 640))
        self._txt_cache: dict[tuple, torch.Tensor] = {}

        self.timing: dict[str, float] = {}
        self._warmup()

    # --- timing -------------------------------------------------------------------------------

    def _t(self, name, t0):
        torch.cuda.synchronize()
        self.timing[name] = (time.perf_counter() - t0) * 1000.0

    def _warmup(self):
        rgb = np.zeros((480, 640, 3), dtype=np.uint8)
        hs = self.clip_hidden(rgb)
        self.clip_probs(hs, self.clip.cond)
        feats = self.sam_features(rgb)
        self.sam_masks(feats, [(0, 0, 100, 100)], (480, 640), per_instance=True)
        self.detect(rgb, ["person"], 0.25)
        self.log("models warm")

    # --- CLIPSeg ------------------------------------------------------------------------------

    @torch.inference_mode()
    def clip_hidden(self, rgb: np.ndarray) -> torch.Tensor:
        """RGB uint8 HxWx3 -> CLIPSeg hidden states [1, L, S, D] (TensorRT)."""
        t0 = time.perf_counter()
        hs = trt_encode_hs(self.clip_vision, self.clip.preprocess_rgb_batch([rgb]))
        self._t("clip_image_ms", t0)
        return hs

    @torch.inference_mode()
    def label_cond(self, labels: list[str]) -> torch.Tensor:
        """Text conditioning [P, C] for any labels, cached per label."""
        missing = [s for s in labels if s not in self._cond_cache]
        if missing:
            tok = self.clip.proc.tokenizer(missing, return_tensors="pt", padding=True).to("cuda")
            cond = self.clip.model.get_conditional_embeddings(
                batch_size=len(missing), input_ids=tok.input_ids, attention_mask=tok.attention_mask)
            for s, c in zip(missing, cond):
                self._cond_cache[s] = c
        return torch.stack([self._cond_cache[s] for s in labels])

    @torch.inference_mode()
    def clip_probs(self, hs: torch.Tensor, cond: torch.Tensor) -> torch.Tensor:
        """Per-prompt probabilities [P, 224, 224], from the PyTorch FiLM decoder."""
        t0 = time.perf_counter()
        with torch.autocast("cuda", dtype=torch.float16):
            probs = self.film(hs, cond)
        self._t("clip_text_ms", t0)
        return probs[0].float()

    def nav_groups(self, probs: torch.Tensor) -> torch.Tensor:
        """Navigation prompt probabilities [P, h, w] -> group maps [G, h, w] (max over a group)."""
        return self.clip.aggregate_groups_batch(probs.unsqueeze(0), "max")[0]

    # --- SAM ----------------------------------------------------------------------------------

    @torch.inference_mode()
    def sam_features(self, rgb: np.ndarray) -> torch.Tensor:
        t0 = time.perf_counter()
        feats = self.sam_enc.encode_tensor(self.sam_enc.preprocess_batch([rgb]), synchronize=False)
        self._t("sam_encode_ms", t0)
        return feats

    @torch.inference_mode()
    def sam_masks(self, feats: torch.Tensor, boxes, image_hw, *, classes=None,
                  per_instance: bool) -> torch.Tensor:
        """Box prompts -> masks at full image size, float 0/1.

        per_instance: one mask per box, [K, H, W]. Otherwise boxes of the same class are merged,
        [n_classes, H, W], as navigation needs.
        """
        t0 = time.perf_counter()
        boxes = [tuple(int(v) for v in b) for b in boxes][:SAM_MAX_BOXES]
        if per_instance:
            classes, n = list(range(len(boxes))), len(boxes)
        else:
            classes = list(classes)[: len(boxes)]
            n = max(classes, default=-1) + 1
        out = self.sam_dec.decode_multi(feats, [(classes, boxes, max(n, 1), image_hw)])[0]
        self._t("sam_decode_ms", t0)
        return out

    # --- YOLO-World ---------------------------------------------------------------------------

    @torch.inference_mode()
    def detect(self, rgb: np.ndarray, labels: list[str], conf: float):
        """Open-vocabulary boxes for these labels: [(label, score, (x0, y0, x1, y1))].

        The head's class count is set to len(labels) on each call (one call at a time), and the
        text features are passed in. Never zero-pad text features: a zero vector normalised in
        FP16 gives NaN scores and NMS drops every box (Phase 0.7).
        """
        from ultralytics.data.augment import LetterBox
        from ultralytics.utils.nms import non_max_suppression
        from ultralytics.utils.ops import scale_boxes

        t0 = time.perf_counter()
        key = tuple(labels)
        if key not in self._txt_cache:
            self._txt_cache[key] = self.world.get_text_pe(list(labels), cache_clip_model=True).to("cuda").half()
        feats = self._txt_cache[key]
        head = self.world.model[-1]
        head.nc = len(labels)
        head.no = head.nc + head.reg_max * 4

        img = LetterBox((self.yolo_imgsz, self.yolo_imgsz), auto=True, stride=32)(image=rgb)
        x = torch.from_numpy(img.transpose(2, 0, 1).copy()).to("cuda").half().unsqueeze(0) / 255.0
        raw = self.world.predict(x, txt_feats=feats)
        raw = raw[0] if isinstance(raw, (tuple, list)) else raw
        det = non_max_suppression(raw, conf_thres=conf, iou_thres=0.7, max_det=100)[0]
        boxes = scale_boxes(x.shape[2:], det[:, :4].clone(), rgb.shape[:2]).round().int().cpu().numpy()
        out = [(labels[int(c)], float(s), tuple(int(v) for v in b))
               for b, s, c in zip(boxes, det[:, 4].cpu().numpy(), det[:, 5].cpu().numpy())]
        self._t("detect_ms", t0)
        return out


def upsample(maps: torch.Tensor, hw) -> torch.Tensor:
    """[C, h, w] -> [C, H, W], bilinear."""
    return F.interpolate(maps.unsqueeze(0), size=tuple(hw), mode="bilinear", align_corners=False)[0]
