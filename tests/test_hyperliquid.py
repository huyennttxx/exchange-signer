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
OPS = TRADE_OPS + ("sub_account_transfer", "earn_transfer", "create_sub_account")
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
        p = hl("place_order")
        p["action"]["orders"] = []
        self.rejects("INVALID_REQUEST", "place_order", p)

    def test_op_only_ignores_extra_params(self):
        # place_*: vẫn cho field lạ; chỉ check asset/qty
        p = hl("place_order")
        p["expiresAfter"] = NOW + 1000
        p["action"]["builder"] = {"b": "0x" + "22" * 20, "f": 10}
        p["action"]["orders"][0]["x"] = 1
        hyperliquid.verify("place_order", p, RULE_HL, MAX_AGE_MS, NOW)
        # cancel / leverage / …: chỉ khớp op
        p = hl("cancel_order")
        p["action"]["cancels"][0]["oid"] = 1
        p["action"]["cancels"].append({"asset": 1, "cloid": "0x1"})  # asset ngoài allowlist
        p["nonce"] = NOW - 10_001
        hyperliquid.verify("cancel_order", p, RULE_HL, MAX_AGE_MS, NOW)
        p = hl("set_leverage")
        p["action"].update(extra=1, leverage=99, isCross=False, asset=False)
        hyperliquid.verify("set_leverage", p, RULE_HL, MAX_AGE_MS, NOW)

    def test_vault_address_not_validated(self):
        # Verify không kiểm vaultAddress (BE / sàn chịu)
        for vault in (VAULT, None, "0xab", 1, True):
            with self.subTest(vault=vault):
                p = hl("place_order")
                p["vaultAddress"] = vault
                hyperliquid.verify("place_order", p, RULE_HL, MAX_AGE_MS, NOW)

    def test_symbol(self):
        for a in (1, False, "0", 0.0, None):
            with self.subTest(a=a):
                self.rejects("SYMBOL_NOT_ALLOWED", "place_order", self.with_order("place_order", a=a))
        # Op không phải place: không check asset
        p = hl("cancel_order")
        p["action"]["cancels"][0]["asset"] = 1
        hyperliquid.verify("cancel_order", p, RULE_HL, MAX_AGE_MS, NOW)

    def test_rule_per_key(self):
        rule = Rule(max_leverage=10, allowed={1: Decimal("1")})  # chỉ ETH
        hyperliquid.verify("place_order", self.with_order("place_order", a=1, s="0.5"), rule, MAX_AGE_MS, NOW)
        self.rejects("QTY_EXCEEDS_LIMIT", "place_order", self.with_order("place_order", a=1, s="1"), rule)
        self.rejects("SYMBOL_NOT_ALLOWED", "place_order", hl("place_order"), rule)
        hyperliquid.verify("cancel_order", hl("cancel_order"), rule, MAX_AGE_MS, NOW)
        p = hl("set_leverage")
        p["action"].update(asset=1, leverage=11)
        hyperliquid.verify("set_leverage", p, rule, MAX_AGE_MS, NOW)

    def test_qty(self):
        for s in ("0.01", "0", "-1", "NaN", "Infinity", True, None, [], "x"):
            with self.subTest(s=s):
                self.rejects("QTY_EXCEEDS_LIMIT", "place_order", self.with_order("place_order", s=s))
        hyperliquid.verify("place_order", self.with_order("place_order", s="0.0099"), RULE_HL, MAX_AGE_MS, NOW)

    def test_close_skips_qty(self):
        hyperliquid.verify("place_order", self.with_order("place_order", r=True, s="5"), RULE_HL, MAX_AGE_MS, NOW)
        hyperliquid.verify("place_order", self.with_order("place_order", r=True, s="NaN"), RULE_HL, MAX_AGE_MS, NOW)
        hyperliquid.verify("place_conditional", self.with_order("place_conditional", s="5"), RULE_HL, MAX_AGE_MS, NOW)
        hyperliquid.verify("place_conditional", self.with_order("place_conditional", s="0"), RULE_HL, MAX_AGE_MS, NOW)
        # Không phải đóng (r ≠ true) → check qty
        self.rejects("QTY_EXCEEDS_LIMIT", "place_conditional",
                     self.with_order("place_conditional", r=False, s="5"))
        hyperliquid.verify("place_conditional",
                           self.with_order("place_conditional", r=False, s="0.0099",
                                           t={"limit": {"tif": "Gtc"}}),
                           RULE_HL, MAX_AGE_MS, NOW)

    def test_multiple_orders_each_checked(self):
        p = hl("place_order")
        p["action"]["orders"].append(copy.deepcopy(p["action"]["orders"][0]))
        hyperliquid.verify("place_order", p, RULE_HL, MAX_AGE_MS, NOW)
        p["action"]["orders"][1]["a"] = 1
        self.rejects("SYMBOL_NOT_ALLOWED", "place_order", p)
        p = hl("place_order")
        p["action"]["orders"].append(copy.deepcopy(p["action"]["orders"][0]))
        p["action"]["orders"][1]["s"] = "5"
        self.rejects("QTY_EXCEEDS_LIMIT", "place_order", p)


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

    def test_op_only(self):
        # Chỉ khớp op — không check địa chỉ / usd / vault / freshness
        self.ok("sub_account_transfer", subAccountUser="bad", usd=0, isDeposit="x")
        p = hl("sub_account_transfer")
        p["vaultAddress"] = SUB_ACCOUNT
        p["expiresAfter"] = NOW
        p["nonce"] = NOW - 10_001
        hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
        self.ok("sub_account_spot_transfer", token="bad", amount="0")

    def test_not_tied_to_symbols(self):
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

    def test_op_only(self):
        # borrow / repay / field lạ / vault sai / stale: chỉ cần type = borrowLend
        for operation in ("borrow", "repay", "Supply", "", None):
            with self.subTest(operation=operation):
                self.ok(operation=operation, amount="bad", token=-1)
        p = hl(self.OP)
        p["vaultAddress"] = "0xab"
        p["expiresAfter"] = NOW
        p["nonce"] = NOW + 10_001
        hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)

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


