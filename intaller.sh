#!/usr/bin/env bash
# Compatibility with the originally requested filename.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/installer.sh" "$@"
