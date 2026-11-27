#!/bin/sh
# On Spot's Orin, as root, once: the students' "traffic" account, which can only forward a port to live
# mode (127.0.0.1:8100) and can't get a shell. The owner approved it on 2026-11-27. Run it from the Mac,
# in the separate Terminal app, with the students' PUBLIC key file copied to the Orin first:
#   scp traffic-team.pub dino@10.18.3.175:/tmp/
#   ssh -t dino@10.18.3.175 sudo sh /home/opendubs/opendubs-spot/orin/add-traffic-account.sh /tmp/traffic-team.pub YYYYMMDD
# YYYYMMDD is the owner's expiry date for the key. The students then connect with
#   ssh -N -L 8100:127.0.0.1:8100 traffic@<the Orin>     and drive ws://127.0.0.1:8100/live
#
# What it does: makes a system account with no password and no shell, puts the key in it with
# restrict, port-forwarding to 127.0.0.1:8100 only, the expiry and /bin/false, and appends a
# "Match User traffic" block to sshd_config that allows the key alone and that one forward. It checks the
# config with sshd -t before reloading, and puts the old config back if the check fails.
# Running it again replaces the key line and leaves the Match block as it is.
set -eu
[ "$(id -u)" = 0 ] || { echo "refused: run it with sudo" >&2; exit 1; }
[ $# = 2 ] || { echo "usage: $0 PUBLIC_KEY_FILE YYYYMMDD" >&2; exit 1; }
KEYFILE=$1 EXPIRY=$2
CONF=/etc/ssh/sshd_config

echo "$EXPIRY" | grep -Eq '^20[0-9]{6}$' || { echo "refused: the expiry is YYYYMMDD, e.g. 20270601" >&2; exit 1; }
[ "$EXPIRY" -gt "$(date +%Y%m%d)" ] || { echo "refused: the expiry $EXPIRY is not in the future" >&2; exit 1; }
[ "$(grep -cv '^[[:space:]]*$' "$KEYFILE")" = 1 ] || { echo "refused: $KEYFILE must hold exactly one key" >&2; exit 1; }
grep -q 'PRIVATE KEY' "$KEYFILE" && { echo "refused: that is a PRIVATE key; it never leaves the students" >&2; exit 1; }
KEY=$(awk 'NF {print $1, $2; exit}' "$KEYFILE")
echo "$KEY" | grep -Eq '^ssh-ed25519 [A-Za-z0-9+/]+=*$' || { echo "refused: not an ssh-ed25519 public key" >&2; exit 1; }
ssh-keygen -l -f "$KEYFILE" >/dev/null || { echo "refused: ssh-keygen can't read the key" >&2; exit 1; }

# An AllowUsers or AllowGroups line elsewhere would lock the account out (or, worse, be widened by hand).
if grep -hEq '^[[:space:]]*(AllowUsers|AllowGroups)' "$CONF" /etc/ssh/sshd_config.d/*.conf 2>/dev/null; then
    echo "refused: sshd has AllowUsers/AllowGroups; tell the Spot lane, which decides how traffic fits in" >&2
    exit 1
fi

id traffic >/dev/null 2>&1 || adduser --system --group --shell /usr/sbin/nologin --home /home/traffic traffic
install -d -m 700 -o traffic -g traffic /home/traffic/.ssh
printf 'restrict,port-forwarding,permitopen="127.0.0.1:8100",expiry-time="%s",command="/bin/false" %s traffic-team\n' \
    "$EXPIRY" "$KEY" > /home/traffic/.ssh/authorized_keys
chown traffic:traffic /home/traffic/.ssh/authorized_keys
chmod 600 /home/traffic/.ssh/authorized_keys

if ! grep -q '^Match User traffic$' "$CONF"; then
    cp -p "$CONF" "$CONF.before-traffic"
    cat >> "$CONF" <<'EOF'

# opendubs: the students' traffic account forwards to Spot's live mode only (software/spot/orin/add-traffic-account.sh)
Match User traffic
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:8100
    PermitListen none
    X11Forwarding no
    AllowAgentForwarding no
    PermitTTY no
    ForceCommand /bin/false
EOF
fi
if ! sshd -t; then
    [ -f "$CONF.before-traffic" ] && cp -p "$CONF.before-traffic" "$CONF"
    echo "refused: sshd -t failed, so the old sshd_config is back and ssh was not reloaded" >&2
    exit 1
fi
echo "sshd -t: OK"
systemctl reload ssh
echo "traffic: key $(ssh-keygen -l -f "$KEYFILE" | awk '{print $2}'), expires $EXPIRY, forwards to 127.0.0.1:8100 only"
echo "Done."
