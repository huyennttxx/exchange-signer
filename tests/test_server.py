import http.client
import json
import threading
import unittest
from urllib.parse import unquote

from signer.auth import SIGNATURE_HEADER, TIMESTAMP_HEADER, compute_signature
from signer.keystore import Key
from signer.server import Signer, make_server
from tests.helpers import BN, ED25519_SEED, LIMITS, NOW, SECP256K1_KEY, bn, hl

BINANCE_ID = "3f2b8c1e-6d4a-4f7b-9c2e-1a5d8e7f0b34"
HL_ID = "a91d4e7c-2b38-4c55-8f0a-6e3b9d1c7f22"
OTHER_IP_ID = "11111111-1111-4111-8111-111111111111"
BROKEN_ID = "22222222-2222-4222-8222-222222222222"
NO_HMAC_ID = "33333333-3333-4333-8333-333333333333"
# Mỗi key một HMAC secret riêng
HMAC = {BINANCE_ID: b"b" * 64, HL_ID: b"h" * 64, OTHER_IP_ID: b"o" * 64}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keys = {
            BINANCE_ID: Key(BINANCE_ID, "binance", frozenset({"127.0.0.1"}), ED25519_SEED, HMAC[BINANCE_ID]),
            HL_ID: Key(HL_ID, "hyperliquid", frozenset({"127.0.0.1"}), SECP256K1_KEY, HMAC[HL_ID]),
            OTHER_IP_ID: Key(OTHER_IP_ID, "binance", frozenset({"10.0.1.12"}), ED25519_SEED, HMAC[OTHER_IP_ID]),
            BROKEN_ID: Key(BROKEN_ID, "binance", frozenset({"127.0.0.1"}), None),
            NO_HMAC_ID: Key(NO_HMAC_ID, "binance", frozenset({"127.0.0.1"}), ED25519_SEED),
        }
        cls.server = make_server(Signer(keys, LIMITS, True, clock=lambda: NOW), "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, method, path, body=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        resp = conn.getresponse()
        out = (resp.status, resp.getheader("Content-Type"), json.loads(resp.read()))
        conn.close()
        return out

    @staticmethod
    def hmac_headers(raw: bytes, secret: bytes, ts: int = NOW) -> dict:
        return {TIMESTAMP_HEADER: str(ts), SIGNATURE_HEADER: compute_signature(secret, str(ts), raw)}

    def sign(self, key_id, op, payload, secret=None, ts=NOW):
        """Gửi request ký đúng chuẩn; secret mặc định là HMAC secret của key_id."""
        raw = json.dumps({"key_id": key_id, "op": op, "payload": payload}).encode()
        secret = secret or HMAC.get(str(key_id), b"x" * 64)
        return self.request("POST", "/v1/sign", raw=raw, headers=self.hmac_headers(raw, secret, ts))

    def test_health(self):
        self.assertEqual(self.request("GET", "/health"), (200, "application/json", {"status": "ok", "time_ms": NOW}))

    def test_other_paths_404(self):
        for method, path in (("GET", "/"), ("GET", "/v1/sign"), ("POST", "/health"), ("POST", "/v1/sign/"),
                             ("DELETE", "/v1/sign"), ("POST", "/sign")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.request(method, path, {})[0], 404)

    def test_binance_sign(self):
        status, _, body = self.sign(BINANCE_ID, "place_order", bn(**BN["place_order"]))
        self.assertEqual(status, 200, body)
        self.assertEqual(len(__import__("base64").b64decode(unquote(body["signature"]))), 64)

    def test_hyperliquid_sign(self):
        status, _, body = self.sign(HL_ID, "place_order", hl("place_order"))
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body["signature"]), {"r", "s", "v"})

    def test_hyperliquid_key_order_preserved(self):
        from signer.venues import hyperliquid
        p = hl("set_leverage")
        p["action"] = {"leverage": 3, "isCross": True, "asset": 0, "type": "updateLeverage"}
        _, _, body = self.sign(HL_ID, "set_leverage", p)
        self.assertEqual(body["signature"], hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True))

    def test_unauthenticated_is_indistinguishable(self):
        unknown = self.sign("00000000-0000-4000-8000-000000000000", "place_order", bn(**BN["place_order"]))
        wrong_ip = self.sign(OTHER_IP_ID, "place_order", bn(**BN["place_order"]))
        non_string = self.sign(["x"], "place_order", bn(**BN["place_order"]))
        self.assertEqual(unknown[0], 401)
        self.assertEqual(unknown, wrong_ip)
        self.assertEqual(unknown, non_string)

    def test_hmac_failures_are_indistinguishable(self):
        raw = json.dumps({"key_id": BINANCE_ID, "op": "place_order", "payload": bn(**BN["place_order"])}).encode()
        good = self.hmac_headers(raw, HMAC[BINANCE_ID])
        unknown = self.sign("00000000-0000-4000-8000-000000000000", "place_order", bn(**BN["place_order"]))
        cases = {
            "no headers": {},
            "no signature": {TIMESTAMP_HEADER: good[TIMESTAMP_HEADER]},
            "no timestamp": {SIGNATURE_HEADER: good[SIGNATURE_HEADER]},
            "other key's secret": self.hmac_headers(raw, HMAC[HL_ID]),
            "timestamp not signed": {**good, TIMESTAMP_HEADER: str(NOW + 1)},
            "bad timestamp": {**good, TIMESTAMP_HEADER: "-1"},
            "short signature": {**good, SIGNATURE_HEADER: good[SIGNATURE_HEADER][:-2]},
            "stale": self.hmac_headers(raw, HMAC[BINANCE_ID], NOW - LIMITS.max_payload_age_ms - 1),
            "future": self.hmac_headers(raw, HMAC[BINANCE_ID], NOW + LIMITS.max_payload_age_ms + 1),
        }
        for name, headers in cases.items():
            with self.subTest(name):
                self.assertEqual(self.request("POST", "/v1/sign", raw=raw, headers=headers), unknown)

    def test_hmac_test_vector(self):
        # Giống test vector trong docs/signer-api.md: BE dùng để đối chiếu
        sig = compute_signature(b"00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff",
                                "1729500000000", b'{"key_id":"k","op":"set_leverage","payload":"x"}')
        self.assertEqual(sig, "e6e89edb2cf9daa6f06be6711e3836dbdf475e84ffa46e24f21f1dd62d40858b")

    def test_hmac_covers_body(self):
        raw = json.dumps({"key_id": BINANCE_ID, "op": "place_order", "payload": bn(**BN["place_order"])}).encode()
        headers = self.hmac_headers(raw, HMAC[BINANCE_ID])
        tampered = raw.replace(b"0.005", b"0.009")
        self.assertEqual(self.request("POST", "/v1/sign", raw=tampered, headers=headers)[0], 401)
        # Chữ ký hex viết hoa vẫn nhận
        headers[SIGNATURE_HEADER] = headers[SIGNATURE_HEADER].upper()
        self.assertEqual(self.request("POST", "/v1/sign", raw=raw, headers=headers)[0], 200)

    def test_hmac_age_boundary(self):
        for ts in (NOW - LIMITS.max_payload_age_ms, NOW + LIMITS.max_payload_age_ms):
            with self.subTest(ts=ts):
                self.assertEqual(self.sign(BINANCE_ID, "place_order", bn(**BN["place_order"]), ts=ts)[0], 200)

    def test_key_without_hmac_not_loaded(self):
        status, _, body = self.sign(NO_HMAC_ID, "place_order", bn(**BN["place_order"]), secret=b"x" * 64)
        self.assertEqual((status, body["error"]), (503, "KEY_NOT_LOADED"))

    def test_auth_before_op_check(self):
        self.assertEqual(self.sign(OTHER_IP_ID, "withdraw", "x")[0], 401)

    def test_op_not_allowed(self):
        status, _, body = self.sign(BINANCE_ID, "withdraw", bn(**BN["place_order"]))
        self.assertEqual((status, body["error"]), (403, "OP_NOT_ALLOWED"))

    def test_key_not_loaded(self):
        status, _, body = self.sign(BROKEN_ID, "place_order", bn(**BN["place_order"]))
        self.assertEqual((status, body["error"]), (503, "KEY_NOT_LOADED"))

    def test_venue_from_key(self):
        # Payload Binance gửi tới key Hyperliquid và ngược lại
        self.assertEqual(self.sign(HL_ID, "place_order", bn(**BN["place_order"]))[2]["error"], "INVALID_REQUEST")
        self.assertEqual(self.sign(BINANCE_ID, "place_order", hl("place_order"))[2]["error"], "INVALID_REQUEST")

    def test_policy_reject_has_no_signature(self):
        status, _, body = self.sign(BINANCE_ID, "place_order", bn(**{**BN["place_order"], "quantity": "1"}))
        self.assertEqual(status, 403)
        self.assertEqual(set(body), {"error", "message"})
        self.assertEqual(body["error"], "QTY_EXCEEDS_LIMIT")

    def test_invalid_bodies(self):
        cases = [
            b"not json",
            b"[]",
            b'{"key_id": "x"}',
            json.dumps({"key_id": BINANCE_ID, "op": "place_order", "payload": "x", "extra": 1}).encode(),
            # key lặp: json.loads mặc định lấy giá trị sau cùng
            ('{"key_id": "%s", "op": "place_order", "op": "set_leverage", "payload": "x"}' % BINANCE_ID).encode(),
            ('{"key_id": "%s", "op": "place_order", "payload": {"action": {}, "nonce": NaN}}' % HL_ID).encode(),
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                status, _, body = self.request("POST", "/v1/sign", raw=raw)
                self.assertEqual((status, body["error"]), (400, "INVALID_REQUEST"))

    def test_body_too_large(self):
        status, _, body = self.request("POST", "/v1/sign", raw=b"{" + b" " * 70_000 + b"}")
        self.assertEqual((status, body["error"]), (400, "INVALID_REQUEST"))


if __name__ == "__main__":
    unittest.main()
