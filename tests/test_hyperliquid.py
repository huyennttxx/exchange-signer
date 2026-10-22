import copy
import unittest
from decimal import Decimal

from coincurve import PublicKey

from signer.errors import Reject
from signer.policy import Rule
from signer.venues import hyperliquid
from tests.helpers import MAX_AGE_MS, NOW, RULE_HL, SECP256K1_KEY, hl

try:
    from eth_account import Account
    from hyperliquid.utils.signing import sign_l1_action
except ImportError:  # SDK chỉ cài trong môi trường test
    sign_l1_action = None

OPS = ("place_order", "cancel_order", "place_conditional", "cancel_conditional", "set_leverage")


@unittest.skipIf(sign_l1_action is None, "hyperliquid-python-sdk not installed")
class SdkCompareTest(unittest.TestCase):
    def test_matches_sdk_for_every_op(self):
        wallet = Account.from_key(SECP256K1_KEY)
        for op in OPS:
            for mainnet in (True, False):
                with self.subTest(op=op, mainnet=mainnet):
                    p = hl(op)
                    expected = sign_l1_action(wallet, copy.deepcopy(p["action"]), None, p["nonce"], None, mainnet)
                    self.assertEqual(hyperliquid.sign(SECP256K1_KEY, p["action"], p["nonce"], mainnet), expected)

    def test_matches_sdk_many_keys(self):
        # Đủ nhiều key để gặp r/s có số 0 ở đầu và cả hai giá trị v
        p = hl("place_order")
        seen_v = set()
        for i in range(1, 300):
            pk = i.to_bytes(32, "big")
            expected = sign_l1_action(Account.from_key(pk), copy.deepcopy(p["action"]), None, p["nonce"], None, True)
            self.assertEqual(hyperliquid.sign(pk, p["action"], p["nonce"], True), expected)
            seen_v.add(expected["v"])
        self.assertEqual(seen_v, {27, 28})

    def test_address_matches_eth_account(self):
        self.assertEqual(hyperliquid.address(SECP256K1_KEY), Account.from_key(SECP256K1_KEY).address)


class SignatureTest(unittest.TestCase):
    def test_recovers_to_agent_address(self):
        p = hl("place_order")
        sig = hyperliquid.sign(SECP256K1_KEY, p["action"], p["nonce"], True)
        struct = hyperliquid._keccak(hyperliquid._AGENT_TYPEHASH + hyperliquid._keccak(b"a")
                                     + hyperliquid.connection_id(p["action"], p["nonce"]))
        digest = hyperliquid._keccak(b"\x19\x01" + hyperliquid._DOMAIN_SEPARATOR + struct)
        raw = int(sig["r"], 16).to_bytes(32, "big") + int(sig["s"], 16).to_bytes(32, "big") + bytes([sig["v"] - 27])
        pub = PublicKey.from_signature_and_message(raw, digest, hasher=None)
        self.assertEqual(pub, PublicKey.from_secret(SECP256K1_KEY))

    def test_key_order_changes_signature(self):
        p = hl("set_leverage")
        reordered = dict(reversed(list(p["action"].items())))
        self.assertNotEqual(hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True),
                            hyperliquid.sign(SECP256K1_KEY, reordered, NOW, True))


