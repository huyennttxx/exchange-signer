import contextlib
import io
import os
import tempfile
import unittest
from unittest import mock

from signer import config, keyops, menu

PASS = "correct horse battery staple"


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ks = os.path.join(self.dir.name, "ks.json")
        self.cfg = os.path.join(self.dir.name, "config.ini")
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(f"[server]\nkeystore = {self.ks}\n")

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

    def ips(self, i):
        return sorted(config.load(self.cfg).keys[self.keys()[i]["key_id"]].allowed_ips)

    def test_key_lifecycle(self):
        # Thêm key binance, rồi hyperliquid; keystore chưa có → passphrase mới nhập 2 lần. Không hỏi IP
        out = self.run_menu(["2", "1", ""], [PASS, PASS])
        self.assertIn("chưa có IP được phép", out)
        # Mục 1: xem danh sách rồi Enter quay lại → không hỏi passphrase
        out = self.run_menu(["2", "2", "", "1", "", ""], [PASS])
        self.assertIn("kill -HUP", out)
        self.assertEqual([k["venue"] for k in self.keys()], ["binance", "hyperliquid"])
        self.assertEqual((self.ips(0), self.ips(1)), ([], []))
        self.assertIn(self.keys()[1]["key_id"], out)
        self.assertIn("(chưa có IP)", out)

        # Menu không sửa IP: chỉ xem public key, HMAC, xoá. IP sửa tay trong config thì danh sách hiện ra
        out = self.run_menu(["1", "1", "", ""])
        self.assertNotIn("Sửa IP", out)
        with open(self.cfg, encoding="utf-8") as f:
            text = f.read()
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(text.replace("allowed_ips =", "allowed_ips = 10.0.0.1, 10.0.0.2", 1))
        self.assertEqual(self.ips(0), ["10.0.0.1", "10.0.0.2"])
        self.assertIn("10.0.0.1, 10.0.0.2", self.run_menu(["1", ""]))

        # Mục 1: chọn key → xem public key
        out = self.run_menu(["1", "1", "1", ""], [PASS])
        self.assertIn("BEGIN PUBLIC KEY", out)
        # Chọn key rồi Enter → quay lại, không làm gì
        self.run_menu(["1", "1", "", ""])

        # Mục 1: chọn key → tạo HMAC secret mới. Key đã có secret: không gõ yes → huỷ; gõ yes → secret mới
        self.assertTrue(all(k["hmac"] for k in self.keys()))
        self.assertIn("Đã huỷ.", self.run_menu(["1", "1", "2", "no", ""]))
        out = self.run_menu(["1", "1", "2", "yes", ""], [PASS])
        self.assertIn("HMAC secret for " + self.keys()[0]["key_id"], out)
        self.assertIn("kill -HUP", out)

        # Mục 1: chọn key → xoá. Không gõ yes → huỷ; gõ yes → xoá
        self.run_menu(["1", "1", "3", "no", ""])
        self.assertEqual(len(self.keys()), 2)
        out = self.run_menu(["1", "1", "3", "yes", ""], [PASS])
        self.assertIn("thu hồi", out)
        self.assertEqual([k["venue"] for k in self.keys()], ["hyperliquid"])
        self.assertEqual(list(config.load(self.cfg).keys), [self.keys()[0]["key_id"]])

    def test_errors_stay_in_menu(self):
        out = self.run_menu(["x", "1", ""])
        self.assertIn("Chưa có keystore", out)
        self.assertIn("Lựa chọn không hợp lệ.", out)
        self.run_menu(["2", "1", ""], [PASS, PASS])
        # Sai passphrase: báo lỗi rồi quay lại menu, mục sau vẫn chạy
        out = self.run_menu(["1", "1", "1", "", "1", "", ""], ["wrong"])
        self.assertIn("wrong passphrase", out)
        self.assertIn("(cột: key_id", out)
        # Huỷ ở bước chọn sàn khi thêm / nhập key
        self.run_menu(["2", "", "", "3", "", ""])
        self.assertEqual(len(self.keys()), 1)

    def test_menu_has_keys_only(self):
        out = self.run_menu(["4", "5", "6", "7", "8", "9", "r"])
        self.assertEqual(out.count("Lựa chọn không hợp lệ."), 7)
        self.assertNotIn("config", out.split("=====")[-1].lower())

    def test_arguments_run_admin(self):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=["", "9000", "testnet", "", "y"]), \
                contextlib.redirect_stdout(out):
            menu.main(["--config", self.cfg, "config-edit"])
            menu.main(["--config", self.cfg, "config-show"])
        cfg = config.load(self.cfg)
        self.assertEqual((cfg.port, cfg.hyperliquid_mainnet), (9000, False))
        self.assertIn("port                 9000", out.getvalue())


if __name__ == "__main__":
    unittest.main()
