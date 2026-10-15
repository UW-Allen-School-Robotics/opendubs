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
# Every file pinned: a changed package or model (a re-published version, a different CDN answer) fails
# here rather than reaching a robot. Update these deliberately, together with MEDIAPIPE above.
sum256() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
if ! ( cd vendor/mediapipe && sum256 -c --quiet - ) <<'PINS'
fc3e9325a460f9ca70b93d58e850532a30fd14ceb6d02c837912af3cf65ed651  VERSION
64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff  face_landmarker.task
55d7ab624fbb70dcc5adc4ae6d7ea9cfcb569139d3dbfbf2b1deafcb966bc0fe  vision_bundle.mjs
e7fd9858e8e8f221d9b96eddc11f8e077f263e0b7bbd79d3cbe882b134274f8c  wasm/vision_wasm_internal.js
6a5c64584c2ab61c763b6e204afbdbc7ce1caf7f5216187322bca8df94f646bc  wasm/vision_wasm_internal.wasm
PINS
then
  rm -rf vendor/mediapipe
  echo "fetch-vendor: a MediaPipe file does not match its pinned sha256; removed vendor/mediapipe" >&2
  exit 1
fi
# The face must not phone home. Fail loudly if a version change brings the usage logger back.
if grep -rqs "odml.pa.googleapis.com" vendor/mediapipe; then
  echo "fetch-vendor: this MediaPipe build contains the usage-logging endpoint; pin one without it" >&2
  exit 1
fi
