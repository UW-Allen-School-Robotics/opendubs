#!/bin/bash
# The choreography studio: the composer, the face twin and the MuJoCo body twin. Now the studio app
# (studio/studio.py, also installable as `opendubs-studio`); this stays as a shortcut from a checkout.
#   ./studio-up.sh            # = opendubs-studio up
# Settings: `opendubs-studio paths`; see studio/README.md.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
exec "$(command -v uv || echo "$HOME/.local/bin/uv")" run -q "$HERE/studio/studio.py" up "$@"
