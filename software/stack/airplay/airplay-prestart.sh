#!/usr/bin/env bash
# ExecStartPre for opendubs-airplay.service: the two FIFOs the receiver writes and the dashboard
# reads, owned so that kiosk (the dashboard's user) can read them and nobody else can. systemd has
# just created /run/opendubs/airplay (RuntimeDirectory) and removes it when the unit stops.
set -euo pipefail
D=/run/opendubs/airplay
chgrp kiosk "$D"
chmod 0750 "$D"
for f in audio.pcm meta; do
  [ -p "$D/$f" ] || mkfifo "$D/$f"
  chgrp kiosk "$D/$f"
  chmod 0660 "$D/$f"
done
