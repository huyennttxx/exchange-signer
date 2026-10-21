import json
import os
import tempfile
import unittest
from decimal import Decimal

from signer import config

LIMITS = {"max_qty_btc": "0.01", "max_leverage": 5, "max_payload_age_ms": 60000}


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = config.parse({"limits": LIMITS})
        self.assertEqual((cfg.keystore, cfg.host, cfg.port, cfg.hyperliquid_mainnet),
                         ("keystore.json", "0.0.0.0", 8080, True))
        self.assertFalse(config.parse({"hyperliquid_network": "testnet", "limits": LIMITS}).hyperliquid_mainnet)
        with self.assertRaises(ValueError):
            config.parse({"hyperliquid_network": "devnet", "limits": LIMITS})
        self.assertEqual(cfg.limits.max_qty_btc, Decimal("0.01"))

    def test_limits_have_no_defaults(self):
        for field in config.LIMIT_FIELDS:
            with self.subTest(field=field), self.assertRaises(ValueError):
                config.parse({"hyperliquid_network": "mainnet",
                              "limits": {k: v for k, v in LIMITS.items() if k != field}})
        with self.assertRaises(ValueError):
            config.parse({"hyperliquid_network": "mainnet"})

    def test_rejects_non_integers(self):
        for value in (5.5, True, "5.0", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                config.parse({"hyperliquid_network": "mainnet", "limits": {**LIMITS, "max_leverage": value}})

    def test_update(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            # File mới mà thiếu limits → lỗi, không ghi gì
            with self.assertRaises(ValueError):
                config.update(path, "ks.json", {"max_qty_btc": "0.01"})
            self.assertFalse(os.path.exists(path))

            raw = config.update(path, "ks.json", {"max_qty_btc": "0.020", "max_leverage": "3",
                                                  "max_payload_age_ms": "30000", "port": "8081"})
            self.assertEqual(raw["limits"]["max_qty_btc"], "0.020")  # chuỗi, giữ đúng Decimal
            cfg = config.load(path)
            self.assertEqual((cfg.keystore, cfg.port, cfg.limits), ("ks.json", 8081, config.Limits(Decimal("0.020"), 3, 30000)))

            # Field khác giữ nguyên; field cũ bị bỏ
            raw.update(keystore="/elsewhere/ks.json", note="kept", reload_interval_s=900)
            raw["limits"]["max_recv_window_ms"] = 5000
            with open(path, "w", encoding="utf-8") as f:
                json.dump(raw, f)
            raw = config.update(path, "ignored.json", {"max_leverage": "4"})
            self.assertEqual((raw["keystore"], raw["note"], raw["limits"]["max_leverage"]), ("/elsewhere/ks.json", "kept", 4))
            self.assertNotIn("reload_interval_s", raw)
            self.assertNotIn("max_recv_window_ms", raw["limits"])

            for changes in ({"max_leverage": "0"}, {"max_qty_btc": "NaN"}, {"port": "70000"},
                            {"hyperliquid_network": "devnet"}, {"keystore": "x.json"}, {"max_recv_window_ms": "5000"}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    config.update(path, "ks.json", changes)
            self.assertEqual(config.load(path).limits.max_leverage, 4)

    def test_keystore_path(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.json")
            self.assertEqual(config.keystore_path(path), "keystore.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"limits": LIMITS}, f)
            self.assertEqual(config.keystore_path(path), "keystore.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"keystore": "/var/lib/signer/ks.json"}, f)
            self.assertEqual(config.keystore_path(path), "/var/lib/signer/ks.json")


if __name__ == "__main__":
    unittest.main()
