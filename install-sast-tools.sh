#!/usr/bin/env bash
# Compatibility alias; use install-tools.sh for new installations.
set -Eeuo pipefail
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$script_dir/install-tools.sh" --groups sast "$@"
