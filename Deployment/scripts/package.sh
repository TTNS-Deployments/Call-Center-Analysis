#!/usr/bin/env bash
# Builds deployable zip packages for the web app and the Function app.
#
#   Deployment/scripts/package.sh            -> build/webapp.zip, build/functions.zip
#
# Each package contains the app, the shared `callcenter` package (Code/core) and the
# default configuration (Config/). Dependencies are installed by the platform during
# deployment (remote build), so packaging needs no Azure SDKs locally.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="$ROOT/build"
rm -rf "$BUILD" && mkdir -p "$BUILD"

stage() {  # stage <name> <app source dir>
  local dir="$BUILD/$1"
  mkdir -p "$dir"
  rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'staticfiles' --exclude 'local.settings*.json' \
        --exclude '.venv' --exclude 'tests' "$2/" "$dir/"
  rsync -a --exclude '__pycache__' --exclude '*.pyc' "$ROOT/Code/core/callcenter" "$dir/"
  rsync -a "$ROOT/Config" "$dir/"
  (cd "$dir" && zip -qr "$BUILD/$1.zip" .)
  echo "Built $BUILD/$1.zip"
}

stage webapp "$ROOT/Code/webapp"
stage functions "$ROOT/Code/functions"
