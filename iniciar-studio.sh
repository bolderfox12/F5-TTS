#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export HF_HOME="$PWD/.cache/huggingface"
export MPLCONFIGDIR="$PWD/.cache/matplotlib"
export NUMBA_CACHE_DIR="$PWD/.cache/numba"
export HF_HUB_DISABLE_XET=1
exec .venv/bin/f5-tts_studio --workspace "$PWD/.studio" "$@"
