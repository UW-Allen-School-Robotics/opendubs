#!/bin/bash
HERE=$(dirname "$(readlink -f "$0")")
# floor-stop.sh: stop the [ODO] log, then SIGINT the floor unit (it writes any waiting burst, stops the bags,
# moves the log into the capture, saves the map and capture.yaml); waits for the save.
sudo -n systemctl stop opendubs-odo-log; sudo -n systemctl kill -s INT opendubs-floor-map
for i in $(seq 1 120); do systemctl is-active -q opendubs-floor-map || break; sleep 5; done
journalctl -u opendubs-floor-map --no-pager -o cat | grep -v pam_unix | sed -n '/recording closed/,$p'
systemctl is-active opendubs-floor-map opendubs-odo-log | tr '\n' ' '; echo
