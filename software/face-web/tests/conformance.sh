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
node software/face-web/tests/test_skin_contract.mjs  # the skin contract: what the engine hands a skin, and no clip left behind
node software/face-web/tests/test_render_hooks.mjs   # offline render: no hidden clock, a seeded source, skinsReady
node software/face-web/tests/test_armed.mjs          # the ARMED overlay, and its lease
node software/face-web/tests/test_caption.mjs        # the plain-English state label: white, in its band, never overflowing
node software/face-web/tests/test_camera_sign.mjs    # a camera is on: shown when one is, never when none is
node software/face-web/tests/test_caption_call.mjs   # CAPTION: kept, renewed or dropped, refused if long, drawn first
node software/face-web/tests/test_charging.mjs       # CHARGING: the purple, the edge gauge, and no invented level
node software/face-web/tests/test_themes.mjs         # a theme loaded from the content folder, and what it may not recolour
node software/face-web/tests/test_reticle.mjs        # the tracking reticle: legible, on screen, and never invented
node software/face-web/tests/test_skin_refresh.mjs   # a skin added after the page loaded
node software/face-web/tests/test_content_skins.mjs  # a code skin loaded from the content folder
node software/face-web/tests/test_levels.mjs        # v5: VOLUME/BRIGHT fades and clamps, ARMED's lease
node software/face-web/tests/test_percept.mjs       # attention, ported from the Tab5: motion, person, face, arbitration
node software/face-web/tests/test_render.mjs        # the offline render: same seed and times, same frames
node software/face-web/tests/test_render_browser.mjs # ... and the same PIXELS in a real Chrome (SKIP without one)
node software/face-web/tests/test_reticle_call.mjs     # the tracking reticle: drawn first, from the eyes' target, never on a fault face
node software/face-web/tests/test_camera_indicator.mjs  # the face says so whenever a camera is open
node software/face-web/tests/test_face_page_browser.mjs # ... live, with a fake camera; and the tap hint (SKIP without Chrome)
python3 software/face-web/tests/test_frame_tap.py    # the body twin's picture (no ports, no page)

FACE=${FACE_PORT:-14244}; NECK=${NECK_PORT:-14245}
node software/face-web/tests/node_door.mjs "$FACE" & D=$!
python3 software/head-protocol/fake_head.py --kind neck --port "$NECK" & N=$!
trap 'kill $D $N 2>/dev/null || true' EXIT
sleep 1
python3 software/head-protocol/conformance.py "127.0.0.1:$FACE" --neck "127.0.0.1:$NECK" --active --motion
