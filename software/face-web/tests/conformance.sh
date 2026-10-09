#!/usr/bin/env bash
# The face engine, headless, held to the head protocol: its face half in Node, a reference neck
# beside it, and the conformance suite with --active --motion (local only). Used by CI.
set -euo pipefail
cd "$(dirname "$0")/../../.."
FACE=${FACE_PORT:-14244}; NECK=${NECK_PORT:-14245}
node software/face-web/tests/node_door.mjs "$FACE" & D=$!
python3 software/head-protocol/fake_head.py --kind neck --port "$NECK" & N=$!
trap 'kill $D $N 2>/dev/null || true' EXIT
sleep 1
python3 software/head-protocol/conformance.py "127.0.0.1:$FACE" --neck "127.0.0.1:$NECK" --active --motion
