import json
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from signer.keystore import save_atomic

DEFAULT_CONFIG = "config.json"
DEFAULT_KEYSTORE = "keystore.json"
LIMIT_FIELDS = ("max_qty_btc", "max_leverage", "max_payload_age_ms")
SERVER_FIELDS = ("host", "port", "hyperliquid_network")
SERVER_DEFAULTS = {"host": "0.0.0.0", "port": 8080, "hyperliquid_network": "mainnet"}
FIELDS = LIMIT_FIELDS + SERVER_FIELDS  # sửa được bằng CLI; tên không trùng nhau nên gọi phẳng
_OLD_FIELDS = ("reload_interval_s",)  # Signer không còn dùng, bỏ khi ghi
_OLD_LIMIT_FIELDS = ("max_recv_window_ms",)


@dataclass(frozen=True)
class Limits:
    max_qty_btc: Decimal
    max_leverage: int
    max_payload_age_ms: int


@dataclass(frozen=True)
class Config:
    keystore: str
    host: str
    port: int
    hyperliquid_mainnet: bool
    limits: Limits


def _int(raw, name: str) -> int:
    # bool là int trong Python, "5.0" / 5.5 cũng không nhận: tránh bị làm tròn ngầm
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError(f"{name} must be an integer")
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer")


def parse(raw) -> Config:
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object")
    lim = raw.get("limits")  # không có giá trị mặc định: thiếu là lỗi khi khởi động
    if not isinstance(lim, dict):
        raise ValueError("limits is required")
    missing = [k for k in LIMIT_FIELDS if k not in lim]
    if missing:
        raise ValueError(f"limits.{missing[0]} is required")
    try:
        max_qty_btc = Decimal(str(lim["max_qty_btc"]))
    except InvalidOperation:
        raise ValueError("limits.max_qty_btc must be a number")
    limits = Limits(
        max_qty_btc=max_qty_btc,
        max_leverage=_int(lim["max_leverage"], "limits.max_leverage"),
        max_payload_age_ms=_int(lim["max_payload_age_ms"], "limits.max_payload_age_ms"),
    )
    if not (limits.max_qty_btc.is_finite() and limits.max_qty_btc > 0 and limits.max_leverage >= 1
            and limits.max_payload_age_ms > 0):
        raise ValueError("limits must be positive")
    network = raw.get("hyperliquid_network", SERVER_DEFAULTS["hyperliquid_network"])
    if network not in ("mainnet", "testnet"):
        raise ValueError("hyperliquid_network must be 'mainnet' or 'testnet'")
    port = _int(raw.get("port", SERVER_DEFAULTS["port"]), "port")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    host = raw.get("host", SERVER_DEFAULTS["host"])
    keystore = raw.get("keystore", DEFAULT_KEYSTORE)
    if not isinstance(host, str) or not host or not isinstance(keystore, str) or not keystore:
        raise ValueError("host and keystore must be non-empty strings")
    return Config(
        keystore=keystore,
        host=host,
        port=port,
        hyperliquid_mainnet=network == "mainnet",
        limits=limits,
    )


def load_raw(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load(path: str) -> Config:
    return parse(load_raw(path))


def get(raw: dict, field: str):
    """Giá trị Signer sẽ dùng: field server không có trong file thì là giá trị mặc định."""
    if field in LIMIT_FIELDS:
        lim = raw.get("limits")
        return lim.get(field) if isinstance(lim, dict) else None
    return raw.get(field, SERVER_DEFAULTS[field])


def update(path: str, keystore: str, changes: dict) -> dict:
    """Ghi các field trong changes vào config (tạo file nếu chưa có); field khác giữ nguyên.

    Kiểm tra cả file như lúc Signer khởi động; lỗi thì không ghi gì.
    """
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}; valid: {', '.join(FIELDS)}")
    raw = load_raw(path) if os.path.exists(path) else {"keystore": keystore}
    if not isinstance(raw, dict):
        raise ValueError(f"{path} is not a JSON object")
    lim = raw.get("limits") if isinstance(raw.get("limits"), dict) else {}
    for field, value in changes.items():
        (lim if field in LIMIT_FIELDS else raw)[field] = value
    raw["limits"] = lim
    cfg = parse(raw)
    # Ghi lại dạng chuẩn: max_qty_btc là chuỗi để giữ đúng Decimal
    raw.update(port=cfg.port)
    lim.update(max_qty_btc=str(cfg.limits.max_qty_btc), max_leverage=cfg.limits.max_leverage,
               max_payload_age_ms=cfg.limits.max_payload_age_ms)
    for k in _OLD_FIELDS:
        raw.pop(k, None)
    for k in _OLD_LIMIT_FIELDS:
        lim.pop(k, None)
    save_atomic(path, raw)
    return raw


def keystore_path(config_path: str) -> str:
    """Keystore mà config trỏ tới; config chưa có hoặc không ghi thì dùng DEFAULT_KEYSTORE."""
    if not os.path.exists(config_path):
        return DEFAULT_KEYSTORE
    raw = load_raw(config_path)
    path = raw.get("keystore", DEFAULT_KEYSTORE) if isinstance(raw, dict) else DEFAULT_KEYSTORE
    return path if isinstance(path, str) and path else DEFAULT_KEYSTORE
