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

OUT_FILE="$OUT_DIR_ABS/inline-css-cleanup.ankiaddon"

# Create .ankiaddon (zip) with only runtime-required files
(
  cd "$ADDON_DIR"
  # -q for quiet, -r recurse, -X no extra file attributes
  zip -q -X "$OUT_FILE" __init__.py manifest.json config.json config.md README.md user_files/README.txt
)

echo "Wrote $OUT_FILE"
