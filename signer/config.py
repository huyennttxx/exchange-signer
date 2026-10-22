"""Config Signer dạng INI: [server] và một section [key:<key_id>] cho mỗi key.

[key:<key_id>]
allowed_ips = 10.0.1.12, 10.0.1.13
max_leverage = 40
BTCUSDC = 0.1

Mọi dòng khác allowed_ips / max_leverage là <symbol> = <max_qty>. Không có comment cuối dòng.
"""
import configparser
import ipaddress
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from signer import keystore as keystore_mod
from signer.policy import Rule
from signer.venues import hyperliquid

DEFAULT_CONFIG = "config.ini"
DEFAULT_KEYSTORE = "keystore.json"
SERVER = "server"
KEY_PREFIX = "key:"
FIELDS = ("host", "port", "hyperliquid_network", "max_payload_age_ms")  # sửa được bằng config-set / config-edit
SERVER_DEFAULTS = {"keystore": DEFAULT_KEYSTORE, "host": "0.0.0.0", "port": 8080, "hyperliquid_network": "mainnet",
                   "max_payload_age_ms": 60000}
KEY_FIELDS = ("allowed_ips", "max_leverage")  # option khác trong [key:…] là symbol
NEW_KEY_LEVERAGE = 40
NEW_KEY_SYMBOL = "BTCUSDC"  # cùng tên trên Binance lẫn Hyperliquid
NEW_KEY_QTY = "0.1"
# Không dùng section DEFAULT của configparser: giá trị trong đó lan sang mọi section
_NO_DEFAULT = "\0"
_SECTION = re.compile(r"\s*\[(?P<name>[^\]]+)\]\s*$")


@dataclass(frozen=True)
class KeyRule:
    allowed_ips: frozenset
    max_leverage: int
    symbols: dict  # tên symbol trong config → max_qty (Decimal)


@dataclass(frozen=True)
class Config:
    keystore: str
    host: str
    port: int
    hyperliquid_mainnet: bool
    max_payload_age_ms: int
    keys: dict  # key_id → KeyRule


def _int(raw, name: str) -> int:
    # bool là int trong Python, "5.0" / 5.5 cũng không nhận: tránh bị làm tròn ngầm
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise ValueError(f"{name} must be an integer")
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer")


def parse_ips(text: str) -> frozenset:
    """IP cách nhau bằng dấu phẩy / dấu cách; rỗng → không IP nào. Chuẩn hoá để so khớp với client_address."""
    return frozenset(str(ipaddress.ip_address(ip)) for ip in re.split(r"[\s,]+", text) if ip)


def _key_rule(key_id: str, opts: dict) -> KeyRule:
    where = f"[{KEY_PREFIX}{key_id}]"
    if not key_id.strip():
        raise ValueError(f"{where}: key_id is empty")
    missing = [k for k in KEY_FIELDS if k not in opts]
    if missing:
        raise ValueError(f"{where}: {missing[0]} is required")
    # allowed_ips để trống: key chưa được gọi từ IP nào (key mới tạo, chưa điền IP)
    try:
        ips = parse_ips(opts["allowed_ips"])
    except ValueError as e:
        raise ValueError(f"{where}: allowed_ips: {e}")
    lev = _int(opts["max_leverage"], f"{where}: max_leverage")
    if lev < 1:
        raise ValueError(f"{where}: max_leverage must be >= 1")
    symbols = {}
    for name, raw in opts.items():
        if name in KEY_FIELDS:
            continue
        try:
            q = Decimal(str(raw))
        except InvalidOperation:
            q = None
        if q is None or not q.is_finite() or q <= 0:
            raise ValueError(f"{where}: {name} = {raw!r}: max_qty must be a number > 0")
        symbols[name] = q
    if not symbols:
        raise ValueError(f"{where}: at least one symbol (<symbol> = <max_qty>) is required")
    return KeyRule(ips, lev, symbols)


def parse(raw: dict) -> Config:
    """raw: kết quả của load_raw / parse_text: {"server": {...}, "keys": {key_id: {...}}}."""
    server = raw.get(SERVER, {})
    unknown = set(server) - set(SERVER_DEFAULTS)
    if unknown:
        raise ValueError(f"[{SERVER}]: unknown option(s): {', '.join(sorted(unknown))}")
    s = {**SERVER_DEFAULTS, **server}
    network = s["hyperliquid_network"]
    if network not in ("mainnet", "testnet"):
        raise ValueError("hyperliquid_network must be 'mainnet' or 'testnet'")
    port = _int(s["port"], "port")
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    age = _int(s["max_payload_age_ms"], "max_payload_age_ms")
    if age <= 0:
        raise ValueError("max_payload_age_ms must be positive")
    if not s["host"] or not s["keystore"]:
        raise ValueError("host and keystore must be non-empty")
    return Config(
        keystore=s["keystore"],
        host=s["host"],
        port=port,
        hyperliquid_mainnet=network == "mainnet",
        max_payload_age_ms=age,
        keys={key_id: _key_rule(key_id, opts) for key_id, opts in raw.get("keys", {}).items()},
    )


