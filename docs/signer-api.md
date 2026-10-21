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
  2. **Duyệt:** kiểm tra rủi ro theo policy.
  3. **Ký:** trả về chữ ký.
- BE gắn chữ ký vào payload rồi tự gửi lên sàn.
- **Signer không ký mù.** Payload nào không nhận ra được, hoặc có tham số nằm ngoài danh sách cho phép, đều bị từ chối.
- **Signer chỉ kiểm tra rủi ro, không kiểm tra định dạng.** Ví dụ giá sai định dạng hay `tif` sai thì sàn sẽ từ chối; caller tự chịu.

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
- **Key Binance chỉ bật quyền Futures**, tắt Spot và Withdraw.
  - Lý do: chữ ký Binance không bao gồm path. Nếu key có quyền Spot, một payload đặt lệnh futures có thể bị gửi sang endpoint spot.

---

## Xác thực

Chỉ áp dụng cho `/v1/sign`, và đi theo `key_id`: request xin ký bằng key nào thì phải qua xác thực của key đó.

### IP allowlist

| Kiểm tra | Lỗi nếu sai |
|---|---|
| `key_id` trong body tồn tại | `401 UNAUTHENTICATED` |
| IP của request nằm trong allowlist của `key_id` đó | `401 UNAUTHENTICATED` |

Hai trường hợp trả **cùng một lỗi**, để người gọi không dò được key nào đang tồn tại.

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

**Không có chống replay riêng** (không lưu chữ ký đã thấy). Lý do: trong cửa sổ `max_payload_age_ms`, gửi lại đúng request chỉ nhận lại đúng chữ ký đã trả (Ed25519 và ECDSA RFC 6979 đều tất định), mà kẻ nghe lén được request thì cũng đã thấy response. Payload bên trong vẫn bị giới hạn bởi quy tắc `STALE_PAYLOAD`.

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

Quy tắc `STALE_PAYLOAD` so `timestamp` / `nonce` với giờ của Signer, mà Signer không có outbound network nên đồng hồ có thể trôi. Gợi ý cho BE:
- Định kỳ gọi `/health` và tính độ lệch: `time_ms − (t_gửi + t_nhận) / 2`, với `t_gửi`, `t_nhận` là giờ của BE lúc gửi và lúc nhận response.
- Cảnh báo khi độ lệch vượt khoảng `max_payload_age_ms / 2`. Nếu để lệch tới `max_payload_age_ms`, Signer sẽ từ chối mọi request, kể cả huỷ lệnh và đặt lệnh bảo hiểm.

---

## `POST /v1/sign`

