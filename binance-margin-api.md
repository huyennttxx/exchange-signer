# Giao dịch margin trên Binance: API, luật của bot và những gì đã đo

Tài liệu này gom mọi việc cần biết để giao dịch trên tài khoản margin của Binance: mở tài khoản, quyền của key, cross
hay isolated, chuyển tiền thế chấp, đòn bẩy, đặt và hủy lệnh, vay, trả nợ, lãi, margin level, luồng dữ liệu, điểm hạn
mức và mã lỗi. Mỗi mục ghi rõ việc nào bot làm, việc nào người vận hành làm tay, theo luật Đợt 317 (`DECISIONS.md`,
mục "Chân margin Binance").

Nguồn:
- tài liệu Binance Margin Trading, đọc ngày 16/11/2026, đường dẫn ở cuối tài liệu;
- lần đọc thật trên tài khoản SUB1_KE ngày 16/11/2026 (Đợt 318).

Chỗ nào tài liệu Binance tự mâu thuẫn hoặc chưa đo được, tài liệu này ghi **chưa kiểm chứng**.

---

## Bot dùng tài khoản margin để làm gì

- **Chân Binance là BTC mua trên tài khoản margin**, chân Hyperliquid là short như bot spot.
  - Bot mua BTC và vay USDC hoặc USDT ngay trong lệnh mua (`MARGIN_BUY`).
  - Khi bán, tiền bán tự trả nợ (`AUTO_REPAY`).
  - So với bot spot vay thế chấp (Flexible Loan), lãi margin USDC rẻ hơn khoảng 0,8 điểm/năm từ 02/2024 (Đợt 317).
- **Mỗi bot chọn một kiểu tài khoản và một cặp**, không đổi giữa chừng.
  - Cross hoặc isolated.
  - BTCUSDC (vay USDC) hoặc BTCUSDT (vay USDT). Hết tiền thì không chuyển sang cặp kia.
- **Chân Binance không có lệnh thoát rủi ro**, như bot spot (Đợt 248).
  - Margin level chạm 1,5: nhắn tin. Chạm 1,3: gọi điện.

### Ai làm việc gì

| Việc | Ai làm |
|---|---|
| Mở tài khoản margin cross, làm bài kiểm tra margin | Người vận hành, trên web hoặc ứng dụng Binance |
| Bật quyền của key, bật cặp isolated | Người vận hành |
| Chuyển tiền thế chấp vào hoặc ra tài khoản margin | Người vận hành |
| Đổi đòn bẩy cross | Người vận hành |
| Đặt, hủy, đọc lệnh margin | Bot |
| Vay và trả nợ | Bot, chỉ qua `sideEffectType` của lệnh đặt. Không gọi lệnh vay hay trả nợ riêng |
| Đọc số dư, nợ, lãi, margin level | Bot |

---

## Chuẩn bị tài khoản trước khi giao dịch

### Mở tài khoản margin cross

- **Binance không có API để mở tài khoản margin cross.**
  - Lần đầu, chủ tài khoản phải làm bài kiểm tra margin và đồng ý điều khoản trên web hoặc ứng dụng, rồi chuyển tiền vào.
- **Biết tài khoản đã mở chưa bằng trường `created`** của `GET /sapi/v1/margin/account`.
  - `created` là true khi tài khoản margin đã được tạo.
  - Không dựa vào mã HTTP: SUB1_KE chưa mở margin mà lệnh đọc này vẫn trả 200, số dư 0, `marginLevel` 999,
    `tradeEnabled` và `borrowEnabled` false.
  - `GET /sapi/v1/margin/maxBorrowable` thì trả `-3003 Margin account does not exist`.

### Quyền của API key

| Quyền trên Binance | Trường trong `GET /sapi/v1/account/apiRestrictions` | Cần cho |
|---|---|---|
| Spot & Margin Trading | `enableSpotAndMarginTrading` | Đặt, hủy lệnh margin |
| Margin Loan, Repay & Transfer | `enableMargin` | Vay, trả nợ, chuyển tiền margin |
| Permits Universal Transfer | `permitsUniversalTransfer` | `POST /sapi/v1/asset/transfer` |

