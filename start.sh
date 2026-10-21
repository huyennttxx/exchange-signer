#!/usr/bin/env bash
# Chạy Signer. Đường dẫn tính từ thư mục chứa script. Config khác: SIGNER_CONFIG=/etc/signer/config.json ./start.sh
# Python: SIGNER_PYTHON nếu có, rồi .venv, rồi python3
cd "$(dirname "$0")" || exit 1

if [ -n "${SIGNER_PYTHON:-}" ]; then PY=$SIGNER_PYTHON
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=python3
fi

exec "$PY" -m signer.server --config "${SIGNER_CONFIG:-config.json}" "$@"
