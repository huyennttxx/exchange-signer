# Signer

Triển khai theo [`docs/signer-api.md`](docs/signer-api.md) và [`docs/signer-impl.md`](docs/signer-impl.md). Python ≥ 3.10, 3 gói ngoài (`requirements.txt`).

## Chạy

```bash
pip install -r requirements.txt
./signer.sh                     # menu quản lý key
./signer.sh config-edit         # sửa config (hỏi từng field)
./signer.sh change-passphrase   # đổi passphrase keystore
./start.sh                      # chạy Signer
```

Trên Windows dùng `signer.bat` và `start.bat`, giống hệt hai file `.sh`.

- Script chỉ tìm Python (`SIGNER_PYTHON`, rồi `.venv`, rồi `python3` / `python`) và gọi `signer.menu` hay `signer.server`.
- Menu (`signer/menu.py`) chỉ quản lý key: xem key (danh sách, chọn một key để xem public key, sửa IP, tạo HMAC secret mới hoặc xoá), thêm / nhập key. Menu tự hỏi sàn, IP và cho chọn key từ danh sách.
- Có tham số thì `signer.sh` chạy thẳng lệnh `signer.admin`: `config-show`, `config-edit`, `config-set`, `change-passphrase`, `list`…
- Đường dẫn tính từ thư mục chứa script. Dùng config khác: `SIGNER_CONFIG=/etc/signer/config.json ./start.sh` (Windows: `set SIGNER_CONFIG=...` trước khi chạy).
- Trên Linux, chạy `chmod +x signer.sh start.sh` một lần.

Không dùng script thì gọi thẳng:

```bash
python -m signer.menu
python -m signer.admin generate --venue binance --allowed-ip 10.0.1.12
python -m signer.admin config-edit                         # hỏi từng field, Enter giữ giá trị cũ
python -m signer.admin config-set max_leverage=10 port=8081
python -m signer.admin config-show
python -m signer.server
```

Mặc định config là `config.json`, keystore là `keystore.json` (cả hai tính từ thư mục đang chạy). Đổi bằng `--config` (cả 2 lệnh) và `--keystore` (`signer.admin`). Không truyền `--keystore` thì dùng field `keystore` trong config; config không có field này thì dùng `keystore.json`. `hyperliquid_network` mặc định là `mainnet`. Các giá trị trong `limits` không có mặc định: thiếu là Signer không khởi động.

Lần `generate` / `import` đầu tiên tự tạo keystore nếu file chưa có (hỏi passphrase mới 2 lần). Lệnh admin khác: `init` (chỉ tạo keystore rỗng), `import`, `set-ips`, `rotate-hmac`, `remove`, `list`, `show` (in lại public key / địa chỉ agent), `change-passphrase`.

`generate` / `import` / `rotate-hmac` in **HMAC secret** của key đúng một lần; BE dùng nó ký mọi request `/v1/sign` (header `X-Signer-Timestamp`, `X-Signer-Signature`, xem [`docs/signer-api.md`](docs/signer-api.md) → Xác thực). Mỗi key một secret. Không in lại được: mất thì `rotate-hmac`. Key tạo trước khi có HMAC (`list` hiện `(no HMAC)`) trả `503 KEY_NOT_LOADED` cho tới khi chạy `rotate-hmac`.

Config sửa được bằng `config-set` / `config-edit`: `max_qty_btc`, `max_leverage`, `max_payload_age_ms`, `host`, `port`, `hyperliquid_network`.
- Giá trị được kiểm tra giống lúc Signer khởi động; sai thì không ghi gì.
- Field `keystore` và các field khác trong file giữ nguyên.
- Signer chỉ đọc config lúc khởi động: sửa xong phải restart Signer.

## Test

```bash
pip install -r requirements-test.txt
python -m unittest discover -s tests -t .
```

Test chữ ký Hyperliquid so từng byte với `sign_l1_action` của SDK (5 op × mainnet/testnet, và 299 key khác nhau); tự bỏ qua nếu chưa cài SDK.

## Các quyết định đang tạm chọn

Những mục trong "Quyết định cần chốt" của [`docs/signer-impl.md`](docs/signer-impl.md), code đang chọn như sau:

| Mục | Đang chọn | Đổi ở đâu |
|---|---|---|
| Mở khoá khi khởi động | Hỗ trợ cả **A** và **B**: có `$CREDENTIALS_DIRECTORY/signer-passphrase` (systemd-creds) thì đọc, không thì hỏi qua terminal | `server.read_passphrase` |
| Loại key Binance | Ed25519 | `venues/binance.py` |
| URL-encode chữ ký Binance | **Signer trả bản đã encode**, BE nối thẳng `&signature=<giá trị>` | `binance.sign` |
| Đồng bộ giờ | Chưa có NTP; `max_payload_age_ms` = 60000 (1 phút) | `config.json` |
| `msgpack` | Dùng gói | — |
| `r` / `s` Hyperliquid | Giống SDK: hex **không** đệm số 0 ở đầu | `hyperliquid.sign` |
| Mạng Hyperliquid | Một giá trị cho cả server: `hyperliquid_network` trong config | `config.py` |

Hành vi khác cần biết:
- Signer không tự nạp lại keystore. Trên Linux, `kill -HUP <pid>` nạp lại (chỉ khi file đã đổi); trên Windows thì phải restart. Nạp lỗi thì giữ nguyên bộ key cũ. Riêng sau `change-passphrase` thì phải restart.
- Key không giải mã được lúc khởi động (file bị sửa) vẫn được nạp ở trạng thái lỗi: request qua IP allowlist sẽ nhận `503 KEY_NOT_LOADED`.
- Body JSON có key lặp, `NaN`/`Infinity`, hoặc field ngoài `key_id`/`op`/`payload` → `400 INVALID_REQUEST`.
- Binance `place_order` bắt buộc có `quantity`. Hyperliquid `cancelByCloid` không giới hạn số phần tử, nhưng mọi `asset` phải là `0`.