| Field | Mô tả |
|---|---|
| `key_id` | Key dùng để ký. Sàn được suy ra từ key |
| `op` | Thao tác, một trong 5 giá trị ở [Thao tác được phép](#thao-tác-được-phép) |
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

BE gửi `POST /exchange` với body `{ "action", "nonce", "signature" }`.
- `action` phải **giữ nguyên thứ tự key** như lúc gửi sang Signer.
- Lý do: chữ ký được tính trên `action` encode bằng msgpack, theo đúng thứ tự key. Đổi thứ tự thì chữ ký sai.

---

## Thao tác được phép

- `op` khác 5 giá trị dưới đây: `403 OP_NOT_ALLOWED`.
- Payload không khớp với `op` đã khai báo: `403 OP_MISMATCH`. Ví dụ khai `op = set_leverage` nhưng payload lại là lệnh đặt.

| `op` | Endpoint của BE | Binance: payload phải có | Hyperliquid: payload phải có |
|---|---|---|---|
| `place_order` | `POST /v1/orders` | `type`, không có `algoType` (→ `POST /fapi/v1/order`) | `action.type = "order"`, `grouping = "na"` |
| `cancel_order` | `DELETE /v1/orders/{client_order_id}` | `origClientOrderId` (→ `DELETE /fapi/v1/order`) | `action.type = "cancelByCloid"` |
| `place_conditional` | `POST /v1/conditional` | `algoType` (→ `POST /fapi/v1/algoOrder`) | `action.type = "order"`, `grouping = "positionTpsl"` |
| `cancel_conditional` | `DELETE /v1/conditional/{client_algo_id}` | `clientAlgoId`, không có `algoType` (→ `DELETE /fapi/v1/algoOrder`) | `action.type = "cancelByCloid"` |
| `set_leverage` | `POST /v1/leverage` | `leverage` (→ `POST /fapi/v1/leverage`) | `action.type = "updateLeverage"` |

Không có trong danh sách, và **không bao giờ được ký**:
- rút tiền, chuyển tiền (`withdraw3`, `usdSend`, `spotSend`, `usdClassTransfer`);
- `approveAgent`, `approveBuilderFee`;
- cancel-all, batch order;
- đổi chế độ margin;
- mọi lệnh spot.

---

## Policy

### Chỉ nhận tham số trong whitelist

Mỗi thao tác có một danh sách tham số được phép. **Có bất kỳ tham số nào khác thì từ chối** (`403 PARAM_NOT_ALLOWED`).

Đây là kiểm tra rủi ro chứ không phải kiểm tra định dạng: một tham số lạ có thể làm đổi ý nghĩa của request, ví dụ `batchOrders`, `vaultAddress` hay `builder`.

| Thao tác | Binance: tham số được phép | Hyperliquid: field được phép |
|---|---|---|
| Đặt lệnh | `symbol`, `side`, `type`, `quantity`, `price`, `timeInForce`, `reduceOnly`, `newClientOrderId`, `recvWindow`, `timestamp` | `action`: `type`, `orders`, `grouping`. Mỗi phần tử trong `orders`: `a`, `b`, `p`, `s`, `r`, `t`, `c` |
| Huỷ lệnh | `symbol`, `origClientOrderId`, `recvWindow`, `timestamp` | `action`: `type`, `cancels`. Mỗi phần tử: `asset`, `cloid` |
| Đặt lệnh bảo hiểm | `algoType`, `symbol`, `side`, `type`, `triggerPrice`, `closePosition`, `workingType`, `clientAlgoId`, `recvWindow`, `timestamp` | Giống "Đặt lệnh" |
| Huỷ lệnh bảo hiểm | `symbol`, `clientAlgoId`, `recvWindow`, `timestamp` | Giống "Huỷ lệnh" |
| Đổi đòn bẩy | `symbol`, `leverage`, `recvWindow`, `timestamp` | `action`: `type`, `asset`, `isCross`, `leverage` |

Với Hyperliquid, ngoài `action` thì `payload` chỉ được có thêm `nonce`. Hai field `vaultAddress` và `expiresAfter` không được phép.

### Quy tắc rủi ro

| Quy tắc | Binance | Hyperliquid | Lỗi |
|---|---|---|---|
| Symbol | `symbol = BTCUSDT` (nếu có tham số `symbol`) | Mọi `a` / `asset` bằng `0` (BTC perp) | `403 SYMBOL_NOT_ALLOWED` |
| Số lệnh | Một payload là một lệnh (Binance không có batch trong whitelist) | `place_order` và `place_conditional`: mảng `orders` có đúng 1 phần tử | `403 TOO_MANY_ORDERS` |
| Khối lượng | `0 < quantity < max_qty_btc` | `0 < s < max_qty_btc` | `403 QTY_EXCEEDS_LIMIT` |
| Đòn bẩy | `1 ≤ leverage ≤ max_leverage` | `1 ≤ leverage ≤ max_leverage`, và `isCross = true` | `403 LEVERAGE_EXCEEDS_LIMIT` |
| Lệnh bảo hiểm phải đóng vị thế | `closePosition = true`, `type` là `STOP_MARKET` hoặc `TAKE_PROFIT_MARKET` | Mọi lệnh có `r = true` và `t.trigger` | `403 MUST_CLOSE_POSITION` |
| Payload còn mới | `timestamp` lệch so với giờ Signer không quá `max_payload_age_ms`; `recvWindow ≤ 20000` | `nonce` lệch so với giờ Signer không quá `max_payload_age_ms` | `403 STALE_PAYLOAD` |

Giải thích hai quy tắc dễ bị hiểu nhầm:
- **Mỗi request chỉ một lệnh.** Nếu cho phép batch, BE có thể gửi 100 lệnh, mỗi lệnh nhỏ hơn hạn mức, và tổng cộng vượt hạn mức rất xa.
- **Payload phải còn mới.** Mục đích là chặn việc ký trước rồi giữ lại để dùng sau. Hyperliquid chấp nhận nonce cũ tới khoảng 2 ngày, nên nếu Signer không kiểm tra thì một chữ ký có thể bị giữ lại và dùng rất lâu sau đó.

Ngoài các quy tắc trên, Signer không kiểm tra gì thêm.
- `price`, `p`, `triggerPrice`, `tif`, `c`, `newClientOrderId`… đều do BE chịu trách nhiệm.
- Gợi ý cho BE: `client_order_id` và `client_algo_id` nên có dạng `0x` + 32 ký tự hex, vì định dạng này hợp lệ cho cả `cloid` của Hyperliquid lẫn `newClientOrderId` của Binance.

### Giới hạn

| Tên | Ý nghĩa | Giá trị |
|---|---|---|
| `max_qty_btc` | Khối lượng tối đa của một lệnh (so sánh `<`) | Chưa chốt |
| `max_leverage` | Đòn bẩy tối đa | Chưa chốt |
| `max_payload_age_ms` | Độ lệch tối đa của `timestamp` / `nonce` so với giờ Signer | `60000` (1 phút) |
| `recvWindow` tối đa của Binance | Cố định trong code (`MAX_RECV_WINDOW_MS`), không có trong config | `20000` (20 giây) |

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

---

## Lỗi

Khi lỗi, Signer **không bao giờ trả về chữ ký**.

```json
{ "error": "QTY_EXCEEDS_LIMIT", "message": "quantity 0.02 must be < 0.01" }
```

| HTTP | `error` | Khi nào |
|---|---|---|
| 400 | `INVALID_REQUEST` | Body không phải JSON, hoặc `payload` không đọc được |
| 401 | `UNAUTHENTICATED` | `key_id` không tồn tại, IP không nằm trong allowlist của key, hoặc HMAC sai / thiếu / quá cũ |
| 403 | `OP_NOT_ALLOWED` | `op` không nằm trong 5 thao tác được phép |
| 403 | `OP_MISMATCH` | Payload không khớp với `op` đã khai báo |
| 403 | `PARAM_NOT_ALLOWED` | Có tham số nằm ngoài whitelist |
| 403 | `SYMBOL_NOT_ALLOWED` | Không phải BTC |
| 403 | `TOO_MANY_ORDERS` | Mảng `orders` của Hyperliquid không có đúng 1 lệnh |
| 403 | `QTY_EXCEEDS_LIMIT` | Khối lượng `≤ 0`, `≥ max_qty_btc`, hoặc không đọc được thành số |
| 403 | `LEVERAGE_EXCEEDS_LIMIT` | Đòn bẩy ngoài khoảng `1…max_leverage`, hoặc không phải cross |
| 403 | `MUST_CLOSE_POSITION` | Lệnh bảo hiểm không phải lệnh đóng vị thế |
| 403 | `STALE_PAYLOAD` | `timestamp` / `nonce` quá cũ hoặc ở tương lai, hoặc `recvWindow` quá lớn |
| 503 | `KEY_NOT_LOADED` | Key tồn tại nhưng hiện chưa dùng được để ký (bản ghi lỗi, hoặc chưa có HMAC secret) |
| 500 | `INTERNAL` | Lỗi không lường trước |

Các lỗi `403` từ `OP_NOT_ALLOWED` trở xuống trong bảng là dấu hiệu BE bị lỗi hoặc bị chiếm quyền. Nên cảnh báo ngay khi gặp, đừng chỉ ghi log.

---

## Câu hỏi mở / cần kiểm tra trước khi code

- [ ] **Binance `algoOrder`:** đối chiếu lại tên tham số với docs hiện hành.
  - Các tên cần kiểm tra: `algoType`, `triggerPrice`, `clientAlgoId`, `closePosition`.
  - Kiểm tra thêm: lệnh huỷ algo có nhận tham số `symbol` không.
- [ ] **Hyperliquid `positionTpsl`:** thử trên testnet xem `s` có tự co giãn theo khối lượng vị thế không.
- [ ] **Giới hạn giá:** Signer không có giá thị trường nên không chặn được lệnh đặt giá vô lý.
  - Có thể thêm giới hạn `qty × price < max_notional_usd`.
  - Hoặc chấp nhận rằng `max_qty_btc` đã đủ để giới hạn thiệt hại.
