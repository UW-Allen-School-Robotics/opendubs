#!/usr/bin/env bash
# Install the pinned, checksum-verified gitleaks binary into a directory, for CI.
#
#   tools/hygiene/install-gitleaks.sh [dir]      (default: $RUNNER_TEMP, else a new temp dir)
#
# Linux x64 only: it is meant for GitHub Actions runners. On a Mac, `brew install gitleaks`.
# Under GitHub Actions the directory is also added to PATH for the steps that follow.
# This is the one place the version and its checksum are pinned; every workflow that runs a hygiene
# check installs gitleaks through it.
set -euo pipefail

VERSION="8.30.1"
SHA256="551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"   # gitleaks_8.30.1_linux_x64.tar.gz

dir="${1:-${RUNNER_TEMP:-$(mktemp -d)}}"
mkdir -p "$dir"
tgz="gitleaks_${VERSION}_linux_x64.tar.gz"
curl -fsSLo "$dir/$tgz" "https://github.com/gitleaks/gitleaks/releases/download/v${VERSION}/$tgz"
echo "$SHA256  $dir/$tgz" | sha256sum -c - >/dev/null
tar -xzf "$dir/$tgz" -C "$dir" gitleaks
rm -f "$dir/$tgz"
if [ -n "${GITHUB_PATH:-}" ]; then
    echo "$dir" >> "$GITHUB_PATH"
fi
echo "gitleaks $VERSION installed in $dir"
