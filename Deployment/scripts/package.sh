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
  # tar instead of rsync so this also runs in Azure Cloud Shell and minimal images
  tar -C "$2" --exclude='__pycache__' --exclude='*.pyc' --exclude='staticfiles' --exclude='local.settings*.json' \
      --exclude='.venv' --exclude='tests' -cf - . | tar -C "$dir" -xf -
  tar -C "$ROOT/Code/core" --exclude='__pycache__' --exclude='*.pyc' -cf - callcenter | tar -C "$dir" -xf -
  tar -C "$ROOT" -cf - Config | tar -C "$dir" -xf -
  (cd "$dir" && zip -qr "$BUILD/$1.zip" .)
  echo "Built $BUILD/$1.zip"
}

stage webapp "$ROOT/Code/webapp"
stage functions "$ROOT/Code/functions"
