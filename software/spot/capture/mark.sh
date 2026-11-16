#!/usr/bin/env bash
# Add a mark to a running Spot capture: the Orin's time now and a free-text note.
#   bash mark.sh DATA_ROOT/CAPTURE_ID "roof: at the door"
# Marks can also be added afterwards, from photo times or memory; this is only the quick way.
set -u
DIR=${1:?usage: mark.sh CAPTURE_DIR "note"}
NOTE=${2:?usage: mark.sh CAPTURE_DIR "note"}
# HallPass's marks.txt line (opendubs-site sitekit/capture/bag.py): ISO time TAB note TAB key=value fields
NOW=$(date -u +%s.%N)
printf '%s\t%s\tstamp=%s\n' "$(date -u -d "@$NOW" +%FT%T.%3NZ)" "${NOTE//$'\t'/ }" "$NOW" >> "$DIR/marks.txt"
tail -1 "$DIR/marks.txt"
