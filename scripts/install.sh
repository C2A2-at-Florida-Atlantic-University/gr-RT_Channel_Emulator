#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"

if ! "${PYTHON}" -c "from gnuradio import gr, qtgui; import PyQt5" >/dev/null 2>&1; then
  echo "${PYTHON} cannot import GNU Radio QT GUI and PyQt5." >&2
  echo "Set PYTHON to the interpreter used by GNU Radio QT GUI and rerun." >&2
  exit 1
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  "${PYTHON}" -m venv --system-site-packages "${ROOT_DIR}/.venv"
fi

if ! "${VENV_PYTHON}" -c "from gnuradio import gr, qtgui; import PyQt5" >/dev/null 2>&1; then
  echo "${VENV_PYTHON} cannot import GNU Radio QT GUI and PyQt5." >&2
  echo "Recreate .venv with the interpreter used by GNU Radio QT GUI." >&2
  exit 1
fi

"${VENV_PYTHON}" -m pip install --editable "${ROOT_DIR}"
"${VENV_PYTHON}" -c \
  "from rt_channel_emulation import GNURADIO_AVAILABLE; assert GNURADIO_AVAILABLE"

echo "Installed GR RT Channel Emulation in ${ROOT_DIR}/.venv"
