import json
import os
import tempfile
import unittest
from unittest import mock

from signer import keystore

FAST_N = 2**10  # scrypt nhỏ cho test nhanh; tham số nằm trong file nên vẫn đúng đường code thật


class KeystoreTest(unittest.TestCase):
    def setUp(self):
        self.data, self.master = keystore.new_keystore(b"correct horse battery", n=FAST_N)
        self.secret = os.urandom(32)
        rec = {"key_id": "k1", "venue": "binance", "allowed_ips": ["10.0.1.12"]}
        self.data["keys"].append(keystore.seal(self.master, rec, self.secret))

    def test_roundtrip(self):
        master = keystore.open_master(self.data, b"correct horse battery")
        keys = keystore.unlock(self.data, master)
        self.assertEqual(keys["k1"].secret, self.secret)
        self.assertEqual(keys["k1"].allowed_ips, frozenset({"10.0.1.12"}))

    def test_wrong_passphrase(self):
        with self.assertRaises(keystore.BadPassphrase):
            keystore.open_master(self.data, b"wrong")

    def test_tampered_allowed_ips_not_loaded(self):
        self.data["keys"][0]["allowed_ips"].append("6.6.6.6")
        keys = keystore.unlock(self.data, self.master)
        self.assertIsNone(keys["k1"].secret)

    def test_tampered_venue_not_loaded(self):
        self.data["keys"][0]["venue"] = "hyperliquid"
        self.assertIsNone(keystore.unlock(self.data, self.master)["k1"].secret)

    def test_tampered_key_id_not_loaded(self):
        self.data["keys"][0]["key_id"] = "k2"
        self.assertIsNone(keystore.unlock(self.data, self.master)["k2"].secret)

    def test_allowed_ips_order_does_not_matter(self):
        rec = {"key_id": "k2", "venue": "binance", "allowed_ips": ["10.0.0.2", "10.0.0.1"]}
        sealed = keystore.seal(self.master, rec, self.secret)
        sealed["allowed_ips"] = ["10.0.0.1", "10.0.0.2"]
        self.assertEqual(keystore.unseal(self.master, sealed), self.secret)

    def test_hmac_roundtrip_and_legacy_record(self):
        # Bản ghi cũ chưa có hmac: vẫn nạp private key, hmac_key = None (request sẽ bị 503)
        self.assertIsNone(keystore.unlock(self.data, self.master)["k1"].hmac_key)
        self.data["keys"][0] = keystore.seal_hmac(self.master, self.data["keys"][0], b"s" * 64)
        key = keystore.unlock(self.data, self.master)["k1"]
        self.assertEqual((key.secret, key.hmac_key), (self.secret, b"s" * 64))

    def test_hmac_box_bound_to_key_id(self):
        rec2 = keystore.seal(self.master, {"key_id": "k2", "venue": "binance", "allowed_ips": ["10.0.1.12"]},
                             self.secret)
        self.data["keys"].append(keystore.seal_hmac(self.master, rec2, b"s" * 64))
        # Chép box hmac của k2 sang k1 → k1 không nạp được
        self.data["keys"][0]["hmac"] = self.data["keys"][1]["hmac"]
        keys = keystore.unlock(self.data, self.master)
        self.assertIsNone(keys["k1"].secret)
        self.assertIsNone(keys["k1"].hmac_key)
        self.assertEqual(keys["k2"].hmac_key, b"s" * 64)

    def test_malformed_hmac_box_not_loaded(self):
        self.data["keys"][0]["hmac"] = "garbage"
        key = keystore.unlock(self.data, self.master)["k1"]
        self.assertEqual((key.secret, key.hmac_key), (None, None))

    def test_no_plaintext_secret_on_disk(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ks.json")
            keystore.save_atomic(path, self.data)
            with open(path, "rb") as f:
                raw = f.read()
            self.assertNotIn(self.secret.hex().encode(), raw)
            self.assertNotIn(self.secret, raw)

    def test_atomic_write_keeps_old_file_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ks.json")
            keystore.save_atomic(path, self.data)
            with open(path, "rb") as f:
                before = f.read()
            with mock.patch("os.fsync", side_effect=OSError("disk gone")):
                with self.assertRaises(OSError):
                    keystore.save_atomic(path, {**self.data, "keys": []})
            with open(path, "rb") as f:
                self.assertEqual(f.read(), before)
            self.assertEqual(os.listdir(d), ["ks.json"])  # file tạm đã được dọn
            self.assertEqual(len(json.loads(before)["keys"]), 1)


if __name__ == "__main__":
    unittest.main()
