#!/usr/bin/env bash
# Builds deployable zip packages for the web app and the Function app.
#
#   Deployment/scripts/package.sh            -> build/webapp.zip, build/functions.zip
#   Deployment/scripts/package.sh --vendor   -> same, with all Python dependencies inside (no remote build needed)
#
# Each package contains the app, the shared `callcenter` package (Code/core) and the
# default configuration (Config/). Without --vendor, dependencies are installed by the platform during
# deployment (remote build). With --vendor, Linux x64 / Python 3.12 wheels are downloaded into the package
# (functions: .python_packages/lib/site-packages, web app: .packages + collected static files).
set -euo pipefail

VENDOR=0
[[ "${1:-}" == "--vendor" ]] && VENDOR=1
PIP_TARGET_FLAGS=(--quiet --disable-pip-version-check --only-binary=:all: --implementation cp --python-version 3.12
  --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 --upgrade)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="$ROOT/build"
rm -rf "$BUILD" && mkdir -p "$BUILD"

stage() {  # stage <name> <app source dir>
  local dir="$BUILD/$1"
  mkdir -p "$dir"
  # tar instead of rsync so this also runs in Azure Cloud Shell and minimal images
  tar -C "$2" --exclude='__pycache__' --exclude='*.pyc' --exclude='staticfiles' --exclude='local.settings*.json' \
      --exclude='.venv' --exclude='tests' -cf - . | tar -C "$dir" -xf -
  tar -C "$ROOT/Code/core" --exclude='__pycache__' --exclude='*.pyc' -cf - callcenter | tar -C "$dir" -xf -
  tar -C "$ROOT" -cf - Config | tar -C "$dir" -xf -
  if [[ $VENDOR -eq 1 ]]; then
    if [[ "$1" == "functions" ]]; then
      python3 -m pip install "${PIP_TARGET_FLAGS[@]}" --target "$dir/.python_packages/lib/site-packages" -r "$dir/requirements.txt"
    else
      python3 -m pip install "${PIP_TARGET_FLAGS[@]}" --target "$dir/.packages" -r "$dir/requirements.txt"
      # Static files must exist in the package (run-from-package is read-only; no build step on the server).
      (cd "$dir" && PYTHONPATH="$dir/.packages" CC_MODE=local DJANGO_DEBUG=false python3 manage.py collectstatic --noinput -v 0)
    fi
  fi
  (cd "$dir" && zip -qr "$BUILD/$1.zip" .)
  echo "Built $BUILD/$1.zip"
}

stage webapp "$ROOT/Code/webapp"
stage functions "$ROOT/Code/functions"
