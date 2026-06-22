"""Тесты шифрования туннеля backend/p2p/crypto_box.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.p2p.crypto_box import generate_identity, Identity, SessionCrypto


class TestCryptoBox(unittest.TestCase):
    def setUp(self):
        priv_a, pub_a = generate_identity()
        priv_b, pub_b = generate_identity()
        self.ident_a = Identity(priv_a)
        self.ident_b = Identity(priv_b)

    def test_public_b64_matches_generated(self):
        priv, pub = generate_identity()
        self.assertEqual(Identity(priv).public_b64, pub)

    def test_roundtrip_encrypt_decrypt(self):
        ca = SessionCrypto.derive(self.ident_a, self.ident_b.public_b64)
        cb = SessionCrypto.derive(self.ident_b, self.ident_a.public_b64)
        msg = b"minecraft packet payload"
        self.assertEqual(cb.decrypt(ca.encrypt(msg)), msg)

    def test_both_sides_derive_same_shared_key(self):
        ca = SessionCrypto.derive(self.ident_a, self.ident_b.public_b64)
        cb = SessionCrypto.derive(self.ident_b, self.ident_a.public_b64)
        msg = b"ping"
        # Шифруем с обеих сторон — должно расшифровываться у партнёра
        self.assertEqual(cb.decrypt(ca.encrypt(msg)), msg)
        self.assertEqual(ca.decrypt(cb.encrypt(msg)), msg)

    def test_tampered_ciphertext_rejected(self):
        ca = SessionCrypto.derive(self.ident_a, self.ident_b.public_b64)
        cb = SessionCrypto.derive(self.ident_b, self.ident_a.public_b64)
        ct = bytearray(ca.encrypt(b"hello"))
        ct[-1] ^= 0x01  # портим последний байт (тег MAC)
        self.assertIsNone(cb.decrypt(bytes(ct)))

    def test_wrong_peer_key_cannot_decrypt(self):
        _, pub_c = generate_identity()
        ca = SessionCrypto.derive(self.ident_a, self.ident_b.public_b64)
        # b расшифровывает с ключом не от a, а от случайного третьего — должно провалиться
        cb_wrong = SessionCrypto.derive(self.ident_b, pub_c)
        self.assertIsNone(cb_wrong.decrypt(ca.encrypt(b"secret")))

    def test_each_encryption_uses_fresh_nonce(self):
        ca = SessionCrypto.derive(self.ident_a, self.ident_b.public_b64)
        ct1 = ca.encrypt(b"same message")
        ct2 = ca.encrypt(b"same message")
        self.assertNotEqual(ct1, ct2)  # разный nonce -> разный шифротекст


if __name__ == "__main__":
    unittest.main()
