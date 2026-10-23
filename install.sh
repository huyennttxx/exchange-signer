#!/usr/bin/env bash
# Cài đặt Signer: tìm Python >= 3.10, tạo .venv, cài requirements.txt. Chạy lại bao nhiêu lần cũng được.
# macOS: chưa có Python phù hợp thì cài bằng Homebrew (hỏi trước khi cài Homebrew nếu máy chưa có).
# Máy test (không phải máy Signer): ./install.sh --dev để cài thêm requirements-test.txt
set -euo pipefail
cd "$(dirname "$0")"

BREW_PYTHON=python@3.12  # bản Homebrew cài khi máy chưa có Python phù hợp

ok() {  # $1 là Python >= 3.10 dùng được
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

find_python() {
    local c
    for c in "${SIGNER_PYTHON:-}" python3.13 python3.12 python3.11 python3.10 python3 python; do
        if [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 && ok "$c"; then echo "$c"; return 0; fi
    done
    # Python của Homebrew khi PATH của shell này chưa có nó (vừa cài xong)
    for c in /opt/homebrew/bin/python3 /usr/local/bin/python3; do
        if [ -x "$c" ] && ok "$c"; then echo "$c"; return 0; fi
    done
    return 1
}

venv_python() {  # .venv/bin trên macOS / Linux, .venv/Scripts trên Windows (Git Bash)
    if [ -x .venv/bin/python ]; then echo .venv/bin/python
    elif [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe
    fi
}

install_python_mac() {
    local brew answer
    brew=$(command -v brew || true)
    [ -z "$brew" ] && [ -x /opt/homebrew/bin/brew ] && brew=/opt/homebrew/bin/brew
    [ -z "$brew" ] && [ -x /usr/local/bin/brew ] && brew=/usr/local/bin/brew
    if [ -z "$brew" ]; then
        echo "Chưa có Homebrew (https://brew.sh). Cài Homebrew để cài Python?"
        read -r -p "Gõ 'yes' để cài, Enter để thoát: " answer
        [ "$answer" = "yes" ] || { echo "Tự cài Python >= 3.10 (https://www.python.org/downloads/) rồi chạy lại."; exit 1; }
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        if [ -x /opt/homebrew/bin/brew ]; then brew=/opt/homebrew/bin/brew; else brew=/usr/local/bin/brew; fi
    fi
    echo "Cài $BREW_PYTHON bằng Homebrew..."
    "$brew" install "$BREW_PYTHON"
}

PY=$(find_python || true)
if [ -z "$PY" ]; then
    if [ "$(uname -s)" = "Darwin" ]; then
        install_python_mac
        PY=$(find_python || true)
    fi
    if [ -z "$PY" ]; then
        echo "Không tìm thấy Python >= 3.10. Cài Python (Ubuntu/Debian: sudo apt install python3 python3-venv) rồi chạy lại." >&2
        exit 1
    fi
fi
echo "Python: $PY ($("$PY" --version 2>&1))"

# .venv cũ tạo bằng Python quá cũ thì tạo lại
VPY=$(venv_python)
if [ -n "$VPY" ] && ! ok "$VPY"; then
    echo "Xoá .venv cũ (Python < 3.10)"
    rm -rf .venv
    VPY=
fi
if [ -z "$VPY" ]; then
    echo "Tạo .venv..."
    "$PY" -m venv .venv || { echo "Không tạo được .venv (Ubuntu/Debian: sudo apt install python3-venv)." >&2; exit 1; }
    VPY=$(venv_python)
fi

"$VPY" -m pip install --upgrade pip
"$VPY" -m pip install -r requirements.txt
if [ "${1:-}" = "--dev" ]; then
    "$VPY" -m pip install -r requirements-test.txt
fi

chmod +x signer.sh start.sh install.sh
echo
echo "Xong. signer.sh / start.sh tự dùng .venv. Bước tiếp theo:"
[ -f config.ini ] || echo "  cp config.example.ini config.ini   # rồi xoá các section [key:…] mẫu"
echo "  ./signer.sh                         # thêm key"
echo "  ./start.sh                          # chạy Signer"
