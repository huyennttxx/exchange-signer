# Bàn giao dự án Signer (chuyển sang Cursor)

Ngày: 2026-11-12 · Trạng thái `main`: `00ba39d`, working tree sạch, **133 test pass** (Python 3.11, `.venv`).

Tài liệu này là điểm vào cho người (và AI agent trong Cursor) tiếp nhận dự án. Chi tiết nghiệp vụ không lặp lại ở đây mà nằm trong:

| File | Nội dung |
|---|---|
| [`README.md`](../README.md) | Cài đặt, config, lệnh admin, quy tắc ký tóm tắt, các quyết định đang tạm chọn |
| [`docs/signer-api.md`](signer-api.md) | **Đặc tả API cho BE** — op, whitelist tham số, quy tắc rủi ro, mã lỗi, câu hỏi mở |
| [`docs/signer-margin.md`](signer-margin.md) | Margin Binance — checklist việc cần làm (app vs bot/Signer), whitelist đặt/hủy đề xuất |
| [`docs/signer-impl.md`](signer-impl.md) | Thiết kế bên trong — thư viện, keystore, mã hoá, ký, quyết định cần chốt |
| [`.cursor/rules/signer.mdc`](../.cursor/rules/signer.mdc) | Rule luôn được nạp cho AI trong Cursor (tóm tắt các ràng buộc dưới đây) |

---

## 1. Dự án là gì

Dịch vụ HTTP nhỏ ký lệnh cho **Binance USDⓈ-M Futures** (Ed25519) và **Hyperliquid** (EIP-712 / secp256k1). BE gửi payload chưa ký → Signer kiểm tra IP + HMAC + policy rủi ro theo từng key → trả chữ ký. **Private key không bao giờ rời máy Signer.** Đây là thành phần nhạy cảm về bảo mật: mọi thay đổi phải nghĩ tới "ai đó có quyền gọi BE có lợi dụng được không".

Luồng `/v1/sign` (trong `server.Signer`):

```
IP allowlist(key_id) → HMAC(key_id) → venue của key → parse payload → khớp op → (place: symbol+qty) → ký
```

Bất kỳ bước nào lỗi → `Reject(code, message)` → JSON lỗi, không bao giờ trả chữ ký.

## 2. Bản đồ code

| File | Vai trò |
|---|---|
| `signer/server.py` | `ThreadingHTTPServer`: `GET /health`, `POST /v1/sign`; parse body chặt (không key lặp, không NaN); `KeyReloader` (SIGHUP); đọc passphrase (systemd-creds hoặc terminal); tắt core dump |
| `signer/auth.py` | IP allowlist + HMAC (`X-Signer-Timestamp`, `X-Signer-Signature`) |
| `signer/policy.py` | `OPS` (danh sách op được phép), `Rule`, các hàm check dùng chung (`check_params`, `check_qty`, `check_asset`, `check_leverage`, `check_fresh`) |
| `signer/venues/binance.py` | Parse query string, `detect_op`, `verify`, ký Ed25519 (trả chữ ký **đã URL-encode**) |
| `signer/venues/hyperliquid.py` | Whitelist action, `detect_op`, `verify`, `connection_id` + ký EIP-712 tự viết (không dùng SDK) |
| `signer/hyperliquid_assets.ini` | Bảng coin → chỉ số asset perp (mainnet, top 100, lấy 2026-10-22) |
| `signer/keystore.py` | scrypt → master key → AES-256-GCM từng key; AAD gắn `key_id`/`venue`; ghi atomic |
| `signer/keyops.py` | Thao tác keystore cấp cao (add, rotate-hmac, remove, change-passphrase) |
| `signer/config.py` | Parse/validate `config.ini`; thêm/xoá section `[key:…]` theo dòng (giữ comment) |
| `signer/admin.py`, `signer/menu.py` | CLI quản lý key và menu tương tác |
| `signer/errors.py` | Bảng mã lỗi → HTTP status; `ALERT` = các lỗi 403 (dấu hiệu BE bị lạm dụng) |
| `tests/` | `unittest`; `helpers.py` dựng payload mẫu cho mọi op (`hl(op)`, …) |
| `*.sh` / `*.bat` | `install`, `signer` (admin/menu), `start` (server), `update` (git pull ff-only) |

## 3. Môi trường dev

```bash
bash install.sh --dev          # Windows: install.bat --dev — tạo .venv, cài requirements-test.txt
```

```bash
.venv/Scripts/python -m unittest discover -s tests -t .     # Linux/macOS: .venv/bin/python
```

- Python ≥ 3.10 (máy dev hiện tại: 3.11.0).
- Runtime chỉ có **3 gói** pin cứng: `pycryptodome==3.23.0`, `coincurve==21.0.0`, `msgpack==1.1.0`.
- `hyperliquid-python-sdk` **chỉ cài cho test** (để so chữ ký từng byte), không bao giờ cài lên máy Signer.
- Trong Cursor: chọn interpreter `.venv` (Ctrl+Shift+P → *Python: Select Interpreter*). `.idea/` là cấu hình PyCharm cũ, đã bị gitignore, có thể bỏ.

### File bí mật trong thư mục làm việc

`config.ini` và `keystore.json` có trong thư mục dự án trên máy dev (đã gitignore). Chúng đã được đưa vào [`.cursorignore`](../.cursorignore) để Cursor **không index và không gửi lên AI**. Đừng xoá các dòng đó, và đừng dán nội dung hai file này vào chat.

## 4. Ràng buộc không được phá

