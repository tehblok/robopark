#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
if [ ! -f deploy/installer/install.sh ]; then
    echo 'FAIL: installer entry point is missing' >&2
    exit 1
fi
exec python3 tests/host/installer_scenarios.py
