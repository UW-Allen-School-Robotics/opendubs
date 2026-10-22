#!/usr/bin/env bash
# Build the SAM engines from MobileSAM's own checkpoint, without NanoSAM's ResNet18 encoder ONNX
# (which is only published on Google Drive). Run inside archit-perception:dev.
#
#   bash build_mobilesam_engines.sh [/data/models/mobile_sam.pt] [/data/engines]
#
# Writes, at SAM's native 1024 input (64x64 embedding):
#   mobilesam_vit_t_encoder_1024.{onnx,engine}       MobileSAM's TinyViT image encoder
#   mobile_sam_mask_decoder_batched_1024.{onnx,engine} HOUND's batched box decoder, opt 8, max 32
# Server settings: sam_image_size 1024 and these two engine paths.
set -euo pipefail

CKPT="${1:-/data/models/mobile_sam.pt}"
OUT="${2:-/data/engines}"
TRTEXEC="${TRTEXEC:-/usr/src/tensorrt/bin/trtexec}"
mkdir -p "${OUT}"

ENC_ONNX="${OUT}/mobilesam_vit_t_encoder_1024.onnx"
ENC_ENGINE="${OUT}/mobilesam_vit_t_encoder_1024.engine"
DEC_ONNX="${OUT}/mobile_sam_mask_decoder_batched_1024.onnx"
DEC_ENGINE="${OUT}/mobile_sam_mask_decoder_batched_1024.engine"

echo "=== 1) MobileSAM image encoder -> ONNX"
[[ -f "${ENC_ONNX}" ]] || python3 -m nanosam.tools.export_sam_image_encoder_onnx \
  --checkpoint "${CKPT}" --output "${ENC_ONNX}" --model_type vit_t --batch_size 1 --opset 17

echo "=== 2) Encoder engine (FP16, batch 1)"
"${TRTEXEC}" --onnx="${ENC_ONNX}" --saveEngine="${ENC_ENGINE}" --fp16 \
  --minShapes=image:1x3x1024x1024 --optShapes=image:1x3x1024x1024 --maxShapes=image:1x3x1024x1024

echo "=== 3) Batched mask decoder -> ONNX (1024 input)"
[[ -f "${DEC_ONNX}" ]] || python3 /opt/perception_models/scripts/export_nanosam_mask_decoder_batched_onnx.py \
  --checkpoint "${CKPT}" --output "${DEC_ONNX}" --image-size 1024

echo "=== 4) Decoder engine (embed 64x64, mask input 256, opt 8 boxes, max 32)"
USE_DLA=0 bash /opt/perception_models/scripts/build_nanosam_mask_decoder_batched_engine.sh \
  "${DEC_ONNX}" "${DEC_ENGINE}" 8 32 64 64 256

ls -lh "${OUT}"/mobilesam_* "${OUT}"/mobile_sam_mask_decoder_batched_1024.*
