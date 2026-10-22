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
from dataclasses import dataclass, replace
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


def build_rules(keys: dict[str, keystore.Key], cfg: config_mod.Config) -> tuple[dict, list[str], list[str]]:
    """key_id → policy.Rule cho mọi key có trong cả keystore lẫn config.

    Trả (rules, errors, warnings). Key có lỗi (symbol Hyperliquid không có trong bảng asset) không có rule.
    """
    rules, errors, warnings = {}, [], []
    for key_id, key in keys.items():
        if key_id not in cfg.keys:
            warnings.append(f"key {key_id} has no [key:{key_id}] section in config: requests will be rejected")
            continue
        if not cfg.keys[key_id].allowed_ips:
            warnings.append(f"[key:{key_id}] has no allowed_ips yet: fill them in the config")
        try:
            rules[key_id] = config_mod.resolve(cfg.keys[key_id], key.venue, cfg.hyperliquid_mainnet)
        except ValueError as e:
            errors.append(f"[key:{key_id}]: {e}")
    for key_id in sorted(cfg.keys.keys() - keys.keys()):
        warnings.append(f"config section [key:{key_id}] has no key in the keystore")
    return rules, errors, warnings


@dataclass(frozen=True)
class State:
    keys: dict  # key_id → keystore.Key
    cfg: config_mod.Config
    rules: dict  # key_id → policy.Rule
    errors: list  # lỗi cấu hình của các key không có rule (đã ghi log)


class Signer:
    def __init__(self, keys: dict[str, keystore.Key], cfg: config_mod.Config, clock=lambda: int(time.time() * 1000)):
        self.clock = clock
        self.set_state(keys, cfg)

    def set_state(self, keys: dict[str, keystore.Key], cfg: config_mod.Config) -> None:
        """Thay bộ key và config."""
        rules, errors, warnings = build_rules(keys, cfg)
        for w in warnings:
            log.warning(w)
        for e in errors:
            log.error("%s: requests for this key will be rejected", e)
        # Gán một tham chiếu: request đang chạy thấy bộ cũ hoặc bộ mới, không lẫn
        self.state = State(keys, cfg, rules, errors)

    @property
    def keys(self) -> dict[str, keystore.Key]:
        return self.state.keys

    def sign(self, body: bytes, client_ip: str, headers) -> dict:
        """IP allowlist + HMAC(key_id) → venue của key → parse payload → khớp op → whitelist → rủi ro → ký."""
        st = self.state
        req = parse_body(body)
        now_ms = self.clock()
        max_age = st.cfg.max_payload_age_ms
        key = authenticate(st.keys, st.cfg.keys, req["key_id"], client_ip, headers, body, now_ms, max_age)
        op, payload = req["op"], req["payload"]
        policy.check_op(op)
        if key.secret is None:
            raise Reject("KEY_NOT_LOADED", "key is not loaded")
        rule = st.rules.get(key.key_id)
        if rule is None:
            raise Reject("SYMBOL_NOT_ALLOWED", "key symbols are misconfigured", "see the signer log")

        # key.secret có giá trị → venue đã được keystore.unlock kiểm tra
        if key.venue == "binance":
            binance.verify(op, payload, rule, max_age, now_ms)
            return {"signature": binance.sign(key.secret, payload)}
        hyperliquid.verify(op, payload, rule, max_age, now_ms)
        return {"signature": hyperliquid.sign(key.secret, payload["action"], payload["nonce"],
                                              st.cfg.hyperliquid_mainnet)}


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


def _digest(path: str) -> bytes:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).digest()


class KeyReloader:
    """Nạp lại keystore và các section [key:…] của config khi file thay đổi, để thêm / xoá key, đổi IP
    hay symbol không cần restart. Field [server] chỉ áp dụng khi restart.

    Giữ master key trong bộ nhớ để giải mã bản ghi mới. Nạp lỗi thì giữ nguyên bộ đang dùng.
    """

    def __init__(self, path: str, config_path: str, master: bytes, signer: Signer):
        self.path = path
        self.config_path = config_path
        self.master = master
        self.signer = signer
        self.lock = threading.Lock()
        self.digest = (_digest(path), _digest(config_path))

    def reload_if_changed(self) -> bool:
        with self.lock:
            try:
                with open(self.path, "rb") as f:
                    raw = f.read()
                digest = (hashlib.sha256(raw).digest(), _digest(self.config_path))
                if digest == self.digest:
                    return False
                data = json.loads(raw)
                keystore.check_master(data, self.master)
                keys = keystore.unlock(data, self.master)
                new_cfg = config_mod.load(self.config_path)
            except keystore.BadPassphrase:
                self.digest = digest  # chỉ báo lỗi một lần cho mỗi phiên bản file
                log.error("keystore passphrase changed: keeping current keys, restart the signer to load new ones")
                return False
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.error("keystore / config reload failed, keeping current keys: %s", e)
                return False
            old, old_cfg = self.signer.keys, self.signer.state.cfg
            cfg = replace(old_cfg, keys=new_cfg.keys)  # [server] giữ nguyên tới khi restart
            if cfg != new_cfg:
                log.warning("[server] in %s changed: restart the signer to apply", self.config_path)
            self.signer.set_state(keys, cfg)
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
        log.error("%s not found: run ./signer.sh (Config), or copy config.example.ini", args.config)
        return 1
    try:
        cfg = config_mod.load(args.config)
    except ValueError as e:
        log.error("%s: %s", args.config, e)
        return 1
    data = keystore.load(cfg.keystore)
    try:
        master = keystore.open_master(data, read_passphrase())
    except keystore.BadPassphrase:
        log.error("wrong passphrase")
        return 1
    keys = keystore.unlock(data, master)
    loaded = sum(k.secret is not None for k in keys.values())
    log.info("loaded %d/%d keys", loaded, len(keys))

    signer = Signer(keys, cfg)
    if signer.state.errors:
        log.error("fix the [key:...] sections in %s, then start again", args.config)
        return 1
    if hasattr(signal, "SIGHUP"):
        # kill -HUP <pid>: nạp lại keystore và section [key:…]. Không có SIGHUP (Windows) thì phải restart
        reloader = KeyReloader(cfg.keystore, args.config, master, signer)
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
