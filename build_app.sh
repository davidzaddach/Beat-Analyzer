#!/bin/bash
set -euo pipefail

#
# Build "Beat Analyzer.app" – a standalone macOS application bundle.
# Copies the existing uv-managed venv with all dependencies so the
# target Mac needs nothing pre-installed except macOS 12+.
#
# Usage:  ./build_app.sh
# Result: dist/Beat Analyzer.app
#

APP_NAME="Beat Analyzer"
BUNDLE_ID="com.davidmain.beat-analyzer"
VERSION="0.1.0"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST_DIR="$SCRIPT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
VENV_SRC="$SCRIPT_DIR/.venv"

echo "=== Building $APP_NAME.app ==="

# Verify source venv exists
if [ ! -d "$VENV_SRC" ]; then
    echo "ERROR: .venv not found. Run 'uv sync' or 'pip install -e .' first."
    exit 1
fi

PY_MM="$("$VENV_SRC/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")"
echo "Using Python ${PY_MM} from .venv"

# Clean previous build
rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"

# ── 1. Copy beats_cli package ──
echo "[1/8] Copying application code..."
cp -R "$SCRIPT_DIR/beats_cli" "$RESOURCES/beats_cli"
find "$RESOURCES/beats_cli" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── 2. Copy venv (site-packages + scripts) ──
echo "[2/8] Copying Python environment..."
cp -R "$VENV_SRC" "$RESOURCES/venv"

# ── 3. Replace Python symlinks with actual binary ──
echo "[3/8] Embedding Python interpreter..."
PYTHON_HOME="$(grep -E '^home[[:space:]]*=' "$VENV_SRC/pyvenv.cfg" | head -1 | cut -d'=' -f2 | tr -d ' ')"
REAL_PYTHON="$("$VENV_SRC/bin/python" -c "import sys; print(sys.executable)")"
PYTHON_BASE="$(dirname "$PYTHON_HOME")"

# Copy Python binary (replace symlinks)
VENV_BIN="$RESOURCES/venv/bin"
rm -f "$VENV_BIN/python" "$VENV_BIN/python3" "$VENV_BIN/python${PY_MM}"
cp "$REAL_PYTHON" "$VENV_BIN/python${PY_MM}"
ln -s "python${PY_MM}" "$VENV_BIN/python3"
ln -s "python${PY_MM}" "$VENV_BIN/python"

# Copy Python runtime library
mkdir -p "$RESOURCES/python_runtime"
PYTHON_DYLIB="$PYTHON_BASE/lib/libpython${PY_MM}.dylib"
if [ -f "$PYTHON_DYLIB" ]; then
    cp "$PYTHON_DYLIB" "$RESOURCES/python_runtime/"
fi
cp "$REAL_PYTHON" "$RESOURCES/python_runtime/python${PY_MM}"

# ── 3b. libSDL2 für Essentia ins Bundle (Build-Rechner: brew install sdl2) ──
SDL_LIB_DIR="$RESOURCES/lib"
mkdir -p "$SDL_LIB_DIR"
SDL2_SRC=""
if command -v brew >/dev/null 2>&1; then
  _sdl_prefix="$(brew --prefix sdl2 2>/dev/null || true)"
  if [[ -n "${_sdl_prefix}" && -f "${_sdl_prefix}/lib/libSDL2-2.0.0.dylib" ]]; then
    SDL2_SRC="${_sdl_prefix}/lib/libSDL2-2.0.0.dylib"
  fi
fi
if [[ -z "$SDL2_SRC" && -f "/opt/homebrew/opt/sdl2/lib/libSDL2-2.0.0.dylib" ]]; then
  SDL2_SRC="/opt/homebrew/opt/sdl2/lib/libSDL2-2.0.0.dylib"
fi
if [[ -z "$SDL2_SRC" && -f "/usr/local/opt/sdl2/lib/libSDL2-2.0.0.dylib" ]]; then
  SDL2_SRC="/usr/local/opt/sdl2/lib/libSDL2-2.0.0.dylib"
fi
if [[ -n "$SDL2_SRC" ]]; then
  echo "[3b/8] Bundling libSDL2 (Essentia)…"
  cp "$SDL2_SRC" "$SDL_LIB_DIR/"
  if command -v install_name_tool >/dev/null 2>&1; then
    install_name_tool -id "@loader_path/libSDL2-2.0.0.dylib" \
      "$SDL_LIB_DIR/libSDL2-2.0.0.dylib" 2>/dev/null || true
  fi
else
  echo "WARN: libSDL2 nicht gefunden — auf dem Build-Mac: brew install sdl2, dann build_app.sh erneut."
fi

# ── 3c. SDL2 für Essentia ohne DYLD_* (Finder-Start / Hardened Runtime) ──
# Viele .app ignorieren oder leeren DYLD_LIBRARY_PATH. Essentia dlopen't dann
# „libSDL2-2.0.0.dylib“ und zeigt „Failed loading SDL2 library“.
# Zwei Wege: Kopie neben _essentia*.so + LC_RPATH (@loader_path → gleicher Ordner,
# @loader_path/../../../../../lib → Contents/Resources/lib).
if [[ -f "$SDL_LIB_DIR/libSDL2-2.0.0.dylib" ]]; then
  echo "[3c/8] SDL2 für Essentia (rpath + Kopie neben Extension)…"
  ESSENTIA_DIR="$RESOURCES/venv/lib/python${PY_MM}/site-packages/essentia"
  if [[ -d "$ESSENTIA_DIR" ]]; then
    cp -f "$SDL_LIB_DIR/libSDL2-2.0.0.dylib" "$ESSENTIA_DIR/"
    while IFS= read -r _eso; do
      [[ -f "$_eso" ]] || continue
      install_name_tool -add_rpath @loader_path "$_eso" 2>/dev/null || true
      install_name_tool -add_rpath @loader_path/../../../../../lib "$_eso" 2>/dev/null || true
    done < <(find "$ESSENTIA_DIR" -maxdepth 1 -name '_essentia*.so' 2>/dev/null)
  fi
