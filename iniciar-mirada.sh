#!/usr/bin/env bash
set -eu
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export FOVEA_MODEL_PATH="${FOVEA_MODEL_PATH:-$PWD/.local-model/face_landmarker.task}"
exec .venv/bin/python -m examples.simulation.server --camera-preview "${1:-0}"
