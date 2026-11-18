#!/usr/bin/env bash
# Builds the Windows .exe on Linux: PyInstaller cannot cross-compile, so it
# runs a Windows Python under Wine. Headless: no window opens, no focus is
# taken, nothing touches the desktop.
#
#   tools/build_windows_exe.sh                    -> dist/<name from packaging/build.spec>.exe
#   tools/build_windows_exe.sh --distpath DIR     -> DIR/<name>.exe (any PyInstaller option works)
#
# The build environment lives in ~/.local/share/fishbot-winbuild
# (override with FISHBOT_WINBUILD):
#   prefix/  its own Wine prefix (never the game's)
#   py/      Windows Python from the official NuGet package "python"
#   dl/      the downloaded package
# It is created on first use (one download from nuget.org); every run then
# installs the exact package versions pinned in tools/build.py into it.
# The exe name/version comes from packaging/build.spec (tools/build.py
# --version X.Y.Z --no-build updates it).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
W="${FISHBOT_WINBUILD:-$HOME/.local/share/fishbot-winbuild}"
PY_VERSION="${FISHBOT_WIN_PY:-3.14.4}"
export WINEPREFIX="$W/prefix"
export WINEDEBUG=-all
export WINEDLLOVERRIDES="mscoree,mshtml="   # no Mono/Gecko install dialogs in a new prefix
PY="$W/py/tools/python.exe"

command -v wine >/dev/null || { echo "wine is not installed"; exit 1; }

if [ ! -f "$PY" ]; then
    echo "== Setting up the Windows build environment in $W (Python $PY_VERSION)"
    mkdir -p "$W/dl" "$W/py"
    [ -f "$W/dl/python.nupkg" ] || curl -fL -o "$W/dl/python.nupkg" \
        "https://www.nuget.org/api/v2/package/python/$PY_VERSION"
    (cd "$W/py" && unzip -qo "$W/dl/python.nupkg" 'tools/*')
    wineboot -i >/dev/null 2>&1 || true
    wine "$PY" -m pip install --upgrade pip
fi

# The exact versions the release is built with (tools/build.py, between
# BEGIN_REQUIRED_VERSIONS and END_REQUIRED_VERSIONS), applied on every run so
# the environment cannot drift from them. pip does nothing when they match.
mapfile -t PINS < <(python3 - "$ROOT/tools/build.py" <<'EOF_PINS'
import ast, re, sys
text = open(sys.argv[1], encoding="utf-8").read()
block = re.search(r"BEGIN_REQUIRED_VERSIONS\s*\n(.*?)# END_REQUIRED_VERSIONS", text, re.S).group(1)
pins = ast.literal_eval(block.split("=", 1)[1].strip())
for name, version in pins.items():
    if name != "Python":
        # build.py checks the version the module reports: OpenCV's
        # cv2.__version__ is "4.13.0" while its wheels are "4.13.0.92".
        # "==4.13.0.*" makes pip take any build of that release.
        star = ".*" if name.startswith("opencv") and version.count(".") == 2 else ""
        print(f"{name}=={version}{star}")
EOF_PINS
)
[ "${#PINS[@]}" -gt 0 ] || { echo "no pinned versions found in tools/build.py"; exit 1; }
wine "$PY" -m pip install "${PINS[@]}"

cd "$ROOT"
echo "== $(wine "$PY" --version) / PyInstaller $(wine "$PY" -m PyInstaller --version)"
wine "$PY" -m PyInstaller --noconfirm --clean --workpath build/win "$@" packaging/build.spec
echo "== Done:"
ls -la "$(python3 - "$@" <<'EOF'
import sys
a = sys.argv[1:]
print(a[a.index("--distpath") + 1] if "--distpath" in a else "dist")
EOF
)"/*.exe
