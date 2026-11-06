import copy
import unittest
from decimal import Decimal

from coincurve import PublicKey

from signer.errors import Reject
from signer.policy import Rule
from signer.venues import hyperliquid
from tests.helpers import MAX_AGE_MS, NOW, RULE_HL, SECP256K1_KEY, SUB_ACCOUNT, hl

try:
    from eth_account import Account
    from hyperliquid.utils.signing import sign_l1_action
except ImportError:  # SDK chỉ cài trong môi trường test
    sign_l1_action = None

TRADE_OPS = ("place_order", "cancel_order", "place_conditional", "cancel_conditional", "set_leverage")
OPS = TRADE_OPS + ("sub_account_transfer", "earn_transfer")
# Mẫu payload của helpers.hl: mọi op, thêm bản spot của sub_account_transfer
SAMPLES = OPS + ("sub_account_spot_transfer",)


def op_of(sample: str) -> str:
    return "sub_account_transfer" if sample == "sub_account_spot_transfer" else sample
VAULT = "0x" + "ab" * 20


@unittest.skipIf(sign_l1_action is None, "hyperliquid-python-sdk not installed")
class SdkCompareTest(unittest.TestCase):
    def test_matches_sdk_for_every_op(self):
        wallet = Account.from_key(SECP256K1_KEY)
        for op in SAMPLES:
            for mainnet in (True, False):
                with self.subTest(op=op, mainnet=mainnet):
                    p = hl(op)
                    expected = sign_l1_action(wallet, copy.deepcopy(p["action"]), None, p["nonce"], None, mainnet)
                    self.assertEqual(hyperliquid.sign(SECP256K1_KEY, p["action"], p["nonce"], mainnet), expected)

    def test_matches_sdk_with_vault(self):
        wallet = Account.from_key(SECP256K1_KEY)
        for vault in (VAULT, VAULT.upper().replace("0X", "0x")):
            for op in OPS:
                with self.subTest(op=op, vault=vault):
                    p = hl(op)
                    expected = sign_l1_action(wallet, copy.deepcopy(p["action"]), vault, p["nonce"], None, True)
                    self.assertEqual(hyperliquid.sign(SECP256K1_KEY, p["action"], p["nonce"], True, vault), expected)

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

    def test_vault_changes_signature(self):
        p = hl("place_order")
        no_vault = hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True)
        self.assertEqual(no_vault, hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True, None))
        self.assertNotEqual(no_vault, hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True, VAULT))
        self.assertNotEqual(hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True, VAULT),
                            hyperliquid.sign(SECP256K1_KEY, p["action"], NOW, True, "0x" + "cd" * 20))

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
        for sample in SAMPLES:
            with self.subTest(sample=sample):
                hyperliquid.verify(op_of(sample), hl(sample), RULE_HL, MAX_AGE_MS, NOW)

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

    def test_vault_address(self):
        for op in TRADE_OPS:
            for vault in (VAULT, "0x" + "AbCdEf0123" * 4, None):
                with self.subTest(op=op, vault=vault):
                    p = hl(op)
                    p["vaultAddress"] = vault
                    hyperliquid.verify(op, p, RULE_HL, MAX_AGE_MS, NOW)
        for vault in ("", "0x", VAULT[2:], VAULT + "00", VAULT[:-2], "0x" + "zz" * 20, "0X" + "ab" * 20,
                      VAULT + "\n", 1, True, [], {}):
            with self.subTest(vault=vault):
                p = hl("place_order")
                p["vaultAddress"] = vault
                self.rejects("INVALID_REQUEST", "place_order", p)

    def test_param_not_allowed(self):
        p = hl("place_order")
        p["expiresAfter"] = NOW + 1000
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


