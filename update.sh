#!/usr/bin/env bash
# Cập nhật mã nguồn Signer bằng git pull (chỉ fast-forward). Không cài / cập nhật Python hay thư viện.
# Không đụng tới config.ini / keystore.json (không nằm trong git). Signer đang chạy thì phải khởi động lại sau khi cập nhật.
set -euo pipefail

# Cả khối { } được bash đọc hết trước khi chạy, nên git pull có sửa chính file này cũng không làm hỏng phần còn lại.
{
cd "$(dirname "$0")"

command -v git >/dev/null 2>&1 || { echo "Chưa có git (Ubuntu/Debian: sudo apt install git; macOS: xcode-select --install)." >&2; exit 1; }
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "Thư mục này không phải git clone, không cập nhật bằng git pull được." >&2; exit 1; }

echo "Cập nhật mã nguồn..."
if ! git pull --ff-only; then
    echo "git pull lỗi (có sửa đổi local hoặc nhánh đã rẽ nhánh). Xem 'git status', sửa rồi chạy lại." >&2
    exit 1
fi

chmod +x ./*.sh
echo
echo "Xong. Signer đang chạy thì khởi động lại: ./start.sh"
exit 0
}
