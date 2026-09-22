#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PYTHON="${ROOT_DIR}/.venv/bin/python"
# Keep one maintained demo; an explicit path may open a user's own flowgraph.
EXAMPLE="${1:-${ROOT_DIR}/examples/channel_estimation_demo.grc}"
GRC="${GRC:-$(command -v gnuradio-companion || true)}"

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "Missing ${VENV_PYTHON}; run scripts/install.sh first." >&2
  exit 1
fi

if [[ -z "${GRC}" ]]; then
  echo "gnuradio-companion was not found in PATH." >&2
  exit 1
fi

if [[ ! -f "${EXAMPLE}" ]]; then
  echo "GRC example not found: ${EXAMPLE}" >&2
  exit 1
fi

VENV_SITE_PACKAGES="$("${VENV_PYTHON}" -c \
  'import site; print(site.getsitepackages()[0])')"

export PYTHONPATH="${ROOT_DIR}/python:${VENV_SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
export GRC_BLOCKS_PATH="${ROOT_DIR}/grc${GRC_BLOCKS_PATH:+:${GRC_BLOCKS_PATH}}"

exec "${GRC}" "${EXAMPLE}"
