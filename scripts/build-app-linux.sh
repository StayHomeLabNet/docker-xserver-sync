#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
cd "$repo_root"

build_root=$(mktemp -d /tmp/dxsync-linux-build.XXXXXX)
trap 'rm -rf "$build_root"' EXIT
build_venv="$build_root/venv"
UV_PROJECT_ENVIRONMENT="$build_venv" uv sync --python 3.12 --extra dev --extra gui --extra ssh --extra build
"$build_venv/bin/pytest"

"$build_venv/bin/python" -m nuitka \
  --standalone \
  --output-filename="Docker XServer Sync" \
  --enable-plugin=pyside6 \
  --include-package=dxsync \
  --include-package=keyring \
  --output-dir="$build_root" \
  --remove-output \
  scripts/app_main.py

generated_directory="$build_root/app_main.dist"
app_directory="$build_root/Docker XServer Sync"
if [[ ! -d "$generated_directory" ]]; then
  echo "Build completed but the standalone application directory was not found: $generated_directory" >&2
  exit 1
fi
mv "$generated_directory" "$app_directory"

executable="$app_directory/Docker XServer Sync"
if [[ ! -x "$executable" ]]; then
  echo "Build completed but the executable was not found: $executable" >&2
  exit 1
fi
DXSYNC_STATE_DIR="$build_root/state" QT_QPA_PLATFORM=offscreen "$executable" --smoke-test

mkdir -p "$repo_root/dist"
archive_path="$repo_root/dist/Docker XServer Sync-Linux-x64.tar.gz"
rm -f "$archive_path"
tar -C "$build_root" -czf "$archive_path" "Docker XServer Sync"
test -s "$archive_path"

sha256sum "$archive_path"
