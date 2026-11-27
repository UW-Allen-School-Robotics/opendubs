#!/bin/sh
# On the Mac: copy the committed software/spot (HEAD, not the working tree) to ~/opendubs-spot on the
# Orin, and note the commit there. Plans in ~/.opendubs-spot/plans on the Orin are left alone.
#   software/spot/orin/sync.sh
set -e
cd "$(git rev-parse --show-toplevel)"
host="${SPOT_ORIN:-spot-orin-campus}"
sha=$(git rev-parse --short HEAD)
if ssh "$host" 'pgrep -f "[s]pot_player.py" >/dev/null'; then
    echo "refused: a player is running on the Orin; stop it (Ctrl-C in its terminal), then sync" >&2; exit 1
fi
git archive HEAD software/spot | ssh "$host" \
    "rm -rf ~/opendubs-spot.new && mkdir ~/opendubs-spot.new && tar -x -C ~/opendubs-spot.new --strip-components=2 \
     && echo $sha > ~/opendubs-spot.new/COMMIT && rm -rf ~/opendubs-spot && mv ~/opendubs-spot.new ~/opendubs-spot"
echo "the Orin ($host) now has software/spot at $sha"
