"""Provisioning: build per-location state (account + key + certificate + server).

Flow per location:
  1. obtain a session
       auto   : the account pinned/assigned to the location (accounts.txt) —
                reuse its stored session, refresh or re-create when needed
       manual : SRP login with real credentials from accounts.txt
       temp   : legacy — throwaway account via the community backend
  2. fetch the free-server list, pick the least-loaded server in the country
  3. generate a 32-byte seed -> Ed25519 keypair (for the certificate) and the
     matching WireGuard private key
  4. request a WireGuard client certificate for the public key
  5. persist state (0600) and render the sing-box config
"""

import os
import secrets
import time

from . import accounts, api, backend, consts, ed25519, singbox
from . import proxies as proxies_mod
from .util import XProtonError, file_exists, read_json, write_json


def state_path(country: str) -> str:
    return os.path.join(consts.INSTANCES_DIR, country, "state.json")


def load_config() -> dict:
    if file_exists(consts.CONFIG_FILE):
        try:
            return read_json(consts.CONFIG_FILE)
        except Exception:
            pass
    return {}


def read_state(country: str):
    path = state_path(country)
    if not file_exists(path):
        return None
    try:
        return read_json(path)
    except Exception:
        return None


def is_provisioned(country: str) -> bool:
    state = read_state(country)
    if not state:
        return False
    exp = state.get("cert_expiration", 0)
    return exp > time.time() + 3600


def default_ports() -> dict:
    return dict(consts.DEFAULT_SOCKS_PORTS)


def pick_server(servers: list, country: str) -> dict:
    candidates = [
        s
        for s in servers
        if s.get("exit_country", "").upper() == country
        and s.get("entry_ip")
        and s.get("public_key")
    ]
    if not candidates:
        raise XProtonError(
            f"no free server available in {country} right now "
            f"(the free-country pool rotates; try another location)"
        )
    candidates.sort(key=lambda s: (s.get("load", 100), s.get("score", 10**9), s["name"]))
    return candidates[0]


def _socks_port(country: str) -> int:
    state = read_state(country)
    if state and state.get("socks_port"):
        return int(state["socks_port"])
    return consts.DEFAULT_SOCKS_PORTS[country]


def _next_proxy(proxies):
    it = proxies_mod.pool(proxies)
    return next(it)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------
def _manual_session(entry: dict, client: api.ProtonClient):
    """Reuse a stored manual session (refresh) or do a full SRP login."""
    if entry.get("session"):
        try:
            s = client.refresh(api.Session.from_dict(entry["session"]))
            entry["session"] = s.to_dict()
            accounts.update_entry(entry)
            return s
        except XProtonError:
            pass  # fall through to full login
    s = client.login(entry["email"], entry["password"], entry.get("totp"))
    entry["session"] = s.to_dict()
    accounts.update_entry(entry)
    return s


def _api_session(entry: dict, quiet: bool = False):
    """Reuse a stored API session, or self-heal by creating a fresh account."""
    if entry.get("session"):
        try:
            servers = backend.get_servers(entry["session"])
            return entry["session"], servers
        except XProtonError as e:
            if not consts.SELF_HEAL:
                raise
            if not quiet:
                print(f"  stored session died ({e}) — re-creating the account")
    session = backend.create_session()["session"]
    servers = backend.get_servers(session)
    entry["session"] = session
    accounts.update_entry(entry)
    return session, servers


# ---------------------------------------------------------------------------
# Bulk account creation (panel / `xproton create`)
# ---------------------------------------------------------------------------
def create_accounts(count: int, proxies=None, countries=None,
                    quiet: bool = False, delay=None, proxy_attempts: int = 3):
    """Create `count` temp accounts via the backend.

    proxies  : optional list of curl-ready proxy URLs (cycled round-robin).
               Dead/unreachable proxies are skipped automatically.
    countries: optional list of countries to pin the accounts to (default:
               the first unassigned locations; extras become spares)
    Appends the successfully created accounts to accounts.txt and returns
    (created_entries, failed) where failed is a list of
    {index, country, error} dicts.
    """
    delay = consts.PROVISION_DELAY if delay is None else delay
    existing = accounts.parse_accounts()
    mapping = accounts.assign_accounts(existing)
    taken = set(mapping.keys())
    if countries is None:
        countries = [c for c in consts.LOCATION_COUNTRIES if c not in taken]
    else:
        countries = [c.upper() for c in countries]
        for c in countries:
            if c not in consts.LOCATION_COUNTRIES:
                raise XProtonError(f"unknown location '{c}'")
    it = proxies_mod.pool(proxies)
    created = []
    failed = []
    for i in range(count):
        country = countries[i] if i < len(countries) else None
        where = f" for {country}" if country else " (spare)"
        session = None
        last_err = None
        for attempt in range(proxy_attempts):
            proxy = next(it)
            via = f" via {proxy.split('@')[-1]}" if proxy else ""
            if not quiet:
                print(f"  creating account {i + 1}/{count}{where}{via} ...")
            try:
                session = backend.create_session(proxy=proxy)["session"]
                break
            except XProtonError as e:
                last_err = e
                if not quiet:
                    print(f"    attempt {attempt + 1}/{proxy_attempts} failed: {e}")
                if attempt < proxy_attempts - 1:
                    time.sleep(1)
        if session is None:
            failed.append({"index": i + 1, "country": country, "error": str(last_err)})
            if not quiet:
                print(f"  [FAIL] account {i + 1}/{count}{where}: {last_err}")
            continue
        label = f"tmp-{session.get('uid', '')[:8]}"
        entry = {
            "type": "api",
            "label": label,
            "email": f"api:{label}",
            "password": None,
            "totp": None,
            "country": country,
            "session": session,
            "line": None,
            "session_line": None,
        }
        created.append(entry)
        if i < count - 1 and delay:
            time.sleep(delay)
    if created:
        accounts.append_accounts(consts.ACCOUNTS_FILE, created)
    return created, failed


