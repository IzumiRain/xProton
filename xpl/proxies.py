"""Proxy list parsing and rotation for account creation.

File format — one proxy per line (blank lines and '#' comments are ignored):

    host:port                       (plain - treated as socks5)
    socks5://[user:pass@]host:port
    socks4://[user:pass@]host:port
    http://[user:pass@]host:port
    https://[user:pass@]host:port

Plain `host:port` lines (the format used by public proxy-list repos such as
monosans/proxy-list, TheSpeedX/PROXY-List) default to socks5.

Notes:
  * `socks5://` is normalized to `socks5h://` (DNS resolved through the
    proxy, not locally) — what you want when creating accounts remotely.
  * `socks4://` is normalized to `socks4a://` for the same reason.
"""

import itertools
import re

from .util import XProtonError

_SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
_LINE_RE = re.compile(r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://")


def parse_proxy_line(line: str):
    """Parse one proxy line; returns a curl-ready proxy URL or None for
    blank/comment lines. Raises XProtonError on malformed input."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = _LINE_RE.match(line)
    if m:
        scheme = m.group("scheme").lower()
        if scheme not in _SCHEMES:
            raise XProtonError(
                f"unsupported proxy scheme '{scheme}' in: {line!r} "
                f"(use socks5://, socks4://, http:// or https://)"
            )
        rest = line[m.end():]
    else:
        # Plain "host:port" (public proxy-list repos) -> socks5.
        scheme = "socks5"
        rest = line
    hostport = rest.split("@")[-1]  # strip credentials
    if ":" not in hostport:
        raise XProtonError(f"proxy line must include host:port: {line!r}")
    host, _, port = hostport.rpartition(":")
    if not host or not port.isdigit():
        raise XProtonError(f"proxy line must include host:port: {line!r}")
    port = int(port)
    if not (1 <= port <= 65535):
        raise XProtonError(f"bad proxy port in: {line!r}")
    # Normalize for curl: resolve hostnames through the proxy.
    if scheme == "socks5":
        scheme = "socks5h"
    elif scheme == "socks4":
        scheme = "socks4a"
    return f"{scheme}://{rest}"


def read_proxies(path: str) -> list:
    """Read a proxy file; raises XProtonError with the offending line."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        raise XProtonError(f"cannot read proxy file {path}: {e}")
    proxies = []
    for lineno, raw in enumerate(lines, 1):
        try:
            p = parse_proxy_line(raw)
        except XProtonError as e:
            raise XProtonError(f"{path}:{lineno}: {e}")
        if p:
            proxies.append(p)
    if not proxies:
        raise XProtonError(f"no usable proxies found in {path}")
    return proxies


def pool(proxies: list):
    """Round-robin iterator over proxies (empty list yields None forever)."""
    if not proxies:
        return itertools.repeat(None)
    return itertools.cycle(proxies)
