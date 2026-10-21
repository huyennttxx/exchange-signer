import msgpack
from coincurve import PrivateKey
from Crypto.Hash import keccak

from signer import policy
from signer.config import Limits
from signer.errors import Reject

BTC_ASSET = 0

ALLOWED_PAYLOAD = ("action", "nonce")
ALLOWED_ORDER_ACTION = ("type", "orders", "grouping")
ALLOWED_ORDER = ("a", "b", "p", "s", "r", "t", "c")
ALLOWED_LIMIT = ("tif",)
ALLOWED_TRIGGER = ("isMarket", "triggerPx", "tpsl")
ALLOWED_CANCEL_ACTION = ("type", "cancels")
ALLOWED_CANCEL = ("asset", "cloid")
ALLOWED_LEVERAGE_ACTION = ("type", "asset", "isCross", "leverage")


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


def detect_op(action: dict) -> tuple[str, ...]:
    t = action.get("type")
    if t == "order":
        return {"na": ("place_order",), "positionTpsl": ("place_conditional",)}.get(action.get("grouping"), ())
    if t == "cancelByCloid":
        return ("cancel_order", "cancel_conditional")
    if t == "updateLeverage":
        return ("set_leverage",)
    return ()


def _obj(where: str, x) -> dict:
    if not isinstance(x, dict):
        raise Reject("INVALID_REQUEST", f"{where} must be an object")
    return x


def _list(where: str, x) -> list:
    if not isinstance(x, list):
        raise Reject("INVALID_REQUEST", f"{where} must be an array")
    return x


def _check_order(i: int, order) -> None:
    where = f"orders[{i}]"
    policy.check_params(where, _obj(where, order), ALLOWED_ORDER)
    t = _obj(f"{where}.t", order.get("t"))
    policy.check_params(f"{where}.t", t, ("limit", "trigger"))
    if len(t) != 1:
        raise Reject("INVALID_REQUEST", f"{where}.t must have exactly one of limit, trigger")
    if "limit" in t:
        policy.check_params(f"{where}.t.limit", _obj(f"{where}.t.limit", t["limit"]), ALLOWED_LIMIT)
    else:
        policy.check_params(f"{where}.t.trigger", _obj(f"{where}.t.trigger", t["trigger"]), ALLOWED_TRIGGER)


def verify(op: str, payload, limits: Limits, now_ms: int) -> None:
    payload = _obj("payload", payload)
    action = _obj("payload.action", payload.get("action"))

    if op not in detect_op(action):
        raise Reject("OP_MISMATCH", f"action type {action.get('type')!r} / grouping {action.get('grouping')!r} is not {op}")

    policy.check_params("payload", payload, ALLOWED_PAYLOAD)

    if op in ("place_order", "place_conditional"):
        policy.check_params("action", action, ALLOWED_ORDER_ACTION)
        orders = _list("action.orders", action.get("orders"))
        for i, order in enumerate(orders):
            _check_order(i, order)
        if len(orders) != 1:
            raise Reject("TOO_MANY_ORDERS", f"orders must contain exactly 1 order, got {len(orders)}")
        order = orders[0]
        policy.check_asset(order.get("a"), BTC_ASSET)
        policy.check_qty(order.get("s"), limits.max_qty_btc)
        if op == "place_conditional" and not (order.get("r") is True and "trigger" in order["t"]):
            raise Reject("MUST_CLOSE_POSITION", "conditional order must have r = true and t.trigger")
    elif op in ("cancel_order", "cancel_conditional"):
        policy.check_params("action", action, ALLOWED_CANCEL_ACTION)
        cancels = _list("action.cancels", action.get("cancels"))
        if not cancels:
            raise Reject("INVALID_REQUEST", "cancels must not be empty")
        for i, c in enumerate(cancels):
            policy.check_params(f"cancels[{i}]", _obj(f"cancels[{i}]", c), ALLOWED_CANCEL)
        for c in cancels:
            policy.check_asset(c.get("asset"), BTC_ASSET)
    else:
        policy.check_params("action", action, ALLOWED_LEVERAGE_ACTION)
        policy.check_asset(action.get("asset"), BTC_ASSET)
        if action.get("isCross") is not True:
            raise Reject("LEVERAGE_EXCEEDS_LIMIT", "isCross must be true")
        policy.check_leverage(action.get("leverage"), limits.max_leverage)

    policy.check_fresh("nonce", payload.get("nonce"), now_ms, limits.max_payload_age_ms)

    try:
        msgpack.packb(action)
    except (OverflowError, TypeError, ValueError) as e:
        raise Reject("INVALID_REQUEST", f"action cannot be encoded: {e}")


def connection_id(action: dict, nonce: int) -> bytes:
    # vaultAddress = None → b"\x00"; không dùng expiresAfter
    return _keccak(msgpack.packb(action) + nonce.to_bytes(8, "big") + b"\x00")


def sign(private_key: bytes, action: dict, nonce: int, is_mainnet: bool) -> dict:
    source = b"a" if is_mainnet else b"b"
    struct_hash = _keccak(_AGENT_TYPEHASH + _keccak(source) + connection_id(action, nonce))
    digest = _keccak(b"\x19\x01" + _DOMAIN_SEPARATOR + struct_hash)
    sig = PrivateKey(private_key).sign_recoverable(digest, hasher=None)  # r(32) ‖ s(32) ‖ recid(1)
    # Giống SDK (eth_utils.to_hex của số nguyên): không đệm số 0 ở đầu
    return {
        "r": hex(int.from_bytes(sig[:32], "big")),
        "s": hex(int.from_bytes(sig[32:64], "big")),
        "v": sig[64] + 27,
    }


def address(private_key: bytes) -> str:
    pub = PrivateKey(private_key).public_key.format(compressed=False)
    addr = _keccak(pub[1:])[-20:].hex()
    # EIP-55 checksum
    h = _keccak(addr.encode()).hex()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(addr))


def new_private_key() -> bytes:
    return PrivateKey().secret
