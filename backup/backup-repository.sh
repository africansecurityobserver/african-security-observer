#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/africansecurityobserver/african-security-observer.git"
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
OUT_DIR="${1:-$PWD}"
MIRROR_DIR="$OUT_DIR/african-security-observer-$STAMP.git"
ARCHIVE="$OUT_DIR/african-security-observer-$STAMP.tar.gz"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: Git is required. Install Git and retry." >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
git clone --mirror "$REPO_URL" "$MIRROR_DIR"
tar -czf "$ARCHIVE" -C "$OUT_DIR" "$(basename "$MIRROR_DIR")"
echo "Backup completed: $ARCHIVE"
echo "Keep a copy outside GitHub and test it before relying on it."
