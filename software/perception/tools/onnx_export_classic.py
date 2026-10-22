"""Run a module or script with torch.onnx.export forced onto the classic (TorchScript) exporter.

PyTorch 2.9+ exports with dynamo by default. NanoSAM's and HOUND's export scripts were written for
the classic exporter: under dynamo, MobileSAM's encoder fails to convert to opset 17 ("No Adapter
To Version $17 for Pad") yet still leaves a partial file behind.

  python3 onnx_export_classic.py -m nanosam.tools.export_sam_image_encoder_onnx --checkpoint ...
  python3 onnx_export_classic.py /path/to/script.py --args ...
"""

import functools
import runpy
import sys

import torch

torch.onnx.export = functools.partial(torch.onnx.export, dynamo=False)

if sys.argv[1] == "-m":
    module, sys.argv = sys.argv[2], [sys.argv[2]] + sys.argv[3:]
    runpy.run_module(module, run_name="__main__", alter_sys=True)
else:
    script, sys.argv = sys.argv[1], sys.argv[1:]
    runpy.run_path(script, run_name="__main__")
