"""Official Proton API client (used by the "manual" account provider).

Endpoints (https://vpn-api.proton.me):
  POST /auth/info        -> SRP parameters (version, modulus, salt, 2FA info)
  POST /auth             -> SRP login -> session tokens
  POST /auth/2fa         -> upgrade session scope with a TOTP code
  POST /auth/refresh     -> rotate tokens
  GET  /vpn/v1/logicals  -> server list
  POST /vpn/v1/certificate -> issue a WireGuard client certificate

All request/response field names follow the live API.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time

from . import consts
from .util import XProtonError, curl_json, http_json, read_json, atomic_write, b64d

SUCCESS_CODES = (1000, 1001)


class ApiError(XProtonError):
    def __init__(self, code, message, details=None):
        super().__init__(self._friendly(code, message))
        self.code = code
        self.details = details or {}

    @staticmethod
    def _friendly(code, message):
        hints = {
            9001: (
                "Proton demands a CAPTCHA for this login (code 9001).\n"
                "    Usually caused by logging in from a datacenter IP too often.\n"
                "    Mitigations: wait, use a different account, or provision from\n"
                "    a residential IP once and reuse the session."
            ),
            5003: (
                "Proton rejected the app version (code 5003).\n"
                "    Set a newer 'app_version' in /etc/xproton/config.json"
            ),
            10013: (
                "This account uses legacy two-password mode (code 10013).\n"
                "    Switch it to single-password mode in account.proton.me"
            ),
            8002: "Invalid or expired 2FA/TOTP code (code 8002).",
            8001: "Wrong username or password (code 8001).",
        }
        base = f"Proton API error {code}: {message or 'unknown'}"
        return hints.get(code, base)


class Session:
    def __init__(self, uid, access_token, refresh_token, scopes=None, expires_at=None):
        self.uid = uid
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.scopes = scopes or []
        self.expires_at = expires_at

    def to_dict(self):
        return {
            "uid": self.uid,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "scopes": self.scopes,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d.get("uid"),
            d.get("access_token"),
            d.get("refresh_token"),
            d.get("scopes"),
            d.get("expires_at"),
        )


# ---------------------------------------------------------------------------
# App version (the API rejects stale versions with code 5003)
# ---------------------------------------------------------------------------
def get_app_version(config=None):
    """Return (appversion, user_agent), fetched upstream and cached 24h."""
    if config and config.get("app_version"):
        av = config["app_version"]
        return av, f"ProtonVPN/{av.split('@')[-1]} (Linux)"
    if os.path.isfile(consts.APPVERSION_CACHE):
        try:
            data = read_json(consts.APPVERSION_CACHE)
            if time.time() - data["ts"] < 24 * 3600:
                return data["appversion"], data["user_agent"]
        except Exception:
            pass
    appversion, ua = consts.APPVERSION_FALLBACK, consts.USER_AGENT_FALLBACK
    try:
        _, _, raw = http_json(
            "GET", consts.APPVERSION_URL, timeout=10, retries=0
        )
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        m = re.search(r"version:\s*([\d.]+)", raw or "")
        if m:
            v = m.group(1)
            appversion = f"linux-vpn@{v}"
            ua = f"ProtonVPN/{v} (Linux)"
    except Exception:
        pass
    try:
        atomic_write(
            consts.APPVERSION_CACHE,
            json.dumps({"ts": time.time(), "appversion": appversion, "user_agent": ua}),
        )
    except OSError:
        pass
    return appversion, ua


def _headers(session=None, config=None):
    appversion, ua = get_app_version(config)
    h = {
        "Accept": "application/json",
        "User-Agent": ua,
        "x-pm-appversion": appversion,
    }
    if session:
        h["x-pm-uid"] = session.uid
        h["Authorization"] = f"Bearer {session.access_token}"
    return h


def parse_clearsigned(armored: str) -> str:
    """Extract the payload text of a PGP clearsigned message (no sig check)."""
    payload = []
    in_payload = False
    for line in armored.splitlines():
        if line.startswith("-----BEGIN PGP SIGNATURE-----"):
            break
        if line.startswith("-----BEGIN PGP SIGNED MESSAGE-----"):
            continue
        if line.startswith("Hash:"):
            continue
        if not in_payload:
            if line.strip() == "":
                in_payload = True
            continue
        if line.startswith("- "):  # dash-escaped line
            line = line[2:]
        payload.append(line)
    return "".join(payload).strip()


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------
def totp_now(secret_b32: str, digits: int = 6, period: int = 30) -> str:
    s = secret_b32.strip().replace(" ", "").upper()
    pad = "=" * ((8 - len(s) % 8) % 8)
    key = base64.b32decode(s + pad)
    counter = int(time.time()) // period
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0xF
    code = (
        struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    ) % (10**digits)
    return str(code).zfill(digits)


# ---------------------------------------------------------------------------
# VPN session helpers
# ---------------------------------------------------------------------------
class ProtonClient:
    def __init__(self, config=None, proxy=None):
        self.config = config or {}
        self.proxy = proxy  # optional curl-ready proxy URL for all requests

    # -- low level ---------------------------------------------------------
    def _request(self, method, path, body=None, session=None):
        url = consts.VPN_API_BASE + path
        if self.proxy:
            status, _h, data = curl_json(
                method, url, body=body, headers=_headers(session, self.config),
                timeout=30, proxy=self.proxy,
            )
        else:
            status, _h, data = http_json(
                method,
                url,
                body=body,
                headers=_headers(session, self.config),
                timeout=30,
            )
        if not isinstance(data, dict):
            raise XProtonError(f"unexpected non-JSON response from {path} ({status})")
        return data

    @staticmethod
    def _check(data, path):
        code = data.get("Code")
        if code not in SUCCESS_CODES:
            details = data.get("Details") or {}
            raise ApiError(code, data.get("Error", ""), details)
        return data

    # -- auth ---------------------------------------------------------------
    def login(self, username: str, password: str, totp_secret=None) -> Session:
        info = self._check(
            self._request("POST", "/auth/info", {"Username": username}),
            "/auth/info",
        )
        version = int(info.get("Version", 4))
        modulus = b64d(parse_clearsigned(info["Modulus"]))
        proofs = self._srp_proofs(
            version, username, password, info.get("Salt", ""), modulus,
            info["ServerEphemeral"],
        )
        body = {
            "Username": username,
            "ClientEphemeral": proofs["ClientEphemeral"],
            "ClientProof": proofs["ClientProof"],
            "SRPSession": info.get("SRPSession"),
        }
        two_fa = info.get("2FA") or {}
        if two_fa.get("Enabled") == 1 and two_fa.get("TOTP") == 1:
            if not totp_secret:
                raise XProtonError(
                    "this account has TOTP 2FA enabled.\n"
                    "    Add the TOTP secret as a 3rd field in manual-accounts.txt:\n"
                    "    email:password:TOTPSECRET"
                )
            body["TwoFactorCode"] = totp_now(totp_secret)
        data = self._check(self._request("POST", "/auth", body), "/auth")

        server_proof = data.get("ServerProof", "")
        if server_proof != proofs["ExpectedServerProof"]:
            raise XProtonError("server proof mismatch — aborting login")
        session = Session(
            uid=data.get("UID"),
            access_token=data.get("AccessToken"),
            refresh_token=data.get("RefreshToken"),
            scopes=data.get("Scopes", []),
            expires_at=int(time.time()) + int(data.get("ExpiresIn", 7200)),
        )
        # Some accounts log in with a limited scope; upgrade with TOTP if needed.
        if "vpn" not in session.scopes and "twofactor" in session.scopes:
            if not totp_secret:
                raise XProtonError(
                    "session lacks 'vpn' scope (2FA required to upgrade).\n"
                    "    Provide the TOTP secret in manual-accounts.txt."
                )
            upgraded = self._check(
                self._request(
                    "POST",
                    "/auth/2fa",
                    {"TwoFactorCode": totp_now(totp_secret)},
                    session=session,
                ),
                "/auth/2fa",
            )
            session.scopes = upgraded.get("Scopes", session.scopes)
        return session

    @staticmethod
    def _srp_proofs(version, username, password, salt_b64, modulus, server_ephemeral):
        # Imported lazily to keep module import light.
        from . import srp

        return srp.generate_proofs(
            version,
            username,
            password.encode("utf-8"),
            salt_b64,
            modulus,
            server_ephemeral,
        )

    def refresh(self, session: Session) -> Session:
        h = _headers(None, self.config)
        h["x-pm-uid"] = session.uid
        h["Authorization"] = f"Bearer {session.refresh_token}"
        status, headers, data = http_json(
            "POST", consts.VPN_API_BASE + "/auth/refresh", body={}, headers=h
        )
        if isinstance(data, dict) and data.get("Code") in SUCCESS_CODES:
            session.access_token = data.get("AccessToken", session.access_token)
            session.refresh_token = data.get("RefreshToken", session.refresh_token)
            session.expires_at = int(time.time()) + int(data.get("ExpiresIn", 7200))
            return session
        raise ApiError(
            data.get("Code") if isinstance(data, dict) else 0,
            data.get("Error", "") if isinstance(data, dict) else "refresh failed",
        )

    # -- VPN ----------------------------------------------------------------
    def get_free_servers(self, session: Session):
        data = self._check(
            self._request("GET", "/vpn/v1/logicals", session=session),
            "/vpn/v1/logicals",
        )
        servers = []
        for logical in data.get("LogicalServers", []):
            if logical.get("Tier") != 0 or logical.get("Status") != 1:
                continue
            for phys in logical.get("Servers", []):
                if phys.get("Status") != 1:
                    continue
                servers.append(
                    {
                        "name": logical.get("Name", ""),
                        "exit_country": logical.get("ExitCountry", ""),
                        "city": logical.get("City", ""),
                        "entry_ip": phys.get("EntryIP", ""),
                        "public_key": phys.get("X25519PublicKey", ""),
                        "load": logical.get("Load", 100),
                        "score": logical.get("Score", 10**9),
                    }
                )
        return servers

    def get_certificate(self, session: Session, client_public_pem: str, device_name: str,
                        duration: str = "365d"):
        body = {
            "ClientPublicKey": client_public_pem,
            "Mode": "persistent",
            "DeviceName": device_name,
            "Duration": duration,
        }
        data = self._check(
            self._request("POST", "/vpn/v1/certificate", body, session=session),
            "/vpn/v1/certificate",
        )
        cert = data.get("Certificate", "")
        if cert and "BEGIN CERTIFICATE" not in cert:
            cert = (
                "-----BEGIN CERTIFICATE-----\n"
                + "\n".join(cert[i : i + 64] for i in range(0, len(cert), 64))
                + "\n-----END CERTIFICATE-----\n"
            )
        return {
            "certificate": cert,
            "expiration_time": int(data.get("ExpirationTime", 0)),
            "refresh_time": int(data.get("RefreshTime", 0)),
        }
