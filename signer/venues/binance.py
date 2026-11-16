import base64
from urllib.parse import parse_qsl, quote

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from signer import policy
from signer.errors import Reject
from signer.policy import Rule

# Op đặt lệnh: kiểm symbol + qty (lệnh đóng thì bỏ qty). Mọi op khác chỉ khớp op.
PLACE_OPS = ("place_order", "place_conditional", "place_margin_order")

# Chuyển thế chấp (universal transfer)
TRANSFER_TYPES = (
    "MAIN_MARGIN", "MARGIN_MAIN", "MARGIN_ISOLATEDMARGIN",
    "ISOLATEDMARGIN_MARGIN", "ISOLATEDMARGIN_ISOLATEDMARGIN",
)


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


def detect_op(params: dict) -> str | None:
    # Thứ tự quan trọng: marker margin / transfer trước futures
    if "maxLeverage" in params:
        return "set_margin_leverage"
    t = params.get("type")
    if t in TRANSFER_TYPES:
        return "margin_transfer"
    if t in ("BORROW", "REPAY"):
        return "margin_borrow_repay"
    if "sideEffectType" in params or "autoRepayAtCancel" in params:
        return "place_margin_order"
    if "isIsolated" in params and "type" in params:
        return "place_margin_order"
    if "algoType" in params:
        return "place_conditional"
    if "type" in params:
        return "place_order"
    if "origClientOrderId" in params:
        return "cancel_margin_order" if "isIsolated" in params else "cancel_order"
    if "clientAlgoId" in params:
        return "cancel_conditional"
    if "leverage" in params:
        return "set_leverage"
    # Bật isolated: symbol (+ timestamp/recvWindow), không có marker lệnh / đòn bẩy
    if "symbol" in params:
        return "enable_isolated"
    return None


def _is_close(op: str, params: dict) -> bool:
    if op == "place_order":
        return params.get("reduceOnly") == "true"
    if op == "place_conditional":
        return params.get("closePosition") == "true"
    # place_margin_order: SELL = đóng vị thế
    return params.get("side") == "SELL"


def verify(op: str, payload, rule: Rule, max_age_ms: int, now_ms: int) -> None:
    params = parse(payload)

    actual = detect_op(params)
    if actual != op:
        raise Reject("OP_MISMATCH", f"payload is {actual or 'unrecognised'}, not {op}")

    if op not in PLACE_OPS:
        return

    symbol = params.get("symbol")
    if symbol not in rule.allowed:
        raise Reject("SYMBOL_NOT_ALLOWED",
                     "symbol is required" if symbol is None else
                     f"symbol {symbol!r} is not allowed for this key")
    if not _is_close(op, params):
        policy.check_qty(params.get("quantity"), rule.allowed[symbol])


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
