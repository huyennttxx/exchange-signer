import base64
import unittest
from decimal import Decimal
from urllib.parse import unquote

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

from signer.errors import Reject
from signer.policy import Rule
from signer.venues import binance
from tests.helpers import BN, ED25519_SEED, MAX_AGE_MS, NOW, RULE_BN, bn


class SignatureTest(unittest.TestCase):
    def test_rfc8032_vectors(self):
        vectors = [
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", b"",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb", b"\x72",
             "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
        ]
        for seed, msg, sig in vectors:
            got = base64.b64decode(unquote(binance.sign(bytes.fromhex(seed), msg.decode("latin-1"))))
            self.assertEqual(got.hex(), sig)

    def test_signs_exact_payload_and_verifies(self):
        payload = bn(**BN["place_order"])
        sig = base64.b64decode(unquote(binance.sign(ED25519_SEED, payload)))
        pub = ECC.import_key(binance.public_key_pem(ED25519_SEED))
        eddsa.new(pub, "rfc8032").verify(payload.encode(), sig)  # ném lỗi nếu sai

    def test_signature_is_url_encoded(self):
        sig = binance.sign(ED25519_SEED, bn(**BN["place_order"]))
        self.assertNotRegex(sig, r"[+/=]")

    def test_generated_seed(self):
        seed = binance.new_seed()
        self.assertEqual(len(seed), 32)
        self.assertIn("BEGIN PUBLIC KEY", binance.public_key_pem(seed))


class PolicyTest(unittest.TestCase):
    def ok(self, op, rule=RULE_BN, **overrides):
        binance.verify(op, bn(**{**BN[op], **overrides}), rule, MAX_AGE_MS, NOW)

    def rejects(self, code, op, payload=None, rule=RULE_BN, **overrides):
        payload = payload if payload is not None else bn(**{**BN[op], **overrides})
        with self.assertRaises(Reject) as cm:
            binance.verify(op, payload, rule, MAX_AGE_MS, NOW)
        self.assertEqual(cm.exception.code, code, cm.exception.message)

    def test_valid_payloads(self):
        for op in BN:
            with self.subTest(op=op):
                self.ok(op)

    def test_recv_window_optional(self):
        self.ok("place_order", recvWindow=None)

    def test_op_mismatch(self):
        self.rejects("OP_MISMATCH", "set_leverage", bn(**BN["place_order"]))
        self.rejects("OP_MISMATCH", "place_order", bn(**BN["place_conditional"]))
        self.rejects("OP_MISMATCH", "cancel_order", bn(**BN["cancel_conditional"]))
        self.rejects("OP_MISMATCH", "place_order", "symbol=BTCUSDT&timestamp=1")

    def test_invalid_payload(self):
        self.rejects("INVALID_REQUEST", "place_order", "")
        self.rejects("INVALID_REQUEST", "place_order", "symbol")
        self.rejects("INVALID_REQUEST", "place_order", bn(**BN["place_order"]) + "&quantity=1")

    def test_param_not_allowed(self):
        self.rejects("PARAM_NOT_ALLOWED", "place_order", batchOrders="[]")
        self.rejects("PARAM_NOT_ALLOWED", "place_order", stopPrice="1")
        self.rejects("PARAM_NOT_ALLOWED", "cancel_order", orderId="1")
        self.rejects("PARAM_NOT_ALLOWED", "place_conditional", quantity="1")
        self.rejects("PARAM_NOT_ALLOWED", "set_leverage", marginType="ISOLATED")

    def test_symbol(self):
        self.rejects("SYMBOL_NOT_ALLOWED", "place_order", symbol="ETHUSDT")
        self.rejects("SYMBOL_NOT_ALLOWED", "set_leverage", symbol="btcusdt")
        self.rejects("SYMBOL_NOT_ALLOWED", "place_order", symbol=None)

    def test_rule_per_key(self):
        rule = Rule(max_leverage=10, allowed={"BTCUSDC": Decimal("0.01"), "ETHUSDT": Decimal("1")})
        self.ok("place_order", rule, symbol="ETHUSDT", quantity="0.5")
        self.ok("place_order", rule, symbol="BTCUSDC")
        self.rejects("QTY_EXCEEDS_LIMIT", "place_order", rule=rule, symbol="BTCUSDC", quantity="0.5")
        self.rejects("SYMBOL_NOT_ALLOWED", "place_order", rule=rule)  # BTCUSDT
        self.rejects("SYMBOL_NOT_ALLOWED", "place_conditional", rule=rule)
        self.ok("set_leverage", rule, symbol="ETHUSDT", leverage="10")
        self.rejects("LEVERAGE_EXCEEDS_LIMIT", "set_leverage", rule=rule, symbol="ETHUSDT", leverage="11")

    def test_qty(self):
        for q in ("0.01", "0.02", "0", "-0.001", "NaN", "Infinity", "abc", "", None):
            with self.subTest(q=q):
                self.rejects("QTY_EXCEEDS_LIMIT", "place_order", quantity=q)
        self.ok("place_order", quantity="0.0099")

    def test_reduce_only_qty_unlimited(self):
        self.ok("place_order", reduceOnly="true", quantity="5")
        for q in ("0", "-1", "NaN", "abc"):
            with self.subTest(q=q):
                self.rejects("QTY_EXCEEDS_LIMIT", "place_order", reduceOnly="true", quantity=q)
        self.rejects("QTY_EXCEEDS_LIMIT", "place_order", reduceOnly="false", quantity="5")

    def test_fixed_value_params(self):
        self.ok("place_order", newOrderRespType="RESULT")
        self.ok("place_conditional", priceProtect="false")
        self.ok("place_conditional", priceProtect="FALSE")
        self.rejects("PARAM_NOT_ALLOWED", "place_order", newOrderRespType="ACK")
        self.rejects("PARAM_NOT_ALLOWED", "place_conditional", priceProtect="true")
        self.rejects("PARAM_NOT_ALLOWED", "place_conditional", newOrderRespType="RESULT")
        self.rejects("PARAM_NOT_ALLOWED", "place_order", priceProtect="false")

    def test_leverage(self):
        for lev in ("0", "6", "2.5", "3.0", "-1", "NaN", "x", ""):
            with self.subTest(lev=lev):
                self.rejects("LEVERAGE_EXCEEDS_LIMIT", "set_leverage", leverage=lev)
        self.ok("set_leverage", leverage="5")
        self.ok("set_leverage", leverage="1")

    def test_must_close_position(self):
        self.rejects("MUST_CLOSE_POSITION", "place_conditional", closePosition="false")
        self.rejects("MUST_CLOSE_POSITION", "place_conditional", closePosition=None)
        self.rejects("MUST_CLOSE_POSITION", "place_conditional", type="STOP")
        self.ok("place_conditional", type="TAKE_PROFIT_MARKET")

    def test_stale(self):
        self.rejects("STALE_PAYLOAD", "place_order", timestamp=str(NOW - 10_001))
        self.rejects("STALE_PAYLOAD", "place_order", timestamp=str(NOW + 10_001))
        self.rejects("STALE_PAYLOAD", "place_order", timestamp=None)
        self.rejects("STALE_PAYLOAD", "place_order", timestamp="1e12")
        self.rejects("STALE_PAYLOAD", "place_order", recvWindow="20001")
        self.rejects("STALE_PAYLOAD", "place_order", recvWindow="0")
        self.ok("place_order", timestamp=str(NOW - 10_000))
        self.ok("place_order", recvWindow="20000")


if __name__ == "__main__":
    unittest.main()