fi

# ── 4. Copy Python standard library ──
echo "[4/8] Copying Python standard library..."
mkdir -p "$RESOURCES/python_stdlib/lib"
cp -R "$PYTHON_BASE/lib/python${PY_MM}" "$RESOURCES/python_stdlib/lib/python${PY_MM}"
# Remove unnecessary modules to save space
rm -rf "$RESOURCES/python_stdlib/lib/python${PY_MM}/test" \
       "$RESOURCES/python_stdlib/lib/python${PY_MM}/unittest/test" \
       "$RESOURCES/python_stdlib/lib/python${PY_MM}/idlelib" \
       "$RESOURCES/python_stdlib/lib/python${PY_MM}/tkinter" \
       "$RESOURCES/python_stdlib/lib/python${PY_MM}/turtledemo" \
       "$RESOURCES/python_stdlib/lib/python${PY_MM}/ensurepip" 2>/dev/null || true
find "$RESOURCES/python_stdlib" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── 5. Mach-O-Launcher (nicht Bash): sonst kein sinnvolles codesign, dlopen bricht mit
#     „Code Signature Invalid“ (NumPy/TensorFlow) auf aktuellen macOS-Versionen ab.
echo "[5/8] Compiling Mach-O launcher (clang)…"
LAUNCHER_SRC="$SCRIPT_DIR/build/mac_launcher.c"
if [[ ! -f "$LAUNCHER_SRC" ]]; then
  echo "ERROR: $LAUNCHER_SRC fehlt."
  exit 1
fi
if ! command -v clang >/dev/null 2>&1; then
  echo "ERROR: clang nicht gefunden (Xcode Command Line Tools: xcode-select --install)."
  exit 1
fi
clang -O2 -Wall -Wextra -mmacosx-version-min=12.0 \
  -DPY_MM_STR=\"${PY_MM}\" \
  -o "$MACOS/$APP_NAME" \
  "$LAUNCHER_SRC"
chmod +x "$MACOS/$APP_NAME"

# ── 6. Create Info.plist ──
echo "[6/8] Creating Info.plist..."
cat > "$CONTENTS/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>
    <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleExecutable</key>
    <string>$APP_NAME</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleSignature</key>
    <string>????</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>Beat Analyzer needs Apple Events access to set Finder comments on audio files.</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.music</string>
</dict>
</plist>
PLIST

# ── 7. App icon ──
echo "[7/8] Creating app icon..."
ICONSET="$SCRIPT_DIR/build/AppIcon.iconset"
if [ -f "$SCRIPT_DIR/build/AppIcon.icns" ]; then
    cp "$SCRIPT_DIR/build/AppIcon.icns" "$RESOURCES/AppIcon.icns"
    /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string AppIcon" "$CONTENTS/Info.plist" 2>/dev/null || \
        /usr/libexec/PlistBuddy -c "Set :CFBundleIconFile AppIcon" "$CONTENTS/Info.plist"
fi

# ── 8. Ad-hoc-Signatur: install_name_tool invalidiert Signaturen; ohne Neu-Signatur
#     verweigert dyld beim dlopen von Erweiterungen die Seiten (CODESIGNING / Invalid Page).
echo "[8/8] Ad-hoc codesign (alle .dylib/.so + Bundle)…"
if command -v codesign >/dev/null 2>&1; then
  while IFS= read -r -d '' _sigf; do
    codesign --force --sign - --timestamp=none "$_sigf" 2>/dev/null || true
  done < <(find "$APP_DIR" -type f \( -name '*.dylib' -o -name '*.so' \) -print0)
  for _sigf in "$RESOURCES/venv/bin/python${PY_MM}" \
               "$RESOURCES/python_runtime/python${PY_MM}" \
               "$RESOURCES/python_runtime/libpython${PY_MM}.dylib" \
               "$MACOS/$APP_NAME"; do
    [[ -f "$_sigf" ]] && codesign --force --sign - --timestamp=none "$_sigf" 2>/dev/null || true
  done
  if codesign --force --deep --sign - --timestamp=none "$APP_DIR" 2>/dev/null; then
    echo "  Bundle ad-hoc signiert."
  else
    echo "  WARN: codesign --deep für .app fehlgeschlagen — .so/.dylib wurden einzeln signiert."
  fi
else
  echo "  WARN: codesign nicht im PATH — App kann unter macOS beim ML-Start abstürzen."
fi

# Calculate size
APP_SIZE=$(du -sh "$APP_DIR" | cut -f1)

echo ""
echo "=== Build complete ==="
echo "  App:  $APP_DIR"
echo "  Size: $APP_SIZE"
echo ""
echo "To test:       open \"$APP_DIR\""
echo "To distribute: ./create_dmg.sh"
