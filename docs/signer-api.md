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

BE gửi `POST /exchange` với body `{ "action", "nonce", "signature" }`, kèm `"vaultAddress"` y hệt nếu payload gửi sang Signer có field này. Chữ ký gắn với `vaultAddress`, nên thiếu hoặc khác thì chữ ký sai.
- `action` phải **giữ nguyên thứ tự key** như lúc gửi sang Signer.
- Lý do: chữ ký được tính trên `action` encode bằng msgpack, theo đúng thứ tự key. Đổi thứ tự thì chữ ký sai.

---

## Thao tác được phép

- `op` khác 8 giá trị dưới đây: `403 OP_NOT_ALLOWED`.
- Payload không khớp với `op` đã khai báo: `403 OP_MISMATCH`. Ví dụ khai `op = set_leverage` nhưng payload lại là lệnh đặt.

| `op` | Endpoint của BE | Binance: payload phải có | Hyperliquid: payload phải có |
|---|---|---|---|
| `place_order` | `POST /v1/orders` | `type`, không có `algoType` (→ `POST /fapi/v1/order`) | `action.type = "order"`, `grouping = "na"` |
| `cancel_order` | `DELETE /v1/orders/{client_order_id}` | `origClientOrderId` (→ `DELETE /fapi/v1/order`) | `action.type = "cancelByCloid"` |
| `place_conditional` | `POST /v1/conditional` | `algoType` (→ `POST /fapi/v1/algoOrder`) | `action.type = "order"`, `grouping = "positionTpsl"` |
| `cancel_conditional` | `DELETE /v1/conditional/{client_algo_id}` | `clientAlgoId`, không có `algoType` (→ `DELETE /fapi/v1/algoOrder`) | `action.type = "cancelByCloid"` |
| `set_leverage` | `POST /v1/leverage` | `leverage` (→ `POST /fapi/v1/leverage`) | `action.type = "updateLeverage"` |
| `sub_account_transfer` | — | Không hỗ trợ (luôn `403 OP_MISMATCH`) | `action.type = "subAccountTransfer"` (USDC perp) hoặc `"subAccountSpotTransfer"` (token spot) |
| `earn_transfer` | — | Không hỗ trợ (luôn `403 OP_MISMATCH`) | `action.type = "borrowLend"`, `operation` là `"supply"` hoặc `"withdraw"` |
| `create_sub_account` | — | Không hỗ trợ (luôn `403 OP_MISMATCH`) | `action.type = "createSubAccount"` |

Không có trong danh sách, và **không bao giờ được ký**:
- rút tiền, chuyển tiền ra ngoài hoặc giữa perp ↔ spot (`withdraw3`, `usdSend`, `spotSend`, `sendAsset`, `usdClassTransfer`, `vaultTransfer`). Chỉ chuyển giữa master và sub-account (`sub_account_transfer`) được ký;
- sửa sub-account (`subAccountModify`). Chỉ tạo sub-account mới (`create_sub_account`) được ký;
- vay và trả nợ trong Earn (`borrowLend` với `operation` = `borrow` / `repay`). Chỉ gửi / rút (`supply` / `withdraw`) được ký;
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
| Đặt lệnh | `symbol`, `side`, `type`, `quantity`, `price`, `timeInForce`, `reduceOnly`, `newClientOrderId`, `newOrderRespType` (chỉ `RESULT`), `recvWindow`, `timestamp` | `action`: `type`, `orders`, `grouping`. Mỗi phần tử trong `orders`: `a`, `b`, `p`, `s`, `r`, `t`, `c` |
| Huỷ lệnh | `symbol`, `origClientOrderId`, `recvWindow`, `timestamp` | `action`: `type`, `cancels`. Mỗi phần tử: `asset`, `cloid` |
| Đặt lệnh bảo hiểm | `algoType`, `symbol`, `side`, `type`, `triggerPrice`, `closePosition`, `workingType`, `priceProtect` (chỉ `false`), `clientAlgoId`, `recvWindow`, `timestamp` | Giống "Đặt lệnh" |
| Huỷ lệnh bảo hiểm | `symbol`, `clientAlgoId`, `recvWindow`, `timestamp` | Giống "Huỷ lệnh" |
| Đổi đòn bẩy | `symbol`, `leverage`, `recvWindow`, `timestamp` | `action`: `type`, `asset`, `isCross`, `leverage` |
| Chuyển master ↔ sub-account | — | `subAccountTransfer`: `type`, `subAccountUser`, `isDeposit`, `usd`. `subAccountSpotTransfer`: `type`, `subAccountUser`, `isDeposit`, `token`, `amount` |
| Gửi / rút Earn | — | `action`: `type`, `operation`, `token`, `amount` |
| Tạo sub-account | — | `action`: `type`, `name` |