- Theo trang hướng dẫn của Binance, key cần cả hai quyền đầu, nếu không các lệnh margin bị từ chối (`-2015`).
- **Chưa kiểm chứng:** lệnh `MARGIN_BUY` có cần quyền "Margin Loan, Repay & Transfer" không. Trang mô tả lệnh không nói.
- SUB1_KE ngày 16/11/2026: có `enableSpotAndMarginTrading`, chưa có `enableMargin`, không có quyền rút, có giới hạn IP.
  Lần thử lệnh đầu tiên sẽ trả lời câu hỏi trên.
- Bot không cần "Permits Universal Transfer", vì người vận hành tự chuyển tiền.

### Cross hay isolated

| Kiểu | Ý nghĩa |
|---|---|
| Cross | Một tài khoản chung cho mọi tài sản. Mọi tài sản trong đó đều là thế chấp, một margin level cho cả tài khoản |
| Isolated | Mỗi cặp một tài khoản riêng. Chỉ tiền của cặp đó bị đe dọa khi thanh lý, margin level và ngưỡng theo từng cặp |

- **Bật một cặp isolated:** `POST /sapi/v1/margin/isolated/account?symbol=BTCUSDC`, 300 điểm UID.
  - Theo tài liệu, API này chỉ bật lại cặp đã từng tắt. Lần đầu, người vận hành bật trên web.
- **Tắt:** `DELETE` cùng đường dẫn, 300 điểm UID. Mỗi cặp chỉ tắt được một lần trong 24 giờ.
- **Số cặp được bật:** `GET /sapi/v1/margin/isolated/accountLimit` trả `enabledAccount` và `maxAccount`. Mức tối đa
  theo hạng VIP.
- SUB1_KE ngày 16/11/2026: BTCUSDC và BTCUSDT đều được liệt kê với `enabled` false.

### Chuyển tiền thế chấp

- **Người vận hành tự chuyển.** Bot không gọi lệnh chuyển tiền (Đợt 317).
- **API hiện hành:** `POST /sapi/v1/asset/transfer`, 300 điểm UID, key cần "Permits Universal Transfer".
  - `type`: `MAIN_MARGIN` (spot sang cross), `MARGIN_MAIN` (cross sang spot), `MARGIN_ISOLATEDMARGIN`,
    `ISOLATEDMARGIN_MARGIN`, `ISOLATEDMARGIN_ISOLATEDMARGIN`.
  - `toSymbol` bắt buộc khi chuyển vào isolated; `fromSymbol` bắt buộc khi chuyển ra từ isolated.
  - Các loại chuyển trong tài liệu không có đường thẳng từ spot sang isolated: đi qua cross trước. **Chưa kiểm chứng**;
    trên web có thể chuyển thẳng.
- **Đã bỏ:** `POST /sapi/v1/margin/transfer` và `POST /sapi/v1/margin/isolated/transfer` ngừng từ 31/03/2024.
- **Đọc được:** lịch sử chuyển `GET /sapi/v1/margin/transfer` (30 ngày mỗi lần đọc), số được chuyển ra
  `GET /sapi/v1/margin/maxTransferable` (50 điểm IP).
- Với cross, margin level dưới 2 thì không chuyển tiền ra được.

### Đòn bẩy

- **Cross:** `POST /sapi/v1/margin/max-leverage` với `maxLeverage`.
  - Cross Margin Classic chỉ nhận 3 hoặc 5. Giá trị 10 thuộc Cross Margin Pro, mà Binance đã dừng Pro.
  - Tốn 3000 điểm, mỗi IP một lần mỗi phút.
  - Chỉ đổi được khi margin level đang trên mức vay của đòn bẩy mới: 1,5 với 3x, 1,25 với 5x.
  - Kiểu tài khoản đọc ở `accountType` của `/sapi/v1/margin/account`: `MARGIN_1` là Classic, `MARGIN_2` là Pro.
