#!/usr/bin/env bash
# mDNS names on this robot. Two jobs:
#   1. Publish opendubs-stamplc.local on the StamPLC's behalf (below).
#   2. Make the THOR'S OWN name resolvable, which needs a fix to avahi-daemon.conf.
#
# The StamPLC cannot advertise itself. The Tab5 can — its firmware calls
# ArduinoOTA.setHostname("opendubs-tab5") — but the StamPLC's network is a W5500 behind the
# Arduino Ethernet library, which has no mDNS responder. So the Thor publishes the name on its
# behalf via /etc/avahi/hosts.
#
# The address is NOT hardcoded here. It is read from the dashboard, which is already receiving
# the board's telemetry and therefore knows where it is. Re-run this after a DHCP change; it is
# idempotent.
#
# Usage:  sudo ./register-stamplc-mdns.sh [ip]
set -u

NAME=opendubs-stamplc.local
HOSTS=/etc/avahi/hosts
AVAHI_CONF=/etc/avahi/avahi-daemon.conf

# ---- the Thor's own name -----------------------------------------------------------------
# avahi publishes an A record for the host using whatever interface it feels like, and it chose
# DOCKER0: `avahi-resolve -4 -n opendubs2.local` returned 172.17.0.1, an address that means
# nothing off this machine. So `opendubs2.local` was useless and the dashboard had to be reached
# by IP — which then moved, because the wired address is a DHCP lease.
#
# An ALLOW-list, not deny-interfaces=docker0: Docker creates further br-* bridges on demand, so a
# deny list needs updating every time and will silently rot. Restricting to the one LAN interface
# also keeps the campus Wi-Fi address from being published, which is not where the tablet, the
# Quest or the StamPLC live.
#
# The interface is DERIVED, not hardcoded: whichever one routes to the StamPLC is by definition
# the robot LAN. That keeps this correct on a different Jetson, a renamed interface, or a new
# subnet, none of which a literal "enP2p1s0" would survive.
fix_avahi_interface() {
  local ip="$1" iface
  iface="$(ip -o route get "$ip" 2>/dev/null | grep -oE "dev [^ ]+" | awk "{print \$2}" | head -1)"
  [ -n "$iface" ] || { echo "WARN: could not determine the interface routing to $ip — leaving $AVAHI_CONF alone"; return 0; }

  if grep -qE "^allow-interfaces=$iface\$" "$AVAHI_CONF"; then
    echo "avahi already restricted to $iface"
    return 0
  fi
  cp -n "$AVAHI_CONF" "$AVAHI_CONF.orig" 2>/dev/null || true
  if grep -qE "^allow-interfaces=" "$AVAHI_CONF"; then
    sed -i "s/^allow-interfaces=.*/allow-interfaces=$iface/" "$AVAHI_CONF"
  else
    sed -i "s/^\[server\]\$/[server]\nallow-interfaces=$iface/" "$AVAHI_CONF"
  fi
  grep -qE "^allow-interfaces=$iface\$" "$AVAHI_CONF" || { echo "ERROR: failed to set allow-interfaces in $AVAHI_CONF"; return 1; }
  echo "avahi restricted to $iface (was publishing a docker bridge address)"
  systemctl restart avahi-daemon
  sleep 2
  echo "  $(hostname).local -> $(avahi-resolve -4 -n "$(hostname).local" 2>/dev/null | awk "{print \$2}")"
}

ip_arg="${1:-}"
if [ -n "$ip_arg" ]; then
  IP="$ip_arg"
else
  IP="$(curl -s --max-time 3 http://127.0.0.1:8080/api/snapshot 2>/dev/null \
        | tr ',{}' '\n\n\n' | grep -oE '"port": *"[0-9.]+:[0-9]+"' \
        | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
fi

[ -n "${IP:-}" ] || {
  echo "ERROR: no address. Is opendubs-dash running and hearing the StamPLC?"
  echo "       Check http://127.0.0.1:8080/api/snapshot -> status.stamplc, or pass an IP."
  exit 1
}

# Never publish one of our own addresses: the dashboard has reported the Thor's own (DOWN)
# l4tbr0 at 192.168.55.1 before, and a name pointing at this host is worse than no name.
if ip -4 addr show 2>/dev/null | grep -qE "inet $IP/"; then
  echo "ERROR: $IP is one of this host's own addresses — refusing to publish $NAME for it."
  exit 1
fi

# Do this first: it restarts avahi, and the hosts-file reload below then lands on a daemon
# already publishing on the right interface.
fix_avahi_interface "$IP"

install -d -m 0755 "$(dirname "$HOSTS")"
touch "$HOSTS"
# Replace any existing line for this name, keep everything else.
tmp="$(mktemp)"
grep -v "[[:space:]]${NAME}\$" "$HOSTS" > "$tmp" || true
printf '%s %s\n' "$IP" "$NAME" >> "$tmp"
install -m 0644 "$tmp" "$HOSTS"
rm -f "$tmp"

systemctl reload avahi-daemon 2>/dev/null || systemctl restart avahi-daemon 2>/dev/null || true
echo "== $NAME -> $IP =="
sleep 1
avahi-resolve -n "$NAME" 2>/dev/null || echo "(not resolving yet — give avahi a moment)"
