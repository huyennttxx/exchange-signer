# Signer — margin Binance: việc cần làm

Nguồn nghiệp vụ: [`binance-margin-api.md`](../binance-margin-api.md) (Đợt 317). Đặc tả Signer hiện tại: [`signer-api.md`](signer-api.md).

**Người vận hành** khởi tạo thao tác trên **app** (không phải bot tự chạy). App **gọi Signer** để ký rồi gửi lên Binance cho mọi thao tác cần chữ ký (update).

| Cột | Ý nghĩa |
|---|---|
| Cần làm | Có / Không |
| Ai | Người vận hành (qua app) / Bot |
| Kênh | `app + Signer` · `bot + Signer` · `không dùng` · `không qua Signer` |

---

## Op cần thêm vào Signer (tổng kết)

| Op | Endpoint sàn | Ai gọi Signer | Verify |
|---|---|---|---|
| Đặt lệnh margin | `POST /sapi/v1/margin/order` | Bot | `symbol` (allowlist config). `quantity` (`0 < quantity < max_qty` config) trừ khi đóng vị thế (`side=SELL`) thì không check quantity. Còn lại không check |
| Hủy lệnh margin | `DELETE /sapi/v1/margin/order` | Bot | Chỉ khớp `op` |
| Chuyển thế chấp | `POST /sapi/v1/asset/transfer` | App | Chỉ khớp `op` |
| Đổi đòn bẩy cross | `POST /sapi/v1/margin/max-leverage` | App | Chỉ khớp `op` |
| Bật cặp isolated | `POST /sapi/v1/margin/isolated/account` | App | Chỉ khớp `op` |
| Vay / trả riêng | `POST /sapi/v1/margin/borrow-repay` | App | Chỉ khớp `op` |

Không thêm: đặt đòn bẩy isolated (không có API); đọc lệnh / GET (không qua Signer); hủy mọi lệnh cặp (bot không dùng).

Vay/trả lúc mua/bán vẫn nằm trong **đặt lệnh** (`sideEffectType`), không cần op riêng.

Phân biệt: `set_leverage` futures hiện có (`POST /fapi/v1/leverage`) khác op đòn bẩy margin ở trên.

---

## 1. Chuyển tiền thế chấp và đòn bẩy

| Việc | Endpoint / ghi chú | Cần làm | Ai | Kênh |
|---|---|---|---|---|
| Chuyển thế chấp vào/ra tài khoản margin | `POST /sapi/v1/asset/transfer` (`MAIN_MARGIN`, `MARGIN_MAIN`, …; isolated qua `fromSymbol` / `toSymbol`) | Có | Người vận hành | app + Signer |
| Đổi đòn bẩy cross | `POST /sapi/v1/margin/max-leverage` (`maxLeverage` 3 hoặc 5 trên Classic) | Có | Người vận hành | app + Signer |
| Bật cặp isolated (nếu bot chạy isolated) | API `POST /sapi/v1/margin/isolated/account` (tài liệu Binance: lần đầu có thể phải bật trên web; API bật lại cặp đã từng tắt) | Có | Người vận hành | app + Signer (khi dùng API) |
| Đặt đòn bẩy isolated | Không có API đặt; đòn bẩy theo bậc cặp | Không | — | không dùng |
| Đọc tier / ngưỡng isolated | `GET …/isolatedMarginTier` (và tương tự) | Không | — | không qua Signer |

---

## 2. Đặt, hủy, đọc lệnh; `sideEffectType` và `autoRepayAtCancel`

| Việc | Endpoint | Cần làm | Ai | Kênh |
|---|---|---|---|---|
| Đặt lệnh | `POST /sapi/v1/margin/order` | Có | Bot | bot + Signer |
| Hủy một lệnh | `DELETE /sapi/v1/margin/order` | Có | Bot | bot + Signer |
| Hủy mọi lệnh của một cặp | `DELETE /sapi/v1/margin/openOrders` | Không | — | không dùng |
| Đọc lệnh / trades | các `GET /sapi/v1/margin/…` | Không | Bot (ngoài Signer) | không qua Signer |

### Policy Signer

| Op | Signer kiểm tra | Không kiểm |
|---|---|---|
| Đặt lệnh | `symbol` (allowlist config). `quantity` theo `max_qty` config; **đóng vị thế (`side=SELL`) → không check quantity** — cùng policy với futures `place_order` / `place_conditional` | Mọi tham số còn lại |
| Mọi op khác trong bảng tổng kết (hủy, chuyển thế chấp, đòn bẩy, bật isolated, borrow-repay) | Chỉ verify đúng `op` | Không verify tham số hay quy tắc rủi ro khác |

### Tham số thường gửi (tham khảo, không phải rule Signer trừ `symbol`/`quantity` khi đặt)

Đặt: `symbol`, `isIsolated`, `side`, `type` (`LIMIT` / `LIMIT_MAKER`), `quantity`, `price`, `timeInForce` (chỉ `LIMIT`), `newClientOrderId`, `sideEffectType`, `autoRepayAtCancel`, `recvWindow`, `timestamp`.

Hủy: `symbol`, `isIsolated`, `origClientOrderId`, `recvWindow`, `timestamp`.

### `sideEffectType` (bot)

| Giá trị | Ý nghĩa | Bot hiện dùng |
|---|---|---|
| `MARGIN_BUY` | Vay phần thiếu để đủ tiền lệnh | Lệnh mua |
| `AUTO_REPAY` | Dùng tiền thu về trả nợ (cùng asset đã vay) | Lệnh bán |
| `NO_SIDE_EFFECT` | Không vay, không trả | Không |
| `AUTO_BORROW_REPAY` | Vay rồi tự trả | Không |

### `autoRepayAtCancel` (bot)

- Gửi **lúc đặt lệnh**, không lúc hủy.
- Mặc định sàn = `true` (hủy lệnh `MARGIN_BUY` thì tự trả khoản đã vay).
- Bot gửi `FALSE` — khoản vay còn sau hủy tính lãi cho tới khi bán `AUTO_REPAY` hoặc app gọi op vay/trả riêng (ký qua Signer).

---

## 3. Vay, trả nợ

| Việc | Endpoint / cách | Cần làm | Ai | Kênh |
|---|---|---|---|---|
| Vay khi mua, trả khi bán | `sideEffectType` trên `POST …/margin/order` | Có | Bot | gói trong đặt lệnh (bot + Signer) |
| Vay / trả riêng (vd. nợ còn sau hủy với `autoRepayAtCancel=FALSE`) | `POST /sapi/v1/margin/borrow-repay` | Có khi phát sinh | Người vận hành | app + Signer |

---

## Ràng buộc khi triển khai Signer (chưa làm trong code)

Chữ ký Binance **không** gắn path. Key futures hiện chỉ bật Futures. Margin cần Spot & Margin Trading (và quyền transfer/loan tương ứng) → cùng payload có thể bị gửi sang endpoint khác. Cần tách key/op/policy trước khi code.
