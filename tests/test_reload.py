import json
import os
import tempfile
import unittest
from unittest import mock

from signer import config, keyops, keystore, server
from signer.server import KeyReloader, Signer

PASS = b"correct horse battery staple"


class ReloadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "ks.json")
        self.cfg = os.path.join(self.dir.name, "config.ini")
        data, master = keystore.new_keystore(PASS, n=2**10)
        keystore.save_atomic(self.path, data)
        self.first = self.add("binance")
        data = keystore.load(self.path)
        self.signer = Signer(keystore.unlock(data, master), config.load(self.cfg))
        self.reloader = KeyReloader(self.path, self.cfg, master, self.signer)

    def tearDown(self):
        self.dir.cleanup()

    def add(self, venue) -> str:
        key_id = keyops.add(self.path, PASS, venue)["key_id"]
        config.add_key_section(self.cfg, self.path, key_id)
        return key_id

    def edit_config(self, old: str, new: str) -> None:
        """Sửa tay config, như người vận hành."""
        with open(self.cfg, encoding="utf-8") as f:
            text = f.read()
        assert old in text
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(text.replace(old, new, 1))

    def test_unchanged_files_are_not_reloaded(self):
        keys = self.signer.keys
        self.assertFalse(self.reloader.reload_if_changed())
        self.assertIs(self.signer.keys, keys)

    def test_add_edit_ips_remove(self):
        second = self.add("hyperliquid")
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertIsNotNone(self.signer.keys[second].secret)
        self.assertIn(second, self.signer.state.rules)

        # Chỉ sửa config cũng nạp lại
        self.edit_config(f"[key:{second}]\nallowed_ips =", f"[key:{second}]\nallowed_ips = 10.0.2.1")
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertEqual(self.signer.state.cfg.keys[second].allowed_ips, frozenset({"10.0.2.1"}))

        keyops.remove(self.path, PASS, self.first)
        config.remove_key_section(self.cfg, self.first)
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertEqual(set(self.signer.keys), {second})
        self.assertEqual(set(self.signer.state.cfg.keys), {second})

    def test_server_fields_need_restart(self):
        config.update(self.cfg, self.path, {"port": "9090"})
        with self.assertLogs("signer", "WARNING") as logs:
            self.assertTrue(self.reloader.reload_if_changed())
        self.assertIn("restart", "\n".join(logs.output))
        self.assertEqual(self.signer.state.cfg.port, 8080)

    def test_broken_config_keeps_current_state(self):
        state = self.signer.state
        with open(self.cfg, "a", encoding="utf-8") as f:
            f.write("\n[oops]\n")
        with self.assertLogs("signer", "ERROR"):
            self.assertFalse(self.reloader.reload_if_changed())
        self.assertIs(self.signer.state, state)

    def test_hmac_kept_across_passphrase_change(self):
        hmac_key = self.signer.keys[self.first].hmac_key
        self.assertEqual(len(hmac_key), 64)
        keyops.change_passphrase(self.path, PASS, b"another long passphrase")
        data = keystore.load(self.path)
        keys = keystore.unlock(data, keystore.open_master(data, b"another long passphrase"))
        self.assertEqual(keys[self.first].hmac_key, hmac_key)

    def test_rotate_hmac(self):
        old = self.signer.keys[self.first].hmac_key
        new = keyops.rotate_hmac(self.path, PASS, self.first)
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertEqual(self.signer.keys[self.first].hmac_key, new.encode())
        self.assertNotEqual(old, new.encode())
        self.assertIsNotNone(self.signer.keys[self.first].secret)
        with self.assertRaises(keyops.NotFound):
            keyops.rotate_hmac(self.path, PASS, "missing")

    def test_tampered_record_not_loaded(self):
        data = keystore.load(self.path)
        data["keys"][0]["venue"] = "hyperliquid"
        keystore.save_atomic(self.path, data)
        with self.assertLogs("signer", "ERROR"):
            self.assertTrue(self.reloader.reload_if_changed())
        self.assertIsNone(self.signer.keys[self.first].secret)

    def test_changed_passphrase_keeps_current_keys(self):
        keys = self.signer.keys
        keyops.change_passphrase(self.path, PASS, b"another long passphrase")
        with self.assertLogs("signer", "ERROR"):
            self.assertFalse(self.reloader.reload_if_changed())
        self.assertIs(self.signer.keys, keys)
        # Không báo lỗi lặp lại cho cùng một phiên bản file
        self.assertFalse(self.reloader.reload_if_changed())

    def test_broken_or_missing_file_keeps_current_keys(self):
        keys = self.signer.keys
        with open(self.path, "w") as f:
            f.write("{not json")
        with self.assertLogs("signer", "ERROR"):
            self.assertFalse(self.reloader.reload_if_changed())
        os.remove(self.path)
        with self.assertLogs("signer", "ERROR"):
            self.assertFalse(self.reloader.reload_if_changed())
        self.assertIs(self.signer.keys, keys)

    def test_main_refuses_unknown_hyperliquid_symbol(self):
        hl = self.add("hyperliquid")
        section = config.new_key_section(hl)
        self.edit_config(section, section.replace("BTCUSDC = 0.1", "NOPEUSDC = 0.1"))
        with mock.patch.object(server, "read_passphrase", return_value=PASS), \
                self.assertLogs("signer", "ERROR") as logs:
            self.assertEqual(server.main(["--config", self.cfg]), 1)
        self.assertIn(f"[key:{hl}]", "\n".join(logs.output))

    def test_wrong_version_keeps_current_keys(self):
        data = keystore.load(self.path)
        data["version"] = 99
        with open(self.path, "w") as f:
            json.dump(data, f)
        with self.assertLogs("signer", "ERROR"):
            self.assertFalse(self.reloader.reload_if_changed())
        self.assertIn(self.first, self.signer.keys)


if __name__ == "__main__":
    unittest.main()
