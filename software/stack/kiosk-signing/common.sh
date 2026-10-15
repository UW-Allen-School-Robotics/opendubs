# Shared settings for the kiosk-signing scripts. Sourced, not run.
#
# The keystore folder holds SECRETS. Nothing here ever prints a password or copies a key anywhere;
# apksigner and keytool read the password from a file (`file:` / `:file`), never from a command line,
# where any user could read it in `ps`.
#
# The paths can be overridden for a dry run on a laptop with throwaway keys; on a robot the defaults
# are the only ones that should ever be used.
KS_DIR="${OPENDUBS_KIOSK_KS_DIR:-/etc/opendubs/kiosk-signing}"   # root 0700, every file 0400
OUT_DIR="${OPENDUBS_KIOSK_OUT_DIR:-/var/lib/opendubs/kiosk}"     # root:kiosk 0755, files 0644
OUT_GROUP="${OPENDUBS_KIOSK_OUT_GROUP:-kiosk}"
RELEASE_KS="$KS_DIR/release.p12"
RELEASE_PASS="$KS_DIR/release.pass"
RELEASE_SHA="$KS_DIR/release.sha256"     # the release certificate's SHA-256 (public)
LINEAGE="$KS_DIR/lineage"                # proof of rotation, debug -> release (public, kept here)
DEBUG_KS="$KS_DIR/debug.keystore"        # present only for the one-time rotation
ALIAS=opendubs-kiosk

# The certificate that signs the kiosk installed on the tablet today: the build Mac's default
# Android debug key. Public; pinned so the rotation can only ever start from it.
# OPENDUBS_KIOSK_DEBUG_SHA256 overrides it for a laptop dry run with throwaway keys only; sudo's default
# env_reset drops it on a robot.
DEBUG_CERT_SHA256="${OPENDUBS_KIOSK_DEBUG_SHA256:-e781fe8c5c9192d7f68d3a80cb8b9ee49f2813ca471d9937039186456f8e7777}"

# apksigner: Ubuntu's package (`apksigner`, a java wrapper) unless APKSIGNER names another, e.g.
# "java -jar /path/build-tools/apksigner.jar" for a dry run.
APKSIGNER="${APKSIGNER:-apksigner}"

die() { echo "!! $*" >&2; exit 2; }

cert_sha256() {  # the SHA-256 of the certificate in keystore $1 (alias $2), from password file $3
  keytool -exportcert -rfc -keystore "$1" -alias "$2" -storepass:file "$3" 2>/dev/null \
    | openssl x509 -outform DER 2>/dev/null | openssl dgst -sha256 -r | cut -d' ' -f1
}
