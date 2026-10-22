# Signer — đề xuất triển khai

| Trường | Giá trị |
|---|---|
| **Phiên bản** | 0.2 — **đề xuất**, chưa chốt |
| **Ngày** | 2026-10-21 |
| **API** | [`signer-api.md`](signer-api.md) |
| **Cần chốt** | Xem [Quyết định cần chốt](#quyết-định-cần-chốt) ở cuối |

Tài liệu này mô tả cách làm bên trong Signer. Mọi thứ ở đây là đề xuất; BE không phụ thuộc vào tài liệu này.

Nguyên tắc: **dùng ít thư viện ngoài nhất có thể.** Cái gì thư viện chuẩn của Python làm được thì dùng thư viện chuẩn.

---

## Thư viện

### Thư viện ngoài: 3 gói

| Gói | Dùng cho | Vì sao không tránh được |
|---|---|---|
| `pycryptodome` | AES-256-GCM, Ed25519 (Binance), Keccak-256 (Hyperliquid) | Thư viện chuẩn không có AES, Ed25519 hay Keccak. `hashlib.sha3_256` **không phải** Keccak-256 (khác padding) |
| `coincurve` | Ký secp256k1 có recovery id (Hyperliquid) | Binding của `libsecp256k1`. Tự viết ECDSA bằng Python thuần thì dễ sai và lộ key qua side-channel |
| `msgpack` | Encode `action` của Hyperliquid trước khi hash | Phải ra đúng từng byte như SDK của Hyperliquid, mà SDK dùng chính gói này |

- Cả ba gói đều pin version cố định, và kiểm tra lại dependency kéo theo khi pin.
- **Không dùng** `hyperliquid-python-sdk` và `eth-account`. Hai gói này kéo theo rất nhiều gói khác (`requests`, `eth-abi`, `pydantic`…). Phần ký của Hyperliquid tự viết khoảng 30 dòng, rồi so với test vector của SDK.

### Thư viện chuẩn

| Việc | Module |
|---|---|
| HTTP server | `http.server.ThreadingHTTPServer` (chỉ có 2 endpoint) |
| JSON | `json` |
| Parse query Binance | `urllib.parse.parse_qsl` |
| Dẫn xuất master key | `hashlib.scrypt` |
| So sánh số | `decimal.Decimal` |
| `key_id` | `uuid.uuid4` |
| Nhập passphrase / private key | `getpass` |
| Ghi file atomic | `os`, `tempfile` |

---

## Cấu trúc code

```
signer/
  server.py         # http.server: GET /health, POST /v1/sign
  auth.py           # IP allowlist + HMAC theo key_id
  keystore.py       # đọc, giải mã, mã hoá, ghi file keystore
  policy.py         # giới hạn, whitelist tham số, quy tắc rủi ro
  errors.py         # Reject(code, message) → JSON lỗi
  venues/
    binance.py      # parse payload, nhận diện op, ký
    hyperliquid.py  # kiểm tra action, ký (EIP-712 tự viết)
  admin.py          # công cụ quản lý key, chạy trên máy Signer
tests/
```

Luồng xử lý `/v1/sign`:

```
IP allowlist(key_id) → HMAC(key_id) → venue của key → parse payload → khớp op → whitelist tham số → quy tắc rủi ro → ký
```

Bước nào lỗi thì dừng và trả lỗi ngay; không bao giờ ký khi chưa qua hết các bước.

---

## Lưu trữ và mã hoá key

### Nguyên tắc

- **Private key không bao giờ nằm trên đĩa ở dạng rõ.** Trên đĩa chỉ có file keystore đã mã hoá.
- **Giải mã khi khởi động và khi nạp lại keystore**, và giữ trong bộ nhớ của process. Không có API nào đọc được key.
  - Để nạp lại được, process giữ cả master key trong bộ nhớ. Rủi ro tăng thêm không đáng kể, vì process vốn đã giữ mọi private key.
- Signer không có mạng nên **không dùng được KMS trên cloud**. Master key phải đến từ chính máy Signer hoặc từ người vận hành.

### Mô hình mã hoá

```
passphrase ──scrypt(salt)──► master key (32 byte) ──AES-256-GCM──► từng private key
```

| Thành phần | Chi tiết |
|---|---|
| KDF | `hashlib.scrypt`, `n = 2^17, r = 8, p = 1`, khoảng 128 MB RAM. Phải truyền `maxmem`, vì mặc định chỉ 32 MB |
| Cipher | AES-256-GCM. Mỗi key một nonce 12 byte ngẫu nhiên |
| AAD | `{key_id, venue}` dạng JSON chuẩn hoá |
| Kiểm tra passphrase | Một bản mã của chuỗi cố định (`check`), giải mã được thì passphrase đúng |

**AAD:** không thể tráo `venue` hay `key_id` giữa các bản ghi; sửa một trong hai thì bản ghi đó **không giải mã được**.

`allowed_ips` không nằm trong keystore mà trong section `[key:<key_id>]` của `config.ini`, cùng với symbol và hạn mức của key.
- File config không được mã hoá: ai sửa được nó thì mở thêm IP, symbol hay nâng hạn mức được. Giữ `config.ini` quyền `0600`, cùng chủ với keystore.
- Keystore định dạng cũ (bản ghi còn `allowed_ips`) không được chuyển đổi: bản ghi đó nạp ở trạng thái `KEY_NOT_LOADED`, phải tạo key mới.

### File keystore

Một file JSON, quyền `0600`, chủ sở hữu là user riêng của Signer.

```json
{
  "version": 1,
  "kdf": { "name": "scrypt", "salt": "9c1e…", "n": 131072, "r": 8, "p": 1 },
  "check": { "nonce": "…", "ciphertext": "…" },
  "keys": [
    {
      "key_id": "3f2b8c1e-6d4a-4f7b-9c2e-1a5d8e7f0b34",
      "venue": "binance",
      "nonce": "…",
      "ciphertext": "…",
      "hmac": { "nonce": "…", "ciphertext": "…" }
    }
  ]
}
```

- `hmac` là HMAC secret của key (xác thực request của BE), mã hoá riêng bằng cùng master key, AAD = `{key_id, purpose: "hmac"}`.
  - Gắn với `key_id` nên không chép được sang bản ghi khác.
  - Bản ghi cũ không có `hmac` vẫn nạp được, nhưng request tới key đó trả `503 KEY_NOT_LOADED` cho tới khi chạy `rotate-hmac`.

- Nội dung được mã hoá là private key thô 32 byte: seed Ed25519 với Binance, secp256k1 với Hyperliquid.
- `ciphertext` gồm bản mã nối với tag GCM 16 byte.
- File này **sao lưu được an toàn**, vì đã mã hoá. Passphrase phải cất ở chỗ khác, offline.
- Ghi file theo kiểu atomic: ghi ra file tạm, `fsync`, rồi `os.replace`. Mất điện giữa chừng không làm hỏng file.

### Code mẫu

```python
# signer/keystore.py
import hashlib
import json
import os
import tempfile

from Crypto.Cipher import AES

CHECK_PLAINTEXT = b"signer-keystore-v1"


def derive_master_key(passphrase: bytes, kdf: dict) -> bytes:
    return hashlib.scrypt(
        passphrase, salt=bytes.fromhex(kdf["salt"]),
        n=kdf["n"], r=kdf["r"], p=kdf["p"], maxmem=256 * 1024 * 1024, dklen=32,
    )


def _aad(rec: dict) -> bytes:
    bound = {"key_id": rec["key_id"], "venue": rec["venue"]}
    return json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()


def seal(master: bytes, rec: dict, secret: bytes) -> dict:
    nonce = os.urandom(12)
    cipher = AES.new(master, AES.MODE_GCM, nonce=nonce)
    cipher.update(_aad(rec))
    ct, tag = cipher.encrypt_and_digest(secret)
    return {**rec, "nonce": nonce.hex(), "ciphertext": (ct + tag).hex()}


def unseal(master: bytes, rec: dict) -> bytes:
    data = bytes.fromhex(rec["ciphertext"])
    cipher = AES.new(master, AES.MODE_GCM, nonce=bytes.fromhex(rec["nonce"]))
    cipher.update(_aad(rec))
    # Sai master key, hoặc file bị sửa (key_id, venue) → ValueError
    return cipher.decrypt_and_verify(data[:-16], data[-16:])


def save_atomic(path: str, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
```

### Mở khoá khi khởi động

Có ba cách lấy passphrase/master key khi Signer khởi động. **Cần chốt một cách.**

| Cách | Tự khởi động lại sau crash? | Rủi ro | Ghi chú |
|---|---|---|---|
| **A. Người vận hành nhập passphrase** qua terminal | Không. Signer ngừng ký cho tới khi có người mở khoá | Thấp nhất: trên máy không có gì để giải mã | Đơn giản nhất về hạ tầng |
| **B. `systemd-creds` gắn với TPM2 của máy** | Có | Master key chỉ giải mã được trên đúng máy đó; copy ổ đĩa sang máy khác là vô dụng | Cần Linux có TPM2. Root trên máy vẫn lấy được |
| C. Passphrase để trong file hoặc env | Có | Đọc được file là lấy được toàn bộ key | **Không khuyến nghị** |

Đề xuất: **A** nếu chấp nhận được việc phải có người mở khoá sau mỗi lần restart; ngược lại là **B**. Cả hai cách đều không cần thêm thư viện Python.

### Bảo vệ key trong bộ nhớ

Python không xoá được bộ nhớ một cách chắc chắn, nên giảm rủi ro ở tầng hệ điều hành:
- Chạy bằng user riêng, không có shell đăng nhập.
- Tắt core dump: `resource.setrlimit(resource.RLIMIT_CORE, (0, 0))` khi khởi động.
- Tắt swap, hoặc dùng swap đã mã hoá.
- Không log payload kèm bất kỳ thứ gì liên quan tới key.

---

## Quản lý key (`admin.py`)

Chạy trực tiếp trên máy Signer. Mọi thao tác ghi keystore đều cần passphrase, vì phải mã hoá lại. `generate` / `import` / `remove` tự thêm / xoá section `[key:<key_id>]` trong `config.ini` (theo dòng, giữ nguyên comment); IP, symbol và hạn mức trong section thì sửa tay.

| Lệnh | Việc làm |
|---|---|
| `generate --venue …` | **Sinh keypair ngay trong Signer**, sinh `key_id` (UUID v4), lưu vào keystore, thêm section `[key:<key_id>]` vào config (`allowed_ips` để trống, `max_leverage = 40`, `BTCUSDC = 0.1`). In ra public key (Binance) hoặc địa chỉ ví agent (Hyperliquid) để đăng ký trên sàn |
| `import --venue …` | Nhập private key có sẵn (đọc bằng `getpass`, không hiện ra màn hình, không qua tham số dòng lệnh) |
| `rotate-hmac <key_id>` | Sinh HMAC secret mới (thay secret cũ), in ra một lần để đưa cho BE |
| `remove <key_id>` | Xoá bản ghi và section của key trong config |
| `list` | Liệt kê `key_id`, `venue`, `allowed_ips` (từ config), đánh dấu key chưa có HMAC. Không hiện gì bí mật |
| `change-passphrase` | Giải mã toàn bộ rồi mã hoá lại bằng master key mới |

- Ưu tiên `generate` hơn `import`: private key sinh ra trong Signer và **không bao giờ rời khỏi máy**.
  - Binance: tạo API key loại Ed25519 trên Binance bằng cách dán public key (PEM) vào.
  - Hyperliquid: dùng ví chính để approve địa chỉ agent trên giao diện Hyperliquid. Ví chính không đi qua Signer.
  - Địa chỉ agent tính được bằng `coincurve` + Keccak, không cần thêm thư viện: lấy 20 byte cuối của `keccak256(public_key_uncompressed[1:])`.
- Signer không tự nạp lại keystore theo chu kỳ. Trên Linux, `kill -HUP <pid>` nạp lại, chỉ khi nội dung file đã đổi; nơi không có `SIGHUP` thì restart.
  - Nạp lỗi (file hỏng, sai version) thì giữ nguyên bộ key đang dùng và ghi log lỗi.
  - Sau `change-passphrase` thì phải restart: master key cũ không mở được keystore mới.
  - Xoá key chỉ có hiệu lực ở lần nạp lại tiếp theo; muốn chặn ngay thì gửi `SIGHUP` hoặc restart, và thu hồi key trên sàn.

---

## Ký

### Binance

```python
# signer/venues/binance.py
import base64
from urllib.parse import parse_qsl

from Crypto.Signature import eddsa

from signer.errors import Reject


def parse(payload: str) -> dict[str, str]:
    try:
        pairs = parse_qsl(payload, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise Reject("INVALID_REQUEST", "payload is not a query string")
    params = dict(pairs)
    if len(params) != len(pairs):
        # Tham số lặp lại thì không chắc sàn lấy giá trị nào → không ký
        raise Reject("INVALID_REQUEST", "duplicate parameter")
    return params


def sign(seed: bytes, payload: str) -> str:
    key = eddsa.import_private_key(seed)
    # Ký đúng chuỗi BE gửi, không dựng lại từ params đã parse
    return base64.b64encode(eddsa.new(key, "rfc8032").sign(payload.encode())).decode()
```

- Policy kiểm tra trên `params` đã parse, nhưng chữ ký tính trên **chuỗi gốc**. Nhờ vậy cái được kiểm tra và cái được ký là một.
- Chữ ký Ed25519 dạng base64 có thể chứa `+`, `/`, `=`, nên phải URL-encode khi nối vào query. Xem [Quyết định cần chốt](#quyết-định-cần-chốt).

### Hyperliquid

Tự viết lại cách SDK ký một L1 action, gồm ba bước:
1. Tính `connectionId` từ action đã encode msgpack, nonce và cờ vault.
2. Băm EIP-712 kiểu `Agent`.
3. Ký secp256k1.

```python
# signer/venues/hyperliquid.py
import msgpack
from coincurve import PrivateKey
from Crypto.Hash import keccak


def _keccak(data: bytes) -> bytes:
    return keccak.new(digest_bits=256, data=data).digest()


_DOMAIN_TYPEHASH = _keccak(b"EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)")
_AGENT_TYPEHASH = _keccak(b"Agent(string source,bytes32 connectionId)")
_DOMAIN_SEPARATOR = _keccak(
    _DOMAIN_TYPEHASH
    + _keccak(b"Exchange")
    + _keccak(b"1")
    + (1337).to_bytes(32, "big")
    + bytes(32)  # verifyingContract = 0x000…0
)


def connection_id(action: dict, nonce: int) -> bytes:
    # vaultAddress = None → b"\x00"; không dùng expiresAfter
    return _keccak(msgpack.packb(action) + nonce.to_bytes(8, "big") + b"\x00")


def sign(private_key: bytes, action: dict, nonce: int, is_mainnet: bool) -> dict:
    source = b"a" if is_mainnet else b"b"
    struct_hash = _keccak(_AGENT_TYPEHASH + _keccak(source) + connection_id(action, nonce))
    digest = _keccak(b"\x19\x01" + _DOMAIN_SEPARATOR + struct_hash)
    sig = PrivateKey(private_key).sign_recoverable(digest, hasher=None)  # r(32) ‖ s(32) ‖ recid(1)
    return {"r": "0x" + sig[:32].hex(), "s": "0x" + sig[32:64].hex(), "v": sig[64] + 27}
```

- `action` lấy thẳng từ JSON BE gửi. `json.loads` của Python giữ nguyên thứ tự key, nên msgpack được tính đúng theo thứ tự BE gửi.
- **Bắt buộc có test so với SDK.** Dùng cùng `action` và `nonce`, chữ ký phải khớp với kết quả `sign_l1_action` của `hyperliquid-python-sdk`. SDK chỉ cần cài trong môi trường test, không cài lên máy Signer.
- Hai điểm cần đối chiếu kỹ với SDK:
  - định dạng `r` / `s` (có đệm số 0 ở đầu hay không);
  - các tuỳ chọn của `msgpack.packb` mà SDK đang dùng.

---

## Kiểm tra rủi ro: các điểm dễ sai

```python
# signer/policy.py
from decimal import Decimal, InvalidOperation

from signer.errors import Reject


def check_qty(raw, limit: Decimal) -> None:
    try:
        q = Decimal(str(raw))
    except InvalidOperation:
        raise Reject("QTY_EXCEEDS_LIMIT", f"quantity {raw!r} is not a number")
    if not (q.is_finite() and 0 < q < limit):
        raise Reject("QTY_EXCEEDS_LIMIT", f"quantity {raw} must be > 0 and < {limit}")


def check_asset(a, allowed) -> None:
    # bool là subclass của int: False == 0 → phải so cả kiểu
    if type(a) is not int or a not in allowed:
        raise Reject("SYMBOL_NOT_ALLOWED", f"asset {a!r} is not allowed for this key")
```

| Điểm | Cách xử lý |
|---|---|
| `NaN`, `Infinity` | `Decimal("NaN")` vẫn parse được → phải kiểm tra `is_finite()` |
| `true` / `false` thay cho số | Trong Python `False == 0` → phải kiểm tra `type(x) is int` |
| Tham số lặp lại trong query Binance | Từ chối, vì không biết sàn lấy giá trị nào |
| `recvWindow` không gửi | Binance dùng mặc định 5000 ms. Phải coi như 5000 khi so với trần `MAX_RECV_WINDOW_MS` (20000) |
| Field lạ lồng sâu (trong `orders[i]`, `t`, `t.trigger`…) | Whitelist áp dụng cho mọi cấp, không chỉ cấp ngoài cùng |

---

## Vận hành

- **HTTP server:** `ThreadingHTTPServer`, đặt timeout cho socket để một client chậm không giữ thread mãi. Giới hạn kích thước body, đọc theo `Content-Length`.
- **IP của request:** lấy từ `self.client_address[0]`, tức IP của kết nối TCP. Không đặt reverse proxy phía trước và không đọc `X-Forwarded-For`.
- **Đồng hồ:** quy tắc `STALE_PAYLOAD` dựa vào giờ của Signer. Nhưng Signer không có outbound network nên không đồng bộ NTP ra ngoài được. Xem [Quyết định cần chốt](#quyết-định-cần-chốt).
- **Log:** dùng `logging` của thư viện chuẩn, ghi ra file hoặc journald trên máy. Không ghi private key, master key hay passphrase.

---

## Test

- Dùng `unittest` của thư viện chuẩn. Môi trường test được cài thêm `hyperliquid-python-sdk` để lấy chữ ký đối chiếu; máy Signer thì không.
- **Chữ ký:** so với test vector có sẵn.
  - Binance: ví dụ ký Ed25519 trong docs Binance.
  - Hyperliquid: so từng byte với `sign_l1_action` của SDK, cho đủ 5 loại `op`.
- **Policy:** mỗi quy tắc trong `signer-api.md` có ít nhất một test cho trường hợp bị chặn, kèm một payload hợp lệ đi qua được.
- **Keystore:**
  - sai passphrase → không mở được;
  - sửa `key_id` hoặc `venue` trong file → bản ghi đó không giải mã được;
  - ghi atomic → file cũ còn nguyên nếu ghi lỗi giữa chừng.

---

## Quyết định cần chốt

- [ ] **Cách mở khoá khi khởi động:** A (nhập tay), B (TPM2) hay C. Xem [Mở khoá khi khởi động](#mở-khoá-khi-khởi-động).
- [ ] **Loại API key Binance:** tài liệu này giả định Ed25519.
  - HMAC thì Binance cũng giữ secret, nhưng `hmac` có sẵn trong thư viện chuẩn.
  - RSA thì chữ ký dài và chậm hơn.
- [ ] **Ai URL-encode chữ ký Binance:** Signer trả về sẵn bản đã encode, hay BE tự encode. Cần ghi rõ vào `signer-api.md`.
- [ ] **Đồng bộ giờ cho Signer:** cho phép NTP tới một time server nội bộ, hay nới `max_payload_age_ms`.
- [ ] **`msgpack`: dùng gói hay tự viết?**
  - Tự viết một encoder tối giản (dict, list, str, int, bool, None) thì bớt được một gói, khoảng 40 dòng.
  - Đổi lại, phải tự đảm bảo khớp từng byte với SDK, và test đối chiếu là bắt buộc.
