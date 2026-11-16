"""Web Push: VAPID signing (RFC 8292) and payload encryption (RFC 8291, aes128gcm).

Payloads are encrypted for each device, so Apple's push service only ever sees ciphertext.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .files import write_private

log = logging.getLogger("usher.push")

# Push services Usher will deliver to. Anything else is refused, so a subscription can't point Usher elsewhere.
PUSH_HOSTS = (".push.apple.com", "fcm.googleapis.com", ".push.services.mozilla.com", ".notify.windows.com")


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def endpoint_ok(url: str) -> bool:
    u = urllib.parse.urlsplit(url)
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h.lstrip(".") or host.endswith(h) for h in PUSH_HOSTS) and len(url) < 1024


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    out, block, i = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
        i += 1
    return out[:length]


def _raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes = None, private=None) -> bytes:
    """RFC 8291 aes128gcm body for one device. `salt` and `private` are only passed in tests."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    as_key = private or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(as_key)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf_expand(_hkdf_extract(auth_secret, shared), b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # \x02: last (and only) record
    header = salt + struct.pack(">I", 4096) + bytes([len(as_public)]) + as_public
    return header + ciphertext


class Vapid:
    """The server's own push identity: one P-256 key, made on first start and kept in the data folder."""

    def __init__(self, data_dir: Path, contact: str):
        path = data_dir / "vapid.pem"
        if path.exists():
            self.key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            write_private(path, pem)
            log.info("made a new push key")
        self.public = b64u(_raw_public(self.key))
        self.contact = contact
        self._jwt: dict = {}

    def header(self, endpoint: str) -> str:
        u = urllib.parse.urlsplit(endpoint)
        aud = f"{u.scheme}://{u.netloc}"
        cached = self._jwt.get(aud)
        if cached and cached[1] - time.time() > 3600:
            return cached[0]
        exp = int(time.time()) + 12 * 3600
        head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
        body = b64u(json.dumps({"aud": aud, "exp": exp, "sub": self.contact}, separators=(",", ":")).encode())
        der = self.key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        token = f"{head}.{body}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
        value = f"vapid t={token}, k={self.public}"
        self._jwt[aud] = (value, exp)
        return value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def deliver(vapid: Vapid, sub, message: dict, ttl: int = 86400, topic: str = None) -> int:
    """Send one encrypted notification. Returns the push service's HTTP status (0 if it couldn't be reached)."""
    if not endpoint_ok(sub["endpoint"]):
        return 400
    body = encrypt(json.dumps(message, separators=(",", ":")).encode(), sub["p256dh"], sub["auth"])
    headers = {
        "TTL": str(ttl),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "Authorization": vapid.header(sub["endpoint"]),
        "Urgency": "normal",
    }
    if topic:
        headers["Topic"] = b64u(hashlib.sha256(topic.encode()).digest())[:32]
    req = urllib.request.Request(sub["endpoint"], data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except (urllib.error.URLError, OSError) as e:
        log.warning("push service unreachable: %s", e)
        return 0
