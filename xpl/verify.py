"""Account verification + certificate refresh.

verify_entries() checks every account in accounts.txt:
  * API accounts   : the stored session is exercised against the backend
                     (dead session -> account is unrecoverable, mark FAIL)
  * manual accounts: the stored session is refreshed on the official API;
                     if that fails, a full SRP login is attempted
                     (invalid credentials / banned -> mark FAIL)

reissue_expiring() then renews the WireGuard certificate of every provisioned
location whose account is healthy and whose certificate is about to expire,
so a location keeps working for as long as its account lives.
"""

import time

from . import accounts, api, backend, consts, ed25519, provision, singbox
from .util import XProtonError


def _verify_api(entry: dict):
    if not entry.get("session"):
        return False, "no session stored (cannot verify an API account without one)"
    try:
        servers = backend.get_servers(entry["session"])
        return True, f"session OK ({len(servers)} free servers)"
    except XProtonError as e:
        return False, f"dead session: {e}"


def _verify_manual(entry: dict, config: dict, proxy=None):
    client = api.ProtonClient(config, proxy=proxy)
    if entry.get("session"):
        try:
            s = client.refresh(api.Session.from_dict(entry["session"]))
            return True, "session refreshed", s.to_dict()
        except XProtonError:
            pass  # token dead — full login below
    try:
        s = client.login(entry["email"], entry["password"], entry.get("totp"))
        return True, "login OK", s.to_dict()
    except XProtonError as e:
        return False, f"login failed: {e}", None


def verify_entries(entries=None, config=None, progress=None, proxy=None):
    """Check all accounts; updates stored sessions in accounts.txt when they
    change. Returns a list of {entry, ok, detail} dicts."""
    if entries is None:
        entries = accounts.parse_accounts()
    config = config if config is not None else provision.load_config()
    results = []
    changed = False
    for entry in entries:
        label = accounts.display_name(entry)
        if progress:
            progress(label, "checking...")
        if entry["type"] == "api":
            ok, detail = _verify_api(entry)
            new_session = None
        else:
            ok, detail, new_session = _verify_manual(entry, config, proxy=proxy)
        if new_session is not None and new_session != entry.get("session"):
            entry["session"] = new_session
            changed = True
        results.append({"entry": entry, "ok": ok, "detail": detail})
    if changed:
        # Rewrite the file in canonical form with the refreshed sessions.
        accounts.write_accounts(consts.ACCOUNTS_FILE, entries)
    return results


def reissue_expiring(results, force=False):
    """Re-issue certificates of provisioned locations that are about to
    expire. Returns a list of (country, action) strings."""
    healthy = {}
    for r in results:
        if r["ok"]:
            entry = r["entry"]
            if entry.get("session") and entry["session"].get("uid"):
                healthy[entry["session"]["uid"]] = entry
    mapping = accounts.assign_accounts()
    config = provision.load_config()
    actions = []
    for country in consts.LOCATION_COUNTRIES:
        entry = mapping.get(country)
        if not entry:
            continue
        state = provision.read_state(country)
        if not state:
            continue
        exp = state.get("cert_expiration", 0)
        if not force and exp > time.time() + consts.CERT_REISSUE_WINDOW:
            continue
        # the account behind this location must be healthy
        sid = entry.get("session") and entry["session"].get("uid")
        if sid and sid not in healthy:
            # for manual accounts the entry session may have been refreshed
            continue
        try:
            seed = bytes.fromhex(state["seed"])
            pem = ed25519.spki_pem(ed25519.ed25519_public_key(seed))
            if entry["type"] == "api":
                cert = backend.get_certificate(entry["session"], pem)
            else:
                client = api.ProtonClient(config)
                session = api.Session.from_dict(entry["session"])
                try:
                    client.refresh(session)
                except XProtonError:
                    pass  # cert call below will surface the real error
                cert = client.get_certificate(session, pem, f"xproton-{country.lower()}")
            if not cert.get("certificate"):
                actions.append((country, "reissue failed (empty certificate)"))
                continue
            state["cert"] = cert["certificate"]
            state["cert_expiration"] = cert.get("expiration_time", 0)
            provision.write_json(provision.state_path(country), state, mode=0o600)
            singbox.write_config(state)
            actions.append((country, "certificate renewed"))
        except XProtonError as e:
            actions.append((country, f"reissue failed: {e}"))
    return actions
