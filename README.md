# Signer

Dịch vụ ký lệnh cho Binance USDⓈ-M Futures và Hyperliquid. BE gửi payload, Signer kiểm tra theo quy tắc rủi ro của từng key rồi trả chữ ký; private key không bao giờ rời máy Signer.

Đặc tả: [`docs/signer-api.md`](docs/signer-api.md) (API, quy tắc, mã lỗi) và [`docs/signer-impl.md`](docs/signer-impl.md) (keystore, triển khai). Python ≥ 3.10, 3 gói ngoài (`requirements.txt`).

## Bắt đầu trên máy mới

```bash
bash install.sh                      # Windows: install.bat
cp config.example.ini config.ini     # rồi xoá các section [key:…] mẫu, sửa [server]
./signer.sh                          # menu: 2 → thêm key (chỉ hỏi sàn)
```

`install.sh` / `install.bat` tìm Python ≥ 3.10, tạo `.venv` trong thư mục dự án và cài `requirements.txt` vào đó (`signer.sh` / `start.sh` tự dùng `.venv`). Chạy lại bao nhiêu lần cũng được; thêm `--dev` để cài cả gói test.
- macOS: chưa có Python phù hợp thì `install.sh` cài `python@3.12` bằng Homebrew; máy chưa có Homebrew thì hỏi trước khi cài.
- Linux / Windows: script không tự cài Python, chỉ báo cách cài (`sudo apt install python3 python3-venv`, `winget install Python.Python.3.12`).

1. Thêm key bằng menu (hoặc `./signer.sh generate --venue binance`). Lần đầu tự tạo keystore, hỏi passphrase mới 2 lần.
2. Ghi lại những gì được in ra **một lần**: public key Ed25519 (Binance) hoặc địa chỉ agent (Hyperliquid) để đăng ký trên sàn, và **HMAC secret** để đưa cho BE.
3. Mở `config.ini`, điền `allowed_ips` trong section `[key:<key_id>]` vừa được thêm; sửa symbol / hạn mức nếu cần.
4. Chạy Signer: `./start.sh`.

Trên Windows dùng `signer.bat` và `start.bat` thay cho hai file `.sh`. `install.sh` tự `chmod +x` các script `.sh`.

## Config (`config.ini`)

Định dạng INI, xem [`config.example.ini`](config.example.ini). File chứa IP và hạn mức của mọi key, không mã hoá: giữ quyền `600`, cùng chủ với keystore. `config.ini` không đưa lên git.

```ini
[server]
keystore = /var/lib/signer/keystore.json
host = 0.0.0.0
port = 8080
hyperliquid_network = mainnet
max_payload_age_ms = 60000

[key:3f2b8c1e-6d4a-4f7b-9c2e-1a5d8e7f0b34]
allowed_ips = 10.0.1.12, 10.0.1.13
max_leverage = 40
BTCUSDC = 0.1
ETHUSDC = 2
```

**`[server]`** — mọi field đều có mặc định: `keystore` (`keystore.json`), `host` (`0.0.0.0`), `port` (`8080`), `hyperliquid_network` (`mainnet`), `max_payload_age_ms` (`60000`, độ lệch tối đa của `timestamp` / `nonce` so với giờ Signer).

**`[key:<key_id>]`** — một section cho mỗi key. Key không có section thì không ký được gì (`401 UNAUTHENTICATED`).

| Dòng | Ý nghĩa |
|---|---|
| `allowed_ips` | IP của BE được gọi bằng key này, cách nhau bằng dấu phẩy. Để trống: chưa IP nào gọi được |
| `max_leverage` | Đòn bẩy tối đa |
| `<symbol> = <max_qty>` | Mọi dòng còn lại: symbol được ký và khối lượng tối đa một lệnh (so sánh `<`) |

- Binance: tên symbol đầy đủ (`BTCUSDT`, `BTCUSDC`…).
- Hyperliquid: `<COIN>USDC`, phải có trong [`signer/hyperliquid_assets.ini`](signer/hyperliquid_assets.ini). File này đi kèm code, chứa chỉ số asset perp của 100 coin mainnet có khối lượng 24h lớn nhất (lấy 2026-10-22), chưa có testnet. Cần coin khác thì thêm dòng từ `POST https://api.hyperliquid.xyz/info {"type":"meta"}` (chỉ số = vị trí trong `universe`), rồi restart.
- Không viết comment ở cuối dòng; comment phải nằm trên dòng riêng (`#` hoặc `;`).
- Section / option lạ, giá trị sai, hay symbol Hyperliquid không có trong bảng asset → Signer không khởi động.

**Ai sửa gì:**
- `generate` / `import` tự thêm section cho key mới: `allowed_ips` để trống, `max_leverage = 40`, `BTCUSDC = 0.1`. `remove` xoá section. Hai lệnh này sửa theo dòng, giữ nguyên comment.
- IP, symbol, hạn mức trong section: **sửa tay**. Menu và admin không sửa IP.
- `config-set` / `config-edit` sửa `[server]`. Giá trị được kiểm tra như lúc Signer khởi động, sai thì không ghi gì; nhưng cả file được ghi lại nên **comment bị mất**.

