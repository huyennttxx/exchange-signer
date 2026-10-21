import base64
import re
from urllib.parse import parse_qsl, quote

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from signer import policy
from signer.config import Limits
from signer.errors import Reject

SYMBOL = "BTCUSDT"
DEFAULT_RECV_WINDOW_MS = 5000
MAX_RECV_WINDOW_MS = 20000  # chữ ký Binance hết hạn tối đa 20 s sau timestamp
CONDITIONAL_TYPES = ("STOP_MARKET", "TAKE_PROFIT_MARKET")
_DIGITS = re.compile(r"[0-9]{1,19}")

ALLOWED = {
    "place_order": ("symbol", "side", "type", "quantity", "price", "timeInForce", "reduceOnly",
                    "newClientOrderId", "recvWindow", "timestamp"),
    "cancel_order": ("symbol", "origClientOrderId", "recvWindow", "timestamp"),
    "place_conditional": ("algoType", "symbol", "side", "type", "triggerPrice", "closePosition",
                          "workingType", "clientAlgoId", "recvWindow", "timestamp"),
    "cancel_conditional": ("symbol", "clientAlgoId", "recvWindow", "timestamp"),
    "set_leverage": ("symbol", "leverage", "recvWindow", "timestamp"),
}


def parse(payload) -> dict[str, str]:
    if not isinstance(payload, str) or not payload:
        raise Reject("INVALID_REQUEST", "payload must be a non-empty query string")
    try:
        pairs = parse_qsl(payload, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise Reject("INVALID_REQUEST", "payload is not a query string")
    params = dict(pairs)
    if len(params) != len(pairs):
        # Tham số lặp lại thì không chắc sàn lấy giá trị nào → không ký
        raise Reject("INVALID_REQUEST", "duplicate parameter")
    return params


def _int(raw):
    """Chuỗi chữ số → int; giá trị khác giữ nguyên để các hàm check báo lỗi kèm giá trị gốc."""
    return int(raw) if isinstance(raw, str) and _DIGITS.fullmatch(raw) else raw


def detect_op(params: dict) -> str | None:
    # Thứ tự quan trọng: algoType trước type, origClientOrderId/clientAlgoId trước leverage
    if "algoType" in params:
        return "place_conditional"
    if "type" in params:
        return "place_order"
    if "origClientOrderId" in params:
        return "cancel_order"
    if "clientAlgoId" in params:
        return "cancel_conditional"
    if "leverage" in params:
        return "set_leverage"
    return None


def verify(op: str, payload, limits: Limits, now_ms: int) -> None:
    params = parse(payload)

    actual = detect_op(params)
    if actual != op:
        raise Reject("OP_MISMATCH", f"payload is {actual or 'unrecognised'}, not {op}")

    policy.check_params(op, params, ALLOWED[op])

    if "symbol" in params and params["symbol"] != SYMBOL:
        raise Reject("SYMBOL_NOT_ALLOWED", f"symbol {params['symbol']!r} is not {SYMBOL}")

    if op == "place_order":
        policy.check_qty(params.get("quantity"), limits.max_qty_btc)
    elif op == "place_conditional":
        if params.get("closePosition") != "true":
            raise Reject("MUST_CLOSE_POSITION", "closePosition must be true")
        if params.get("type") not in CONDITIONAL_TYPES:
            raise Reject("MUST_CLOSE_POSITION", f"type must be one of {', '.join(CONDITIONAL_TYPES)}")
    elif op == "set_leverage":
        policy.check_leverage(_int(params["leverage"]), limits.max_leverage)

    policy.check_fresh("timestamp", _int(params.get("timestamp")), now_ms, limits.max_payload_age_ms)
    # Không gửi recvWindow → sàn dùng mặc định 5000 ms
    rw = _int(params.get("recvWindow", str(DEFAULT_RECV_WINDOW_MS)))
    if type(rw) is not int or not (0 < rw <= MAX_RECV_WINDOW_MS):
        raise Reject("STALE_PAYLOAD", f"recvWindow {rw!r} must be > 0 and <= {MAX_RECV_WINDOW_MS}")


def sign(seed: bytes, payload: str) -> str:
    key = eddsa.import_private_key(seed)
    # Ký đúng chuỗi BE gửi, không dựng lại từ params đã parse
    sig = base64.b64encode(eddsa.new(key, "rfc8032").sign(payload.encode())).decode()
    # Base64 có +, /, = → trả về bản đã URL-encode để BE nối thẳng "&signature=<giá trị>"
    return quote(sig, safe="")


def public_key_pem(seed: bytes) -> str:
    return eddsa.import_private_key(seed).public_key().export_key(format="PEM")


def new_seed() -> bytes:
    return ECC.generate(curve="ed25519").seed
