import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from signer import admin, config, keystore

PASS = "correct horse battery staple"


def run(argv, answers):
    out = io.StringIO()
    with mock.patch("getpass.getpass", side_effect=answers), contextlib.redirect_stdout(out):
        admin.main(argv)
    return out.getvalue()


class AdminTest(unittest.TestCase):
    def test_generate_creates_keystore_on_first_use(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "ks.json")
            out = run(["--keystore", path, "generate", "--venue", "binance", "--allowed-ip", "10.0.1.12"],
                      [PASS, PASS])  # passphrase mới, nhập 2 lần
            self.assertIn("creating a new keystore", out)
            self.assertIn("BEGIN PUBLIC KEY", out)
            printed = out.splitlines()[-1]  # HMAC secret in ra một lần, phải khớp với bản trong keystore

            # Lần sau: keystore đã có → chỉ hỏi passphrase 1 lần
            run(["--keystore", path, "generate", "--venue", "hyperliquid", "--allowed-ip", "10.0.1.12"], [PASS])

            data = keystore.load(path)
            keys = keystore.unlock(data, keystore.open_master(data, PASS.encode()))
            self.assertEqual(sorted(k.venue for k in keys.values()), ["binance", "hyperliquid"])
            self.assertTrue(all(k.secret is not None for k in keys.values()))
            self.assertIn(printed.encode(), {k.hmac_key for k in keys.values()})
            self.assertNotEqual(*[k.hmac_key for k in keys.values()])  # mỗi key một secret
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(printed, f.read())
            self.assertNotIn("no HMAC", run(["--keystore", path, "list"], []))

    def test_first_use_rejects_mismatched_passphrase(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ks.json")
            with self.assertRaises(SystemExit):
                run(["--keystore", path, "generate", "--venue", "binance", "--allowed-ip", "10.0.1.12"],
                    [PASS, PASS + "x"])
            self.assertFalse(os.path.exists(path))

    def test_existing_keystore_wrong_passphrase(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ks.json")
            run(["--keystore", path, "init"], [PASS, PASS])
            with self.assertRaises(SystemExit):
                run(["--keystore", path, "generate", "--venue", "binance", "--allowed-ip", "10.0.1.12"], ["wrong"])
            self.assertEqual(keystore.load(path)["keys"], [])

    def test_keystore_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            os.chdir(d)
            try:
                # Không có config.json → keystore.json
                run(["init"], [PASS, PASS])
                self.assertTrue(os.path.exists("keystore.json"))
                # Config ghi keystore khác → dùng keystore đó
                with open("other.json", "w", encoding="utf-8") as f:
                    json.dump({"keystore": "from-config.json"}, f)
                run(["--config", "other.json", "init"], [PASS, PASS])
                self.assertTrue(os.path.exists("from-config.json"))
            finally:
                os.chdir(cwd)

    def test_config_commands(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.json")
            base = ["--config", cfg, "--keystore", "ks.json"]
            self.assertIn("(not found)", run([*base, "config-show"], []))
            with self.assertRaises(SystemExit):  # thiếu limits
                run([*base, "config-set", "max_leverage=3"], [])
            with self.assertRaises(SystemExit):
                run([*base, "config-set", "max_leverage"], [])

            # config-edit trên file mới: Enter giữ giá trị gợi ý, limits phải nhập
            with mock.patch("builtins.input", side_effect=["0.01", "3", "60000", "", "", "", "y"]):
                run([*base, "config-edit"], [])
            c = config.load(cfg)
            self.assertEqual((c.keystore, c.host, c.port, c.hyperliquid_mainnet, c.limits.max_leverage),
                             ("ks.json", "0.0.0.0", 8080, True, 3))

            run([*base, "config-set", "max_leverage=5", "hyperliquid_network=testnet"], [])
            self.assertFalse(config.load(cfg).hyperliquid_mainnet)
            out = run([*base, "config-show"], [])
            self.assertIn("max_leverage         5", out)
            self.assertNotIn("INVALID", out)

            # Huỷ ở bước xác nhận → không ghi
            with mock.patch("builtins.input", side_effect=["", "10", "", "", "", "", "n"]):
                self.assertIn("cancelled", run([*base, "config-edit"], []))
            self.assertEqual(config.load(cfg).limits.max_leverage, 5)

            # File chỉ có limits: Enter ở field server → dùng mặc định, không ghi chuỗi rỗng
            with open(cfg, "w", encoding="utf-8") as f:
                json.dump({"limits": {"max_qty_btc": "0.01", "max_leverage": 5, "max_payload_age_ms": 60000}}, f)
            with mock.patch("builtins.input", side_effect=["", "10", "", "", "", "", "y"]):
                run([*base, "config-edit"], [])
            c = config.load(cfg)
            self.assertEqual((c.limits.max_leverage, c.host, c.port, c.hyperliquid_mainnet), (10, "0.0.0.0", 8080, True))

    def test_other_commands_do_not_create(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ks.json")
            for argv in (["list"], ["remove", "x"], ["set-ips", "x", "10.0.0.1"]):
                with self.subTest(argv=argv), self.assertRaises(SystemExit):
                    run(["--keystore", path, *argv], [PASS])
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