class CreateSubAccountTest(unittest.TestCase):
    OP = "create_sub_account"

    def rejects(self, code, payload=None, **fields):
        p = payload or hl(self.OP)
        p["action"].update(fields)
        with self.assertRaises(Reject) as cm:
            hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)
        self.assertEqual(cm.exception.code, code, cm.exception.message)

    def test_ok(self):
        rule = Rule(max_leverage=1, allowed={1: Decimal("1")})
        for name in ("a", "bot-1", "Sub Account 2", "x" * 64, "tài khoản", "", None, 1):
            with self.subTest(name=name):
                p = hl(self.OP)
                p["action"]["name"] = name
                hyperliquid.verify(self.OP, p, rule, MAX_AGE_MS, NOW)

    def test_op_only(self):
        p = hl(self.OP)
        p["vaultAddress"] = SUB_ACCOUNT
        p["expiresAfter"] = NOW
        p["nonce"] = NOW - 10_001
        p["action"]["subAccountUser"] = SUB_ACCOUNT
        hyperliquid.verify(self.OP, p, RULE_HL, MAX_AGE_MS, NOW)

    def test_op_mismatch(self):
        for op in TRADE_OPS + ("sub_account_transfer", "earn_transfer"):
            with self.subTest(op=op):
                with self.assertRaises(Reject) as cm:
                    hyperliquid.verify(op, hl(self.OP), RULE_HL, MAX_AGE_MS, NOW)
                self.assertEqual(cm.exception.code, "OP_MISMATCH")
        self.rejects("OP_MISMATCH", {"action": {"type": "subAccountModify", "subAccountUser": SUB_ACCOUNT,
                                                "name": "x"}, "nonce": NOW})


if __name__ == "__main__":
    unittest.main()
