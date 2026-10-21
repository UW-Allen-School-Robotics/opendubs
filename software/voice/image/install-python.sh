#!/bin/sh
# install-python.sh VARIANT — the voice image's Python, for Dockerfile.voice. Not for a host.
#
#   cpu  FROM the pinned ROS base: a new venv in /opt/voice/venv with CPU PyTorch.
#   gpu  FROM the pinned vLLM image (VOICE_LLM_IMAGE): its own venv, /opt/venv, already holds CUDA
#        PyTorch for the Thor, so the voice image reuses it and its layers are shared with
#        opendubs-llm. Kokoro runs on the GPU in about 0.1 s a sentence against 1 to 2 s on the CPU.
#
# Either way kokoro and misaki go in with --no-deps (English only, no GPL; NOTICE.md), and
# /opt/voice/python is a wrapper for the interpreter, so run.sh and CMD name one path for both.
set -eu
VARIANT=${1:?usage: install-python.sh cpu|gpu}
R=/opt/voice

case "$VARIANT" in
  cpu)
    apt-get update
    apt-get install -y --no-install-recommends python3-venv
    rm -rf /var/lib/apt/lists/*
    python3 -m venv "$R/venv"
    PY="$R/venv/bin/python"
    "$PY" -m pip install --no-cache-dir -r "$R/requirements-torch-cpu.txt"
    "$PY" -m pip install --no-cache-dir -r "$R/requirements-speech.txt"
    ;;
  gpu)
    # The base's interpreter lives under /root/.local (uv). Let other users traverse /root, without
    # listing it, so the non-root opendubs-voice user can run it.
    chmod 711 /root
    PY=/opt/venv/bin/python
    # Keep the base's PyTorch stack exactly as it is; add only what the voice needs.
    "$PY" -m pip freeze | grep -i -E '^(torch|transformers|numpy|huggingface[-_]hub|tokenizers|safetensors)==' \
      > "$R/base-constraints.txt" || true
    "$PY" -m pip install --no-cache-dir -c "$R/base-constraints.txt" -r "$R/requirements-speech-gpu.txt"
    # From the vLLM base, unused by the voice service, and refused by check_no_gpl: docutils lists
    # GPL among its licences, pycountry is LGPL and unreviewed.
    "$PY" -m pip uninstall -y docutils pycountry || true
    ;;
  *)
    echo "install-python.sh: unknown variant '$VARIANT' (cpu or gpu)" >&2
    exit 1
    ;;
esac

"$PY" -m pip install --no-cache-dir --no-deps -r "$R/requirements-speech-nodeps.txt"
"$PY" -m pip install --no-cache-dir -r "$R/requirements.txt"
printf '#!/bin/sh\nexec %s "$@"\n' "$PY" > "$R/python"
chmod 755 "$R/python"
"$R/python" -c "import sys; print('voice python:', sys.executable, sys.version.split()[0])"
