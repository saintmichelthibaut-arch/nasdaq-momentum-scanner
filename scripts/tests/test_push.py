"""Vérifie le chiffrement Web Push en jouant le rôle du téléphone (déchiffrement RFC 8291)."""

import json
import os
import sys
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import push  # noqa: E402


class WebPush(unittest.TestCase):
    def test_roundtrip_decrypt(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        ua_pub = push.raw_public(ua)
        auth = os.urandom(16)
        msg = {"title": "Test é", "body": "ligne"}
        blob = push.encrypt(json.dumps(msg).encode(), push.b64u(ua_pub), push.b64u(auth))
        salt, idlen = blob[:16], blob[20]
        as_pub = blob[21:21 + idlen]
        shared = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = HKDF(hashes.SHA256(), 32, salt=auth, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
        cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, blob[21 + idlen:], None)
        self.assertEqual(plain[-1:], b"\x02")
        self.assertEqual(json.loads(plain[:-1]), msg)

    def test_vapid_is_stable_and_valid(self):
        os.environ["PUSH_SEED"] = "phrase de test"
        k1, k2 = push.vapid_key(), push.vapid_key()
        self.assertEqual(push.raw_public(k1), push.raw_public(k2))
        hdr = push.vapid_header("https://web.push.apple.com/abc", k1)
        token = hdr.split("t=")[1].split(",")[0]
        h, c, s = token.split(".")
        sig = push.b64u_dec(s)
        der = encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        k1.public_key().verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))
        self.assertEqual(json.loads(push.b64u_dec(c))["aud"], "https://web.push.apple.com")
        del os.environ["PUSH_SEED"]


if __name__ == "__main__":
    unittest.main()