- **Isolated:** không có lệnh đặt đòn bẩy. Đòn bẩy và ngưỡng thanh lý theo bậc của từng cặp.
  - `GET /sapi/v1/margin/isolatedMarginTier?symbol=BTCUSDC` (1 điểm IP) trả theo bậc: `effectiveMultiple`,
    `initialRiskRatio`, `liquidationRiskRatio`, `baseAssetMaxBorrowable`, `quoteAssetMaxBorrowable`.
- **Người vận hành đổi đòn bẩy.** Bot chỉ đọc. Đòn bẩy quyết định vay được bao nhiêu và ngưỡng gọi ký quỹ của Binance
  (mục "Margin level").

---

## Đặt, hủy và đọc lệnh

### Đặt lệnh: `POST /sapi/v1/margin/order`

| Tham số | Bot gửi | Ghi chú |
|---|---|---|
| `symbol` | `BTCUSDC` hoặc `BTCUSDT` | Một cặp mỗi bot |
| `isIsolated` | `TRUE` hoặc `FALSE` | Mặc định của Binance là `FALSE` |
| `side` | `BUY` hoặc `SELL` | |
| `type` | `LIMIT_MAKER` (lệnh chờ) hoặc `LIMIT` | Binance còn nhận `MARKET`, `STOP_LOSS`, `STOP_LOSS_LIMIT`, `TAKE_PROFIT`, `TAKE_PROFIT_LIMIT`; bot không dùng |
| `quantity`, `price` | Theo bước giá và bước lượng của cặp | |
| `timeInForce` | Chỉ với `LIMIT`: `GTC` hoặc `IOC` | `LIMIT_MAKER` không gửi |
| `newClientOrderId` | Mã lệnh của bot | |
| `sideEffectType` | Mua `MARGIN_BUY`, bán `AUTO_REPAY` | Mục dưới |
| `autoRepayAtCancel` | Luôn `FALSE` | Mục "Hủy lệnh" |
| `recvWindow`, `timestamp` | Người ký thêm vào | `recvWindow` tối đa 60000 |

- **Bot không gửi** `quoteOrderQty`, `stopPrice`, `icebergQty`, `trailingDelta`, `selfTradePreventionMode`,
  `newOrderRespType`.
  - Không gửi `newOrderRespType` thì `LIMIT` trả đủ (`FULL`), còn `LIMIT_MAKER` chỉ trả xác nhận (`ACK`).
- **Giá lệnh giới hạn phải cách giá chỉ số không quá 15%**, nếu không Binance trả `-3064`.
- Tham số do `app/venues/binance_margin/orders.py` dựng. Mac mini sẽ kiểm tra cùng luật đó
  (`signer-margin-request.md`).

### `sideEffectType`: lệnh tự vay hay tự trả

| Giá trị | Ý nghĩa | Bot dùng |
|---|---|---|
| `NO_SIDE_EFFECT` | Không vay, không trả. Mặc định khi không gửi | Không |
| `MARGIN_BUY` | Vay phần còn thiếu để lệnh đủ tiền | Lệnh mua |
| `AUTO_REPAY` | Dùng tiền thu về để trả nợ, chỉ khi tiền đó cùng loại với khoản đã vay | Lệnh bán |
| `AUTO_BORROW_REPAY` | Vay rồi tự trả | Không bao giờ |

- **Lệnh bán không bao giờ mang `MARGIN_BUY` hay `AUTO_BORROW_REPAY`.** Hai giá trị đó vay BTC để bán, tức là bán khống
  trên Binance, ngược chiều chiến lược.
