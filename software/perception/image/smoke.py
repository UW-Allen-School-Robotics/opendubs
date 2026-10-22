"""Phase 0.3: does this container see the GPU through CUDA, PyTorch and TensorRT?

Run inside archit-perception:dev. Prints one line per check and exits non-zero on the first
failure.
"""

import sys


def check(name, fn):
    try:
        print(f"OK   {name}: {fn()}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL {name}: {exc!r}", flush=True)
        sys.exit(1)


def torch_gpu():
    import torch

    x = torch.ones(1024, 1024, device="cuda", dtype=torch.float16)
    total = (x @ x).sum().item()
    cap = torch.cuda.get_device_capability()
    return f"torch {torch.__version__}, {torch.cuda.get_device_name()}, sm_{cap[0]}{cap[1]}, matmul sum {total:.0f}"


def tensorrt_runtime():
    import tensorrt as trt

    builder = trt.Builder(trt.Logger(trt.Logger.WARNING))
    return f"tensorrt {trt.__version__}, fast fp16 {builder.platform_has_fast_fp16}"


def imports():
    import numpy
    import transformers
    import ultralytics
    from nanosam.utils.predictor import load_image_encoder_engine  # noqa: F401
    from torch2trt import TRTModule  # noqa: F401
    import perception_models.clipseg.shared  # noqa: F401
    import perception_models.nanosam_trt  # noqa: F401
    import perception_models.yolo_world.shared  # noqa: F401
    import rclpy  # noqa: F401

    return f"numpy {numpy.__version__}, transformers {transformers.__version__}, ultralytics {ultralytics.__version__}, rclpy"


check("torch on the GPU", torch_gpu)
check("TensorRT", tensorrt_runtime)
check("imports", imports)
