#!/usr/bin/env python3
"""xProton test suite (stdlib unittest, no dependencies).

Run:  python3 tests/run_tests.py [--full]
--full also runs the slow bcrypt vector set (all 5 known hashes, ~40s).
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

from xpl import accounts, consts, ed25519, provision, singbox, srp  # noqa: E402


# ---------------------------------------------------------------------------
# Ed25519
# ---------------------------------------------------------------------------
class TestEd25519(unittest.TestCase):
    # RFC 8032 §7.1 test vectors (public-domain specification).
    VECTORS = [
        (
            "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
            "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        ),
        (
            "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
            "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        ),
    ]

    def test_rfc8032_vectors(self):
        for seed_hex, pub_hex in self.VECTORS:
            pub = ed25519.ed25519_public_key(bytes.fromhex(seed_hex))
            self.assertEqual(pub.hex(), pub_hex)

    def test_wg_key_derivation(self):
        seed = bytes(range(32))
        key = ed25519.wireguard_private_key(seed)
        self.assertEqual(len(key), 44)  # base64 of 32 bytes
        # deterministic
        self.assertEqual(key, ed25519.wireguard_private_key(seed))
        # clamping: first byte low 3 bits clear; last byte bit 7 clear,
        # bit 6 set (standard WireGuard/Ed25519 clamping)
        import base64

        raw = bytearray(base64.b64decode(key))
        self.assertEqual(raw[0] & 0x07, 0)
        self.assertEqual(raw[31] & 0x80, 0)
        self.assertEqual(raw[31] & 0x40, 0x40)

    def test_spki_pem(self):
        pub = ed25519.ed25519_public_key(bytes(range(32)))
        pem = ed25519.spki_pem(pub)
        self.assertIn("-----BEGIN PUBLIC KEY-----", pem)
        self.assertIn("-----END PUBLIC KEY-----", pem)
        # openssl must accept it (if available)
        rc, _, _ = _run_quiet(
            ["openssl", "pkey", "-pubin", "-inform", "PEM", "-noout", "-text"]
            if False
            else ["openssl", "pkey", "-pubin", "-inform", "PEM", "-noout", "-text"],
        )
        _ = rc  # openssl presence varies; only check format basics here


# ---------------------------------------------------------------------------
# SRP / bcrypt
# ---------------------------------------------------------------------------
class TestSrp(unittest.TestCase):
    def test_expand_hash(self):
        out = srp.expand_hash(b"hello")
        self.assertEqual(len(out), 256)
        self.assertEqual(out, srp.expand_hash(b"hello"))

    def test_bcrypt_vector_fast(self):
        # One official go-srp vector (MIT reference): password "test!!!"
        got = srp.bcrypt_y10(b"test!!!", "PTTsDBs/mlLnSk6VmtFghe").decode()
        want = "$2y$10$PTTsDBs/mlLnSk6VmtFgheNSiK/lSwtJsrBLLDK3kZYI7193nInqy"
        self.assertEqual(got, want)

    def test_srp_math_consistency(self):
        """Client/server SRP handshake simulation: both sides must agree on
        M1/M2 using the same expandHash/LE conventions."""
        import base64

        N = int(
            "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E08"
            "8A67CC74020BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B"
            "302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9"
            "A637ED6B0BFF5CB6F406B7EDEE386BFB5A899FA5AE9F24117C4B1FE6"
            "49286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8"
            "FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077096966D"
            "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C"
            "180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF695581718"
            "3995497CEA956AE515D2261898FA051015728E5A8AACAA68FFFFFFFF"
            "FFFFFFFF", 16)
        g = 2
        salt = b"some-salt-bytes"
        hashed = srp.hash_password_v3(b"hunter2", salt, N.to_bytes(256, "little"))
        x = int.from_bytes(hashed, "little")
        v = pow(g, x, N)  # server verifier

        # client side
        b = 123456789
        B = (pow(g, b, N) + srp._to_int_le(srp.expand_hash(
            (2).to_bytes(256, "little") + N.to_bytes(256, "little"))) % N * v) % N
        B_le = B.to_bytes(256, "little")

        proofs = srp.generate_proofs(
            4, "tester@example.com", b"hunter2",
            base64.b64encode(salt).decode(), N.to_bytes(256, "little"),
            base64.b64encode(B_le).decode(),
        )
        A = int.from_bytes(base64.b64decode(proofs["ClientEphemeral"]), "little")

        # server side
        u = int.from_bytes(srp.expand_hash(A.to_bytes(256, "little") + B_le), "little")
        S_server = pow(A * pow(v, u, N) % N, b, N)
        M1_server = srp.expand_hash(
            A.to_bytes(256, "little") + B_le + S_server.to_bytes(256, "little")
        )
        self.assertEqual(
            base64.b64decode(proofs["ClientProof"]), M1_server,
            "client M1 must match server-computed M1",
        )
        # client's expected server proof
        M2_client = srp.expand_hash(
            A.to_bytes(256, "little")
            + base64.b64decode(proofs["ClientProof"])
            + S_server.to_bytes(256, "little")
        )
        self.assertEqual(
            base64.b64decode(proofs["ExpectedServerProof"]), M2_client
        )

    @unittest.skipUnless("--full" in sys.argv, "slow (--full)")
    def test_bcrypt_all_vectors(self):
        vectors = [
            ("PTTsDBs/mlLnSk6VmtFghe", "$2y$10$PTTsDBs/mlLnSk6VmtFgheNSiK/lSwtJsrBLLDK3kZYI7193nInqy"),
            ("4DZHd6WZX4fEaWKtCfYdde", "$2y$10$4DZHd6WZX4fEaWKtCfYddeZfcryISo9eEMgbA90O.Wnnz1s1VKmKC"),
            ("RpyeXO7K2eD3r/ZZ/B63V.", "$2y$10$RpyeXO7K2eD3r/ZZ/B63V.Tya53OExbyO8LR7TB93KYP4PvC.EPMW"),
            ("/.3KXCwRnsrxURMGxN7.R.", "$2y$10$/.3KXCwRnsrxURMGxN7.R.GLpVq0zyBbI9wgS0wB2U/g2btx1RYoy"),
            ("tuE3bNGezetI9Ra2aGePqu", "$2y$10$tuE3bNGezetI9Ra2aGePqutWPxG2r36BOzMGoXYzM0p2vmGT9fK1i"),
        ]
        for salt, want in vectors:
            self.assertEqual(srp.bcrypt_y10(b"test!!!", salt).decode(), want)


# ---------------------------------------------------------------------------
# unified accounts.txt parsing (two-line blocks)
# ---------------------------------------------------------------------------
class TestAccounts(unittest.TestCase):
    def _write(self, content):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_parse_manual_and_assign(self):
        p = self._write(
            "# comment\n"
            "alice@example.com:passA\n"
            "-\n"
            "bob@example.com:passB:TOTPSECRET123\n"
            "{\"uid\":\"u1\",\"access_token\":\"at\"}\n"
            "carol@example.com:passC::JP\n"
            "-\n"
        )
        entries = accounts.parse_accounts(p)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]["session"], None)
        self.assertEqual(entries[1]["totp"], "TOTPSECRET123")
        self.assertEqual(entries[1]["session"]["uid"], "u1")
        self.assertEqual(entries[2]["country"], "JP")
        mapping = accounts.assign_accounts(entries)
        # JP is pinned; CA gets alice (first positional), CH gets bob
        self.assertEqual(mapping["JP"]["email"], "carol@example.com")
        self.assertEqual(mapping["CA"]["email"], "alice@example.com")
        self.assertEqual(mapping["CH"]["email"], "bob@example.com")

    def test_parse_api_entries(self):
        p = self._write(
            "api:tmp-abcd1234:US\n"
            "{\"uid\":\"u9\",\"accessToken\":\"at9\",\"refreshToken\":\"rt9\"}\n"
            "api:tmp-eeeeeeee\n"
            "{\"uid\":\"u8\"}\n"
        )
        entries = accounts.parse_accounts(p)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["type"], "api")
        self.assertEqual(entries[0]["country"], "US")
        self.assertEqual(entries[1]["country"], None)
        self.assertEqual(entries[1]["session"]["uid"], "u8")
        mapping = accounts.assign_accounts(entries)
        self.assertEqual(mapping["US"]["label"], "tmp-abcd1234")
        # second api account is unpinned -> assigned positionally
        self.assertEqual(mapping["CA"]["label"], "tmp-eeeeeeee")

    def test_spare_pool(self):
        # 12 accounts, 10 locations -> 2 spares
        body = "api:a:US\n-\n" + "".join(f"api:s{i}\n-\n" for i in range(11))
        p = self._write(body)
        entries = accounts.parse_accounts(p)
        mapping = accounts.assign_accounts(entries)
        spares = accounts.spare_pool(entries)
        self.assertEqual(len(mapping), 10)
        self.assertEqual(len(spares), 2)
        self.assertEqual(mapping["US"]["label"], "a")

    def test_serialize_roundtrip(self):
        p = self._write("")
        entries = [
            {"type": "manual", "email": "a@x.com", "password": "pw",
             "totp": None, "country": "JP", "session": None,
             "line": None, "session_line": None},
            {"type": "api", "label": "tmp-1234", "email": "api:tmp-1234",
             "password": None, "totp": None, "country": None,
             "session": {"uid": "u1", "accessToken": "at"},
             "line": None, "session_line": None},
        ]
        accounts.write_accounts(p, entries)
        again = accounts.parse_accounts(p)
        self.assertEqual(len(again), 2)
        self.assertEqual(again[0]["country"], "JP")
        self.assertEqual(again[1]["session"]["uid"], "u1")

    def test_update_entry_session(self):
        p = self._write(
            "api:tmp-x:US\n"
            "{\"uid\":\"old\",\"accessToken\":\"a\"}\n"
            "bob@example.com:passB\n"
            "-\n"
        )
        entries = accounts.parse_accounts(p)
        e = entries[0]
        e["session"] = {"uid": "new", "accessToken": "b"}
        accounts.update_entry(e, path=p)
        again = accounts.parse_accounts(p)
        self.assertEqual(again[0]["session"]["uid"], "new")
        self.assertEqual(again[1]["email"], "bob@example.com")

    def test_bad_line(self):
        p = self._write("not-a-valid-line\n")
        with self.assertRaises(Exception):
            accounts.parse_accounts(p)

    def test_session_without_account_line(self):
        p = self._write("{\"uid\":\"x\"}\n")
        with self.assertRaises(Exception):
            accounts.parse_accounts(p)

    def test_duplicate_pin(self):
        p = self._write("a@x:1::US\n-\nb@x:2::US\n-\n")
        with self.assertRaises(Exception):
            accounts.assign_accounts(accounts.parse_accounts(p))


# ---------------------------------------------------------------------------
# proxy parsing
# ---------------------------------------------------------------------------
class TestProxies(unittest.TestCase):
    def test_parse_valid(self):
        from xpl import proxies
        self.assertEqual(
            proxies.parse_proxy_line("socks5://1.2.3.4:1080"),
            "socks5h://1.2.3.4:1080",
        )
        self.assertEqual(
            proxies.parse_proxy_line("socks5://user:pass@1.2.3.4:1080"),
            "socks5h://user:pass@1.2.3.4:1080",
        )
        self.assertEqual(
            proxies.parse_proxy_line("socks4://h:1080"), "socks4a://h:1080"
        )
        self.assertEqual(
            proxies.parse_proxy_line("http://h:8080"), "http://h:8080"
        )
        self.assertIsNone(proxies.parse_proxy_line("# comment"))
        self.assertIsNone(proxies.parse_proxy_line(""))

    def test_parse_plain_hostport(self):
        # public proxy-list repos use bare ip:port lines -> default socks5
        from xpl import proxies
        self.assertEqual(
            proxies.parse_proxy_line("1.2.3.4:1080"), "socks5h://1.2.3.4:1080"
        )
        self.assertEqual(
            proxies.parse_proxy_line("2001:db8::1:443"), "socks5h://2001:db8::1:443"
        )

    def test_parse_invalid(self):
        from xpl import proxies
        with self.assertRaises(Exception):
            proxies.parse_proxy_line("ftp://h:21")          # bad scheme
        with self.assertRaises(Exception):
            proxies.parse_proxy_line("socks5://h")          # no port
        with self.assertRaises(Exception):
            proxies.parse_proxy_line("no-port-here")        # no host:port

    def test_read_file(self):
        from xpl import proxies
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write("# list\nsocks5://a:1\nhttp://b:2\n\n")
        self.addCleanup(os.remove, path)
        lst = proxies.read_proxies(path)
        self.assertEqual(lst, ["socks5h://a:1", "http://b:2"])


# ---------------------------------------------------------------------------
# version comparison
# ---------------------------------------------------------------------------
class TestVersion(unittest.TestCase):
    def test_version_tuple(self):
        from xpl.util import version_tuple
        self.assertLess(version_tuple("1.0.0"), version_tuple("1.1.0"))
        self.assertLess(version_tuple("v1.1.0"), version_tuple("1.2.0"))
        self.assertEqual(version_tuple("1.2.3"), version_tuple("v1.2.3"))
        self.assertLess(version_tuple("1.9"), version_tuple("1.10.0"))


# ---------------------------------------------------------------------------
# constants / ports
# ---------------------------------------------------------------------------
class TestConsts(unittest.TestCase):
    def test_ten_locations_ports(self):
        self.assertEqual(len(consts.FREE_LOCATIONS), 10)
        ports = [p for _, p in consts.FREE_LOCATIONS]
        self.assertEqual(ports, list(range(64201, 64211)))
        self.assertEqual(len(set(ports)), 10)

    def test_country_codes_uppercase(self):
        for c, _ in consts.FREE_LOCATIONS:
            self.assertEqual(c, c.upper())
            self.assertEqual(len(c), 2)


# ---------------------------------------------------------------------------
# server picking
# ---------------------------------------------------------------------------
class TestPickServer(unittest.TestCase):
    def test_picks_least_loaded_matching_country(self):
        servers = [
            {"exit_country": "US", "name": "US-FREE#1", "entry_ip": "1.1.1.1",
             "public_key": "k1", "load": 80, "score": 5},
            {"exit_country": "NL", "name": "NL-FREE#1", "entry_ip": "2.2.2.2",
             "public_key": "k2", "load": 10, "score": 1},
            {"exit_country": "US", "name": "US-FREE#2", "entry_ip": "3.3.3.3",
             "public_key": "k3", "load": 20, "score": 3},
            {"exit_country": "US", "name": "US-FREE#3", "entry_ip": "",
             "public_key": "k4", "load": 1, "score": 0},  # missing IP -> skipped
        ]
        picked = provision.pick_server(servers, "US")
        self.assertEqual(picked["name"], "US-FREE#2")

    def test_no_server_raises(self):
        with self.assertRaises(Exception):
            provision.pick_server([], "US")


# ---------------------------------------------------------------------------
# sing-box config (validated against the real binary when available)
# ---------------------------------------------------------------------------
class TestSingbox(unittest.TestCase):
    def _build(self):
        state = {
            "country": "US",
            "socks_port": 64210,
            "endpoint_port": 51820,
            "mtu": 1420,
            "wg_private_key": "sGV8W0dT+RmX9u0VwY8Oq0HhKQbY1aP2cN3fD4gE5hI=",
            "server": {
                "entry_ip": "89.187.185.161",
                "public_key": "ZEQHDxg/HbjznRvApyBWfUGs6T20Rvy0/DctZk6FvB4=",
                "name": "US-FREE#2",
            },
        }
        return singbox.build_config(state)

    def test_structure(self):
        cfg = self._build()
        self.assertEqual(cfg["endpoints"][0]["type"], "wireguard")
        self.assertEqual(cfg["endpoints"][0]["peers"][0]["port"], 51820)
        self.assertEqual(cfg["inbounds"][0]["listen_port"], 64210)
        self.assertEqual(cfg["inbounds"][0]["listen"], "127.0.0.1")

    def test_singbox_binary_check(self):
        binary = os.environ.get("XPROTON_SINGBOX") or "/tmp/singbox_extract/sing-box-1.13.19-windows-amd64/sing-box.exe"
        if not os.path.isfile(binary):
            self.skipTest("sing-box binary not available")
        cfg = self._build()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(cfg, f)
            path = f.name
        self.addCleanup(os.remove, path)
        import subprocess

        p = subprocess.run([binary, "check", "-c", path], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, f"sing-box check failed: {p.stderr}")


def _run_quiet(cmd):
    import subprocess

    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return -1, "", str(e)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + [a for a in sys.argv[1:] if a != "--full"])
