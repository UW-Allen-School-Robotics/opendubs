#!/usr/bin/env bash
# The face engine, headless: first the pieces the protocol cannot see (spin geometry, a late skin,
# the body twin's picture), then the engine held to the head protocol -- its face half in Node, a
# reference neck beside it, and the conformance suite with --active --motion. Used by CI.
set -euo pipefail
cd "$(dirname "$0")/../../.."
# First the things the protocol suite cannot see. All cheap, and all fail before a port is opened.
node software/face-web/tests/test_draw_smoke.mjs     # every skin actually DRAWS, every state
node software/face-web/tests/test_spin.mjs           # SPIN's timing, and that nothing ever jumps
node software/face-web/tests/test_moves.mjs          # MOVE: each gesture lands on its published peak
node software/face-web/tests/test_layout_full.mjs   # layout: full -- a still across the whole face
node software/face-web/tests/test_lids_and_gaze.mjs  # lid-shape expression, and the gaze break
node software/face-web/tests/test_mouth.mjs          # the optional mouth; OFF must change nothing
node software/face-web/tests/test_armed.mjs          # the ARMED overlay, and its lease
node software/face-web/tests/test_skin_refresh.mjs   # a skin added after the page loaded
node software/face-web/tests/test_levels.mjs        # v5: VOLUME/BRIGHT fades and clamps, ARMED's lease
python3 software/face-web/tests/test_frame_tap.py    # the body twin's picture (no ports, no page)

FACE=${FACE_PORT:-14244}; NECK=${NECK_PORT:-14245}
node software/face-web/tests/node_door.mjs "$FACE" & D=$!
python3 software/head-protocol/fake_head.py --kind neck --port "$NECK" & N=$!
trap 'kill $D $N 2>/dev/null || true' EXIT
sleep 1
python3 software/head-protocol/conformance.py "127.0.0.1:$FACE" --neck "127.0.0.1:$NECK" --active --motion
