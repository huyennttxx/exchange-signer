import json
import os
import tempfile
import unittest

from signer import keyops, keystore
from signer.server import KeyReloader, Signer
from tests.helpers import LIMITS

PASS = b"correct horse battery staple"


class ReloadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "ks.json")
        data, master = keystore.new_keystore(PASS, n=2**10)
        keystore.save_atomic(self.path, data)
        self.first = keyops.add(self.path, PASS, "binance", ["10.0.1.12"])["key_id"]
        data = keystore.load(self.path)
        self.signer = Signer(keystore.unlock(data, master), LIMITS, True)
        self.reloader = KeyReloader(self.path, master, self.signer)

    def tearDown(self):
        self.dir.cleanup()

    def test_unchanged_file_is_not_reloaded(self):
        keys = self.signer.keys
        self.assertFalse(self.reloader.reload_if_changed())
        self.assertIs(self.signer.keys, keys)

    def test_add_set_ips_remove(self):
        second = keyops.add(self.path, PASS, "hyperliquid", ["10.0.1.13"])["key_id"]
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertIsNotNone(self.signer.keys[second].secret)

        keyops.set_ips(self.path, PASS, second, ["10.0.2.1"])
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertEqual(self.signer.keys[second].allowed_ips, frozenset({"10.0.2.1"}))

        keyops.remove(self.path, PASS, self.first)
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertEqual(set(self.signer.keys), {second})

    def test_hmac_kept_across_set_ips_and_passphrase_change(self):
        hmac_key = self.signer.keys[self.first].hmac_key
        self.assertEqual(len(hmac_key), 64)
        keyops.set_ips(self.path, PASS, self.first, ["10.0.2.1"])
        self.assertTrue(self.reloader.reload_if_changed())
        self.assertEqual(self.signer.keys[self.first].hmac_key, hmac_key)

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
        data["keys"][0]["allowed_ips"].append("6.6.6.6")
        keystore.save_atomic(self.path, data)
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
