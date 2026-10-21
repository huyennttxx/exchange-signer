import argparse
import getpass
import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from signer import config as config_mod
from signer import keystore, policy
from signer.auth import SIGN_PATH, authenticate
from signer.errors import ALERT, Reject
from signer.venues import binance, hyperliquid

log = logging.getLogger("signer")

MAX_BODY = 64 * 1024
DRAIN_MAX = 1024 * 1024
SOCKET_TIMEOUT_S = 10
PASSPHRASE_CREDENTIAL = "signer-passphrase"  # tên credential khi chạy bằng systemd-creds


def _no_duplicate_keys(pairs):
    obj = dict(pairs)
    if len(obj) != len(pairs):
        raise Reject("INVALID_REQUEST", "duplicate key in JSON")
    return obj


def _no_constants(name):
    raise Reject("INVALID_REQUEST", f"{name} is not valid JSON")


def parse_body(body: bytes) -> dict:
    try:
        # Python giữ nguyên thứ tự key → msgpack của Hyperliquid đúng theo thứ tự BE gửi
        req = json.loads(body, object_pairs_hook=_no_duplicate_keys, parse_constant=_no_constants)
    except (ValueError, RecursionError):
        raise Reject("INVALID_REQUEST", "body is not valid JSON")
    if not isinstance(req, dict):
        raise Reject("INVALID_REQUEST", "body must be a JSON object")
    missing = {"key_id", "op", "payload"} - req.keys()
    if missing:
        raise Reject("INVALID_REQUEST", f"missing field(s): {', '.join(sorted(missing))}")
    extra = req.keys() - {"key_id", "op", "payload"}
    if extra:
        raise Reject("INVALID_REQUEST", f"unknown field(s): {', '.join(sorted(extra))}")
    return req


class Signer:
    def __init__(self, keys: dict[str, keystore.Key], limits: config_mod.Limits, hyperliquid_mainnet: bool,
                 clock=lambda: int(time.time() * 1000)):
        self.keys = keys
        self.limits = limits
        self.hyperliquid_mainnet = hyperliquid_mainnet
        self.clock = clock

    def sign(self, body: bytes, client_ip: str, headers) -> dict:
        """IP allowlist + HMAC(key_id) → venue của key → parse payload → khớp op → whitelist → rủi ro → ký."""
        req = parse_body(body)
        now_ms = self.clock()
        key = authenticate(self.keys, req["key_id"], client_ip, headers, body, now_ms, self.limits.max_payload_age_ms)
        op, payload = req["op"], req["payload"]
        policy.check_op(op)
        if key.secret is None:
            raise Reject("KEY_NOT_LOADED", "key is not loaded")

        # key.secret có giá trị → venue đã được keystore.unlock kiểm tra
        if key.venue == "binance":
            binance.verify(op, payload, self.limits, now_ms)
            return {"signature": binance.sign(key.secret, payload)}
        hyperliquid.verify(op, payload, self.limits, now_ms)
        return {"signature": hyperliquid.sign(key.secret, payload["action"], payload["nonce"], self.hyperliquid_mainnet)}


class Handler(BaseHTTPRequestHandler):
    signer: Signer  # gán khi tạo server
    timeout = SOCKET_TIMEOUT_S
    server_version = "signer"
    sys_version = ""

    def _send(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _content_length(self) -> int | None:
        try:
            return int(self.headers.get("Content-Length", ""))
        except ValueError:
            return None

    def _discard_body(self) -> None:
        # Đóng kết nối khi còn dữ liệu chưa đọc thì Windows gửi RST, client có thể mất luôn response lỗi
        length = self._content_length()
        if length and 0 < length <= DRAIN_MAX:
            self.rfile.read(length)
        else:
            self.close_connection = True

    def _not_found(self) -> None:
        self._discard_body()
        self._send(404, {"error": "NOT_FOUND", "message": "not found"})

    def do_GET(self):
        if self.path == "/health":
            # Giờ của Signer, để BE theo dõi độ lệch đồng hồ (quy tắc STALE_PAYLOAD dựa vào giờ này)
            self._send(200, {"status": "ok", "time_ms": self.signer.clock()})
        else:
            self._not_found()

    def do_POST(self):
        if self.path != SIGN_PATH:
            return self._not_found()
        # IP của kết nối TCP; không đọc X-Forwarded-For
        ip = self.client_address[0]
        try:
            length = self._content_length()
            if length is None:
                raise Reject("INVALID_REQUEST", "Content-Length required")
            if not 0 <= length <= MAX_BODY:
                self._discard_body()
                self.close_connection = True
                raise Reject("INVALID_REQUEST", f"body must be at most {MAX_BODY} bytes")
            result = self.signer.sign(self.rfile.read(length), ip, self.headers)
        except Reject as e:
            level = logging.ERROR if e.code in ALERT else logging.INFO
            log.log(level, "sign rejected ip=%s error=%s message=%s%s", ip, e.code, e.message,
                    f" detail={e.detail}" if e.detail else "")
            return self._send(e.status, e.body())
        except Exception:
            log.exception("sign failed ip=%s", ip)
            return self._send(500, {"error": "INTERNAL", "message": "internal error"})
        log.info("signed ip=%s", ip)
        self._send(200, result)

    do_PUT = do_DELETE = do_PATCH = do_HEAD = _not_found

    def log_message(self, fmt, *args):
        log.debug("%s " + fmt, self.client_address[0], *args)


def make_server(signer: Signer, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"signer": signer})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


