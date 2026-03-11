#!/usr/bin/env bash
set -euo pipefail

ADDON_DIR="${1:-.}"
OUT_DIR="${2:-.}"

if [[ ! -d "$ADDON_DIR" ]]; then
  echo "Addon dir not found: $ADDON_DIR" >&2
  exit 1
fi

if [[ ! -f "$ADDON_DIR/manifest.json" ]]; then
  echo "manifest.json not found in $ADDON_DIR" >&2
  exit 1
fi

if [[ ! -f "$ADDON_DIR/__init__.py" ]]; then
  echo "__init__.py not found in $ADDON_DIR" >&2
  exit 1
fi

if [[ ! -f "$ADDON_DIR/config.json" ]]; then
  echo "config.json not found in $ADDON_DIR" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
OUT_DIR_ABS=$(cd "$OUT_DIR" && pwd)

# Use package name from manifest.json if possible
PKG_NAME=$(python3 - "$ADDON_DIR/manifest.json" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as f:
    data = json.load(f)
print(data.get('package') or 'anki-addon')
PY
)

OUT_FILE="$OUT_DIR_ABS/${PKG_NAME}.ankiaddon"

# Create .ankiaddon (zip) with only runtime-required files
(
  cd "$ADDON_DIR"
  # -q for quiet, -r recurse, -X no extra file attributes
  zip -q -X "$OUT_FILE" __init__.py manifest.json config.json config.md README.md user_files/README.txt
)

echo "Wrote $OUT_FILE"
