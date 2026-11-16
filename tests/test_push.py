"""Push encryption checked from the device's side (RFC 8291), and the VAPID signature (RFC 8292)."""

import hashlib
import hmac
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from usher import push


def hkdf(salt, ikm, info, n):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:n]


def device_decrypt(body, ua_priv, auth):
    """What Safari does with an incoming push, written independently of usher.push."""
    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    assert rs == 4096 and idlen == 65
    ua_public = ua_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    ikm = hkdf(auth, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


class PushTests(unittest.TestCase):
    def test_round_trip(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        auth = os.urandom(16)
        p256dh = push.b64u(ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        msg = json.dumps({"title": "Example Show is ready to watch", "badge": 3}).encode()
        body = push.encrypt(msg, p256dh, push.b64u(auth))
        self.assertEqual(device_decrypt(body, ua, auth), msg)
        self.assertNotIn(b"Example Show", body)

    def test_vapid_signature_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            v = push.Vapid(Path(d), "https://usher.example")
            self.assertEqual(oct(os.stat(Path(d) / "vapid.pem").st_mode & 0o777), "0o600")
            header = v.header("https://web.push.apple.com/abc")
            token = header.split("t=")[1].split(",")[0]
            head, body, sig = token.split(".")
            claims = json.loads(push.unb64u(body))
            self.assertEqual(claims["aud"], "https://web.push.apple.com")
            raw = push.unb64u(sig)
            der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
            pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.unb64u(v.public))
            pub.verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))  # raises if wrong
            self.assertEqual(push.Vapid(Path(d), "x").public, v.public)  # same key next start

    def test_only_known_push_services(self):
        self.assertTrue(push.endpoint_ok("https://web.push.apple.com/QGuQyavXut"))
        self.assertFalse(push.endpoint_ok("http://web.push.apple.com/x"))
        self.assertFalse(push.endpoint_ok("https://evil.example/web.push.apple.com"))
        self.assertFalse(push.endpoint_ok("https://10.0.0.5:8989/api"))


if __name__ == "__main__":
    unittest.main()
