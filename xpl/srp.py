"""Proton SRP authentication (client side), implemented from the protocol.

Algorithm reference: Proton's MIT-licensed `go-srp` library
(https://github.com/ProtonMail/go-srp, MIT (c) 2019 Proton Technologies AG)
and Proton's MIT-licensed `bcrypt` fork. This is an independent Python
implementation — no Go code is copied.

Protocol facts implemented here:
  * expandHash(data) = SHA512(data+0x00) || SHA512(data+0x01)
                       || SHA512(data+0x02) || SHA512(data+0x03)  (256 bytes)
  * all SRP big integers are little-endian
  * generator g = 2, modulus is a 2048-bit safe prime fetched per-login
  * password hashing versions:
      v1/v2: bcrypt(md5(lower(username)) hex used as salt, cost 10)
      v3/v4: bcrypt(base64dotslash(salt + b"proton"), cost 10)
    in both cases -> expandHash(bcrypt_output_bytes || modulus), and the
    bcrypt salt is the FIRST 22 characters of the encoded string (Proton's
    hasher consumes exactly 22 salt characters).
  * bcrypt = $2y$10$ EksBlowfish; the Blowfish P-array/S-boxes are the
    hexadecimal digits of pi (computed here with the Machin formula, so no
    constants are transcribed by hand).
"""

import hashlib
import secrets