1. **Không thêm thư viện ngoài** cho runtime nếu thư viện chuẩn làm được. Thêm gói = phải pin version và kiểm tra dependency kéo theo. Không dùng `eth-account`, `hyperliquid-python-sdk`, `requests` ở runtime.
2. **Fail closed.** Tham số lạ, field lạ, key JSON lặp, giá trị sai kiểu → từ chối. Không bao giờ "bỏ qua cho qua".
3. **Kiểm tra cái gì thì ký đúng cái đó.** Binance: policy chạy trên params đã parse nhưng ký **chuỗi gốc** BE gửi. Hyperliquid: giữ nguyên thứ tự key JSON (msgpack phụ thuộc thứ tự).
4. **Mỗi request một lệnh**, không batch / cancel-all. Danh sách hành động **không bao giờ ký** nằm ở `docs/signer-api.md` → *Thao tác được phép* (rút tiền, `usdSend`, `approveAgent`, borrow/repay…). Mở rộng danh sách này là quyết định nghiệp vụ, không phải quyết định code.
5. **Không log** private key, HMAC secret, passphrase, hay payload kèm thông tin key.
6. **Chữ ký Hyperliquid phải khớp SDK từng byte** — test so với `sign_l1_action` cho mọi op × mainnet/testnet.
7. Docs viết bằng **tiếng Việt**, comment trong code tiếng Việt, message lỗi trả cho BE tiếng Anh.

## 5. Quy trình thêm một op mới (mẫu từ commit `a3fe6a8` "create sub accounts")

Một op mới thường chạm đúng các chỗ sau — làm đủ cả 8 bước trong cùng một commit:

1. `signer/policy.py` — thêm tên vào `OPS`.
2. `signer/venues/<venue>.py` — hằng `ALLOWED_…` (whitelist field), nhánh trong `detect_op`, hàm `_check_…`, nhánh trong `verify` (kể cả quy tắc `vaultAddress` với Hyperliquid).
3. Venue kia không hỗ trợ → phải trả `403 OP_MISMATCH` (đã tự động nếu `detect_op` không trả op đó).
4. `tests/helpers.py` — payload mẫu cho op.
5. `tests/test_<venue>.py` — case hợp lệ, từng case từ chối, và **so chữ ký với SDK** (Hyperliquid).
6. `tests/test_server.py` — test end-to-end qua HTTP.
7. `docs/signer-api.md` — bảng *Thao tác được phép* (cập nhật cả số lượng "N giá trị"), whitelist tham số, quy tắc rủi ro, ví dụ payload, bảng lỗi (`OP_NOT_ALLOWED` cũng ghi số op), câu hỏi mở nếu chưa thử testnet.
8. `README.md` (dòng test SDK đếm số op) và `docs/signer-impl.md` nếu có liên quan.

Hiện có 14 op: `place_order`, `cancel_order`, `place_conditional`, `cancel_conditional`, `set_leverage` (cả hai sàn); `sub_account_transfer`, `earn_transfer`, `create_sub_account` (chỉ Hyperliquid); `place_margin_order`, `cancel_margin_order`, `margin_transfer`, `set_margin_leverage`, `enable_isolated`, `margin_borrow_repay` (chỉ Binance margin — xem [`signer-margin.md`](signer-margin.md)).

## 6. Việc đang dở / cần theo dõi

Chưa có tính năng nào đang làm dở trong code. Các mục còn mở (chi tiết trong `docs/signer-api.md` → *Câu hỏi mở* và `docs/signer-impl.md` → *Quyết định cần chốt*):

- [ ] **Thử trên testnet trước khi BE dùng:** `subAccountTransfer` / `subAccountSpotTransfer`, `borrowLend`, `createSubAccount` — sàn có nhận chữ ký của agent key không (hay phải key master); độ dài tối đa tên sub-account (đang cho 64). `borrowLend` đang ký như L1 action, chưa có trong docs chính thức / SDK.
- [ ] **Hyperliquid `positionTpsl`:** `s` có tự co giãn theo vị thế không.
- [ ] **Binance `algoOrder`:** đối chiếu tên tham số (`algoType`, `triggerPrice`, `clientAlgoId`, `closePosition`) với docs hiện hành.
- [ ] **Giới hạn giá / notional:** Signer chưa chặn được lệnh giá vô lý (đề xuất `qty × price < max_notional_usd`).
- [ ] **Bảng asset Hyperliquid** chỉ có mainnet; chưa có testnet. Coin mới phải thêm tay rồi restart.
- [ ] **Đồng bộ giờ:** chưa có NTP; `max_payload_age_ms = 60000`.
- [ ] Các quyết định tạm chọn (mở khoá A+B, Ed25519, chữ ký Binance đã URL-encode, `r`/`s` không đệm 0…) — xem bảng trong `README.md`.

## 7. Vận hành (nhắc nhanh)

- Thêm key: `./signer.sh` → menu, hoặc `./signer.sh generate --venue binance|hyperliquid`. HMAC secret in **một lần**.
- Sửa IP / symbol / hạn mức: sửa tay `config.ini` → Linux `kill -HUP <pid>`, Windows restart.
- Sửa `[server]` hoặc đổi passphrase → restart.
- Cập nhật code trên máy Signer: `./update.sh` (git pull ff-only) rồi restart.

## 8. Thói quen làm việc khi dùng AI trong Cursor

- Trước khi sửa: đọc `docs/signer-api.md` phần liên quan; đặc tả là nguồn sự thật, code và docs phải đi cùng nhau.
- Sau khi sửa: chạy toàn bộ test (mất ~8 s). Không merge khi test đỏ.
- Commit message ngắn gọn, mô tả *vì sao* (xem commit `3053e02` làm mẫu).
- Mọi thay đổi chạm tới policy / auth / keystore nên được tự review như security review: tìm cách để BE gửi payload vượt hạn mức hoặc ký hành động không có trong whitelist.
