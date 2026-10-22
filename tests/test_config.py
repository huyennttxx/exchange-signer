import os
import tempfile
import unittest
from decimal import Decimal

from signer import config

KEY = "3f2b8c1e-6d4a-4f7b-9c2e-1a5d8e7f0b34"
SECTION = f"""
[key:{KEY}]
allowed_ips = 10.0.1.13, 10.0.1.12
max_leverage = 5
BTCUSDT = 0.01
ETHUSDT = 0.5
"""


def parse(text: str) -> config.Config:
    return config.parse(config.parse_text(text))


class ParseTest(unittest.TestCase):
    def test_defaults(self):
        cfg = parse("")
        self.assertEqual((cfg.keystore, cfg.host, cfg.port, cfg.hyperliquid_mainnet, cfg.max_payload_age_ms, cfg.keys),
                         ("keystore.json", "0.0.0.0", 8080, True, 60000, {}))
        self.assertFalse(parse("[server]\nhyperliquid_network = testnet\n").hyperliquid_mainnet)

    def test_key_section(self):
        rule = parse(SECTION).keys[KEY]
        self.assertEqual(rule.allowed_ips, frozenset({"10.0.1.12", "10.0.1.13"}))
        self.assertEqual(rule.max_leverage, 5)
        self.assertEqual(rule.symbols, {"BTCUSDT": Decimal("0.01"), "ETHUSDT": Decimal("0.5")})
        # Giữ hoa/thường của tên symbol; % không phải ký tự đặc biệt
        rule = parse(f"[server]\nkeystore = /tmp/100%.json\n[key:k]\nallowed_ips = ::1\nmax_leverage = 1\nkPEPEUSDC = 5\n")
        self.assertEqual(list(rule.keys["k"].symbols), ["kPEPEUSDC"])
        self.assertEqual(rule.keystore, "/tmp/100%.json")

    def test_rejects(self):
        bad = {
            "unknown section": "[limits]\nmax_leverage = 5\n",
            "DEFAULT section": "[DEFAULT]\nport = 1\n",
            "unknown server option": "[server]\nmax_qty_btc = 0.01\n",
            "duplicate option": "[server]\nport = 1\nport = 2\n",
            "duplicate section": SECTION + SECTION,
            "old JSON": '{"limits": {}}',
            "bad network": "[server]\nhyperliquid_network = devnet\n",
            "bad port": "[server]\nport = 70000\n",
            "bad age": "[server]\nmax_payload_age_ms = 0\n",
            "missing ips": SECTION.replace("allowed_ips = 10.0.1.13, 10.0.1.12\n", ""),
            "bad ip": SECTION.replace("10.0.1.13", "10.0.1.300"),
            "missing leverage": SECTION.replace("max_leverage = 5\n", ""),
            "leverage 0": SECTION.replace("max_leverage = 5", "max_leverage = 0"),
            "leverage float": SECTION.replace("max_leverage = 5", "max_leverage = 5.0"),
            "no symbol": SECTION.replace("BTCUSDT = 0.01\nETHUSDT = 0.5\n", ""),
            "qty 0": SECTION.replace("0.01", "0"),
            "qty NaN": SECTION.replace("0.01", "NaN"),
            "qty text": SECTION.replace("0.01", "0.01 # comment"),
            "no value": SECTION + "SOLUSDT\n",
        }
        for name, text in bad.items():
            with self.subTest(name), self.assertRaises(ValueError):
                parse(text)

    def test_dump_roundtrip(self):
        raw = config.parse_text("[server]\nport = 8081\n" + SECTION)
        self.assertEqual(config.parse_text(config.dump(raw)), raw)

    def test_resolve(self):
        rule = config.KeyRule(frozenset(), 3, {"BTCUSDC": Decimal("0.1"), "ETHUSDC": Decimal("1")})
        self.assertEqual(config.resolve(rule, "binance", True).allowed, rule.symbols)
        hl = config.resolve(rule, "hyperliquid", True)
        self.assertEqual((hl.max_leverage, hl.allowed), (3, {0: Decimal("0.1"), 1: Decimal("1")}))
        with self.assertRaises(ValueError):
            config.resolve(config.KeyRule(frozenset(), 3, {"BTCUSDT": Decimal("1")}), "hyperliquid", True)
        with self.assertRaises(ValueError):  # bảng asset chưa có testnet
            config.resolve(rule, "hyperliquid", False)


class FileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "config.ini")

    def tearDown(self):
        self.dir.cleanup()

    def read(self) -> str:
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def write(self, text: str) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_update(self):
        raw = config.update(self.path, "ks.json", {"port": "08081", "max_payload_age_ms": "30000"})
        self.assertEqual(raw["server"]["port"], "8081")
        cfg = config.load(self.path)
        self.assertEqual((cfg.keystore, cfg.port, cfg.max_payload_age_ms), ("ks.json", 8081, 30000))

        # Section key giữ nguyên
        self.write(self.read() + SECTION)
        config.update(self.path, "ignored.json", {"host": "127.0.0.1"})
        cfg = config.load(self.path)
        self.assertEqual((cfg.keystore, cfg.host, cfg.keys[KEY].max_leverage), ("ks.json", "127.0.0.1", 5))

        for changes in ({"port": "70000"}, {"hyperliquid_network": "devnet"}, {"keystore": "x.json"},
                        {"max_leverage": "5"}, {"max_payload_age_ms": "-1"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                config.update(self.path, "ks.json", changes)
        self.assertEqual(config.load(self.path).host, "127.0.0.1")

    def test_keystore_path(self):
        self.assertEqual(config.keystore_path(self.path), "keystore.json")
        self.write("[server]\nport = 8080\n")
        self.assertEqual(config.keystore_path(self.path), "keystore.json")
        self.write("[server]\nkeystore = /var/lib/signer/ks.json\n")
        self.assertEqual(config.keystore_path(self.path), "/var/lib/signer/ks.json")

    def test_key_sections_keep_comments(self):
        # File chưa có → tạo với [server] trỏ tới keystore. allowed_ips để trống: điền tay
        config.add_key_section(self.path, "ks.json", "a")
        self.write("# đầu file\n" + self.read() + "# comment của b\n")
        config.add_key_section(self.path, "ks.json", "b")
        cfg = config.load(self.path)
        self.assertEqual(cfg.keystore, "ks.json")
        self.assertEqual(cfg.keys["a"], config.KeyRule(frozenset(), 40, {"BTCUSDC": Decimal("0.1")}))
        self.assertEqual(cfg.keys["b"].symbols, {"BTCUSDC": Decimal("0.1")})
        with self.assertRaises(ValueError):  # section đã có
            config.add_key_section(self.path, "ks.json", "a")

        self.assertTrue(config.remove_key_section(self.path, "a"))
        self.assertFalse(config.remove_key_section(self.path, "a"))
        self.assertEqual(set(config.load(self.path).keys), {"b"})
        text = self.read()
        self.assertIn("# đầu file", text)
        self.assertIn("# comment của b", text)
        self.assertEqual(config.read_key_ips(self.path), {"b": ""})

    def test_invalid_edit_writes_nothing(self):
        self.write("[server]\n" + SECTION + "[oops]\n")
        broken = self.read()
        with self.assertRaises(ValueError):
            config.add_key_section(self.path, "ks.json", "new")
        with self.assertRaises(ValueError):
            config.remove_key_section(self.path, KEY)
        self.assertEqual(self.read(), broken)


if __name__ == "__main__":
    unittest.main()
