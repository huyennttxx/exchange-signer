# Signer — tài liệu API

| Trường | Giá trị |
|---|---|
| **Phiên bản** | 0.3 — bản nháp, chưa có code |
| **Ngày** | 2026-10-21 |
| **Mục đích** | Thống nhất API giữa BE và Signer |
| **Sơ đồ** | [`signing-flow.svg`](signing-flow.svg) |

## Signer làm gì

- Signer giữ private key của sàn (Binance USDⓈ-M Futures, Hyperliquid). Nó giữ được **nhiều key**, mỗi key có một `key_id` (xem [Key](#key)).
- **Signer không có outbound network.**
  - Nó không gọi sàn và không đọc giá thị trường.
- **BE tự dựng payload theo đúng định dạng của sàn.** Signer làm ba việc:
  1. **Verify:** đọc payload và kiểm tra nó đúng là thao tác `op` mà BE khai báo.
  2. **Duyệt:** với op đặt lệnh — kiểm symbol và qty (lệnh đóng thì bỏ qty); op khác chỉ khớp `op`.
  3. **Ký:** trả về chữ ký.
- BE gắn chữ ký vào payload rồi tự gửi lên sàn.
- **Signer không ký mù** đối với loại action lạ (không khớp `op` / không nằm trong danh sách). Tham số chi tiết ngoài symbol/qty của lệnh đặt do BE chịu.
- **Signer chỉ kiểm tra rủi ro hẹp, không kiểm tra định dạng.** Ví dụ giá sai, `tif` sai, `vaultAddress` sai thì sàn / BE tự chịu.

## Danh sách endpoint

| Method | Path | Xác thực | Mục đích |
|---|---|---|---|
| `GET` | `/health` | Không | Kiểm tra Signer còn sống |
| `POST` | `/v1/sign` | IP allowlist + HMAC, theo `key_id` | Kiểm tra và ký một payload |

Mọi path khác trả `404`.

---

## Key

- Mỗi key có một `key_id` dạng UUID, do Signer sinh. Ví dụ `3f2b8c1e-6d4a-4f7b-9c2e-1a5d8e7f0b34`.
- Mỗi key thuộc đúng **một sàn**. Sàn của một request được suy ra từ `key_id`.
- **Nạp và xoá key chỉ làm thao tác trên máy Signer**, không có API cho việc này. BE không thể thêm, đổi hay đọc key.
- **Mỗi key có IP allowlist và HMAC secret riêng** (xem [Xác thực](#xác-thực)).
- **Key Binance futures** chỉ bật quyền Futures, tắt Spot và Withdraw.
  - Lý do: chữ ký Binance không bao gồm path. Nếu key có quyền Spot, một payload đặt lệnh futures có thể bị gửi sang endpoint spot.
- **Key Binance margin** (op `place_margin_order`, …): cần quyền Spot & Margin (và loan/transfer nếu dùng); nên tách key khỏi key futures — xem [`signer-margin.md`](signer-margin.md).

---

## Xác thực

Chỉ áp dụng cho `/v1/sign`, và đi theo `key_id`: request xin ký bằng key nào thì phải qua xác thực của key đó.

### IP allowlist

| Kiểm tra | Lỗi nếu sai |
|---|---|
| `key_id` trong body tồn tại, và có section `[key:<key_id>]` trong `config.ini` | `401 UNAUTHENTICATED` |
| IP của request nằm trong `allowed_ips` của section đó | `401 UNAUTHENTICATED` |

Các trường hợp trả **cùng một lỗi**, để người gọi không dò được key nào đang tồn tại.

### HMAC

Mỗi key có một **HMAC secret riêng**, do Signer sinh lúc `generate` / `import` (hoặc `rotate-hmac`) và chỉ in ra **một lần** trên máy Signer. Người vận hành đưa secret đó cho BE, cấu hình theo từng `key_id`. Lộ secret của một key không ảnh hưởng key khác.

BE gửi thêm 2 header:

| Header | Giá trị |
|---|---|
| `X-Signer-Timestamp` | Giờ của BE, Unix epoch tính bằng mili giây, chỉ gồm chữ số |
| `X-Signer-Signature` | `hex(HMAC-SHA256(secret, string_to_sign))`, 64 ký tự hex (hoa hay thường đều được) |

```
string_to_sign = timestamp + "
" + "POST" + "
" + "/v1/sign" + "
" + <body thô>
```

- **Key HMAC là nguyên chuỗi secret** (64 ký tự hex) dạng UTF-8, **không** decode hex.
- **Body thô** là đúng các byte gửi đi. Ký xong thì không được serialize lại JSON (đổi khoảng trắng hay thứ tự key là sai chữ ký).
- `key_id` nằm trong body nên cũng được ký.

| Kiểm tra (theo thứ tự) | Lỗi nếu sai |
|---|---|
| `key_id` tồn tại, IP nằm trong allowlist | `401 UNAUTHENTICATED` |
| Key có HMAC secret (key tạo trước khi có HMAC thì chưa có: chạy `rotate-hmac`) | `503 KEY_NOT_LOADED` |
| Có đủ 2 header, đúng định dạng | `401 UNAUTHENTICATED` |
| Chữ ký đúng (so sánh constant-time) | `401 UNAUTHENTICATED` |
| `timestamp` lệch so với giờ Signer không quá `max_payload_age_ms` | `401 UNAUTHENTICATED` |

Mọi lỗi xác thực trả cùng một body `{"error": "UNAUTHENTICATED", "message": "unauthenticated"}`. Lý do cụ thể chỉ có trong log của Signer (`detail=…`).

Ví dụ (Python):

```python
import hashlib, hmac, json, time

body = json.dumps({"key_id": key_id, "op": op, "payload": payload}).encode()
ts = str(int(time.time() * 1000))
sig = hmac.new(secret.encode(), f"{ts}
POST
/v1/sign
".encode() + body, hashlib.sha256).hexdigest()
requests.post(url, data=body, headers={"Content-Type": "application/json",
                                       "X-Signer-Timestamp": ts, "X-Signer-Signature": sig})
```

Test vector: secret `00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff`, timestamp `1729500000000`, body `{"key_id":"k","op":"set_leverage","payload":"x"}` → `X-Signer-Signature` = `e6e89edb2cf9daa6f06be6711e3836dbdf475e84ffa46e24f21f1dd62d40858b`.

**Không có chống replay riêng** (không lưu chữ ký đã thấy). Lý do: trong cửa sổ `max_payload_age_ms` của HMAC, gửi lại đúng request chỉ nhận lại đúng chữ ký đã trả (Ed25519 và ECDSA RFC 6979 đều tất định), mà kẻ nghe lén được request thì cũng đã thấy response. Signer **không** kiểm tra độ mới của `timestamp` / `nonce` trong payload.

**Đổi secret:** `./signer.sh rotate-hmac <key_id>` (hoặc menu → Xem key → Tạo HMAC secret mới), rồi `kill -HUP` / restart. Secret cũ hết hiệu lực ngay khi Signer nạp lại, nên cập nhật BE cùng lúc.

---

## `GET /health`

Kiểm tra Signer còn sống, và cho biết giờ của Signer.

**200 OK**, `Content-Type: application/json`

```json
{ "status": "ok", "time_ms": 1729500000000 }
```

| Field | Mô tả |
|---|---|
| `time_ms` | Giờ hiện tại của Signer, Unix epoch tính bằng mili giây |

HMAC so `X-Signer-Timestamp` với giờ của Signer (`max_payload_age_ms`), mà Signer không có outbound network nên đồng hồ có thể trôi. Gợi ý cho BE:
- Định kỳ gọi `/health` và tính độ lệch: `time_ms − (t_gửi + t_nhận) / 2`, với `t_gửi`, `t_nhận` là giờ của BE lúc gửi và lúc nhận response.
- Cảnh báo khi độ lệch vượt khoảng `max_payload_age_ms / 2`. Nếu để lệch tới `max_payload_age_ms`, Signer sẽ từ chối mọi request (HMAC `STALE_PAYLOAD`).

---

## `POST /v1/sign`

| Field | Mô tả |
|---|---|
| `key_id` | Key dùng để ký. Sàn được suy ra từ key |
| `op` | Thao tác, một trong các giá trị ở [Thao tác được phép](#thao-tác-được-phép) |
| `payload` | Nội dung cần ký, theo định dạng của sàn |

### Binance

Request: `payload` là chuỗi params do BE dựng, **chưa có** `signature`.

```json
{
  "key_id": "3f2b8c1e-6d4a-4f7b-9c2e-1a5d8e7f0b34",
  "op": "place_order",
  "payload": "symbol=BTCUSDT&side=BUY&type=LIMIT&timeInForce=GTC&quantity=0.005&price=60000&newClientOrderId=0x8c1f…&recvWindow=2000&timestamp=1729500000000"
}
```

Response:

```json
{ "signature": "…" }
```

BE nối `&signature=<giá trị>` vào cuối **đúng chuỗi `payload` đã gửi**, rồi gửi lên sàn:
- `POST`: làm body, `Content-Type: application/x-www-form-urlencoded`.
- `DELETE`: nối sau `?` của URL.
- Header `X-MBX-APIKEY` do BE tự gắn.

### Hyperliquid

Request: `payload` là phần cần ký của body gửi lên `/exchange`, tức là mọi field trừ `signature`.

```json
{
  "key_id": "a91d4e7c-2b38-4c55-8f0a-6e3b9d1c7f22",
  "op": "place_order",
  "payload": {
    "action": {
      "type": "order",
      "orders": [{ "a": 0, "b": true, "p": "60000", "s": "0.005", "r": false, "t": { "limit": { "tif": "Gtc" } }, "c": "0x8c1f…" }],
      "grouping": "na"
    },
    "nonce": 1729500000000
  }
}
```

Response:

```json
{ "signature": { "r": "0x…", "s": "0x…", "v": 27 } }
```

BE gửi `POST /exchange` với body `{ "action", "nonce", "signature" }`, kèm `"vaultAddress"` y hệt nếu payload gửi sang Signer có field này. Chữ ký gắn với `vaultAddress`, nên thiếu hoặc khác thì chữ ký sai.
- `action` phải **giữ nguyên thứ tự key** như lúc gửi sang Signer.
- Lý do: chữ ký được tính trên `action` encode bằng msgpack, theo đúng thứ tự key. Đổi thứ tự thì chữ ký sai.

---

## Thao tác được phép

- `op` khác 14 giá trị dưới đây: `403 OP_NOT_ALLOWED`.
- Payload không khớp với `op` đã khai báo: `403 OP_MISMATCH`. Ví dụ khai `op = set_leverage` nhưng payload lại là lệnh đặt.

| `op` | Endpoint của BE | Binance: payload phải có | Hyperliquid: payload phải có |
|---|---|---|---|
| `place_order` | `POST /v1/orders` | `type`, không có `algoType` / marker margin (→ `POST /fapi/v1/order`) | `action.type = "order"`, `grouping = "na"` |
| `cancel_order` | `DELETE /v1/orders/{client_order_id}` | `origClientOrderId`, không có `isIsolated` (→ `DELETE /fapi/v1/order`) | `action.type = "cancelByCloid"` |
| `place_conditional` | `POST /v1/conditional` | `algoType` (→ `POST /fapi/v1/algoOrder`) | `action.type = "order"`, `grouping = "positionTpsl"` |
| `cancel_conditional` | `DELETE /v1/conditional/{client_algo_id}` | `clientAlgoId`, không có `algoType` (→ `DELETE /fapi/v1/algoOrder`) | `action.type = "cancelByCloid"` |
| `set_leverage` | `POST /v1/leverage` | `leverage` (→ `POST /fapi/v1/leverage`) | `action.type = "updateLeverage"` |
| `sub_account_transfer` | — | Không hỗ trợ (luôn `403 OP_MISMATCH`) | `action.type = "subAccountTransfer"` (USDC perp) hoặc `"subAccountSpotTransfer"` (token spot) |
| `earn_transfer` | — | Không hỗ trợ (luôn `403 OP_MISMATCH`) | `action.type = "borrowLend"` (mọi `operation`; BE tự giới hạn supply/withdraw nếu cần) |
| `create_sub_account` | — | Không hỗ trợ (luôn `403 OP_MISMATCH`) | `action.type = "createSubAccount"` |
| `place_margin_order` | — | `sideEffectType` / `autoRepayAtCancel` / (`isIsolated`+`type`) (→ `POST /sapi/v1/margin/order`) | Không hỗ trợ (`403 OP_MISMATCH`) |
| `cancel_margin_order` | — | `origClientOrderId` + `isIsolated` (→ `DELETE /sapi/v1/margin/order`) | Không hỗ trợ |
| `margin_transfer` | — | `type` ∈ `MAIN_MARGIN` / `MARGIN_MAIN` / … (→ `POST /sapi/v1/asset/transfer`) | Không hỗ trợ |
| `set_margin_leverage` | — | `maxLeverage` (→ `POST /sapi/v1/margin/max-leverage`) | Không hỗ trợ |
| `enable_isolated` | — | `symbol` không kèm marker lệnh khác (→ `POST /sapi/v1/margin/isolated/account`) | Không hỗ trợ |
| `margin_borrow_repay` | — | `type` = `BORROW` / `REPAY` (→ `POST /sapi/v1/margin/borrow-repay`) | Không hỗ trợ |

Chi tiết policy margin: [`signer-margin.md`](signer-margin.md).

Không có trong danh sách op (detect không khớp) thì **không được ký**, ví dụ: `withdraw3`, `usdSend`, `spotSend`, `sendAsset`, `approveAgent`, `approveBuilderFee`, `subAccountModify`, cancel-all, đổi chế độ margin futures, lệnh spot thuần (không phải margin op ở trên). Chuyển thế chấp margin (`margin_transfer`) và master ↔ sub-account Hyperliquid (`sub_account_transfer`) nằm trong danh sách được ký.

---

## Policy

### Nguyên tắc

1. **Khớp `op`:** payload phải đúng loại thao tác đã khai báo (`403 OP_MISMATCH` nếu không).
2. **Op tạo lệnh** (`place_order`, `place_conditional`, và Binance `place_margin_order`): kiểm **symbol/asset** và **qty**. Lệnh đóng thì **không** kiểm qty.
3. **Mọi op khác:** chỉ khớp `op` — không whitelist tham số, không kiểm đòn bẩy, freshness payload, vaultAddress, v.v. BE chịu trách nhiệm nội dung.

### Quy tắc rủi ro (chỉ op đặt lệnh)

| Quy tắc | Binance | Hyperliquid | Lỗi |
|---|---|---|---|
| Symbol | `place_order` / `place_conditional` / `place_margin_order`: `symbol` phải có trong section của key | Mỗi phần tử trong `orders`: `a` là chỉ số asset của một symbol trong section (tra `<COIN>USDC` trong `signer/hyperliquid_assets.ini`) | `403 SYMBOL_NOT_ALLOWED` |
| Khối lượng | `0 < quantity < max_qty` của symbol. **Không kiểm** nếu lệnh đóng: `place_order` với `reduceOnly=true`, `place_conditional` với `closePosition=true`, `place_margin_order` với `side=SELL` | `0 < s < max_qty` của asset. **Không kiểm** nếu `r = true` (giảm / đóng). Mọi phần tử trong `orders` đều được kiểm | `403 QTY_EXCEEDS_LIMIT` |

Giải thích ngắn:
- **Lệnh đóng không giới hạn qty** — chỉ còn chặn mở / tăng vị thế vượt `max_qty`.
- **Huỷ, đòn bẩy, chuyển tiền, Earn, tạo sub-account, …** — Signer chỉ xác nhận đúng loại action; không siết tham số.
- `price`, `tif`, `nonce`, `vaultAddress`, `builder`, … do BE / sàn chịu.
- Gợi ý cho BE: `client_order_id` / `client_algo_id` nên dạng `0x` + 32 hex (hợp lệ cho cả Binance và Hyperliquid `cloid`).

### Giới hạn

Giới hạn đi theo từng key, trong section `[key:<key_id>]` của `config.ini`. Key mới (`generate` / `import`) được `max_leverage = 40` và symbol `BTCUSDC` với `max_qty = 0.1` (cùng tên trên Binance lẫn Hyperliquid).

| Tên | Nằm ở | Ý nghĩa | Giá trị |
|---|---|---|---|
| `allowed_ips` | `[key:…]` | IP của BE được gọi bằng key này | Theo key |
| `<symbol> = <max_qty>` | `[key:…]` | Symbol được đặt lệnh và khối lượng tối đa (so sánh `<`); không áp dụng khi lệnh đóng | Theo key |
| `max_leverage` | `[key:…]` | Có trong config (tương thích); **verify hiện không dùng** | Theo key |
| `max_payload_age_ms` | `[server]` | Độ lệch tối đa của `X-Signer-Timestamp` (HMAC) so với giờ Signer | `60000` (1 phút) |

---

## Ví dụ payload cho từng thao tác

### Đặt lệnh bảo hiểm

- **Binance**:
  ```
  algoType=CONDITIONAL&symbol=BTCUSDT&side=SELL&type=STOP_MARKET&triggerPrice=55000&closePosition=true&workingType=MARK_PRICE&clientAlgoId=0x…&recvWindow=2000&timestamp=…
  ```
- **Hyperliquid**:
  ```json
  {
    "action": {
      "type": "order",
      "orders": [{ "a": 0, "b": false, "p": "54000", "s": "0.005", "r": true, "t": { "trigger": { "isMarket": true, "triggerPx": "55000", "tpsl": "sl" } }, "c": "0x…" }],
      "grouping": "positionTpsl"
    },
    "nonce": 1729500000000
  }
  ```

### Huỷ lệnh, huỷ lệnh bảo hiểm

- **Binance**:
  - huỷ lệnh: `symbol=BTCUSDT&origClientOrderId=0x…&recvWindow=2000&timestamp=…`
  - huỷ lệnh bảo hiểm: `clientAlgoId=0x…&recvWindow=2000&timestamp=…`
- **Hyperliquid** (giống nhau cho cả hai):
  ```json
  { "action": { "type": "cancelByCloid", "cancels": [{ "asset": 0, "cloid": "0x…" }] }, "nonce": 1729500000000 }
  ```

### Đổi đòn bẩy

- **Binance**: `symbol=BTCUSDT&leverage=3&recvWindow=2000&timestamp=…`
- **Hyperliquid**:
  ```json
  { "action": { "type": "updateLeverage", "asset": 0, "isCross": true, "leverage": 3 }, "nonce": 1729500000000 }
  ```

### Chuyển giữa master và sub-account (chỉ Hyperliquid)

- USDC perp, master → sub (`usd` = 1 USDC):
  ```json
  { "action": { "type": "subAccountTransfer", "subAccountUser": "0x…", "isDeposit": true, "usd": 1000000 }, "nonce": 1729500000000 }
  ```
- Token spot, sub → master:
  ```json
  { "action": { "type": "subAccountSpotTransfer", "subAccountUser": "0x…", "isDeposit": false, "token": "USDC:0x6d1e7cde53ba9467b783cb7c530ce054", "amount": "12.5" }, "nonce": 1729500000000 }
  ```

### Gửi / rút Earn (chỉ Hyperliquid)

- Gửi 100 USDC vào Earn:
  ```json
  { "action": { "type": "borrowLend", "operation": "supply", "token": 0, "amount": "100" }, "nonce": 1729500000000 }
  ```
- Rút toàn bộ USDC khỏi Earn:
  ```json
  { "action": { "type": "borrowLend", "operation": "withdraw", "token": 0, "amount": null }, "nonce": 1729500000000 }
  ```

### Tạo sub-account (chỉ Hyperliquid)

```json
{ "action": { "type": "createSubAccount", "name": "bot-1" }, "nonce": 1729500000000 }
```

Địa chỉ của sub-account mới nằm trong response của sàn (hoặc query `/info` với `{"type": "subAccounts", "user": <master>}`).

### Margin Binance (chỉ Binance)

- Đặt: `symbol=BTCUSDT&isIsolated=FALSE&side=BUY&type=LIMIT&timeInForce=GTC&quantity=0.005&price=60000&newClientOrderId=0x…&sideEffectType=MARGIN_BUY&autoRepayAtCancel=FALSE&recvWindow=2000&timestamp=…`
- Huỷ: `symbol=BTCUSDT&isIsolated=FALSE&origClientOrderId=0x…&recvWindow=2000&timestamp=…`
- Chuyển thế chấp: `type=MAIN_MARGIN&asset=USDC&amount=50&recvWindow=2000&timestamp=…`
- Đòn bẩy cross: `maxLeverage=3&recvWindow=2000&timestamp=…`
- Bật isolated: `symbol=BTCUSDT&recvWindow=2000&timestamp=…`
- Vay/trả riêng: `asset=USDC&amount=10&type=REPAY&isIsolated=FALSE&recvWindow=2000&timestamp=…`

---

## Lỗi

Khi lỗi, Signer **không bao giờ trả về chữ ký**.

```json
{ "error": "QTY_EXCEEDS_LIMIT", "message": "quantity 0.02 must be < 0.01" }
```

| HTTP | `error` | Khi nào |
|---|---|---|
| 400 | `INVALID_REQUEST` | Body không phải JSON, hoặc `payload` không đọc được |
| 401 | `UNAUTHENTICATED` | `key_id` không tồn tại hoặc không có section trong config, IP không nằm trong `allowed_ips` của key, hoặc HMAC sai / thiếu / quá cũ |
| 403 | `OP_NOT_ALLOWED` | `op` không nằm trong 14 thao tác được phép |
| 403 | `OP_MISMATCH` | Payload không khớp với `op` đã khai báo |
| 403 | `SYMBOL_NOT_ALLOWED` | Symbol / asset không có trong section của key (chỉ op đặt lệnh) |
| 403 | `QTY_EXCEEDS_LIMIT` | Khối lượng `≤ 0`, `≥ max_qty`, hoặc không đọc được thành số (chỉ op đặt lệnh khi không phải lệnh đóng) |
| 403 | `STALE_PAYLOAD` | `X-Signer-Timestamp` (HMAC) quá cũ hoặc ở tương lai |
| 403 | `PARAM_NOT_ALLOWED` / `TOO_MANY_ORDERS` / `LEVERAGE_EXCEEDS_LIMIT` / `MUST_CLOSE_POSITION` | Còn trong mã lỗi (tương thích); verify hiện không trả |
| 503 | `KEY_NOT_LOADED` | Key tồn tại nhưng hiện chưa dùng được để ký (bản ghi lỗi, hoặc chưa có HMAC secret) |
| 500 | `INTERNAL` | Lỗi không lường trước |

Các lỗi `403` từ `OP_NOT_ALLOWED` trở xuống trong bảng là dấu hiệu BE bị lỗi hoặc bị chiếm quyền. Nên cảnh báo ngay khi gặp, đừng chỉ ghi log.

---

## Câu hỏi mở / cần kiểm tra trước khi code

- [ ] **Binance `algoOrder`:** đối chiếu lại tên tham số với docs hiện hành.
  - Các tên cần kiểm tra: `algoType`, `triggerPrice`, `clientAlgoId`, `closePosition`.
  - Kiểm tra thêm: lệnh huỷ algo có nhận tham số `symbol` không.
- [ ] **Hyperliquid `positionTpsl`:** thử trên testnet xem `s` có tự co giãn theo khối lượng vị thế không.
- [ ] **Hyperliquid `subAccountTransfer` bằng agent key:** thử trên testnet xem sàn có nhận chữ ký của API wallet (agent) cho hai action chuyển sub-account không, hay bắt buộc ký bằng key của master.
- [ ] **Hyperliquid `borrowLend`:** Signer ký như L1 action (giống `sign_l1_action`, theo ví dụ của Chainstack). Docs chính thức và SDK chưa có action này, một số nguồn gọi là user-signed EIP-712: thử `supply` / `withdraw` trên testnet trước khi BE dùng, kể cả bằng agent key.
- [ ] **Hyperliquid `createSubAccount` bằng agent key và độ dài tên:** thử trên testnet xem sàn có nhận chữ ký của agent không (hay bắt buộc key master), và tên dài tối đa bao nhiêu ký tự (Signer đang cho tới 64).
- [ ] **Giới hạn giá:** Signer không có giá thị trường nên không chặn được lệnh đặt giá vô lý.
  - Có thể thêm giới hạn `qty × price < max_notional_usd`.
  - Hoặc chấp nhận rằng `max_qty` theo symbol đã đủ để giới hạn thiệt hại.