- **Lệnh `MARGIN_BUY` tốn 1500 điểm UID**, lệnh thường 6 điểm UID. Mức 1500 áp dụng từ 07/05/2026.
  - Hạn mức là 180.000 điểm UID mỗi phút, tức khoảng 120 lệnh `MARGIN_BUY` mỗi phút.
  - Chưa đo được trên SUB1_KE.
- Câu trả lời có `marginBuyBorrowAmount` và `marginBuyBorrowAsset` khi lệnh có vay.
- **Chưa kiểm chứng: Binance vay lúc đặt lệnh hay lúc lệnh khớp.**
  - Trang hướng dẫn nói chỉ vay khi lệnh khớp.
  - Nhưng `autoRepayAtCancel` sinh ra để trả "khoản nợ do lệnh tạo ra" khi hủy, tức là lệnh chưa khớp vẫn có thể có nợ.
  - Bước (a) của lần thử trên SUB1_KE đo đúng điều này: đặt lệnh mua xa giá, đọc `borrowed` trước khi hủy và sau khi
    hủy.

### Hủy lệnh và `autoRepayAtCancel`

- **Hủy một lệnh:** `DELETE /sapi/v1/margin/order` với `symbol`, `isIsolated`, `origClientOrderId`. Tốn 10 điểm IP.
- **Hủy mọi lệnh của một cặp:** `DELETE /sapi/v1/margin/openOrders`. Bot không dùng.
- **`autoRepayAtCancel` gửi lúc đặt lệnh, không phải lúc hủy.**
  - Mặc định của Binance là true: hủy lệnh `MARGIN_BUY` thì tự trả khoản lệnh đó đã vay.
  - Bot luôn gửi `FALSE` (Đợt 317). Binance cũng khuyên đặt `FALSE` khi đặt và hủy lệnh nhiều, để không vay rồi trả liên
    tục.
  - Hệ quả: khoản vay còn lại sau khi hủy, vẫn tính lãi, cho tới khi một lệnh bán `AUTO_REPAY` trả nó hoặc người vận
    hành trả tay.

### Đọc lệnh và giao dịch đã khớp

| Lệnh đọc | Điểm | Ghi chú |
|---|---|---|
| `GET /sapi/v1/margin/order` | 10 IP | Một lệnh, theo `origClientOrderId` |
| `GET /sapi/v1/margin/openOrders` | 10 IP | Isolated phải có `symbol` |
| `GET /sapi/v1/margin/allOrders` | 200 IP | Khoảng thời gian dưới 24 giờ |
| `GET /sapi/v1/margin/myTrades` | 10 IP | Khoảng thời gian dưới 24 giờ |
| `GET /sapi/v1/margin/forceLiquidationRec` | 1 IP | Lịch sử bị thanh lý |

- Lệnh OCO, OTO, OTOCO của margin cũng có (`/sapi/v1/margin/order/oco`, `/oto`, `/otoco`), cùng mức 6 hoặc 1500 điểm.
  Bot không dùng.

---

## Nợ, lãi và margin level

### Vay và trả nợ

- **Bot chỉ vay và trả qua lệnh đặt** (`MARGIN_BUY`, `AUTO_REPAY`).
- **Lệnh vay và trả riêng** là `POST /sapi/v1/margin/borrow-repay` với `asset`, `isIsolated`, `symbol` (isolated),
  `amount`, `type` (`BORROW` hoặc `REPAY`). Tốn 1500 điểm UID.
  - Bot không gọi lệnh này. Người vận hành trả tay trên web khi cần, ví dụ khoản còn lại sau một lệnh mua bị hủy.
- **Số vay được tối đa:** `GET /sapi/v1/margin/maxBorrowable` trả `amount` và `borrowLimit`.
  - **Tốn 750 điểm UID mỗi lần.** Binance đổi từ 50 điểm IP ngày 16/04/2026, nhưng trang mô tả lệnh vẫn ghi 50.
  - Đo trên SUB1_KE: header `x-sapi-used-uid-weight-1m` lên 750 rồi 1500 sau hai lần gọi.
  - Vì vậy bot không đọc số này trước mỗi lần gom.

