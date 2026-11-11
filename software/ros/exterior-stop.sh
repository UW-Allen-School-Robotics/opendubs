#!/bin/bash
HERE=$(dirname "$(readlink -f "$0")")
sudo -n systemctl kill -s INT opendubs-ext-bag; sudo -n systemctl stop opendubs-odo-log
for i in $(seq 1 30); do systemctl is-active -q opendubs-ext-bag || break; sleep 2; done
systemctl is-active opendubs-ext-bag opendubs-odo-log | tr '\n' ' '; echo; du -sh /var/lib/opendubs/bags/*ext* 2>/dev/null
