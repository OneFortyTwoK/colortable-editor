#!/usr/bin/env bash
# Wrap PyInstaller's folder build (dist/ColortableEditor/) into ColortableEditor-x86_64.AppImage.
#   pyinstaller colortable_editor.spec --noconfirm && bash packaging/build_appimage.sh
# APPIMAGETOOL names an appimagetool to use; without it, this downloads AppImage's own.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [ ! -d "dist/ColortableEditor" ]; then
    echo "dist/ColortableEditor not found: run 'pyinstaller colortable_editor.spec --noconfirm' first" >&2
    exit 1
fi

APPDIR="build/ColortableEditor.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -a "dist/ColortableEditor/." "$APPDIR/usr/bin/"
cp packaging/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/colortable-editor.desktop "$APPDIR/"
cp packaging/colortable-editor.png "$APPDIR/"
ln -sf colortable-editor.png "$APPDIR/.DirIcon"

TOOL="${APPIMAGETOOL:-}"
if [ -z "$TOOL" ]; then
    TOOL="build/appimagetool"
    if [ ! -x "$TOOL" ]; then
        curl -fsSL -o "$TOOL" \
            "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
        chmod +x "$TOOL"
    fi
fi
# appimagetool is an AppImage itself: unpacked and run, it needs no FUSE (CI has none)
export APPIMAGE_EXTRACT_AND_RUN=1
ARCH=x86_64 "$TOOL" "$APPDIR" "ColortableEditor-x86_64.AppImage"
echo "built: $ROOT/ColortableEditor-x86_64.AppImage"
