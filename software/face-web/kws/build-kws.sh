#!/usr/bin/env bash
# Build the face page's wake-word spotter: sherpa-onnx's keyword spotter compiled to WebAssembly, with
# the English KWS model baked into its data file (engine/wake.js, notes/workplan-voice.md).
#
#   software/face-web/kws/build-kws.sh            # build into software/face-web/vendor/kws/
#   software/face-web/kws/build-kws.sh --check    # exit 0 if vendor/kws/ holds this exact build
#
# WHY A BUILD: sherpa-onnx publishes browser builds of its TTS and VAD but not of its keyword spotter,
# and the build preloads the model into its data file (it exposes no file system to load one later). So
# it is compiled here, once, on the dev machine, from pinned sources; the deploy copies vendor/kws/ to
# the robot with the rest of the face's vendor files (deploy.sh), so an offline robot needs nothing.
#
# Needs the internet, git, cmake and ~2 GB of scratch (the Emscripten SDK), never in the repo:
# KWS_WORK=<dir> to keep the work tree between runs (default: a temporary directory, removed after).
set -euo pipefail

SHERPA_TAG=v1.13.8                                  # k2-fsa/sherpa-onnx, Apache-2.0
EMSDK_VERSION=4.0.23                                # the version sherpa-onnx's build script names
MODEL=sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01   # Apache-2.0 (its model card)
MODEL_URL="https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$MODEL.tar.bz2"
MODEL_SHA256=f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a
STAMP="sherpa-onnx $SHERPA_TAG, emsdk $EMSDK_VERSION, $MODEL (int8), $MODEL_SHA256"

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../vendor/kws"
FILES=(sherpa-onnx-wasm-kws-main.js sherpa-onnx-wasm-kws-main.wasm sherpa-onnx-wasm-kws-main.data sherpa-onnx-kws.js)

if [ "${1:-}" = "--check" ]; then
  [ -f "$OUT/VERSION" ] && [ "$(cat "$OUT/VERSION")" = "$STAMP" ] || exit 1
  for f in "${FILES[@]}"; do [ -s "$OUT/$f" ] || exit 1; done
  exit 0
fi

WORK="${KWS_WORK:-$(mktemp -d)}"
[ -n "${KWS_WORK:-}" ] || trap 'rm -rf "$WORK"' EXIT
cd "$WORK"
echo "== working in $WORK"

if [ ! -d emsdk ]; then git clone --quiet https://github.com/emscripten-core/emsdk.git; fi
( cd emsdk && ./emsdk install "$EMSDK_VERSION" >/dev/null && ./emsdk activate "$EMSDK_VERSION" >/dev/null )
# shellcheck disable=SC1091
source emsdk/emsdk_env.sh >/dev/null 2>&1

if [ ! -d sherpa-onnx ]; then git clone --quiet --depth 1 --branch "$SHERPA_TAG" https://github.com/k2-fsa/sherpa-onnx.git; fi

curl -fsSL --connect-timeout 10 --max-time 600 -o model.tar.bz2 "$MODEL_URL"
echo "$MODEL_SHA256  model.tar.bz2" | shasum -a 256 -c - >/dev/null
tar xjf model.tar.bz2
# The int8 models, under the names the build expects (wasm/kws/assets/README.md): ~5 MB, not ~14.
A=sherpa-onnx/wasm/kws/assets
for part in encoder decoder joiner; do
  cp "$MODEL/$part-epoch-12-avg-2-chunk-16-left-64.int8.onnx" "$A/$part-epoch-12-avg-2-chunk-16-left-64.onnx"
done
cp "$MODEL/tokens.txt" "$A/tokens.txt"

( cd sherpa-onnx && ./build-wasm-simd-kws.sh )

SRC="$(dirname "$(find sherpa-onnx/build-wasm-simd-kws/install -name sherpa-onnx-wasm-kws-main.js | head -1)")"
mkdir -p "$OUT"
for f in "${FILES[@]}"; do cp "$SRC/$f" "$OUT/$f"; done
cp sherpa-onnx/LICENSE "$OUT/LICENSE"
echo "$STAMP" > "$OUT/VERSION"
echo "== built: $OUT"
ls -la "$OUT"
