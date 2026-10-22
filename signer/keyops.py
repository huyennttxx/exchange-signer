"""Thao tác trên keystore cho admin CLI. Không in, không hỏi passphrase."""
import os
import secrets
import uuid

from signer import keystore
from signer.venues import binance, hyperliquid

MIN_PASSPHRASE = 12


class KeyOpError(Exception):
    pass


class WrongPassphrase(KeyOpError):
    pass


class NotFound(KeyOpError):
    pass


def _check_new_passphrase(passphrase: bytes) -> None:
    if len(passphrase) < MIN_PASSPHRASE:
        raise KeyOpError(f"passphrase must be at least {MIN_PASSPHRASE} characters")


def exists(path: str) -> bool:
    return os.path.exists(path)


def list_keys(path: str) -> list[dict]:
    if not exists(path):
        raise NotFound(f"{path} not found")
    return [{"key_id": r["key_id"], "venue": r["venue"], "hmac": "hmac" in r} for r in keystore.load(path)["keys"]]


def create(path: str, passphrase: bytes) -> tuple[dict, bytes]:
    if exists(path):
        raise KeyOpError(f"{path} already exists")
    _check_new_passphrase(passphrase)
    data, master = keystore.new_keystore(passphrase)
    os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
    keystore.save_atomic(path, data)
    return data, master


def unlock_file(path: str, passphrase: bytes) -> tuple[dict, bytes]:
    if not exists(path):
        raise NotFound(f"{path} not found")
    data = keystore.load(path)
    try:
        return data, keystore.open_master(data, passphrase)
    except keystore.BadPassphrase:
        raise WrongPassphrase("wrong passphrase")


def _find(data: dict, key_id: str) -> int:
    for i, rec in enumerate(data["keys"]):
        if rec["key_id"] == key_id:
            return i
    raise NotFound(f"key {key_id} not found")


def public_info(venue: str, secret: bytes) -> dict:
    if venue == "binance":
        return {"kind": "ed25519_public_key_pem", "value": binance.public_key_pem(secret)}
    return {"kind": "agent_address", "value": hyperliquid.address(secret)}


def new_secret(venue: str) -> bytes:
    return binance.new_seed() if venue == "binance" else hyperliquid.new_private_key()


def new_hmac_secret() -> str:
    # BE dùng nguyên chuỗi này (UTF-8) làm key HMAC: không phải decode hex, khó dùng sai
    return secrets.token_hex(32)


def add(path: str, passphrase: bytes, venue: str, secret: bytes | None = None) -> dict:
    """Thêm key; secret = None → sinh mới. Keystore chưa có thì tạo (caller phải xác nhận passphrase mới).

    IP được phép không nằm trong keystore mà trong section [key:…] của config.
    """
    if venue not in keystore.VENUES:
        raise KeyOpError(f"venue must be one of {', '.join(keystore.VENUES)}")
    if secret is None:
        secret = new_secret(venue)
    elif len(secret) != 32:
        raise KeyOpError("private key must be 32 bytes")
    data, master = unlock_file(path, passphrase) if exists(path) else create(path, passphrase)
    rec = keystore.seal(master, {"key_id": str(uuid.uuid4()), "venue": venue}, secret)
    hmac_secret = new_hmac_secret()
    data["keys"].append(keystore.seal_hmac(master, rec, hmac_secret.encode()))
    keystore.save_atomic(path, data)
    return {"key_id": rec["key_id"], "venue": venue, "public": public_info(venue, secret), "hmac_secret": hmac_secret}


def rotate_hmac(path: str, passphrase: bytes, key_id: str) -> str:
    """Sinh HMAC secret mới cho key, thay secret cũ (nếu có). Trả secret mới để đưa cho BE."""
    data, master = unlock_file(path, passphrase)
    i = _find(data, key_id)
    keystore.unseal(master, data["keys"][i])  # bản ghi hỏng → ValueError, không ghi gì
    hmac_secret = new_hmac_secret()
    data["keys"][i] = keystore.seal_hmac(master, data["keys"][i], hmac_secret.encode())
    keystore.save_atomic(path, data)
    return hmac_secret


def remove(path: str, passphrase: bytes, key_id: str) -> None:
    data, _ = unlock_file(path, passphrase)
    del data["keys"][_find(data, key_id)]
    keystore.save_atomic(path, data)


def show(path: str, passphrase: bytes, key_id: str) -> dict:
    data, master = unlock_file(path, passphrase)
    rec = data["keys"][_find(data, key_id)]
    return public_info(rec["venue"], keystore.unseal(master, rec))


def change_passphrase(path: str, passphrase: bytes, new_passphrase: bytes) -> None:
    _check_new_passphrase(new_passphrase)
    data, master = unlock_file(path, passphrase)
    plain = [(rec, keystore.unseal(master, rec), keystore.unseal_hmac(master, rec)) for rec in data["keys"]]
    new, new_master = keystore.new_keystore(new_passphrase, n=data["kdf"]["n"])
    for rec, secret, hmac_key in plain:
        rec = keystore.seal(new_master, rec, secret)
        # seal giữ box hmac cũ (mã hoá bằng master cũ) → luôn thay bằng box mới, hoặc bỏ nếu không có
        rec.pop("hmac", None)
        new["keys"].append(rec if hmac_key is None else keystore.seal_hmac(new_master, rec, hmac_key))
    keystore.save_atomic(path, new)
