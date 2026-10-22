import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass

from Crypto.Cipher import AES

log = logging.getLogger(__name__)

VERSION = 1
CHECK_PLAINTEXT = b"signer-keystore-v1"
VENUES = ("binance", "hyperliquid")
SCRYPT_N = 2**17
SCRYPT_MAXMEM = 256 * 1024 * 1024


class BadPassphrase(Exception):
    pass


@dataclass
class Key:
    key_id: str
    venue: str
    secret: bytes | None  # None → bản ghi không giải mã được (KEY_NOT_LOADED)
    hmac_key: bytes | None = None  # secret xác thực request của BE; None → bản ghi chưa có hoặc lỗi


def derive_master_key(passphrase: bytes, kdf: dict) -> bytes:
    if kdf.get("name") != "scrypt":
        raise ValueError(f"unsupported kdf {kdf.get('name')!r}")
    return hashlib.scrypt(
        passphrase, salt=bytes.fromhex(kdf["salt"]),
        n=kdf["n"], r=kdf["r"], p=kdf["p"], maxmem=SCRYPT_MAXMEM, dklen=32,
    )


def _aad(rec: dict) -> bytes:
    bound = {"key_id": rec["key_id"], "venue": rec["venue"]}
    return json.dumps(bound, sort_keys=True, separators=(",", ":")).encode()


def _encrypt(master: bytes, aad: bytes, plaintext: bytes) -> dict:
    nonce = os.urandom(12)
    cipher = AES.new(master, AES.MODE_GCM, nonce=nonce)
    cipher.update(aad)
    ct, tag = cipher.encrypt_and_digest(plaintext)
    return {"nonce": nonce.hex(), "ciphertext": (ct + tag).hex()}


def _decrypt(master: bytes, aad: bytes, box: dict) -> bytes:
    data = bytes.fromhex(box["ciphertext"])
    cipher = AES.new(master, AES.MODE_GCM, nonce=bytes.fromhex(box["nonce"]))
    cipher.update(aad)
    return cipher.decrypt_and_verify(data[:-16], data[-16:])


def _hmac_aad(key_id: str) -> bytes:
    # Chỉ gắn với key_id: không chép được sang bản ghi khác
    return json.dumps({"key_id": key_id, "purpose": "hmac"}, sort_keys=True, separators=(",", ":")).encode()


def seal(master: bytes, rec: dict, secret: bytes) -> dict:
    """Mã hoá private key; giữ nguyên box hmac của rec (nếu có)."""
    out = {"key_id": rec["key_id"], "venue": rec["venue"]}
    out.update(_encrypt(master, _aad(out), secret))
    if "hmac" in rec:
        out["hmac"] = rec["hmac"]
    return out


def unseal(master: bytes, rec: dict) -> bytes:
    # Sai master key, hoặc file bị sửa (key_id, venue) → ValueError
    return _decrypt(master, _aad(rec), rec)


def seal_hmac(master: bytes, rec: dict, hmac_key: bytes) -> dict:
    return {**rec, "hmac": _encrypt(master, _hmac_aad(rec["key_id"]), hmac_key)}


def unseal_hmac(master: bytes, rec: dict) -> bytes | None:
    """None → bản ghi tạo trước khi có HMAC. Box bị sửa → ValueError."""
    if "hmac" not in rec:
        return None
    return _decrypt(master, _hmac_aad(rec["key_id"]), rec["hmac"])


def new_keystore(passphrase: bytes, n: int = SCRYPT_N) -> tuple[dict, bytes]:
    kdf = {"name": "scrypt", "salt": os.urandom(16).hex(), "n": n, "r": 8, "p": 1}
    master = derive_master_key(passphrase, kdf)
    data = {"version": VERSION, "kdf": kdf, "check": _encrypt(master, b"check", CHECK_PLAINTEXT), "keys": []}
    return data, master


def check_master(data: dict, master: bytes) -> None:
    """Master key có mở được keystore này không. Đổi passphrase thì salt đổi → master cũ không mở được."""
    if data.get("version") != VERSION:
        raise ValueError(f"unsupported keystore version {data.get('version')!r}")
    try:
        ok = _decrypt(master, b"check", data["check"]) == CHECK_PLAINTEXT
    except ValueError:
        ok = False
    if not ok:
        raise BadPassphrase("wrong passphrase")


def open_master(data: dict, passphrase: bytes) -> bytes:
    master = derive_master_key(passphrase, data["kdf"])
    check_master(data, master)
    return master


def unlock(data: dict, master: bytes) -> dict[str, Key]:
    keys = {}
    for rec in data["keys"]:
        try:
            if rec["venue"] not in VENUES:
                raise ValueError(f"unknown venue {rec['venue']!r}")
            if "allowed_ips" in rec:
                # allowed_ips nay nằm trong config.ini; không chuyển định dạng cũ
                raise ValueError("old keystore format (allowed_ips in record): create a new key")
            secret = unseal(master, rec)
            hmac_key = unseal_hmac(master, rec)
        except (ValueError, KeyError, TypeError) as e:
            log.error("key %s cannot be decrypted, not loaded: %s", rec.get("key_id"), e)
            secret = hmac_key = None
        else:
            if hmac_key is None:
                log.warning("key %s has no HMAC secret, requests will be rejected: run rotate-hmac", rec["key_id"])
        keys[rec["key_id"]] = Key(rec["key_id"], rec["venue"], secret, hmac_key)
    return keys


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_atomic(path: str, data: dict) -> None:
    write_atomic(path, json.dumps(data, indent=2))


def write_atomic(path: str, text: str) -> None:
    """Ghi file tạm, fsync, quyền 600, rồi đổi tên: lỗi giữa chừng thì file cũ còn nguyên."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