### Lãi

- **Lãi tính từ lúc vay.**
  - Lần đầu tính theo phần giờ còn lại: gốc × lãi giờ × (số giây tới đầu giờ tiếp theo ÷ 3600).
  - Sau đó tính mỗi đầu giờ.
- **Lịch sử lãi:** `GET /sapi/v1/margin/interestHistory` (1 điểm IP).
  - Chỉ có 90 ngày gần nhất. Mỗi lần đọc tối đa 30 ngày; không ghi thời gian thì lấy 7 ngày.
  - `type`: `ON_BORROW` (lần tính đầu lúc vay), `PERIODIC` (mỗi giờ), `PERIODIC_CONVERTED`, `ON_BORROW_CONVERTED`,
    `PORTFOLIO`.
  - Mỗi dòng có `interestAccuredTime` (Binance viết sai chính tả như vậy), `asset`, `principal`, `interest`,
    `interestRate`, `isolatedSymbol`.
- **Lãi suất:**
  - Lịch sử lãi theo ngày: `GET /sapi/v1/margin/interestRateHistory` (1 điểm IP, tối đa một tháng mỗi lần).
  - Lãi giờ tới: `GET /sapi/v1/margin/next-hourly-interest-rate` (100 điểm IP).
- Đợt 317 đã so lãi margin với lãi vay thế chấp từ 02/2024: số liệu ở changelog Đợt 317.

### Margin level, ngưỡng và thanh lý

- **Margin level = tổng giá trị thế chấp ÷ (tổng nợ + lãi chưa trả).**
  - Không có nợ thì Binance trả 999.
  - Isolated tính cùng cách, chỉ trên tài sản của cặp đó.
- **Ngưỡng của Binance cho cross Classic:**

| Đòn bẩy | Được vay thêm khi | Gọi ký quỹ | Thanh lý |
|---|---|---|---|
| 3x | margin level > 1,5 | 1,1 < margin level ≤ 1,3 | margin level ≤ 1,1 |
| 5x | margin level > 1,25 | 1,1 < margin level ≤ 1,16 | margin level ≤ 1,1 |

- `GET /sapi/v1/margin/tradeCoeff` (10 điểm IP) trả ba mức của tài khoản: `normalBar`, `marginCallBar`,
  `forceLiquidationBar`. Ví dụ trong tài liệu: 1,5, 1,3 và 1,1.
- **Isolated:** ngưỡng theo bậc của cặp, nằm giữa `liquidationRiskRatio` và mức gọi ký quỹ. Bot đọc thêm
  `marginLevelStatus` (`EXCESSIVE`, `NORMAL`, `MARGIN_CALL`, `PRE_LIQUIDATION`, `FORCE_LIQUIDATION`) và
  `liquidatePrice`.
- **Mức nhắn 1,5 và gọi 1,3 của bot (Đợt 317)** trùng `normalBar` và `marginCallBar` trong ví dụ của Binance.
  - Ở 3x, lúc bot gọi điện là lúc Binance bắt đầu gọi ký quỹ, còn cách mức thanh lý 1,1.
  - Ở 5x, Binance gọi ký quỹ muộn hơn (1,16), nên 1,3 của bot đến trước.
- **Khi thanh lý:**
  - Cross: Binance tự bán tài sản thế chấp để trả nợ.
  - Isolated: chỉ tiền của cặp đó bị bán.
  - Sau đó, `GET /sapi/v1/margin/forceLiquidationRec` liệt kê các lệnh thanh lý.
- **Bot không có lệnh thoát trên chân Binance.** Thanh lý chân Binance là điều người vận hành phải ngăn bằng cách thêm
  thế chấp hoặc giảm vị thế sau khi nhận tin nhắn hoặc cuộc gọi.

---

## Theo dõi tài khoản: lệnh đọc, luồng dữ liệu, điểm hạn mức, mã lỗi

