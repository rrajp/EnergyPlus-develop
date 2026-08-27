#!/usr/bin/env bash
# Download an EnergyPlus runtime into apps/engine/.
#
# The server auto-discovers whatever lands here, so this is the only setup
# step that needs network access. Override the release with EPLUS_VERSION.
set -euo pipefail

VERSION="${EPLUS_VERSION:-26.1.0}"
BUILD="${EPLUS_BUILD:-6f2e40d102}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HERE/engine"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64)  ASSET="Darwin-macOS13-arm64" ;;
  Darwin-x86_64) ASSET="Darwin-macOS13-x86_64" ;;
  Linux-x86_64)  ASSET="Linux-Ubuntu24.04-x86_64" ;;
  Linux-aarch64) ASSET="Linux-Ubuntu24.04-arm64" ;;
  *)
    echo "No prebuilt EnergyPlus for $(uname -s)-$(uname -m)." >&2
    echo "Install it yourself and point ENERGYPLUS_DIR at the directory." >&2
    exit 1
    ;;
esac

NAME="EnergyPlus-${VERSION}-${BUILD}-${ASSET}"
URL="https://github.com/NREL/EnergyPlus/releases/download/v${VERSION}/${NAME}.tar.gz"

if [ -d "$DEST/$NAME" ]; then
  echo "Already present: $DEST/$NAME"
  exit 0
fi

echo "Fetching $NAME ..."
mkdir -p "$DEST"
curl -fL --progress-bar -o "$DEST/ep.tar.gz" "$URL"
tar xzf "$DEST/ep.tar.gz" -C "$DEST"
rm -f "$DEST/ep.tar.gz"

"$DEST/$NAME/energyplus" --version
echo "Installed to $DEST/$NAME"
