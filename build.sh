#!/usr/bin/env bash
# Build the clickable app for macOS / Linux. Just run: ./build.sh
set -e
cd "$(dirname "$0")"
python3 build.py
