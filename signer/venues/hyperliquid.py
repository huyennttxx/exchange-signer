import configparser
import os

import msgpack
from coincurve import PrivateKey
from Crypto.Hash import keccak

from signer import policy
from signer.errors import Reject
from signer.policy import Rule

ASSETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hyperliquid_assets.ini")

# Op đặt lệnh: kiểm asset + qty (r = true = đóng → bỏ qty). Mọi op khác chỉ khớp op.
PLACE_OPS = ("place_order", "place_conditional")

_asset_tables: dict[str, dict[str, int]] = {}


def asset_table(mainnet: bool) -> dict[str, int]:
    """Tên symbol (<coin>USDC) → chỉ số asset, đọc từ hyperliquid_assets.ini một lần."""
    net = "mainnet" if mainnet else "testnet"
    if net not in _asset_tables:
        cp = configparser.ConfigParser(interpolation=None)
        cp.optionxform = str  # kPEPE ≠ KPEPE
        with open(ASSETS_FILE, encoding="utf-8") as f:
            cp.read_file(f)
        if not cp.has_section(net):
            raise ValueError(f"{ASSETS_FILE} has no [{net}] section")
        table = {}
        for name, raw in cp[net].items():
            if not raw.isdigit():
                raise ValueError(f"{ASSETS_FILE} [{net}] {name} = {raw!r}: asset must be an integer >= 0")
            table[name] = int(raw)
        if len(set(table.values())) != len(table):
            raise ValueError(f"{ASSETS_FILE} [{net}]: duplicate asset index")
        _asset_tables[net] = table
    return _asset_tables[net]


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
    if t in ("subAccountTransfer", "subAccountSpotTransfer"):
        return ("sub_account_transfer",)
    if t == "borrowLend":
        return ("earn_transfer",)
    if t == "createSubAccount":
        return ("create_sub_account",)
    return ()


def _obj(where: str, x) -> dict:
    if not isinstance(x, dict):
        raise Reject("INVALID_REQUEST", f"{where} must be an object")
    return x


def _list(where: str, x) -> list:
    if not isinstance(x, list):
        raise Reject("INVALID_REQUEST", f"{where} must be an array")
    return x


def verify(op: str, payload, rule: Rule, max_age_ms: int, now_ms: int) -> None:
    payload = _obj("payload", payload)
    action = _obj("payload.action", payload.get("action"))

    if op not in detect_op(action):
        raise Reject("OP_MISMATCH", f"action type {action.get('type')!r} / grouping {action.get('grouping')!r} is not {op}")

    if op not in PLACE_OPS:
        return

    orders = _list("action.orders", action.get("orders"))
    if not orders:
        raise Reject("INVALID_REQUEST", "action.orders must not be empty")
    for i, order in enumerate(orders):
        order = _obj(f"orders[{i}]", order)
        policy.check_asset(order.get("a"), rule.allowed)
        # r = true: lệnh giảm / đóng vị thế → không giới hạn khối lượng
        if order.get("r") is not True:
            policy.check_qty(order.get("s"), rule.allowed[order["a"]], allow_zero=False)


def connection_id(action: dict, nonce: int, vault_address: str | None = None) -> bytes:
    # vaultAddress = None → b"\x00", có → b"\x01" + 20 byte địa chỉ; không dùng expiresAfter
    vault = b"\x00" if vault_address is None else b"\x01" + bytes.fromhex(vault_address[2:])
    return _keccak(msgpack.packb(action) + nonce.to_bytes(8, "big") + vault)


def sign(private_key: bytes, action: dict, nonce: int, is_mainnet: bool, vault_address: str | None = None) -> dict:
    source = b"a" if is_mainnet else b"b"
    struct_hash = _keccak(_AGENT_TYPEHASH + _keccak(source) + connection_id(action, nonce, vault_address))
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
