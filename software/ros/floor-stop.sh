#!/bin/bash
HERE=$(dirname "$(readlink -f "$0")")
# floor-stop.sh: SIGINT the floor unit (it saves the map + capture.yaml), stop the [ODO] log, wait for the save.
sudo -n systemctl kill -s INT opendubs-floor-map; sudo -n systemctl stop opendubs-odo-log
for i in $(seq 1 120); do systemctl is-active -q opendubs-floor-map || break; sleep 5; done
journalctl -u opendubs-floor-map --no-pager -o cat | grep -v pam_unix | sed -n '/recording closed/,$p'
systemctl is-active opendubs-floor-map opendubs-odo-log | tr '\n' ' '; echo