def parse_text(text: str) -> dict:
    if text.lstrip().startswith("{"):
        raise ValueError("JSON config is no longer supported: rewrite it as INI, see config.example.ini")
    cp = configparser.ConfigParser(interpolation=None, default_section=_NO_DEFAULT)
    cp.optionxform = str  # giữ hoa/thường của tên symbol
    try:
        cp.read_string(text)
    except configparser.Error as e:
        raise ValueError(str(e).replace("\n", " "))
    raw = {SERVER: {}, "keys": {}}
    for name in cp.sections():
        if name == SERVER:
            raw[SERVER] = dict(cp[name])
        elif name.startswith(KEY_PREFIX):
            raw["keys"][name[len(KEY_PREFIX):]] = dict(cp[name])
        else:
            raise ValueError(f"unknown section [{name}]: only [{SERVER}] and [{KEY_PREFIX}<key_id>]")
    return raw


def load_raw(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return parse_text(f.read())


def load(path: str) -> Config:
    return parse(load_raw(path))


def dump(raw: dict) -> str:
    lines = [f"[{SERVER}]"] + [f"{k} = {v}" for k, v in raw.get(SERVER, {}).items()]
    for key_id, opts in raw.get("keys", {}).items():
        lines += ["", f"[{KEY_PREFIX}{key_id}]"] + [f"{k} = {v}" for k, v in opts.items()]
    return "\n".join(lines) + "\n"


def get(raw: dict, field: str):
    """Giá trị Signer sẽ dùng: field không có trong file thì là giá trị mặc định."""
    return raw.get(SERVER, {}).get(field, SERVER_DEFAULTS[field])


def _save(path: str, text: str) -> None:
    parse(parse_text(text))  # kiểm tra cả file như lúc Signer khởi động; lỗi thì không ghi gì
    keystore_mod.write_atomic(path, text)


def update(path: str, keystore: str, changes: dict) -> dict:
    """Ghi các field [server] trong changes (tạo file nếu chưa có). Ghi lại cả file nên comment bị mất."""
    unknown = set(changes) - set(FIELDS)
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}; valid: {', '.join(FIELDS)}")
    raw = load_raw(path) if os.path.exists(path) else {SERVER: {"keystore": keystore}, "keys": {}}
    raw[SERVER].update(changes)
    cfg = parse(raw)
    raw[SERVER]["port"] = str(cfg.port)
    _save(path, dump(raw))
    return raw


def keystore_path(config_path: str) -> str:
    """Keystore mà config trỏ tới; config chưa có hoặc không ghi thì dùng DEFAULT_KEYSTORE."""
    if not os.path.exists(config_path):
        return DEFAULT_KEYSTORE
    return load_raw(config_path)[SERVER].get("keystore") or DEFAULT_KEYSTORE


# --- Sửa section [key:…] theo dòng, để giữ comment và phần còn lại của file ---

def _read_lines(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return f.read().splitlines()


def _section_span(lines: list[str], key_id: str) -> tuple[int, int] | None:
    """(dòng header, dòng sau dòng cuối) của [key:<key_id>]; None nếu không có.

    Comment và dòng trống ngay trước header kế tiếp thuộc về section sau, không tính vào đây.
    """
    start = None
    for i, line in enumerate(lines):
        m = _SECTION.match(line)
        if not m:
            continue
        if start is not None:
            end = i
            while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].lstrip()[:1] in "#;"):
                end -= 1
            return start, end
        if m["name"] == KEY_PREFIX + key_id:
            start = i
    return None if start is None else (start, len(lines))


def new_key_section(key_id: str) -> str:
    """allowed_ips để trống: key chưa gọi được từ IP nào cho tới khi người vận hành điền tay vào config."""
    return (f"[{KEY_PREFIX}{key_id}]\n"
            f"allowed_ips =\n"
            f"max_leverage = {NEW_KEY_LEVERAGE}\n"
            f"{NEW_KEY_SYMBOL} = {NEW_KEY_QTY}\n")


def add_key_section(path: str, keystore: str, key_id: str) -> str:
    """Nối section của key mới vào cuối config (tạo file nếu chưa có). Trả về section đã thêm."""
    section = new_key_section(key_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
    else:
        text = f"[{SERVER}]\nkeystore = {keystore}\n"
    _save(path, text.rstrip("\n") + "\n\n" + section)
    return section


def remove_key_section(path: str, key_id: str) -> bool:
    """Xoá section của key; False nếu file hoặc section không có."""
    if not os.path.exists(path):
        return False
    lines = _read_lines(path)
    span = _section_span(lines, key_id)
    if span is None:
        return False
    start, end = span
    del lines[start:end]
    _save(path, "\n".join(lines).rstrip("\n") + "\n")
    return True


def read_key_ips(path: str) -> dict[str, str]:
    """key_id → allowed_ips như ghi trong file, để hiển thị; file chưa có hoặc lỗi thì rỗng."""
    try:
        raw = load_raw(path)
    except (OSError, ValueError):
        return {}
    return {key_id: opts.get("allowed_ips", "") for key_id, opts in raw["keys"].items()}


# --- Từ KeyRule sang Rule mà venue dùng ---

def resolve(rule: KeyRule, venue: str, hyperliquid_mainnet: bool) -> Rule:
    """Binance: symbol → max_qty. Hyperliquid: chỉ số asset → max_qty, tra trong hyperliquid_assets.ini."""
    if venue == "binance":
        return Rule(rule.max_leverage, dict(rule.symbols))
    table = hyperliquid.asset_table(hyperliquid_mainnet)
    unknown = [s for s in rule.symbols if s not in table]
    if unknown:
        net = "mainnet" if hyperliquid_mainnet else "testnet"
        raise ValueError(f"symbol(s) {', '.join(unknown)} not in {hyperliquid.ASSETS_FILE} [{net}]")
    return Rule(rule.max_leverage, {table[s]: q for s, q in rule.symbols.items()})
