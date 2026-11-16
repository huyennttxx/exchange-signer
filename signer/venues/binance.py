import base64
import re
from urllib.parse import parse_qsl, quote

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from signer import policy
from signer.errors import Reject
from signer.policy import Rule

DEFAULT_RECV_WINDOW_MS = 5000
MAX_RECV_WINDOW_MS = 20000  # chữ ký Binance hết hạn tối đa 20 s sau timestamp
CONDITIONAL_TYPES = ("STOP_MARKET", "TAKE_PROFIT_MARKET")
MARGIN_ORDER_TYPES = ("LIMIT", "LIMIT_MAKER")
# BUY chỉ được vay quote; SELL chỉ được trả nợ / không side-effect (không bán khống)
MARGIN_BUY_SIDE_EFFECTS = ("MARGIN_BUY", "NO_SIDE_EFFECT")
MARGIN_SELL_SIDE_EFFECTS = ("AUTO_REPAY", "NO_SIDE_EFFECT")
# Hậu tố quote phổ biến để suy ra tài sản gốc từ symbol config (BTCUSDT → BTC)
QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "BTC", "ETH", "BNB")
# Tham số chỉ được phép với đúng một giá trị
FIXED_VALUES = {"newOrderRespType": ("RESULT",), "priceProtect": ("false", "FALSE")}
_DIGITS = re.compile(r"[0-9]{1,19}")

# Chuyển thế chấp (universal transfer)
TRANSFER_TYPES = (
    "MAIN_MARGIN", "MARGIN_MAIN", "MARGIN_ISOLATEDMARGIN",
    "ISOLATEDMARGIN_MARGIN", "ISOLATEDMARGIN_ISOLATEDMARGIN",
)

