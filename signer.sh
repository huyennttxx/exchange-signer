#!/usr/bin/env bash
# Menu quản lý Signer (signer/menu.py). Có tham số thì chạy thẳng lệnh signer.admin, ví dụ: ./signer.sh list
# Đường dẫn tính từ thư mục chứa script. Config khác: SIGNER_CONFIG=/etc/signer/config.ini ./signer.sh
# Python: SIGNER_PYTHON nếu có, rồi .venv, rồi python3
cd "$(dirname "$0")" || exit 1

if [ -n "${SIGNER_PYTHON:-}" ]; then PY=$SIGNER_PYTHON
elif [ -x .venv/bin/python ]; then PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then PY=.venv/Scripts/python.exe
else PY=python3
fi

exec "$PY" -m signer.menu --config "${SIGNER_CONFIG:-config.ini}" "$@"
