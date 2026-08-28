"""Static constants for xProton."""

import os

VERSION = "1.1.0-beta"

# GitHub repository used by `xproton update` (override via XPROTON_REPO).
GITHUB_REPO = os.environ.get("XPROTON_REPO", "IzumiRain/xProton")

# ---------------------------------------------------------------------------
# Locations and their fixed local SOCKS5 ports (user requirement: 64201-64210).
# Countries are the current ProtonVPN free-tier pool (verified live).
# ---------------------------------------------------------------------------
FREE_LOCATIONS = [
    ("CA", 64201),
    ("CH", 64202),
    ("JP", 64203),
    ("MX", 64204),
    ("NL", 64205),
    ("NO", 64206),
    ("PL", 64207),
    ("RO", 64208),
    ("SG", 64209),
    ("US", 64210),
]
DEFAULT_SOCKS_PORTS = dict(FREE_LOCATIONS)
LOCATION_COUNTRIES = [c for c, _ in FREE_LOCATIONS]

# ---------------------------------------------------------------------------
# WireGuard tunnel parameters used by every ProtonVPN client config.
# ---------------------------------------------------------------------------
WG_TUNNEL_ADDRESS_V4 = "10.2.0.2/32"
WG_TUNNEL_ADDRESS_V6 = "2a07:b944::2:2/128"
WG_TUNNEL_DNS = "10.2.0.1"
WG_MTU = 1420
WG_ENDPOINT_PORT = 51820

# ---------------------------------------------------------------------------
# Paths on the server (overridable via env for development/testing).
# ---------------------------------------------------------------------------
ETC_DIR = os.environ.get("XPROTON_ETC_DIR", "/etc/xproton")
INSTANCES_DIR = os.path.join(ETC_DIR, "instances")
BIN_DIR = os.path.join(ETC_DIR, "bin")
RUN_DIR = os.environ.get("XPROTON_RUN_DIR", "/run/xproton")
CONFIG_FILE = os.path.join(ETC_DIR, "config.json")
# Unified account store: every account (API-created or manual) lives here.
ACCOUNTS_FILE = os.environ.get(
    "XPROTON_ACCOUNTS", os.path.join(ETC_DIR, "accounts.txt")
)
# Backwards-compatible alias for older installs.
MANUAL_ACCOUNTS_FILE = ACCOUNTS_FILE
APPVERSION_CACHE = os.path.join(ETC_DIR, "appversion.cache")
SINGBOX_BIN = os.environ.get("XPROTON_SINGBOX", os.path.join(BIN_DIR, "sing-box"))
UNIT_FILE = "/etc/systemd/system/xproton@.service"
UNIT_TEMPLATE = "xproton@.service"
UNIT_INSTANCE_PREFIX = "xproton@"
OPT_DIR = "/opt/xproton"
BIN_SYMLINKS = ["/usr/local/bin/xproton", "/usr/local/bin/xpn", "/usr/local/bin/xpt"]

# ---------------------------------------------------------------------------
# Account creation limits (user requirement).
# ---------------------------------------------------------------------------
# Creating accounts straight from the server IP risks a Proton block, so the
# panel caps the count when no proxy list is provided...
MAX_ACCOUNTS_NO_PROXY = 10
# ...and allows more when each request exits through a different proxy.
MAX_ACCOUNTS_WITH_PROXY = 50
# Seconds to sleep between consecutive temp-account creations.
PROVISION_DELAY = 4
# When a stored temp session dies, re-create the account automatically.
SELF_HEAL = True
# Re-issue a WireGuard certificate when it expires within this window.
CERT_REISSUE_WINDOW = 14 * 24 * 3600

# ---------------------------------------------------------------------------
# Account providers.
# ---------------------------------------------------------------------------
# Public community backend used by proton-generator.github.io (closed source).
# Used by the "temp" provider to create throwaway Proton sessions.
BACKEND_BASE = "https://proton-api.vercel.app"
BACKEND_SESSION = "/api/proton/session"
BACKEND_SERVERS = "/api/proton/servers"
BACKEND_CERTIFICATE = "/api/proton/certificate"

# Official Proton APIs (used by the "manual" provider with real accounts).
VPN_API_BASE = "https://vpn-api.proton.me"

# Current client version is fetched from upstream and cached (24h TTL);
# the API rejects stale versions with code 5003.
APPVERSION_URL = (
    "https://raw.githubusercontent.com/ProtonVPN/proton-vpn-gtk-app/stable/versions.yml"
)
APPVERSION_FALLBACK = "linux-vpn@4.18.0"
USER_AGENT_FALLBACK = "ProtonVPN/4.18.0 (Linux; Ubuntu)"

# ---------------------------------------------------------------------------
# sing-box (userspace WireGuard) — pinned release. GPL-3.0, run as an external
# binary; no sing-box code is linked into this project.
# ---------------------------------------------------------------------------
SINGBOX_VERSION = "1.13.19"
SINGBOX_RELEASE_URL = (
    "https://github.com/SagerNet/sing-box/releases/download/"
    "v{ver}/sing-box-{ver}-{os}-{arch}.{ext}"
)

# Provision pacing: seconds to sleep between creating consecutive temp
# accounts (be gentle with the signup endpoint).