class SubAccountTransferTest(unittest.TestCase):
    OP = "sub_account_transfer"

    def ok(self, sample, **fields):
        p = hl(sample)
        p["action"].update(fields)
        hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)

    def rejects(self, code, sample, payload=None, **fields):
        p = payload or hl(sample)
        p["action"].update(fields)
        with self.assertRaises(Reject) as cm:
            hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
        self.assertEqual(cm.exception.code, code, cm.exception.message)

    def test_both_directions(self):
        for sample in ("sub_account_transfer", "sub_account_spot_transfer"):
            for deposit in (True, False):
                with self.subTest(sample=sample, deposit=deposit):
                    self.ok(sample, isDeposit=deposit)

    def test_not_tied_to_symbols(self):
        # Không có asset / symbol: key chỉ có ETH vẫn chuyển được
        rule = Rule(max_leverage=1, allowed={1: Decimal("1")})
        hyperliquid.verify(self.OP, hl("sub_account_transfer"), rule, MAX_AGE_MS, NOW)

    def test_op_mismatch(self):
        for sample in ("sub_account_transfer", "sub_account_spot_transfer"):
            for op in TRADE_OPS:
                with self.subTest(sample=sample, op=op):
                    with self.assertRaises(Reject) as cm:
                        hyperliquid.verify(op, hl(sample), RULE_HL, MAX_AGE_MS, NOW)
                    self.assertEqual(cm.exception.code, "OP_MISMATCH")
        for t in ("usdSend", "spotSend", "usdClassTransfer", "withdraw3", "vaultTransfer", "subAccountModify",
                  "createSubAccount", "sendAsset"):
            with self.subTest(t=t):
                self.rejects("OP_MISMATCH", None, {"action": {"type": t}, "nonce": NOW})

    def test_param_not_allowed(self):
        self.rejects("PARAM_NOT_ALLOWED", "sub_account_transfer", token="USDC:0x" + "00" * 16)
        self.rejects("PARAM_NOT_ALLOWED", "sub_account_spot_transfer", usd=1)
        self.rejects("PARAM_NOT_ALLOWED", "sub_account_transfer", destination="0x" + "11" * 20)
        for extra in ("expiresAfter", "evil"):
            p = hl("sub_account_transfer")
            p[extra] = NOW
            self.rejects("PARAM_NOT_ALLOWED", None, p)

    def test_no_vault_address(self):
        for sample in ("sub_account_transfer", "sub_account_spot_transfer"):
            p = hl(sample)
            p["vaultAddress"] = None
            hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
            p["vaultAddress"] = SUB_ACCOUNT
            self.rejects("PARAM_NOT_ALLOWED", None, p)

    def test_sub_account_user(self):
        for user in (None, "", SUB_ACCOUNT[2:], SUB_ACCOUNT + "00", "0x" + "zz" * 20, SUB_ACCOUNT + "\n", 1, []):
            with self.subTest(user=user):
                self.rejects("INVALID_REQUEST", "sub_account_transfer", subAccountUser=user)
                self.rejects("INVALID_REQUEST", "sub_account_spot_transfer", subAccountUser=user)
        p = hl("sub_account_transfer")
        del p["action"]["subAccountUser"]
        self.rejects("INVALID_REQUEST", None, p)

    def test_is_deposit(self):
        for v in (None, 1, 0, "true", "false"):
            with self.subTest(v=v):
                self.rejects("INVALID_REQUEST", "sub_account_transfer", isDeposit=v)

    def test_usd(self):
        self.ok("sub_account_transfer", usd=1)
        self.ok("sub_account_transfer", usd=2**64 - 1)
        for usd in (0, -1, 2**64, True, 1.5, 1.0, "1000000", None):
            with self.subTest(usd=usd):
                self.rejects("QTY_EXCEEDS_LIMIT", "sub_account_transfer", usd=usd)

    def test_spot_amount(self):
        for amount in ("0.000001", "100", "1000000.5"):
            with self.subTest(amount=amount):
                self.ok("sub_account_spot_transfer", amount=amount)
        for amount in ("0", "-1", "NaN", "Infinity", "x", "", 1, 1.5, True, None):
            with self.subTest(amount=amount):
                self.rejects("QTY_EXCEEDS_LIMIT", "sub_account_spot_transfer", amount=amount)

    def test_spot_token(self):
        self.ok("sub_account_spot_transfer", token="PURR:0xc1fb593aeffbeb02f85e0308e9956a90")
        for token in ("USDC", "USDC:", "USDC:0x" + "00" * 15, "USDC:0x" + "00" * 17, ":0x" + "00" * 16,
                      "USDC:" + "00" * 16, "US DC:0x" + "00" * 16, None, 0):
            with self.subTest(token=token):
                self.rejects("INVALID_REQUEST", "sub_account_spot_transfer", token=token)

    def test_stale(self):
        p = hl("sub_account_transfer")
        p["nonce"] = NOW - 10_001
        self.rejects("STALE_PAYLOAD", None, p)



