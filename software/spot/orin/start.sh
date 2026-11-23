#!/bin/sh
# On the Mac, in the separate Terminal app: start the player on Spot's Orin and bring its operator page
# to this Mac at http://127.0.0.1:18099/ (an SSH tunnel; the player listens on the payload network only).
#   software/spot/orin/start.sh [--fake]
# It asks for Spot's login on this terminal; the Orin keeps it in memory only. Ctrl-C stops the player,
# and Spot sits. HOST is spot-orin-campus (campus Wi-Fi) unless SPOT_ORIN names another ~/.ssh/config host.
exec ssh -t -o ExitOnForwardFailure=yes -L 18099:192.168.50.5:8099 "${SPOT_ORIN:-spot-orin-campus}" \
    opendubs-spot/orin/run-player.sh "$@"
