import hashlib
import hmac
import re

from signer.errors import Reject
from signer.keystore import Key

SIGN_PATH = "/v1/sign"
TIMESTAMP_HEADER = "X-Signer-Timestamp"
SIGNATURE_HEADER = "X-Signer-Signature"
_TIMESTAMP = re.compile(r"[0-9]{1,16}")
_SIGNATURE = re.compile(r"[0-9a-fA-F]{64}")


def compute_signature(hmac_key: bytes, timestamp: str, body: bytes) -> str:
    """Giá trị BE gửi trong SIGNATURE_HEADER. hmac_key là chuỗi secret in ra lúc tạo key, dạng bytes."""
    message = f"{timestamp}\nPOST\n{SIGN_PATH}\n".encode() + body
    return hmac.new(hmac_key, message, hashlib.sha256).hexdigest()


def authenticate(keys: dict[str, Key], key_id, client_ip: str, headers, body: bytes,
                 now_ms: int, max_age_ms: int) -> Key:
    """IP allowlist(key_id) → HMAC của key đó trên (timestamp, body) → timestamp còn mới."""
    def fail(detail: str) -> Reject:
        # Mọi lý do trả cùng một lỗi, để không dò được key nào tồn tại; lý do thật chỉ ghi log
        return Reject("UNAUTHENTICATED", "unauthenticated", detail)

    key = keys.get(key_id) if isinstance(key_id, str) else None
    if key is None:
        raise fail("unknown key_id")
    if client_ip not in key.allowed_ips:
        raise fail("ip not allowed")
    if key.hmac_key is None:
        raise Reject("KEY_NOT_LOADED", "key is not loaded", "key has no HMAC secret: run rotate-hmac")
    timestamp, signature = headers.get(TIMESTAMP_HEADER), headers.get(SIGNATURE_HEADER)
    if not (timestamp and _TIMESTAMP.fullmatch(timestamp) and signature and _SIGNATURE.fullmatch(signature)):
        raise fail("missing or malformed HMAC headers")
    if not hmac.compare_digest(compute_signature(key.hmac_key, timestamp, body), signature.lower()):
        raise fail("bad HMAC signature")
    # Kiểm tra sau HMAC: timestamp chỉ đáng tin khi chữ ký đúng
    if abs(now_ms - int(timestamp)) > max_age_ms:
        raise fail("stale timestamp")
    return key
