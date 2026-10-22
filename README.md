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
- Menu (`signer/menu.py`) chỉ quản lý key: xem key (danh sách, chọn một key để xem public key, tạo HMAC secret mới hoặc xoá), thêm / nhập key. Thêm key chỉ hỏi sàn. Menu và admin không sửa IP: IP được phép sửa tay trong `config.ini`.
- Có tham số thì `signer.sh` chạy thẳng lệnh `signer.admin`: `config-show`, `config-edit`, `config-set`, `change-passphrase`, `list`…
- Đường dẫn tính từ thư mục chứa script. Dùng config khác: `SIGNER_CONFIG=/etc/signer/config.ini ./start.sh` (Windows: `set SIGNER_CONFIG=...` trước khi chạy).
- Trên Linux, chạy `chmod +x signer.sh start.sh` một lần.

Không dùng script thì gọi thẳng:

```bash
python -m signer.menu
python -m signer.admin generate --venue binance            # rồi điền allowed_ips trong config.ini
python -m signer.admin config-edit                         # hỏi từng field, Enter giữ giá trị cũ
python -m signer.admin config-set port=8081 max_payload_age_ms=30000
python -m signer.admin config-show
python -m signer.server
```

Mặc định config là `config.ini`, keystore là `keystore.json` (cả hai tính từ thư mục đang chạy). Đổi bằng `--config` (cả 2 lệnh) và `--keystore` (`signer.admin`). Không truyền `--keystore` thì dùng `keystore` trong `[server]` của config; không có thì dùng `keystore.json`.

## Config (`config.ini`)

Định dạng INI, xem [`config.example.ini`](config.example.ini). `config.json` không còn được hỗ trợ: viết lại theo file mẫu.
`config.ini` không đưa lên git (chứa `key_id` và IP thật): máy mới thì copy từ `config.example.ini` rồi xoá các section key mẫu, hoặc để `generate` / `import` / `config-edit` tự tạo.

- `[server]`: `keystore`, `host` (mặc định `0.0.0.0`), `port` (`8080`), `hyperliquid_network` (`mainnet`), `max_payload_age_ms` (`60000`).
- Mỗi key một section `[key:<key_id>]`:
  - `allowed_ips`: IP của BE được gọi bằng key này, cách nhau bằng dấu phẩy. Để trống thì chưa IP nào gọi được.
  - `max_leverage`: đòn bẩy tối đa.
  - Mọi dòng còn lại là `<symbol> = <max_qty>`: symbol được ký và khối lượng tối đa một lệnh (so sánh `<`). Lệnh reduce-only không bị giới hạn khối lượng.
  - Binance: tên symbol đầy đủ (`BTCUSDT`, `BTCUSDC`…). Hyperliquid: `<COIN>USDC`, phải có trong [`signer/hyperliquid_assets.ini`](signer/hyperliquid_assets.ini).
  - Key không có section thì không ký được gì (`401 UNAUTHENTICATED`).
- Tên lạ (section, option trong `[server]`), giá trị sai, hay symbol Hyperliquid không có trong bảng asset → Signer không khởi động.
- File chứa IP và hạn mức của mọi key, không mã hoá: giữ quyền `600`, cùng chủ với keystore.

Admin chỉ thêm / xoá section của key, theo dòng, giữ nguyên comment. Mọi thứ trong section (IP, symbol, hạn mức) sửa tay:
- `generate` / `import` thêm section với `allowed_ips` để trống, `max_leverage = 40` và `BTCUSDC = 0.1` (cả Binance lẫn Hyperliquid). Key chưa gọi được từ IP nào cho tới khi điền `allowed_ips`.
- `remove` xoá cả section.

`signer/hyperliquid_assets.ini` là bảng `<COIN>USDC` → chỉ số asset perp, đi kèm code. Hiện có 100 coin mainnet có khối lượng 24h lớn nhất (lấy 2026-10-22), chưa có testnet. Cần coin khác thì thêm dòng từ `POST https://api.hyperliquid.xyz/info {"type":"meta"}` (chỉ số = vị trí trong `universe`).

Keystore định dạng cũ (bản ghi còn `allowed_ips`) không được chuyển đổi: key đó nhận `503 KEY_NOT_LOADED`, phải tạo key mới.

Lần `generate` / `import` đầu tiên tự tạo keystore nếu file chưa có (hỏi passphrase mới 2 lần). Lệnh admin khác: `init` (chỉ tạo keystore rỗng), `import`, `rotate-hmac`, `remove`, `list`, `show` (in lại public key / địa chỉ agent), `change-passphrase`.

`generate` / `import` / `rotate-hmac` in **HMAC secret** của key đúng một lần; BE dùng nó ký mọi request `/v1/sign` (header `X-Signer-Timestamp`, `X-Signer-Signature`, xem [`docs/signer-api.md`](docs/signer-api.md) → Xác thực). Mỗi key một secret. Không in lại được: mất thì `rotate-hmac`. Key tạo trước khi có HMAC (`list` hiện `(no HMAC)`) trả `503 KEY_NOT_LOADED` cho tới khi chạy `rotate-hmac`.

`config-set` / `config-edit` sửa `[server]`: `host`, `port`, `hyperliquid_network`, `max_payload_age_ms`.
- Giá trị được kiểm tra giống lúc Signer khởi động; sai thì không ghi gì.
- `keystore` và các section key giữ nguyên, nhưng cả file được ghi lại nên **comment bị mất**.
- `[server]` chỉ có hiệu lực sau khi restart Signer. Section `[key:…]` nạp lại được bằng `kill -HUP` (xem dưới).

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
| Đồng bộ giờ | Chưa có NTP; `max_payload_age_ms` = 60000 (1 phút) | `config.ini` |
| `msgpack` | Dùng gói | — |
| `r` / `s` Hyperliquid | Giống SDK: hex **không** đệm số 0 ở đầu | `hyperliquid.sign` |
| Mạng Hyperliquid | Một giá trị cho cả server: `hyperliquid_network` trong config | `config.py` |

Hành vi khác cần biết:
- Signer không tự nạp lại keystore và config. Trên Linux, `kill -HUP <pid>` nạp lại keystore và các section `[key:…]` (chỉ khi file đã đổi); trên Windows thì phải restart. Nạp lỗi thì giữ nguyên bộ cũ. Riêng sau `change-passphrase` hay khi sửa `[server]` thì phải restart.
- Key không giải mã được lúc khởi động (file bị sửa) vẫn được nạp ở trạng thái lỗi: request qua IP allowlist sẽ nhận `503 KEY_NOT_LOADED`.
- Body JSON có key lặp, `NaN`/`Infinity`, hoặc field ngoài `key_id`/`op`/`payload` → `400 INVALID_REQUEST`.
- Binance `place_order` bắt buộc có `symbol` và `quantity`. Hyperliquid `cancelByCloid` không giới hạn số phần tử, nhưng mọi `asset` phải thuộc section của key.
