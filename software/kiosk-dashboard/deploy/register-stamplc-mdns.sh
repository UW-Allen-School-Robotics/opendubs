#!/usr/bin/env bash
# Give the StamPLC a name: opendubs-stamplc.local
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
