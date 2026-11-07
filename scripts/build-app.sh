#!/bin/zsh
set -euo pipefail

script_dir=${0:A:h}
repo_root=${script_dir:h}
cd "$repo_root"

build_root=$(/usr/bin/mktemp -d /private/tmp/dxsync-macos-build.XXXXXX)
trap '/bin/rm -rf "$build_root"' EXIT
build_venv="$build_root/venv"
UV_PROJECT_ENVIRONMENT="$build_venv" uv sync --python 3.12 --extra dev --extra gui --extra ssh --extra build
"$build_venv/bin/pytest"

"$build_venv/bin/python" -m nuitka \
  --standalone \
  --macos-create-app-bundle \
  --macos-app-mode=gui \
  --macos-app-name="Docker XServer Sync" \
  --macos-app-version="0.1.6" \
  --product-name="Docker XServer Sync" \
  --product-version="0.1.6" \
  --output-filename="Docker XServer Sync" \
  --macos-app-icon=none \
  --enable-plugin=pyside6 \
  --include-package=dxsync \
  --include-package=keyring \
  --output-dir="$build_root" \
  --remove-output \
  scripts/app_main.py

generated_app="$build_root/app_main.app"
app_path="$build_root/Docker XServer Sync.app"
if [[ ! -d "$generated_app" ]]; then
  print -u2 "Build completed but application bundle was not found: $app_path"
  exit 1
fi
/bin/mv "$generated_app" "$app_path"

executable=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$app_path/Contents/Info.plist")
DXSYNC_STATE_DIR="$build_root/state" "$app_path/Contents/MacOS/$executable" --smoke-test

mkdir -p "$repo_root/dist"
zip_path="$repo_root/dist/Docker XServer Sync-macOS-arm64.zip"
if [[ -e "$zip_path" ]]; then
  /bin/rm "$zip_path"
fi
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app_path" "$zip_path"

print "$zip_path"