Với Hyperliquid, ngoài `action` thì `payload` chỉ được có thêm:
- `nonce`
- `vaultAddress` (tuỳ chọn): địa chỉ sub-account hoặc vault mà lệnh được thực hiện trên đó. Phải là `null` hoặc chuỗi `0x` + 40 ký tự hex; sai định dạng thì trả `400 INVALID_REQUEST`. Không có hoặc `null` thì lệnh áp dụng cho chính tài khoản của agent key. Với `sub_account_transfer` và `create_sub_account`, `vaultAddress` phải không có hoặc `null` (sai thì `403 PARAM_NOT_ALLOWED`): sub-account nằm trong `action.subAccountUser`.

Field `expiresAfter` không được phép.

### Quy tắc rủi ro

| Quy tắc | Binance | Hyperliquid | Lỗi |
|---|---|---|---|
| Symbol | `symbol` là một symbol trong section của key (nếu có tham số `symbol`). `place_order` bắt buộc có `symbol` | Mọi `a` / `asset` là chỉ số asset của một symbol trong section của key (tra `<COIN>USDC` trong `signer/hyperliquid_assets.ini`) | `403 SYMBOL_NOT_ALLOWED` |
| Số lệnh | Một payload là một lệnh (Binance không có batch trong whitelist) | `place_order` và `place_conditional`: mảng `orders` có đúng 1 phần tử | `403 TOO_MANY_ORDERS` |
| Khối lượng | `0 < quantity < max_qty` của symbol đó. Lệnh `reduceOnly=true`: chỉ cần `quantity > 0` | `0 < s < max_qty` của symbol đó. Lệnh `r = true`: chỉ cần `s > 0`. `place_conditional` (`positionTpsl`): nhận thêm `s = 0` (đóng cả vị thế) | `403 QTY_EXCEEDS_LIMIT` |
| Đòn bẩy | `1 ≤ leverage ≤ max_leverage` của key | `1 ≤ leverage ≤ max_leverage` của key, và `isCross = true` | `403 LEVERAGE_EXCEEDS_LIMIT` |
| Lệnh bảo hiểm phải đóng vị thế | `closePosition = true`, `type` là `STOP_MARKET` hoặc `TAKE_PROFIT_MARKET` | Mọi lệnh có `r = true` và `t.trigger` | `403 MUST_CLOSE_POSITION` |
| Chuyển master ↔ sub-account | — | `subAccountUser` là `0x` + 40 hex, `isDeposit` là `true` / `false`, `token` dạng `<tên>:0x` + 32 hex (sai thì `400 INVALID_REQUEST`). `usd` là số nguyên `> 0` (đơn vị 1e-6 USDC: `1000000` = 1 USDC); `amount` là chuỗi số thập phân `> 0` | `403 QTY_EXCEEDS_LIMIT` |
| Gửi / rút Earn | — | `operation` là `supply` hoặc `withdraw` (khác thì `403 PARAM_NOT_ALLOWED`). `token` là chỉ số token, số nguyên `≥ 0`, mọi token (`0` = USDC; sai thì `400 INVALID_REQUEST`). `amount` là chuỗi số thập phân `> 0`, hoặc `null` = toàn bộ | `403 QTY_EXCEEDS_LIMIT` |
| Tạo sub-account | — | `name` là chuỗi 1–64 ký tự in được, không có khoảng trắng ở đầu / cuối | `400 INVALID_REQUEST` |
| Payload còn mới | `timestamp` lệch so với giờ Signer không quá `max_payload_age_ms`; `recvWindow ≤ 20000` | `nonce` lệch so với giờ Signer không quá `max_payload_age_ms` | `403 STALE_PAYLOAD` |

