#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export HF_HOME="$PWD/.cache/huggingface"
export MPLCONFIGDIR="$PWD/.cache/matplotlib"
export NUMBA_CACHE_DIR="$PWD/.cache/numba"
export PKUSEG_HOME="${PKUSEG_HOME:-$PWD/.cache/pkuseg}"
export HF_HUB_DISABLE_XET=1
python="${F5_STUDIO_UI_PYTHON:-$PWD/.studio/interface-python315/bin/python}"
if [[ ! -x "$python" && -z "${F5_STUDIO_UI_PYTHON:-}" ]]; then
    python="$PWD/.venv/bin/python"
fi
if [[ ! -x "$python" ]] || ! "$python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 15))'; then
    echo "Indique um ambiente Python 3.15 com o Studio instalado em F5_STUDIO_UI_PYTHON." >&2
    exit 1
fi
exec "$python" -m f5_tts.studio.app --workspace "$PWD/.studio" "$@"
