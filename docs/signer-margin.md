# Signer — margin Binance: việc cần làm

Nguồn nghiệp vụ: [`binance-margin-api.md`](../binance-margin-api.md) (Đợt 317). Đặc tả Signer hiện tại: [`signer-api.md`](signer-api.md).

**Người vận hành** khởi tạo thao tác trên **app** (không phải bot tự chạy). App **gọi Signer** để ký rồi gửi lên Binance cho mọi thao tác cần chữ ký (update).

| Cột | Ý nghĩa |
|---|---|
| Cần làm | Có / Không |
| Ai | Người vận hành (qua app) / Bot |
| Kênh | `app + Signer` · `bot + Signer` · `không dùng` · `không qua Signer` |

---

## Op margin trong Signer (tổng kết)

| `op` | Endpoint sàn | Ai gọi | Ý nghĩa | Giới hạn / verify Signer |
|---|---|---|---|---|
| `place_margin_order` | `POST /sapi/v1/margin/order` | Bot | Đặt lệnh margin (cross/isolated). Vay/trả lúc khớp nằm trong `sideEffectType` (`MARGIN_BUY` mua, `AUTO_REPAY` bán) | Whitelist tham số; `symbol` ∈ config; `type` ∈ {`LIMIT`,`LIMIT_MAKER`}; `autoRepayAtCancel=FALSE`; sideEffect theo side; BUY: `0 < quantity < max_qty`; **SELL không check quantity**. Freshness |
| `cancel_margin_order` | `DELETE /sapi/v1/margin/order` | Bot | Hủy **một** lệnh margin theo `origClientOrderId` (+ `isIsolated`) | Whitelist; `symbol` ∈ config; freshness. Không ký hủy-all |
| `margin_transfer` | `POST /sapi/v1/asset/transfer` | App | Chuyển thế chấp spot ↔ cross ↔ isolated (`type` = `MAIN_MARGIN`, `MARGIN_MAIN`, …) | Whitelist; freshness. Không hạn mức `amount` / `asset` |
| `set_margin_leverage` | `POST /sapi/v1/margin/max-leverage` | App | Đặt đòn bẩy tối đa tài khoản **cross** (`maxLeverage`, Classic: 3 hoặc 5) | Whitelist; freshness. Không so với `max_leverage` config (khác `set_leverage` futures) |
| `enable_isolated` | `POST /sapi/v1/margin/isolated/account` | App | Bật lại cặp isolated đã từng tắt (`symbol`). Lần đầu Binance có thể bắt bật trên web | Whitelist (`symbol`,`recvWindow`,`timestamp`); `symbol` ∈ config; freshness. Không đoán op từ mọi payload chỉ có `symbol` |
| `margin_borrow_repay` | `POST /sapi/v1/margin/borrow-repay` | App | Vay hoặc trả nợ **riêng** (`type` = `BORROW` / `REPAY`), vd. nợ còn sau hủy với `autoRepayAtCancel=FALSE` | Whitelist; freshness; **`BORROW` không được vay tài sản gốc** (vd. `BTC` của `BTCUSDT`). Không hạn mức `amount` |

Không có op: đặt đòn bẩy isolated (không API); đọc lệnh / GET (không qua Signer); hủy mọi lệnh cặp (bot không dùng).

Vay/trả lúc mua/bán vẫn nằm trong `place_margin_order` (`sideEffectType`), không cần op riêng.

Phân biệt: `set_leverage` futures (`POST /fapi/v1/leverage`, có check `max_leverage` config) ≠ `set_margin_leverage`.

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

| Op | Signer kiểm tra |
|---|---|
| Đặt lệnh | Whitelist; `symbol` ∈ config; `type` ∈ {`LIMIT`,`LIMIT_MAKER`}; `autoRepayAtCancel` = `FALSE`; BUY: `sideEffectType` ∈ {`MARGIN_BUY`,`NO_SIDE_EFFECT`} + `0 < quantity < max_qty`; SELL: `sideEffectType` ∈ {`AUTO_REPAY`,`NO_SIDE_EFFECT`}, **không check quantity**; freshness |
| Hủy / chuyển / đòn bẩy / isolated / borrow-repay | Whitelist theo op; freshness; `symbol` ∈ config nếu có; `BORROW` không vay tài sản gốc |

### Tham số được phép (whitelist Signer)

Đặt: `symbol`, `isIsolated`, `side`, `type` (`LIMIT` / `LIMIT_MAKER`), `quantity`, `price`, `timeInForce` (chỉ `LIMIT`), `newClientOrderId`, `sideEffectType`, `autoRepayAtCancel`, `recvWindow`, `timestamp`. Không nhận `quoteOrderQty`.

Hủy: `symbol`, `isIsolated`, `origClientOrderId`, `recvWindow`, `timestamp`.

### `sideEffectType` (rule Signer)

| Giá trị | Ý nghĩa | Được phép |
|---|---|---|
| `MARGIN_BUY` | Vay phần thiếu để đủ tiền lệnh | Chỉ `side=BUY` |
| `AUTO_REPAY` | Dùng tiền thu về trả nợ (cùng asset đã vay) | Chỉ `side=SELL` |
| `NO_SIDE_EFFECT` | Không vay, không trả | BUY hoặc SELL |
| `AUTO_BORROW_REPAY` | Vay rồi tự trả | **Không** (bán khống nếu kèm SELL) |

### `autoRepayAtCancel` (rule Signer)

- Bắt buộc gửi **`FALSE`** lúc đặt lệnh (không gửi thì sàn mặc định `TRUE`).
- Khoản vay còn sau hủy tính lãi cho tới khi bán `AUTO_REPAY` hoặc app gọi op vay/trả riêng (ký qua Signer).

---

## 3. Vay, trả nợ

| Việc | Endpoint / cách | Cần làm | Ai | Kênh |
|---|---|---|---|---|
| Vay khi mua, trả khi bán | `sideEffectType` trên `POST …/margin/order` | Có | Bot | gói trong đặt lệnh (bot + Signer) |
| Vay / trả riêng (vd. nợ còn sau hủy với `autoRepayAtCancel=FALSE`) | `POST /sapi/v1/margin/borrow-repay` | Có khi phát sinh | Người vận hành | app + Signer |

---

## Ràng buộc chữ ký Binance

Chữ ký Binance **không** gắn path/method. Cùng query string có thể bị gửi sang endpoint khác (vd. chữ ký `enable_isolated` với `symbol`+`timestamp` trùng hình dạng `DELETE /fapi/v1/allOpenOrders`). Mitigation trong Signer: whitelist tham số theo op, `symbol` ∈ config, không đoán op từ payload thừa/thiếu marker. Nên tách key futures khỏi key margin (quyền Spot & Margin / loan / transfer).