Giải thích các quy tắc dễ bị hiểu nhầm:
- **Chuyển master ↔ sub-account không có hạn mức và không phụ thuộc symbol của key.** Sàn chỉ nhận `subAccountUser` là sub-account của chính master đã ký, nên tiền không ra khỏi các tài khoản của mình. `isDeposit = true`: master → sub; `false`: sub → master.
- **Gửi / rút Earn không có hạn mức, không phụ thuộc symbol của key, nhận mọi token.** Tiền chỉ chuyển giữa số dư spot và Earn của cùng tài khoản. `vaultAddress` dùng được như với lệnh đặt (gửi / rút trên sub-account). Lệnh rút có thể bị sàn từ chối khi pool đang được vay 100%.
- **Tạo sub-account không có hạn mức và không phụ thuộc symbol của key.** Sub-account mới thuộc master đã ký, chưa có tiền; không có tiền nào được chuyển. Sàn tự giới hạn số sub-account mỗi master.
- **Mỗi request chỉ một lệnh.** Nếu cho phép batch, BE có thể gửi 100 lệnh, mỗi lệnh nhỏ hơn hạn mức, và tổng cộng vượt hạn mức rất xa.
- **Payload phải còn mới.** Mục đích là chặn việc ký trước rồi giữ lại để dùng sau. Hyperliquid chấp nhận nonce cũ tới khoảng 2 ngày, nên nếu Signer không kiểm tra thì một chữ ký có thể bị giữ lại và dùng rất lâu sau đó.

Ngoài các quy tắc trên, Signer không kiểm tra gì thêm.
- `price`, `p`, `triggerPrice`, `tif`, `c`, `newClientOrderId`… đều do BE chịu trách nhiệm.
- Gợi ý cho BE: `client_order_id` và `client_algo_id` nên có dạng `0x` + 32 ký tự hex, vì định dạng này hợp lệ cho cả `cloid` của Hyperliquid lẫn `newClientOrderId` của Binance.

### Giới hạn

Giới hạn đi theo từng key, trong section `[key:<key_id>]` của `config.ini`. Key mới (`generate` / `import`) được `max_leverage = 40` và symbol `BTCUSDC` với `max_qty = 0.1` (cùng tên trên Binance lẫn Hyperliquid).

| Tên | Nằm ở | Ý nghĩa | Giá trị |
|---|---|---|---|
| `allowed_ips` | `[key:…]` | IP của BE được gọi bằng key này | Theo key |
| `<symbol> = <max_qty>` | `[key:…]` | Symbol được ký và khối lượng tối đa của một lệnh (so sánh `<`), không áp dụng cho lệnh reduce-only | Theo key |
| `max_leverage` | `[key:…]` | Đòn bẩy tối đa | Theo key |
| `max_payload_age_ms` | `[server]` | Độ lệch tối đa của `timestamp` / `nonce` so với giờ Signer | `60000` (1 phút) |
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
| 403 | `OP_NOT_ALLOWED` | `op` không nằm trong 8 thao tác được phép |
| 403 | `OP_MISMATCH` | Payload không khớp với `op` đã khai báo |
| 403 | `PARAM_NOT_ALLOWED` | Có tham số nằm ngoài whitelist, hoặc `newOrderRespType` / `priceProtect` có giá trị khác giá trị được phép |
| 403 | `SYMBOL_NOT_ALLOWED` | Symbol / asset không có trong section của key |
| 403 | `TOO_MANY_ORDERS` | Mảng `orders` của Hyperliquid không có đúng 1 lệnh |
| 403 | `QTY_EXCEEDS_LIMIT` | Khối lượng `≤ 0` (`< 0` với lệnh `positionTpsl`), `≥ max_qty` của symbol (trừ lệnh reduce-only), hoặc không đọc được thành số. Với `sub_account_transfer` / `earn_transfer`: `usd` / `amount` không hợp lệ |
| 403 | `LEVERAGE_EXCEEDS_LIMIT` | Đòn bẩy ngoài khoảng `1…max_leverage` của key, hoặc không phải cross |
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
- [ ] **Hyperliquid `subAccountTransfer` bằng agent key:** thử trên testnet xem sàn có nhận chữ ký của API wallet (agent) cho hai action chuyển sub-account không, hay bắt buộc ký bằng key của master.
- [ ] **Hyperliquid `borrowLend`:** Signer ký như L1 action (giống `sign_l1_action`, theo ví dụ của Chainstack). Docs chính thức và SDK chưa có action này, một số nguồn gọi là user-signed EIP-712: thử `supply` / `withdraw` trên testnet trước khi BE dùng, kể cả bằng agent key.
- [ ] **Hyperliquid `createSubAccount` bằng agent key và độ dài tên:** thử trên testnet xem sàn có nhận chữ ký của agent không (hay bắt buộc key master), và tên dài tối đa bao nhiêu ký tự (Signer đang cho tới 64).
- [ ] **Giới hạn giá:** Signer không có giá thị trường nên không chặn được lệnh đặt giá vô lý.
  - Có thể thêm giới hạn `qty × price < max_notional_usd`.
  - Hoặc chấp nhận rằng `max_qty` theo symbol đã đủ để giới hạn thiệt hại.
