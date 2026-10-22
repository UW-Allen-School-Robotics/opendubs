"""Loaded by Python at startup when this directory is on PYTHONPATH: forces torch.onnx.export onto
the classic (TorchScript) exporter for scripts we do not edit, such as HOUND's engine builders.
Same reason as ../onnx_export_classic.py: PyTorch 2.9+ exports with dynamo by default.

  PYTHONPATH=/work/software/perception/tools/classic_onnx:$PYTHONPATH bash build_all_engines.sh
"""

try:
    import functools

    import torch

    torch.onnx.export = functools.partial(torch.onnx.export, dynamo=False)
except ImportError:
    pass
