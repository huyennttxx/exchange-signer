from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from signer.errors import Reject

OPS = ("place_order", "cancel_order", "place_conditional", "cancel_conditional", "set_leverage")


@dataclass(frozen=True)
class Rule:
    """Giới hạn của một key, đã đổi sang tên của sàn: symbol Binance hoặc chỉ số asset Hyperliquid → max_qty."""
    max_leverage: int
    allowed: dict


def check_op(op) -> None:
    if op not in OPS:
        raise Reject("OP_NOT_ALLOWED", f"op {op!r} is not allowed")


def check_params(where: str, keys, allowed) -> None:
    extra = sorted(set(keys) - set(allowed), key=str)
    if extra:
        raise Reject("PARAM_NOT_ALLOWED", f"{where}: parameter(s) not allowed: {', '.join(map(str, extra))}")


def check_qty(raw, limit: Decimal | None, allow_zero: bool = False) -> None:
    """limit = None → không giới hạn trên (lệnh reduce-only); allow_zero → nhận 0 (đóng cả vị thế)."""
    q = None
    # bool là subclass của int: True → Decimal(1) → phải loại trước
    if isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
        try:
            q = Decimal(str(raw))
        except InvalidOperation:
            pass
    # Decimal("NaN"), Decimal("Infinity") vẫn parse được
    if q is None or not q.is_finite():
        raise Reject("QTY_EXCEEDS_LIMIT", f"quantity {raw!r} is not a number")
    if q < 0 or (q == 0 and not allow_zero):
        raise Reject("QTY_EXCEEDS_LIMIT", f"quantity {raw} must be {'>=' if allow_zero else '>'} 0")
    if limit is not None and q >= limit:
        raise Reject("QTY_EXCEEDS_LIMIT", f"quantity {raw} must be < {limit}")


def check_asset(a, allowed) -> None:
    # bool là subclass của int: False == 0 → phải so cả kiểu
    if type(a) is not int or a not in allowed:
        raise Reject("SYMBOL_NOT_ALLOWED", f"asset {a!r} is not allowed for this key")


def check_leverage(lev, limit: int) -> None:
    if type(lev) is not int:
        raise Reject("LEVERAGE_EXCEEDS_LIMIT", f"leverage {lev!r} is not an integer")
    if not (1 <= lev <= limit):
        raise Reject("LEVERAGE_EXCEEDS_LIMIT", f"leverage {lev} must be between 1 and {limit}")


def check_fresh(name: str, ts, now_ms: int, max_age_ms: int) -> None:
    if type(ts) is not int:
        raise Reject("STALE_PAYLOAD", f"{name} {ts!r} is not an integer")
    if abs(now_ms - ts) > max_age_ms:
        raise Reject("STALE_PAYLOAD", f"{name} {ts} is more than {max_age_ms} ms from signer time {now_ms}")