# ---------------------------------------------------------------------------
# Blowfish constants derived from the hex digits of pi (Machin formula).
# ---------------------------------------------------------------------------
def _atan_inv(x: int, one: int) -> int:
    """atan(1/x) * one using integer arithmetic."""
    total = term = one // x
    x2 = x * x
    k = 3
    sign = -1
    while term:
        term //= x2
        if term:
            total += sign * (term // k)
        sign = -sign
        k += 2
    return total


def _pi_hex_digits(n_words: int) -> list:
    """First n_words 32-bit words of the fractional hex digits of pi."""
    hex_per_word = 8  # 32-bit word
    guard = 16  # hex digits of guard against truncation error
    one = 16 ** (hex_per_word * n_words + guard)
    # Machin: pi = 16*atan(1/5) - 4*atan(1/239)
    pi_scaled = 16 * _atan_inv(5, one) - 4 * _atan_inv(239, one)
    # pi_scaled ~= floor(pi * 16^(8n+g)); pi < 4 so hex is "3.243F6A88..."
    digits = format(pi_scaled, "x")  # includes integer part "3"
    frac = digits[1 : 1 + hex_per_word * n_words]  # fractional hex digits
    words = [
        int(frac[hex_per_word * i : hex_per_word * (i + 1)], 16)
        for i in range(n_words)
    ]
    return words


_BF_P, _BF_S = None, None


def _blowfish_constants():
    global _BF_P, _BF_S
    if _BF_P is None:
        words = _pi_hex_digits(18 + 4 * 256)
        _BF_P = words[:18]
        _BF_S = [words[18 + 256 * i : 18 + 256 * (i + 1)] for i in range(4)]
    return _BF_P, _BF_S


_M32 = 0xFFFFFFFF


class _Blowfish:
    __slots__ = ("P", "S")

    def __init__(self):
        P, S = _blowfish_constants()
        self.P = list(P)
        self.S = [list(box) for box in S]

    def _f(self, x: int) -> int:
        s0, s1, s2, s3 = self.S
        return (
            ((s0[x >> 24] + s1[(x >> 16) & 0xFF]) & _M32) ^ s2[(x >> 8) & 0xFF]
        ) + s3[x & 0xFF] & _M32

    def encipher(self, l: int, r: int):
        P, F = self.P, self._f
        for i in range(16):
            l ^= P[i]
            r ^= F(l)
            l, r = r, l
        l, r = r, l
        r ^= P[16]
        l ^= P[17]
        return l & _M32, r & _M32

    def _stream_word(self, data: bytes, pos: int) -> int:
        return int.from_bytes(data[pos : pos + 4], "big")

    def _expand_key_phase(self, key: bytes, salt: bytes | None):
        """OpenBSD expand: xor P with cycled key words, then ECB-fill P and S,
        xoring cycled salt words when salt is given."""
        P = self.P
        klen = len(key)
        j = 0
        for i in range(18):
            w = 0
            for b in range(4):
                w = (w << 8) | key[(j + b) % klen]
            P[i] ^= w
            j = (j + 4) % klen
        slen = len(salt) if salt else 0
        sj = 0
        l = r = 0
        for i in range(0, 18, 2):
            if salt:
                w1 = 0
                w2 = 0
                for b in range(4):
                    w1 = (w1 << 8) | salt[(sj + b) % slen]
                    w2 = (w2 << 8) | salt[(sj + 4 + b) % slen]
                sj = (sj + 8) % slen
                l ^= w1
                r ^= w2
            l, r = self.encipher(l, r)
            P[i], P[i + 1] = l, r
        for box in self.S:
            for i in range(0, 256, 2):
                if salt:
                    w1 = 0
                    w2 = 0
                    for b in range(4):
                        w1 = (w1 << 8) | salt[(sj + b) % slen]
                        w2 = (w2 << 8) | salt[(sj + 4 + b) % slen]
                    sj = (sj + 8) % slen
                    l ^= w1
                    r ^= w2
                l, r = self.encipher(l, r)
                box[i], box[i + 1] = l, r

    def eks_blowfish_setup(self, key: bytes, salt: bytes, cost: int):
        """EksBlowfishSetup as used by bcrypt ($2a/$2y)."""
        self._expand_key_phase(key, salt)
        for _ in range(1 << cost):
            self._expand_key_phase(key, None)
            self._expand_key_phase(salt, None)


_B64_ALT = "./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _b64_alt_encode(data: bytes) -> str:
    out = []
    acc = 0
    bits = 0
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 6:
            bits -= 6
            out.append(_B64_ALT[(acc >> bits) & 0x3F])
    if bits:
        out.append(_B64_ALT[(acc << (6 - bits)) & 0x3F])
    return "".join(out)


def _b64_alt_decode(s: str) -> bytes:
    table = {c: i for i, c in enumerate(_B64_ALT)}
    acc = 0
    bits = 0
    out = bytearray()
    for ch in s:
        acc = (acc << 6) | table[ch]
        bits += 6
        if bits >= 8:
            bits -= 8
            out.append((acc >> bits) & 0xFF)
    return bytes(out)


def _bcrypt_hashbytes(password: bytes, encoded_salt_22: str, rounds: int = 10) -> bytes:
    """Raw bcrypt: returns the 23-byte (truncated) digest."""
    salt = _b64_alt_decode(encoded_salt_22)
    key = password + b"\x00"
    bf = _Blowfish()
    bf.eks_blowfish_setup(key, salt, rounds)
    # Encrypt "OrpheanBeholderScryDoubt" 64 times (ECB).
    magic = b"OrpheanBeholderScryDoubt"
    blocks = [int.from_bytes(magic[i : i + 8], "big") for i in (0, 8, 16)]
    for _ in range(64):
        for idx in range(3):
            l = (blocks[idx] >> 32) & _M32
            r = blocks[idx] & _M32
            l, r = bf.encipher(l, r)
            blocks[idx] = (l << 32) | r
    out = b"".join(b.to_bytes(8, "big") for b in blocks)[:23]
    return out


def bcrypt_y10(password: bytes, encoded_salt: str) -> bytes:
    """Proton's bcryptHash: '$2y$10$' + salt (Proton's fork of golang.org/x/crypto/bcrypt
    semantics). Returns the FULL 60-char string form because go-srp compares against
    the full string; here we return the raw digest for hashing into SRP."""
    salt22 = encoded_salt[:22]
    digest = _bcrypt_hashbytes(password, salt22, 10)
    return f"$2y$10${salt22}{_b64_alt_encode(digest)}".encode("ascii")


# ---------------------------------------------------------------------------
# expandHash + password hashing
# ---------------------------------------------------------------------------
def expand_hash(data: bytes) -> bytes:
    out = b""
    for i in range(4):
        out += hashlib.sha512(data + bytes([i])).digest()
    return out


def hash_password_v3(password: bytes, salt: bytes, modulus: bytes) -> bytes:
    """Versions 3 and 4. salt = raw decoded salt bytes from the API."""
    encoded_salt = _b64_alt_encode(salt + b"proton")
    crypted = bcrypt_y10(password, encoded_salt)
    return expand_hash(crypted + modulus)


def hash_password_v1(password: bytes, username: str, modulus: bytes) -> bytes:
    """Versions 1 and 2 (v2 = v1 with cleaned username, handled by caller)."""
    prehashed = hashlib.md5(username.lower().encode("utf-8")).hexdigest()
    crypted = bcrypt_y10(password, prehashed)
    return expand_hash(crypted + modulus)


def clean_username(username: str) -> str:
    return (
        username.replace("-", "").replace(".", "").replace("_", "").lower()
    )


def hash_password(
    version: int, password: bytes, username: str, salt: bytes, modulus: bytes
) -> bytes:
    if version in (3, 4):
        return hash_password_v3(password, salt, modulus)
    if version in (1, 2):
        return hash_password_v1(password, clean_username(username), modulus)
    raise ValueError(f"unsupported auth version {version}")


# ---------------------------------------------------------------------------
# SRP proofs (Proton variant: little-endian, g=2, k=expandHash(g||N))
# ---------------------------------------------------------------------------
def _to_int_le(data: bytes) -> int:
    return int.from_bytes(data, "little")


def _from_int_le(bit_len: int, value: int) -> bytes:
    return value.to_bytes(bit_len // 8, "little")


def generate_proofs(
    version: int,
    username: str,
    password: bytes,
    salt_b64: str,
    modulus_bytes: bytes,
    server_ephemeral_b64: str,
    bit_length: int = 2048,
) -> dict:
    """Run the client side of Proton's SRP flow.

    Returns dict with base64 keys: ClientEphemeral, ClientProof,
    ExpectedServerProof, and SharedSession (hex-free bytes kept internally
    for debugging only).
    """
    import base64

    modulus = modulus_bytes
    N = _to_int_le(modulus)
    N_minus_1 = N - 1
    server_ephemeral = base64.b64decode(server_ephemeral_b64)
    B = _to_int_le(server_ephemeral)
    g = 2

    if B.bit_length() >= bit_length or B <= 1 or B >= N_minus_1:
        raise ValueError("server ephemeral out of bounds")
    if N.bit_length() != bit_length:
        raise ValueError("modulus has wrong size")

    g_le = _from_int_le(bit_length, g)
    # multiplier k = expandHash(LE(g) || N) mod N
    k = _to_int_le(expand_hash(g_le + modulus)) % N

    hashed_password = hash_password(
        version,
        password,
        username,
        base64.b64decode(salt_b64) if version >= 3 else b"",
        modulus,
    )
    x = _to_int_le(hashed_password)

    # client secret a: random, 2*bit_length < a < N-1 (go-srp constraint)
    while True:
        a = secrets.randbelow(N_minus_1)
        if a > bit_length * 2:
            break
    A = pow(g, a, N)
    a_bytes = _from_int_le(bit_length, A)

    u = _to_int_le(expand_hash(a_bytes + server_ephemeral))

    # base = (B - k * g^x) mod N
    base = (B - k * pow(g, x, N)) % N
    # exponent = (u*x + a) mod (N-1)
    exponent = (u * x + a) % N_minus_1
    S = pow(base, exponent, N)
    s_bytes = _from_int_le(bit_length, S)

    client_proof = expand_hash(a_bytes + server_ephemeral + s_bytes)
    server_proof = expand_hash(a_bytes + client_proof + s_bytes)

    return {
        "ClientEphemeral": base64.b64encode(a_bytes).decode("ascii"),
        "ClientProof": base64.b64encode(client_proof).decode("ascii"),
        "ExpectedServerProof": base64.b64encode(server_proof).decode("ascii"),
        "_shared_session": s_bytes,
    }
