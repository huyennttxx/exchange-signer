import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from signer import config, keyops, menu

PASS = "correct horse battery staple"
LIMITS = {"max_qty_btc": "0.01", "max_leverage": 5, "max_payload_age_ms": 60000}


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ks = os.path.join(self.dir.name, "ks.json")
        self.cfg = os.path.join(self.dir.name, "config.json")
        with open(self.cfg, "w", encoding="utf-8") as f:
            json.dump({"keystore": self.ks, "limits": LIMITS}, f)

    def tearDown(self):
        self.dir.cleanup()

    def run_menu(self, answers, passphrases=()):
        """answers: các dòng gõ vào menu; hết dòng = EOF = thoát."""
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=[*answers, EOFError()]), \
                mock.patch("getpass.getpass", side_effect=list(passphrases)), contextlib.redirect_stdout(out):
            menu.main(["--config", self.cfg])
        return out.getvalue()

    def keys(self):
        return keyops.list_keys(self.ks)

    def test_key_lifecycle(self):
        # Thêm key binance, rồi hyperliquid; keystore chưa có → passphrase mới nhập 2 lần
        self.run_menu(["2", "1", "10.0.0.1, 10.0.0.2", ""], [PASS, PASS])
        # Mục 1: xem danh sách rồi Enter quay lại → không hỏi passphrase
        out = self.run_menu(["2", "2", "10.0.0.3", "", "1", "", ""], [PASS])
        self.assertIn("kill -HUP", out)
        self.assertEqual([k["venue"] for k in self.keys()], ["binance", "hyperliquid"])
        self.assertEqual(self.keys()[0]["allowed_ips"], ["10.0.0.1", "10.0.0.2"])
        self.assertIn(self.keys()[1]["key_id"], out)

        # Mục 1: chọn key thứ 2 → sửa IP; IP sai thì hỏi lại
        out = self.run_menu(["1", "2", "2", "not-an-ip", "10.0.0.9", ""], [PASS])
        self.assertIn("IP hiện tại: 10.0.0.3", out)
        self.assertEqual(self.keys()[1]["allowed_ips"], ["10.0.0.9"])

        # Mục 1: chọn key → xem public key
        out = self.run_menu(["1", "1", "1", ""], [PASS])
        self.assertIn("BEGIN PUBLIC KEY", out)
        # Chọn key rồi Enter → quay lại, không làm gì
        self.run_menu(["1", "1", "", ""])

        # Mục 1: chọn key → tạo HMAC secret mới. Key đã có secret: không gõ yes → huỷ; gõ yes → secret mới
        self.assertTrue(all(k["hmac"] for k in self.keys()))
        self.assertIn("Đã huỷ.", self.run_menu(["1", "1", "3", "no", ""]))
        out = self.run_menu(["1", "1", "3", "yes", ""], [PASS])
        self.assertIn("HMAC secret for " + self.keys()[0]["key_id"], out)
        self.assertIn("kill -HUP", out)

        # Mục 1: chọn key → xoá. Không gõ yes → huỷ; gõ yes → xoá
        self.run_menu(["1", "1", "4", "no", ""])
        self.assertEqual(len(self.keys()), 2)
        out = self.run_menu(["1", "1", "4", "yes", ""], [PASS])
        self.assertIn("thu hồi", out)
        self.assertEqual([k["venue"] for k in self.keys()], ["hyperliquid"])

    def test_errors_stay_in_menu(self):
        out = self.run_menu(["x", "1", ""])
        self.assertIn("Chưa có keystore", out)
        self.assertIn("Lựa chọn không hợp lệ.", out)
        self.run_menu(["2", "1", "10.0.0.1", ""], [PASS, PASS])
        # Sai passphrase: báo lỗi rồi quay lại menu, mục sau vẫn chạy
        out = self.run_menu(["1", "1", "1", "", "1", "", ""], ["wrong"])
        self.assertIn("wrong passphrase", out)
        self.assertIn("(cột: key_id", out)
        # Huỷ ở bước chọn sàn / nhập IP khi sửa / nhập IP khi thêm
        self.run_menu(["2", "", "", "1", "1", "2", "", "", "3", "1", "", ""])
        self.assertEqual(len(self.keys()), 1)
        self.assertEqual(self.keys()[0]["allowed_ips"], ["10.0.0.1"])

    def test_menu_has_keys_only(self):
        out = self.run_menu(["4", "5", "6", "7", "8", "9", "r"])
        self.assertEqual(out.count("Lựa chọn không hợp lệ."), 7)
        self.assertNotIn("config", out.split("=====")[-1].lower())

    def test_arguments_run_admin(self):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=["", "10", "", "", "", "testnet", "y"]), \
                contextlib.redirect_stdout(out):
            menu.main(["--config", self.cfg, "config-edit"])
            menu.main(["--config", self.cfg, "config-show"])
        cfg = config.load(self.cfg)
        self.assertEqual((cfg.limits.max_leverage, cfg.hyperliquid_mainnet), (10, False))
        self.assertIn("max_leverage         10", out.getvalue())


if __name__ == "__main__":
    unittest.main()
