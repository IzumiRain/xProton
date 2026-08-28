"""Temporary-account provider via the public community backend used by
proton-generator.github.io (proton-api.vercel.app).

This creates throwaway Proton sessions (one VPN connection each) without
user credentials. The backend is closed-source and third-party — see the
README warnings about availability and ToS.
"""

from . import consts
from .util import XProtonError, curl_json, http_json


def _post(path, body=None, timeout=60, proxy=None):
    if proxy:
        # Route the request through a socks4/socks5/http proxy (curl).
        status, _headers, data = curl_json(
            "POST", consts.BACKEND_BASE + path, body=body or {}, timeout=timeout,
            proxy=proxy,
        )
    else:
        status, _headers, data = http_json(
            "POST", consts.BACKEND_BASE + path, body=body or {}, timeout=timeout
        )
    if not isinstance(data, dict):
        raise XProtonError(f"unexpected response from backend {path} ({status})")
    if status >= 500:
        raise XProtonError(f"backend error {status} on {path}")
    if not data.get("ok"):
        raise XProtonError(
            f"backend error on {path}: {data.get('error', 'unknown error')}"
        )
    return data


def create_session(proxy=None) -> dict:
    """Create a fresh temporary Proton session (throwaway account).

    `proxy` is an optional curl-ready proxy URL (socks5h/socks4a/http);
    account creation goes through it so the server IP is never the source
    of bulk signups. Proxied requests use a shorter timeout so dead proxies
    from public lists fail fast and get skipped.
    """
    return _post(consts.BACKEND_SESSION, {}, proxy=proxy,
                 timeout=25 if proxy else 60)


def get_servers(session: dict) -> list:
    data = _post(consts.BACKEND_SERVERS, {"session": session}, timeout=90)
    servers = []
    for srv in data.get("servers", []):
        servers.append(
            {
                "name": srv.get("name", ""),
                "exit_country": srv.get("exitCountry", ""),
                "city": srv.get("city", ""),
                "entry_ip": srv.get("entryIp", ""),
                "public_key": srv.get("publicKey", ""),
                "load": srv.get("load", 100),
                "score": srv.get("score", 10**9),
            }
        )
    return servers


def get_certificate(session: dict, client_public_pem: str) -> dict:
    data = _post(
        consts.BACKEND_CERTIFICATE,
        {"session": session, "clientPublicKey": client_public_pem, "persistent": True},
        timeout=90,
    )
    cert = data.get("certificate") or {}
    value = cert.get("value", "")
    if value and "BEGIN CERTIFICATE" not in value:
        value = (
            "-----BEGIN CERTIFICATE-----\n"
            + "\n".join(value[i : i + 64] for i in range(0, len(value), 64))
            + "\n-----END CERTIFICATE-----\n"
        )
    return {
        "certificate": value,
        "expiration_time": int(cert.get("expirationTime", 0)),
        "refresh_time": int(cert.get("refreshTime", 0)),
    }