class PolicyTest(unittest.TestCase):
    def rejects(self, code, op, payload, rule=RULE_HL):
        with self.assertRaises(Reject) as cm:
            hyperliquid.verify(op, payload, rule, MAX_AGE_MS, NOW)
        self.assertEqual(cm.exception.code, code, cm.exception.message)

    def with_order(self, op, **fields):
        p = hl(op)
        p["action"]["orders"][0].update(fields)
        return p

    def test_valid_payloads(self):
        for op in OPS:
            with self.subTest(op=op):
                hyperliquid.verify(op, hl(op), RULE_HL, MAX_AGE_MS, NOW)

    def test_op_mismatch(self):
        self.rejects("OP_MISMATCH", "set_leverage", hl("place_order"))
        self.rejects("OP_MISMATCH", "place_order", hl("place_conditional"))
        self.rejects("OP_MISMATCH", "place_conditional", hl("place_order"))
        p = hl("place_order")
        p["action"]["grouping"] = "normalTpsl"
        self.rejects("OP_MISMATCH", "place_order", p)
        for t in ("withdraw3", "usdSend", "spotSend", "usdClassTransfer", "approveAgent", "approveBuilderFee",
                  "cancel", "scheduleCancel", "updateIsolatedMargin"):
            self.rejects("OP_MISMATCH", "place_order", {"action": {"type": t}, "nonce": NOW})

    def test_invalid_shape(self):
        self.rejects("INVALID_REQUEST", "place_order", "action=order")
        self.rejects("INVALID_REQUEST", "place_order", {"action": [], "nonce": NOW})
        p = hl("place_order")
        p["action"]["orders"] = {"0": p["action"]["orders"][0]}
        self.rejects("INVALID_REQUEST", "place_order", p)

    def test_param_not_allowed(self):
        for extra in ("vaultAddress", "expiresAfter"):
            p = hl("place_order")
            p[extra] = "0x" + "11" * 20 if extra == "vaultAddress" else NOW + 1000
            self.rejects("PARAM_NOT_ALLOWED", "place_order", p)
        p = hl("place_order")
        p["action"]["builder"] = {"b": "0x" + "22" * 20, "f": 10}
        self.rejects("PARAM_NOT_ALLOWED", "place_order", p)
        self.rejects("PARAM_NOT_ALLOWED", "place_order", self.with_order("place_order", x=1))
        p = hl("place_order")
        p["action"]["orders"][0]["t"]["limit"]["evil"] = 1
        self.rejects("PARAM_NOT_ALLOWED", "place_order", p)
        p = hl("place_conditional")
        p["action"]["orders"][0]["t"]["trigger"]["evil"] = 1
        self.rejects("PARAM_NOT_ALLOWED", "place_conditional", p)
        p = hl("place_order")
        p["action"]["orders"][0]["t"] = {"other": {}}
        self.rejects("PARAM_NOT_ALLOWED", "place_order", p)
        p = hl("cancel_order")
        p["action"]["cancels"][0]["oid"] = 1
        self.rejects("PARAM_NOT_ALLOWED", "cancel_order", p)
        p = hl("set_leverage")
        p["action"]["extra"] = 1
        self.rejects("PARAM_NOT_ALLOWED", "set_leverage", p)

    def test_symbol(self):
        for a in (1, False, "0", 0.0, None):
            with self.subTest(a=a):
                self.rejects("SYMBOL_NOT_ALLOWED", "place_order", self.with_order("place_order", a=a))
        p = hl("cancel_order")
        p["action"]["cancels"].append({"asset": 1, "cloid": "0x1"})
        self.rejects("SYMBOL_NOT_ALLOWED", "cancel_order", p)
        p = hl("set_leverage")
        p["action"]["asset"] = False
        self.rejects("SYMBOL_NOT_ALLOWED", "set_leverage", p)

    def test_rule_per_key(self):
        rule = Rule(max_leverage=10, allowed={1: Decimal("1")})  # chỉ ETH
        hyperliquid.verify("place_order", self.with_order("place_order", a=1, s="0.5"), rule, MAX_AGE_MS, NOW)
        self.rejects("QTY_EXCEEDS_LIMIT", "place_order", self.with_order("place_order", a=1, s="1"), rule)
        self.rejects("SYMBOL_NOT_ALLOWED", "place_order", hl("place_order"), rule)
        self.rejects("SYMBOL_NOT_ALLOWED", "cancel_order", hl("cancel_order"), rule)
        p = hl("set_leverage")
        p["action"].update(asset=1, leverage=10)
        hyperliquid.verify("set_leverage", p, rule, MAX_AGE_MS, NOW)
        p["action"]["leverage"] = 11
        self.rejects("LEVERAGE_EXCEEDS_LIMIT", "set_leverage", p, rule)

    def test_too_many_orders(self):
        for op in ("place_order", "place_conditional"):
            p = hl(op)
            p["action"]["orders"].append(copy.deepcopy(p["action"]["orders"][0]))
            self.rejects("TOO_MANY_ORDERS", op, p)
            p["action"]["orders"] = []
            self.rejects("TOO_MANY_ORDERS", op, p)

    def test_qty(self):
        for s in ("0.01", "0", "-1", "NaN", "Infinity", True, None, [], "x"):
            with self.subTest(s=s):
                self.rejects("QTY_EXCEEDS_LIMIT", "place_order", self.with_order("place_order", s=s))
        hyperliquid.verify("place_order", self.with_order("place_order", s="0.0099"), RULE_HL, MAX_AGE_MS, NOW)

    def test_reduce_only_qty_unlimited(self):
        hyperliquid.verify("place_order", self.with_order("place_order", r=True, s="5"), RULE_HL, MAX_AGE_MS, NOW)
        hyperliquid.verify("place_conditional", self.with_order("place_conditional", s="5"), RULE_HL, MAX_AGE_MS, NOW)
        for s in ("0", "-1", "NaN", None):
            with self.subTest(s=s):
                self.rejects("QTY_EXCEEDS_LIMIT", "place_order", self.with_order("place_order", r=True, s=s))

    def test_position_tpsl_zero_size(self):
        hyperliquid.verify("place_conditional", self.with_order("place_conditional", s="0"), RULE_HL, MAX_AGE_MS, NOW)
        for s in ("-1", "NaN", None):
            with self.subTest(s=s):
                self.rejects("QTY_EXCEEDS_LIMIT", "place_conditional", self.with_order("place_conditional", s=s))

    def test_leverage(self):
        for fields in ({"leverage": 6}, {"leverage": 0}, {"leverage": True}, {"leverage": 3.0},
                       {"leverage": "3"}, {"isCross": False}, {"isCross": 1}):
            with self.subTest(fields=fields):
                p = hl("set_leverage")
                p["action"].update(fields)
                self.rejects("LEVERAGE_EXCEEDS_LIMIT", "set_leverage", p)

    def test_must_close_position(self):
        self.rejects("MUST_CLOSE_POSITION", "place_conditional", self.with_order("place_conditional", r=False))
        self.rejects("MUST_CLOSE_POSITION", "place_conditional", self.with_order("place_conditional", r=1))
        self.rejects("MUST_CLOSE_POSITION", "place_conditional",
                     self.with_order("place_conditional", t={"limit": {"tif": "Gtc"}}))

    def test_stale(self):
        for nonce in (NOW - 10_001, NOW + 10_001, True, "1729500000000", None, float(NOW)):
            with self.subTest(nonce=nonce):
                p = hl("place_order")
                p["nonce"] = nonce
                self.rejects("STALE_PAYLOAD", "place_order", p)

    def test_unencodable(self):
        p = self.with_order("place_order", c=2**70)
        self.rejects("INVALID_REQUEST", "place_order", p)


if __name__ == "__main__":
    unittest.main()