**Áp dụng thay đổi:**
- Section `[key:…]` và keystore: trên Linux `kill -HUP <pid>` nạp lại (chỉ khi file đã đổi; nạp lỗi thì giữ bộ cũ). Trên Windows phải restart.
- `[server]` và sau `change-passphrase`: phải restart.

## Lệnh

`./signer.sh` không tham số mở menu; có tham số thì chạy thẳng lệnh `signer.admin`. Không dùng script thì gọi `python -m signer.menu`, `python -m signer.admin <lệnh>`, `python -m signer.server`.

| Lệnh | Việc làm |
|---|---|
| `generate --venue binance\|hyperliquid` | Sinh key trên máy này, thêm section vào config, in public key / địa chỉ agent và HMAC secret |
| `import --venue …` | Như `generate` nhưng nhập private key có sẵn (hex, đọc bằng `getpass`) |
| `list` | `key_id`, sàn, `allowed_ips` (từ config), đánh dấu key chưa có HMAC / chưa có section |
| `show <key_id>` | In lại public key / địa chỉ agent |
| `rotate-hmac <key_id>` | Sinh HMAC secret mới, secret cũ hết hiệu lực sau khi Signer nạp lại |
| `remove <key_id>` | Xoá key khỏi keystore và section khỏi config. Nhớ thu hồi key trên sàn |
| `init` | Chỉ tạo keystore rỗng |
| `change-passphrase` | Đổi passphrase keystore (phải restart Signer) |
| `config-show` | In config Signer sẽ dùng, các section key, key chưa có section |
| `config-set field=value …` | Sửa `[server]`: `host`, `port`, `hyperliquid_network`, `max_payload_age_ms` |
| `config-edit` | Như `config-set` nhưng hỏi từng field, Enter giữ giá trị cũ |

Đường dẫn:
- Mặc định `config.ini` và `keystore.json`, tính từ thư mục đang chạy (script thì tính từ thư mục chứa script).
- Config khác: `--config`, hoặc `SIGNER_CONFIG=/etc/signer/config.ini ./start.sh` (Windows: `set SIGNER_CONFIG=...`).
- Keystore: `--keystore` (`signer.admin`), không thì `keystore` trong `[server]`.
- Script tìm Python theo thứ tự `SIGNER_PYTHON`, `.venv`, `python3` / `python`.

**HMAC secret:** `generate` / `import` / `rotate-hmac` in secret của key đúng một lần; BE dùng nó ký mọi request `/v1/sign` (header `X-Signer-Timestamp`, `X-Signer-Signature`, xem [`docs/signer-api.md`](docs/signer-api.md) → Xác thực). Mỗi key một secret, không in lại được: mất thì `rotate-hmac`.

## Quy tắc ký (tóm tắt)

Đầy đủ trong [`docs/signer-api.md`](docs/signer-api.md) → Quy tắc rủi ro.

| Quy tắc | Binance | Hyperliquid |
|---|---|---|
| Symbol | `symbol` thuộc section của key; `place_order` bắt buộc có `symbol` | Mọi `a` / `asset` là asset của một symbol trong section |
| Khối lượng | `0 < quantity < max_qty`; `reduceOnly=true` thì không giới hạn trên | `0 < s < max_qty`; `r = true` thì không giới hạn trên; lệnh bảo hiểm (`positionTpsl`) nhận `s = "0"` = đóng cả vị thế |
| Đòn bẩy | `1 ≤ leverage ≤ max_leverage` | Như Binance, và `isCross = true` |
| Lệnh bảo hiểm | `closePosition=true`, `STOP_MARKET` / `TAKE_PROFIT_MARKET` | `r = true` và `t.trigger` |
| Tham số thêm | `newOrderRespType=RESULT` (đặt lệnh), `priceProtect=false` (lệnh bảo hiểm) | — |

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
| Mạng Hyperliquid | Một giá trị cho cả server: `hyperliquid_network` trong config | `config.ini` |

## Hành vi khác cần biết

- Key không giải mã được lúc khởi động (file bị sửa) vẫn được nạp ở trạng thái lỗi: request qua IP allowlist nhận `503 KEY_NOT_LOADED`. Key chưa có HMAC (`list` hiện `(no HMAC)`) cũng vậy cho tới khi `rotate-hmac`.
- Body JSON có key lặp, `NaN`/`Infinity`, hoặc field ngoài `key_id`/`op`/`payload` → `400 INVALID_REQUEST`.
- Hyperliquid `cancelByCloid` không giới hạn số phần tử, nhưng mọi `asset` phải thuộc section của key.

## Nâng cấp từ bản cũ

- `config.json` không còn được hỗ trợ (Signer báo lỗi và dừng): viết lại thành `config.ini` theo file mẫu. `[limits]` chung đã bỏ, hạn mức nay nằm trong từng section key.
- Keystore cũ (bản ghi còn `allowed_ips`) không được chuyển đổi: các key đó nhận `503 KEY_NOT_LOADED`, phải tạo key mới.
