#!/bin/bash
set -euo pipefail

#
# Create a distributable DMG from "Beat Analyzer.app"
#
# Usage:  ./create_dmg.sh
# Result: dist/Beat Analyzer.dmg
#

APP_NAME="Beat Analyzer"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
DMG_NAME="$APP_NAME"
DMG_PATH="$DIST_DIR/$DMG_NAME.dmg"
DMG_TEMP="$DIST_DIR/tmp_dmg"
VOL_NAME="$APP_NAME"

if [ ! -d "$APP_DIR" ]; then
    echo "ERROR: $APP_DIR not found. Run ./build_app.sh first."
    exit 1
fi

echo "=== Creating $DMG_NAME.dmg ==="

# Clean
rm -rf "$DMG_TEMP" "$DMG_PATH"
mkdir -p "$DMG_TEMP"

# Copy .app to temp staging area
echo "[1/3] Staging application..."
cp -R "$APP_DIR" "$DMG_TEMP/$APP_NAME.app"

# Create symlink to /Applications for drag-install
ln -s /Applications "$DMG_TEMP/Applications"

# Create DMG
echo "[2/3] Creating DMG..."
hdiutil create \
    -volname "$VOL_NAME" \
    -srcfolder "$DMG_TEMP" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "$DMG_PATH" \
    -quiet

# Clean up
rm -rf "$DMG_TEMP"

# Report
DMG_SIZE=$(du -sh "$DMG_PATH" | cut -f1)

echo "[3/3] Done!"
echo ""
echo "=== DMG created ==="
echo "  File: $DMG_PATH"
echo "  Size: $DMG_SIZE"
echo ""
echo "To install on another Mac:"
echo "  1. Open $DMG_NAME.dmg"
echo "  2. Drag '$APP_NAME' into Applications"
echo "  3. Right-click > Open (first launch only, for Gatekeeper)"