### Đọc tài khoản

| Lệnh đọc | Điểm | Bot lấy gì |
|---|---|---|
| `GET /sapi/v1/margin/account` | 10 IP (đo được) | `created`, `marginLevel`, `tradeEnabled`, `borrowEnabled`, `accountType`; mỗi tài sản `free`, `locked`, `borrowed`, `interest`, `netAsset` |
| `GET /sapi/v1/margin/isolated/account?symbols=BTCUSDC` | 10 IP (đo được) | Theo cặp: `enabled`, `marginLevel`, `marginLevelStatus`, `liquidatePrice`, `tradeEnabled`; tài sản gốc và tài sản định giá |
| `GET /sapi/v1/margin/tradeCoeff` | 10 IP | Ba ngưỡng của tài khoản |
| `GET /sapi/v1/account/apiRestrictions` | 1 IP (đo được) | Quyền của key |

- Bộ đọc nằm ở `app/venues/binance_margin/parsers.py`.
- Nợ phải trả của một tài sản = `borrowed` + `interest`.

### Luồng dữ liệu lệnh và tài khoản

- **Cách cũ đã ngừng:** `/sapi/v1/userDataStream` ngừng từ 20/02/2026.
- **Cách hiện hành:**
  - Bot lấy token bằng `POST /sapi/v1/userListenToken` (1 điểm UID; `isIsolated`, `symbol`, `validity` tối đa 24 giờ).
  - Sau đó bot đăng ký `userDataStream.subscribe.listenToken` trên `wss://ws-api.binance.com:443/ws-api/v3`.
  - Hết hạn mà không gia hạn thì Binance gửi `eventStreamTerminated`.
- **Từ 14/10/2026, tài khoản mới hoặc đã chuyển đổi nhận `-3086`** khi lấy token theo cách trên.
  - Các tài khoản này phải dùng `POST`, `PUT`, `DELETE /sapi/v2/margin/listen-key`.
  - Changelog của Binance ghi địa chỉ luồng là `wss://fstream.binance.com/private/ws/<listenKey>`. **Chưa kiểm chứng.**
  - Luồng này có thêm sự kiện `marginLevelChange` và `liabilityChange` (`BORROW`, `REPAY`, `INTEREST`, `DEBT_CHANGE`).
  - Phải thử trên SUB1_KE trước khi viết adapter: tài khoản này nhận cách nào.
- **Sự kiện:** `executionReport` (lệnh và lần khớp), `outboundAccountPosition`, `balanceUpdate`, `listStatus`.
- **Luồng rủi ro riêng:** `wss://margin-stream.binance.com`, chỉ cho cross, có `MARGIN_LEVEL_STATUS_CHANGE` và
  `USER_LIABILITY_CHANGE`.

### Điểm hạn mức

- **Mỗi endpoint có hai hạn mức riêng:** 12.000 điểm IP mỗi phút và 180.000 điểm UID mỗi phút.
  - Header trả về: `x-sapi-used-ip-weight-1m` và `x-sapi-used-uid-weight-1m`.
  - Máy chủ dùng chung IP với các bot đang chạy, nên mọi lệnh đọc thêm phải giãn ra.

| Lệnh | Điểm |
|---|---|
| Đặt lệnh `MARGIN_BUY` hoặc `AUTO_BORROW_REPAY` | 1500 UID |
| Đặt lệnh khác (kể cả `AUTO_REPAY`) | 6 UID |
| Hủy một lệnh | 10 IP |
| `maxBorrowable` | 750 UID (đo được) |
| Vay hoặc trả nợ riêng | 1500 UID |
| Chuyển tiền `asset/transfer` | 300 UID |
| Đổi đòn bẩy cross | 3000, một lần mỗi phút |
| Bật hoặc tắt cặp isolated | 300 UID |

