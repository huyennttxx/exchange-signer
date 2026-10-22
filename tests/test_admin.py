import contextlib
import io
import os
import tempfile
import unittest
from decimal import Decimal
from unittest import mock

from signer import admin, config, keystore

PASS = "correct horse battery staple"


def run(argv, answers):
    out = io.StringIO()
    with mock.patch("getpass.getpass", side_effect=answers), contextlib.redirect_stdout(out):
        admin.main(argv)
    return out.getvalue()


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(self.dir.name, "config.ini")
        self.ks = os.path.join(self.dir.name, "sub", "ks.json")
        self.base = ["--config", self.cfg, "--keystore", self.ks]

    def tearDown(self):
        self.dir.cleanup()

    def test_generate_creates_keystore_and_config_section(self):
        out = run([*self.base, "generate", "--venue", "binance"], [PASS, PASS])  # passphrase mới, nhập 2 lần
        self.assertIn("creating a new keystore", out)
        self.assertIn("BEGIN PUBLIC KEY", out)
        printed = out.split("shown only once; lost → rotate-hmac):\n")[1].splitlines()[0]

        # Lần sau: keystore đã có → chỉ hỏi passphrase 1 lần
        run([*self.base, "generate", "--venue", "hyperliquid"], [PASS])

        data = keystore.load(self.ks)
        keys = keystore.unlock(data, keystore.open_master(data, PASS.encode()))
        self.assertEqual(sorted(k.venue for k in keys.values()), ["binance", "hyperliquid"])
        self.assertTrue(all(k.secret is not None for k in keys.values()))
        self.assertIn(printed.encode(), {k.hmac_key for k in keys.values()})
        self.assertNotEqual(*[k.hmac_key for k in keys.values()])  # mỗi key một secret
        with open(self.ks, encoding="utf-8") as f:
            self.assertNotIn(printed, f.read())

        # Mỗi key một section: chưa có IP (sửa tay trong config), max_leverage 40, BTCUSDC 0.1
        self.assertIn("fill in allowed_ips", out)
        cfg = config.load(self.cfg)
        self.assertEqual(cfg.keystore, self.ks)
        by_venue = {k.venue: cfg.keys[k.key_id] for k in keys.values()}
        self.assertEqual(by_venue["binance"], config.KeyRule(frozenset(), 40, {"BTCUSDC": Decimal("0.1")}))
        self.assertEqual(by_venue["hyperliquid"], config.KeyRule(frozenset(), 40, {"BTCUSDC": Decimal("0.1")}))
        out = run([*self.base, "list"], [])
        self.assertNotIn("no HMAC", out)
        self.assertIn("(no IP)", out)
        with self.assertRaises(SystemExit):  # IP không còn là tham số
            run([*self.base, "generate", "--venue", "binance", "--allowed-ip", "10.0.1.12"], [PASS])

    def test_config_write_failure_keeps_key(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("[oops]\n")
        out = run([*self.base, "generate", "--venue", "binance"], [PASS, PASS])
        self.assertIn("Add this section by hand", out)
        self.assertEqual(len(keystore.load(self.ks)["keys"]), 1)

    def test_remove(self):
        run([*self.base, "generate", "--venue", "binance"], [PASS, PASS])
        key_id = keystore.load(self.ks)["keys"][0]["key_id"]
        out = run([*self.base, "remove", key_id], [PASS])
        self.assertIn(f"removed [key:{key_id}]", out)
        self.assertEqual(config.load(self.cfg).keys, {})
        self.assertEqual(keystore.load(self.ks)["keys"], [])

    def test_first_use_rejects_mismatched_passphrase(self):
        with self.assertRaises(SystemExit):
            run([*self.base, "generate", "--venue", "binance"], [PASS, PASS + "x"])
        self.assertFalse(os.path.exists(self.ks))

    def test_existing_keystore_wrong_passphrase(self):
        run([*self.base, "init"], [PASS, PASS])
        with self.assertRaises(SystemExit):
            run([*self.base, "generate", "--venue", "binance"], ["wrong"])
        self.assertEqual(keystore.load(self.ks)["keys"], [])

    def test_keystore_defaults(self):
        cwd = os.getcwd()
        os.chdir(self.dir.name)
        try:
            # Không có config.ini → keystore.json
            run(["init"], [PASS, PASS])
            self.assertTrue(os.path.exists("keystore.json"))
            # Config ghi keystore khác → dùng keystore đó
            with open("other.ini", "w", encoding="utf-8") as f:
                f.write("[server]\nkeystore = from-config.json\n")
            run(["--config", "other.ini", "init"], [PASS, PASS])
            self.assertTrue(os.path.exists("from-config.json"))
        finally:
            os.chdir(cwd)

    def test_config_commands(self):
        base = ["--config", self.cfg, "--keystore", "ks.json"]
        self.assertIn("(not found)", run([*base, "config-show"], []))
        with self.assertRaises(SystemExit):
            run([*base, "config-set", "max_leverage=3"], [])  # không còn là field [server]
        with self.assertRaises(SystemExit):
            run([*base, "config-set", "port"], [])

        # config-edit trên file mới: Enter giữ giá trị mặc định
        with mock.patch("builtins.input", side_effect=["", "8081", "", "30000", "y"]):
            run([*base, "config-edit"], [])
        c = config.load(self.cfg)
        self.assertEqual((c.keystore, c.host, c.port, c.hyperliquid_mainnet, c.max_payload_age_ms),
                         ("ks.json", "0.0.0.0", 8081, True, 30000))

        run([*base, "config-set", "port=9000", "hyperliquid_network=testnet"], [])
        self.assertFalse(config.load(self.cfg).hyperliquid_mainnet)
        out = run([*base, "config-show"], [])
        self.assertIn("port                 9000", out)
        self.assertNotIn("INVALID", out)

        # Huỷ ở bước xác nhận → không ghi
        with mock.patch("builtins.input", side_effect=["", "10", "", "", "n"]):
            self.assertIn("cancelled", run([*base, "config-edit"], []))
        self.assertEqual(config.load(self.cfg).port, 9000)

        # config-show hiện section key, và key trong keystore chưa có section
        with open(self.cfg, "a", encoding="utf-8") as f:
            f.write("\n[key:k1]\nallowed_ips = 10.0.0.1\nmax_leverage = 2\nBTCUSDT = 0.1\n")
        out = run([*base, "config-show"], [])
        self.assertIn("[key:k1]", out)
        self.assertIn("BTCUSDT              0.1", out)
        run(["--config", self.cfg, "--keystore", self.ks, "generate", "--venue", "binance"], [PASS, PASS])
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("[server]\n")
        self.assertIn("no [key:…] section, cannot sign", run(["--config", self.cfg, "--keystore", self.ks,
                                                              "config-show"], []))
        self.assertIn("(no config section)", run(["--config", self.cfg, "--keystore", self.ks, "list"], []))

    def test_other_commands_do_not_create(self):
        for argv in (["list"], ["remove", "x"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                run([*self.base, *argv], [PASS])
        self.assertFalse(os.path.exists(self.ks))
        self.assertFalse(os.path.exists(self.cfg))


if __name__ == "__main__":
    unittest.main()