ALLOWED = {
    "place_order": ("symbol", "side", "type", "quantity", "price", "timeInForce", "reduceOnly",
                    "newClientOrderId", "newOrderRespType", "recvWindow", "timestamp"),
    "cancel_order": ("symbol", "origClientOrderId", "recvWindow", "timestamp"),
    "place_conditional": ("algoType", "symbol", "side", "type", "triggerPrice", "closePosition",
                          "workingType", "priceProtect", "clientAlgoId", "recvWindow", "timestamp"),
    "cancel_conditional": ("symbol", "clientAlgoId", "recvWindow", "timestamp"),
    "set_leverage": ("symbol", "leverage", "recvWindow", "timestamp"),
    "place_margin_order": ("symbol", "isIsolated", "side", "type", "quantity", "price", "timeInForce",
                           "newClientOrderId", "sideEffectType", "autoRepayAtCancel",
                           "recvWindow", "timestamp"),
    "cancel_margin_order": ("symbol", "isIsolated", "origClientOrderId", "recvWindow", "timestamp"),
    "margin_transfer": ("type", "asset", "amount", "fromSymbol", "toSymbol", "recvWindow", "timestamp"),
    "set_margin_leverage": ("maxLeverage", "recvWindow", "timestamp"),
    "margin_borrow_repay": ("asset", "isIsolated", "symbol", "amount", "type", "recvWindow", "timestamp"),
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


def _base_assets(symbols) -> set[str]:
    """Tài sản gốc suy ra từ symbol config (BTCUSDT → BTC). Dùng để cấm vay base."""
    bases = set()
    for sym in symbols:
        for q in QUOTE_SUFFIXES:
            if sym.endswith(q) and len(sym) > len(q):
                bases.add(sym[:-len(q)])
                break
    return bases


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
    return None


def _check_place_margin_order(params: dict, rule: Rule) -> None:
    symbol = params.get("symbol")
    if symbol not in rule.allowed:
        raise Reject("SYMBOL_NOT_ALLOWED",
                     "symbol is required" if symbol is None else
                     f"symbol {symbol!r} is not allowed for this key")

    order_type = params.get("type")
    if order_type not in MARGIN_ORDER_TYPES:
        raise Reject("PARAM_NOT_ALLOWED",
                     f"type {order_type!r} must be {' or '.join(MARGIN_ORDER_TYPES)}")

    if params.get("autoRepayAtCancel") != "FALSE":
        raise Reject("PARAM_NOT_ALLOWED", "autoRepayAtCancel must be FALSE")

    side = params.get("side")
    effect = params.get("sideEffectType")
    if side == "BUY":
        if effect not in MARGIN_BUY_SIDE_EFFECTS:
            raise Reject("PARAM_NOT_ALLOWED",
                         f"sideEffectType {effect!r} must be {' or '.join(MARGIN_BUY_SIDE_EFFECTS)} for BUY")
        policy.check_qty(params.get("quantity"), rule.allowed[symbol])
    elif side == "SELL":
        if effect not in MARGIN_SELL_SIDE_EFFECTS:
            raise Reject("PARAM_NOT_ALLOWED",
                         f"sideEffectType {effect!r} must be {' or '.join(MARGIN_SELL_SIDE_EFFECTS)} for SELL")
        # Đóng vị thế: không giới hạn quantity; bán khống đã chặn qua sideEffectType + cấm vay base
    else:
        raise Reject("PARAM_NOT_ALLOWED", f"side {side!r} must be BUY or SELL")


def _check_margin_borrow_repay(params: dict, rule: Rule) -> None:
    # Vay tài sản gốc (BTC của BTCUSDT) → bán NO_SIDE_EFFECT vẫn là bán khống
    if params.get("type") == "BORROW":
        asset = params.get("asset")
        if asset in _base_assets(rule.allowed):
            raise Reject("PARAM_NOT_ALLOWED", f"borrowing base asset {asset!r} is not allowed")
    if "symbol" in params and params["symbol"] not in rule.allowed:
        raise Reject("SYMBOL_NOT_ALLOWED", f"symbol {params['symbol']!r} is not allowed for this key")


def verify(op: str, payload, rule: Rule, max_age_ms: int, now_ms: int) -> None:
    params = parse(payload)

    actual = detect_op(params)
    if actual != op:
        raise Reject("OP_MISMATCH", f"payload is {actual or 'unrecognised'}, not {op}")

    policy.check_params(op, params, ALLOWED[op])
    for k, values in FIXED_VALUES.items():
        if k in params and params[k] not in values:
            raise Reject("PARAM_NOT_ALLOWED", f"{k} {params[k]!r} must be {' or '.join(values)}")

    if "symbol" in params and params["symbol"] not in rule.allowed:
        raise Reject("SYMBOL_NOT_ALLOWED", f"symbol {params['symbol']!r} is not allowed for this key")
    if op in ("place_order", "place_margin_order") and "symbol" not in params:
        # Cần symbol để biết max_qty
        raise Reject("SYMBOL_NOT_ALLOWED", "symbol is required")

    if op == "place_order":
        # Lệnh reduce-only chỉ giảm vị thế → không giới hạn khối lượng
        reduce_only = params.get("reduceOnly") == "true"
        policy.check_qty(params.get("quantity"), None if reduce_only else rule.allowed[params["symbol"]])
    elif op == "place_conditional":
        if params.get("closePosition") != "true":
            raise Reject("MUST_CLOSE_POSITION", "closePosition must be true")
        if params.get("type") not in CONDITIONAL_TYPES:
            raise Reject("MUST_CLOSE_POSITION", f"type must be one of {', '.join(CONDITIONAL_TYPES)}")
    elif op == "set_leverage":
        policy.check_leverage(_int(params["leverage"]), rule.max_leverage)
    elif op == "place_margin_order":
        _check_place_margin_order(params, rule)
    elif op == "margin_borrow_repay":
        _check_margin_borrow_repay(params, rule)

    policy.check_fresh("timestamp", _int(params.get("timestamp")), now_ms, max_age_ms)
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