- Giới hạn số lệnh theo tài khoản; vượt thì Binance trả 429.
  - Xem mức đang dùng: `GET /sapi/v1/margin/rateLimit/order` (20 điểm IP).
  - Ví dụ trong tài liệu là 10.000 lệnh mỗi 10 giây và 20.000 lệnh mỗi ngày. **Chưa kiểm chứng** đó là mức thật.

### Mã lỗi hay gặp

| Mã | Ý nghĩa |
|---|---|
| `-3003` | Tài khoản margin chưa mở (đã gặp trên SUB1_KE) |
| `-3006` | Vay vượt mức tối đa |
| `-3007` | Đang có giao dịch chờ, thử lại sau |
| `-3008` | Không được vay |
| `-3013` | Vay dưới mức tối thiểu |
| `-3015` | Trả nhiều hơn số nợ |
| `-3020` | Chuyển ra vượt mức cho phép |
| `-3023` | Margin level hiện tại không cho chuyển ra hay đặt lệnh |
| `-3041` | Không đủ số dư |
| `-3045` | Binance không còn đủ tài sản để cho vay |
| `-3064` | Giá lệnh cách giá chỉ số quá 15% |
| `-3086` | Tài khoản không dùng được cách lấy token luồng dữ liệu này |
| `-2010` | Lệnh bị từ chối (ví dụ `LIMIT_MAKER` sẽ khớp ngay) |
| `-2015` | Key không có quyền hoặc IP không được phép |
| `-1015` | Quá nhiều lệnh |

---

## Những gì còn phải đo trên SUB1_KE

- **Người vận hành chuẩn bị:** mở tài khoản margin, chuyển 30–50 USDC vào, chọn cross hay isolated và cặp.
- **Các câu hỏi lần thử phải trả lời** (script `backend/scripts/operator_margin_test_order.py`):
  - lệnh `MARGIN_BUY` có cần quyền "Margin Loan, Repay & Transfer" không;
  - Binance vay lúc đặt lệnh hay lúc khớp, và khoản vay còn hay mất sau khi hủy với `autoRepayAtCancel=FALSE`;
  - lệnh `MARGIN_BUY` tốn đúng 1500 điểm UID không;
  - lệnh bán `AUTO_REPAY` trả hết nợ không, và lãi lần đầu (`ON_BORROW`) bao nhiêu;
  - SUB1_KE lấy luồng dữ liệu bằng `userListenToken` được không, hay nhận `-3086`.
- Mỗi lệnh thật đều hỏi người vận hành trước.

---

## Nguồn

Đọc ngày 16/11/2026:
- Lệnh đặt, hủy, đọc lệnh: https://developers.binance.com/en/docs/catalog/core-trading-margin-trading/api/rest-api/trade
- Tài khoản, đòn bẩy, ngưỡng: https://developers.binance.com/en/docs/catalog/core-trading-margin-trading/api/rest-api/account
- Vay, trả nợ, lãi: https://developers.binance.com/en/docs/catalog/core-trading-margin-trading/api/rest-api/borrow-repay
- Bậc isolated: https://developers.binance.com/en/docs/catalog/core-trading-margin-trading/api/rest-api/market-data
- Chuyển tiền: https://developers.binance.com/legacy-docs/wallet/asset/user-universal-transfer
- Luồng dữ liệu: https://developers.binance.com/en/docs/products/margin-trading/listen-token-data-stream,
  https://developers.binance.com/en/docs/products/margin-trading/risk-data-stream
- Thay đổi theo ngày: https://developers.binance.com/docs/margin_trading/change-log
- Hướng dẫn, mã lỗi, hạn mức: https://developers.binance.com/en/docs/products/margin-trading/best-practice,
  https://developers.binance.com/en/docs/products/margin-trading/error-code,
  https://developers.binance.com/en/docs/products/margin-trading/general-info
- Margin level và ngưỡng: https://www.binance.com/en/support/faq/binance-margin-level-and-risk-control-360030493931
- Cross margin: https://www.binance.com/en/support/faq/binance-cross-margin-trading-guide-360041505471
