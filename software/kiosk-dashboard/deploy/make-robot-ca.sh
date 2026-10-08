#!/usr/bin/env bash
# This robot's own certificate authority, and the dashboard's HTTPS certificate signed by it
# (notes/design/tablet-device.md TD-1; choreography plan 4.1c).
#
#   sudo ./make-robot-ca.sh            # once: makes the CA (if absent) and the dashboard certificate
#   sudo ./make-robot-ca.sh --renew    # re-issue the dashboard certificate (yearly, or after an IP change)
#
# Run ON THE ROBOT. Each robot has its own CA, made here, and its private key never leaves this
# machine and is never committed (the per-robot rule). Only ca.crt — the public half — goes to this
# robot's tablet. The dashboard (backend/serve.py) serves HTTPS on 8443 once dashboard.{crt,key}
# exist, and restarts to pick them up.
set -euo pipefail

DIR="${OPENDUBS_TLS_DIR:-/etc/opendubs/tls}"   # override only to rehearse it somewhere else
SERVICE_USER="${OPENDUBS_DASH_USER:-kiosk}"     # the account opendubs-dash.service runs as
NAME="$(hostname)"
NAME="${NAME%.local}"                           # mDNS adds .local below
CA_DAYS=3650                                     # ten years: replacing it means touching every tablet
LEAF_DAYS=397                                    # about a year; renew with --renew

[ "$(id -u)" = 0 ] || [ -n "${OPENDUBS_TLS_DIR:-}" ] || { echo "run with sudo: it writes $DIR" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
mkdir -p "$DIR"
chmod 755 "$DIR"
umask 077

if [ ! -f "$DIR/ca.key" ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -days "$CA_DAYS" -nodes \
    -keyout "$DIR/ca.key" -out "$DIR/ca.crt" -subj "/CN=OpenDubs robot CA ($NAME)" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    2>/dev/null
  chmod 644 "$DIR/ca.crt"
  echo "made this robot's CA: $DIR/ca.crt (its key stays in $DIR/ca.key, root only)"
else
  echo "CA already exists: $DIR/ca.crt"
fi

if [ -f "$DIR/dashboard.crt" ] && [ "${1:-}" != "--renew" ]; then
  echo "dashboard certificate exists (expires $(openssl x509 -in "$DIR/dashboard.crt" -noout -enddate | cut -d= -f2)); --renew to re-issue"
else
  # Every name and address the tablet might use to reach the dashboard.
  SAN="DNS:$NAME,DNS:$NAME.local,DNS:localhost,IP:127.0.0.1"
  for ip in $(hostname -I 2>/dev/null); do
    case "$ip" in *:*) ;; *) SAN="$SAN,IP:$ip" ;; esac
  done
  ext="$(mktemp)"
  printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' "$SAN" > "$ext"
  openssl req -newkey rsa:2048 -nodes -keyout "$DIR/dashboard.key" -out "$DIR/dashboard.csr" \
    -subj "/CN=$NAME" 2>/dev/null
  openssl x509 -req -in "$DIR/dashboard.csr" -CA "$DIR/ca.crt" -CAkey "$DIR/ca.key" \
    -CAcreateserial -days "$LEAF_DAYS" -sha256 -extfile "$ext" -out "$DIR/dashboard.crt" 2>/dev/null
  rm -f "$ext" "$DIR/dashboard.csr"
  chmod 644 "$DIR/dashboard.crt"
  echo "issued the dashboard certificate for: $SAN"
fi

# The service account must read the key; nobody else may.
if id "$SERVICE_USER" >/dev/null 2>&1; then
  chown "root:$SERVICE_USER" "$DIR/dashboard.key"
  chmod 640 "$DIR/dashboard.key"
else
  echo "WARNING: no user '$SERVICE_USER' — set OPENDUBS_DASH_USER to the dashboard's account" >&2
fi

cat <<MSG

CA fingerprint (check it on the tablet): $(openssl x509 -in "$DIR/ca.crt" -noout -fingerprint -sha256 | cut -d= -f2)

Next:
  sudo systemctl restart opendubs-dash          # HTTPS comes up on :8443
  # From a machine with the tablet on adb — the kiosk app (device owner) installs the CA itself:
  adb shell am start -n org.opendubs.kiosk/.KioskActivity \\
      --es ca_b64 "\$(ssh <this robot> base64 -w0 $DIR/ca.crt)" \\
      --es url https://$NAME.local:8443/
MSG