# ---------------------------------------------------------------------------
# Per-location provisioning
# ---------------------------------------------------------------------------
def provision_location(
    country: str,
    provider: str = "auto",
    force: bool = False,
    quiet: bool = False,
    proxies=None,
) -> dict:
    if country not in consts.LOCATION_COUNTRIES:
        raise XProtonError(
            f"unknown location '{country}' — choose from: "
            f"{', '.join(consts.LOCATION_COUNTRIES)}"
        )
    if not force and is_provisioned(country):
        return read_state(country)

    config = load_config()
    socks_port = _socks_port(country)

    def log(msg):
        if not quiet:
            print(f"  [{country}] {msg}")

    seed = secrets.token_bytes(32)
    ed_pub = ed25519.ed25519_public_key(seed)
    pem = ed25519.spki_pem(ed_pub)
    wg_private = ed25519.wireguard_private_key(seed)
    device_name = f"xproton-{country.lower()}"

    if provider == "temp":
        # Legacy: throwaway account on the fly (no account file involved).
        log("creating temporary Proton account (community backend)")
        session = backend.create_session()["session"]
        servers = backend.get_servers(session)
        if not servers:
            raise XProtonError("server list came back empty for this session")
        server = pick_server(servers, country)
        log(f"server: {server['name']} ({server['city']}, load {server['load']}%)")
        cert = backend.get_certificate(session, pem)
        provider_used = "temp"
        session_dict = session
        account_email = None
    else:
        if provider == "manual":
            entries = [e for e in accounts.parse_accounts() if e["type"] == "manual"]
        else:
            entries = accounts.parse_accounts()
        mapping = accounts.assign_accounts(entries)
        entry = mapping.get(country)
        if entry is None:
            raise XProtonError(
                f"no account available for {country}.\n"
                f"    Create one:  xproton create   (or add a line to "
                f"{consts.ACCOUNTS_FILE})"
            )
        if entry["type"] == "manual":
            log(f"using manual account {entry['email']}")
            client = api.ProtonClient(config, proxy=_next_proxy(proxies))
            session = _manual_session(entry, client)
            log("login OK — fetching free servers")
            servers = client.get_free_servers(session)
            provider_used = "manual"
            session_dict = session.to_dict()
            account_email = entry["email"]
        else:
            log(f"using API account {entry['email']}")
            session, servers = _api_session(entry, quiet=quiet)
            provider_used = "api"
            session_dict = session
            account_email = entry["email"]
        if not servers:
            raise XProtonError("server list came back empty for this account")
        server = pick_server(servers, country)
        log(f"server: {server['name']} ({server['city']}, load {server['load']}%)")
        if entry["type"] == "manual":
            cert = client.get_certificate(session, pem, device_name)
        else:
            cert = backend.get_certificate(session, pem)

    if not cert.get("certificate"):
        raise XProtonError("certificate issuance returned an empty certificate")

    state = {
        "country": country,
        "provider": provider_used,
        "account_email": account_email,
        "socks_port": socks_port,
        "endpoint_port": int(config.get("endpoint_port", consts.WG_ENDPOINT_PORT)),
        "mtu": int(config.get("mtu", consts.WG_MTU)),
        "seed": seed.hex(),
        "wg_private_key": wg_private,
        "cert": cert["certificate"],
        "cert_expiration": cert.get("expiration_time", 0),
        "server": {
            "name": server["name"],
            "entry_ip": server["entry_ip"],
            "public_key": server["public_key"],
            "city": server.get("city", ""),
            "load": server.get("load"),
        },
        "session": session_dict,
        "created_at": int(time.time()),
    }
    write_json(state_path(country), state, mode=0o600)
    cfg_path = singbox.write_config(state)
    log(f"state + config written ({cfg_path})")
    return state


def provision_all(provider="auto", force=False, delay=None, proxies=None):
    delay = consts.PROVISION_DELAY if delay is None else delay
    results = {}
    first = True
    for country in consts.LOCATION_COUNTRIES:
        if not first and delay:
            time.sleep(delay)  # be gentle with the signup endpoint
        first = False
        if not force and is_provisioned(country):
            print(f"  [{country}] already provisioned — skipped")
            results[country] = read_state(country)
            continue
        results[country] = provision_location(
            country, provider=provider, force=force, proxies=proxies
        )
    return results


def remove_state(country: str) -> None:
    path = state_path(country)
    if file_exists(path):
        os.remove(path)
    cfg = singbox.config_path(country)
    if file_exists(cfg):
        os.remove(cfg)
