"""Minimal Ed25519 public-key derivation (RFC 8032 formulas) + Proton key wiring.

Only what xProton needs:
  * derive the Ed25519 public key from a 32-byte seed (sent to Proton as PEM
    when requesting a WireGuard client certificate),
  * derive the matching WireGuard private key from the same seed exactly the
    way Proton clients do (clamp(sha512(seed)[:32])), so a single seed governs
    both the certificate and the tunnel key.

Point arithmetic follows the reference formulas in RFC 8032 §5.1
(public-domain specification; this implementation is original).
"""

import hashlib

from .util import b64e

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
I = pow(2, (P - 1) // 4, P)  # sqrt(-1) mod p


def _sha512(s: bytes) -> bytes:
    return hashlib.sha512(s).digest()


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(D * y * y + 1)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * I) % P
    if x % 2 != 0:
        x = P - x
    return x


By = (4 * _inv(5)) % P
Bx = _xrecover(By)
B = (Bx, By, 1, (Bx * By) % P)  # extended coordinates
IDENT = (0, 1, 1, 0)


def _edwards_add(pt1, pt2):
    x1, y1, z1, t1 = pt1
    x2, y2, z2, t2 = pt2
    a = ((y1 - x1) * (y2 - x2)) % P
    b = ((y1 + x1) * (y2 + x2)) % P
    c = (t1 * 2 * D * t2) % P
    dd = (z1 * 2 * z2) % P
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    return ((e * f) % P, (g * h) % P, (f * g) % P, (e * h) % P)


def _scalarmult(pt, scalar: int):
    q = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            q = _edwards_add(q, pt)
        pt = _edwards_add(pt, pt)
        scalar >>= 1
    return q


def _encode_point(pt) -> bytes:
    x, y, z, _ = pt
    zinvinv = _inv(z)
    x = (x * zinvinv) % P
    y = (y * zinvinv) % P
    return ((y | ((x & 1) << 255))).to_bytes(32, "little")


def _clamp(b: bytes) -> bytes:
    # Standard Ed25519/WireGuard scalar clamping.
    b = bytearray(b)
    b[0] &= 248
    b[31] &= 127
    b[31] |= 64
    return bytes(b)


def ed25519_public_key(seed: bytes) -> bytes:
    """32-byte seed -> 32-byte Ed25519 public key."""
    if len(seed) != 32:
        raise ValueError("seed must be 32 bytes")
    h = _sha512(seed)
    a = int.from_bytes(_clamp(h[:32]), "little")
    return _encode_point(_scalarmult(B, a))


def wireguard_private_key(seed: bytes) -> str:
    """Same seed -> base64 WireGuard private key (Proton derivation scheme).

    Proton clients derive the WG key from the certificate key's seed so the
    tunnel key matches the issued certificate: wg_priv = clamp(sha512(seed)[:32]).
    """
    return b64e(_clamp(_sha512(seed)[:32]))


# DER prefix of an Ed25519 SubjectPublicKeyInfo (RFC 8410).
_SPKI_ED25519_PREFIX = bytes.fromhex("302a300506032b6570032100")


def spki_pem(public_key: bytes) -> str:
    """Encode a 32-byte Ed25519 public key as a PEM PUBLIC KEY block."""
    der = _SPKI_ED25519_PREFIX + public_key
    b64 = base64_wrap(der, 64)
    return "-----BEGIN PUBLIC KEY-----\n" + b64 + "\n-----END PUBLIC KEY-----\n"


def base64_wrap(data: bytes, width: int) -> str:
    import base64

    raw = base64.b64encode(data).decode("ascii")
    return "\n".join(raw[i : i + width] for i in range(0, len(raw), width))