class EarnTransferTest(unittest.TestCase):
    OP = "earn_transfer"

    def ok(self, **fields):
        p = hl(self.OP)
        p["action"].update(fields)
        hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)

    def rejects(self, code, payload=None, **fields):
        p = payload or hl(self.OP)
        p["action"].update(fields)
        with self.assertRaises(Reject) as cm:
            hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
        self.assertEqual(cm.exception.code, code, cm.exception.message)

    def test_supply_and_withdraw(self):
        for operation in ("supply", "withdraw"):
            for amount in ("100", "0.000001", None):
                with self.subTest(operation=operation, amount=amount):
                    self.ok(operation=operation, amount=amount)

    def test_any_token(self):
        # Mọi token, không phụ thuộc symbol của key
        rule = Rule(max_leverage=1, allowed={1: Decimal("1")})
        for token in (0, 150, 197, 268, 360, 2**64 - 1):
            with self.subTest(token=token):
                p = hl(self.OP)
                p["action"]["token"] = token
                hyperliquid.verify(self.OP, p, rule, MAX_AGE_MS, NOW)
        for token in (-1, 2**64, True, False, "0", 0.0, None):
            with self.subTest(token=token):
                self.rejects("INVALID_REQUEST", token=token)

    def test_borrow_and_repay_not_signed(self):
        for operation in ("borrow", "repay", "Supply", "", None, 0):
            with self.subTest(operation=operation):
                self.rejects("PARAM_NOT_ALLOWED", operation=operation)

    def test_amount(self):
        for amount in ("0", "-1", "NaN", "Infinity", "x", "", 1, 1.5, True, [], {}):
            with self.subTest(amount=amount):
                self.rejects("QTY_EXCEEDS_LIMIT", amount=amount)
        p = hl(self.OP)
        del p["action"]["amount"]
        self.rejects("INVALID_REQUEST", p)

    def test_param_not_allowed(self):
        self.rejects("PARAM_NOT_ALLOWED", user="0x" + "11" * 20)
        p = hl(self.OP)
        p["expiresAfter"] = NOW
        self.rejects("PARAM_NOT_ALLOWED", p)

    def test_op_mismatch(self):
        for op in TRADE_OPS + ("sub_account_transfer",):
            with self.subTest(op=op):
                with self.assertRaises(Reject) as cm:
                    hyperliquid.verify(op, hl(self.OP), RULE_HL, MAX_AGE_MS, NOW)
                self.assertEqual(cm.exception.code, "OP_MISMATCH")
        p = {"action": {"type": "vaultTransfer"}, "nonce": NOW}
        with self.assertRaises(Reject) as cm:
            hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
        self.assertEqual(cm.exception.code, "OP_MISMATCH")

    def test_vault_address(self):
        p = hl(self.OP)
        p["vaultAddress"] = "0x" + "ab" * 20
        hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
        p["vaultAddress"] = "0xab"
        self.rejects("INVALID_REQUEST", p)

    def test_stale(self):
        p = hl(self.OP)
        p["nonce"] = NOW + 10_001
        self.rejects("STALE_PAYLOAD", p)

if __name__ == "__main__":
    unittest.main()
