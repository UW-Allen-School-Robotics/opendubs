#!/usr/bin/env bash
# Fetch the face engine's third-party files into vendor/ (git-ignored: ~20 MB of WebAssembly and a
# model, which are pinned here rather than committed). Run once, and again when a version changes.
# Everything the page loads is then local, so the face runs with no network — on the tablet they are
# bundled into the app (tablet plan DT-9).
set -euo pipefail
cd "$(dirname "$0")"

MEDIAPIPE=0.10.35     # @mediapipe/tasks-vision, Apache-2.0. NOT 1.x: 1.0.0+ POSTs usage logs to odml.pa.googleapis.com every 60 s; 0.10.35 has no such endpoint (checked by grep, below)
MODEL_URL=https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
( cd "$tmp" && npm pack --silent "@mediapipe/tasks-vision@$MEDIAPIPE" >/dev/null && tar xzf ./*.tgz )

mkdir -p vendor/mediapipe/wasm
cp "$tmp/package/vision_bundle.mjs" vendor/mediapipe/
cp "$tmp"/package/wasm/vision_wasm_internal.* vendor/mediapipe/wasm/
cp "$tmp/package/LICENSE" vendor/mediapipe/ 2>/dev/null || true
curl -fsSL -o vendor/mediapipe/face_landmarker.task "$MODEL_URL"
echo "$MEDIAPIPE" > vendor/mediapipe/VERSION
du -sh vendor/mediapipe
# The face must not phone home. Fail loudly if a version change brings the usage logger back.
if grep -rqs "odml.pa.googleapis.com" vendor/mediapipe; then
  echo "fetch-vendor: this MediaPipe build contains the usage-logging endpoint; pin one without it" >&2
  exit 1
fi