class KeyReloader:
    """Nạp lại keystore khi file thay đổi, để thêm / sửa / xoá key không cần restart.

    Giữ master key trong bộ nhớ để giải mã bản ghi mới. Nạp lỗi thì giữ nguyên bộ key đang dùng.
    """

    def __init__(self, path: str, master: bytes, signer: Signer):
        self.path = path
        self.master = master
        self.signer = signer
        self.lock = threading.Lock()
        with open(path, "rb") as f:
            self.digest = hashlib.sha256(f.read()).digest()

    def reload_if_changed(self) -> bool:
        with self.lock:
            try:
                with open(self.path, "rb") as f:
                    raw = f.read()
                digest = hashlib.sha256(raw).digest()
                if digest == self.digest:
                    return False
                data = json.loads(raw)
                keystore.check_master(data, self.master)
                keys = keystore.unlock(data, self.master)
            except keystore.BadPassphrase:
                self.digest = digest  # chỉ báo lỗi một lần cho mỗi phiên bản file
                log.error("keystore passphrase changed: keeping current keys, restart the signer to load new ones")
                return False
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.error("keystore reload failed, keeping current keys: %s", e)
                return False
            old = self.signer.keys
            self.signer.keys = keys  # gán một tham chiếu: request đang chạy vẫn thấy bộ key cũ hoặc mới, không lẫn
            self.digest = digest
            added, removed = keys.keys() - old.keys(), old.keys() - keys.keys()
            log.info("keystore reloaded: %d/%d keys loaded, added=%s removed=%s",
                     sum(k.secret is not None for k in keys.values()), len(keys),
                     sorted(added) or "-", sorted(removed) or "-")
            return True


def read_passphrase() -> bytes:
    # Cách B: systemd-creds giải mã bằng TPM2 rồi đưa vào $CREDENTIALS_DIRECTORY
    cred_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if cred_dir and os.path.exists(os.path.join(cred_dir, PASSPHRASE_CREDENTIAL)):
        with open(os.path.join(cred_dir, PASSPHRASE_CREDENTIAL), "rb") as f:
            return f.read().rstrip(b"\r\n")
    # Cách A: người vận hành nhập
    return getpass.getpass("Keystore passphrase: ").encode()


def disable_core_dumps() -> None:
    try:
        import resource
    except ImportError:  # không phải Unix
        return
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Signer HTTP server")
    ap.add_argument("--config", default=config_mod.DEFAULT_CONFIG, help="default: %(default)s")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    disable_core_dumps()

    if not os.path.exists(args.config):
        log.error("%s not found: run ./signer.sh (Config), or copy config.example.json", args.config)
        return 1
    cfg = config_mod.load(args.config)
    data = keystore.load(cfg.keystore)
    try:
        master = keystore.open_master(data, read_passphrase())
    except keystore.BadPassphrase:
        log.error("wrong passphrase")
        return 1
    keys = keystore.unlock(data, master)
    loaded = sum(k.secret is not None for k in keys.values())
    log.info("loaded %d/%d keys", loaded, len(keys))

    signer = Signer(keys, cfg.limits, cfg.hyperliquid_mainnet)
    if hasattr(signal, "SIGHUP"):
        # kill -HUP <pid>: nạp lại keystore. Không có SIGHUP (Windows) thì phải restart
        reloader = KeyReloader(cfg.keystore, master, signer)
        signal.signal(signal.SIGHUP,
                      lambda *_: threading.Thread(target=reloader.reload_if_changed, daemon=True).start())

    server = make_server(signer, cfg.host, cfg.port)
    log.info("listening on %s:%d", cfg.host, cfg.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
