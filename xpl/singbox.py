"""sing-box (userspace WireGuard) config generation, one instance per location.

Each location gets its own sing-box process:
  inbound  : socks5 on 127.0.0.1:<socks_port>
  tunnel   : wireguard *endpoint* (sing-box >= 1.11 model) to the Proton server
  dns      : resolved through the tunnel (10.2.0.1) for destinations

sing-box is an external GPL-3.0 binary — we only emit JSON config for it.
"""

import json
import os

from . import consts
from .util import atomic_write, read_json


def config_path(country: str) -> str:
    return os.path.join(consts.INSTANCES_DIR, country, "config.json")


def build_config(state: dict) -> dict:
    server = state["server"]
    return {
        "log": {"level": "info", "timestamp": True},
        "dns": {
            "servers": [
                {
                    "type": "udp",
                    "tag": "tunnel-dns",
                    "server": consts.WG_TUNNEL_DNS,
                    "detour": "wg-ep",
                }
            ]
        },
        "endpoints": [
            {
                "type": "wireguard",
                "tag": "wg-ep",
                "system": False,
                "mtu": int(state.get("mtu", consts.WG_MTU)),
                "address": [
                    consts.WG_TUNNEL_ADDRESS_V4,
                    consts.WG_TUNNEL_ADDRESS_V6,
                ],
                "private_key": state["wg_private_key"],
                "peers": [
                    {
                        "address": server["entry_ip"],
                        "port": int(
                            state.get("endpoint_port", consts.WG_ENDPOINT_PORT)
                        ),
                        "public_key": server["public_key"],
                        "allowed_ips": ["0.0.0.0/0", "::/0"],
                        "persistent_keepalive_interval": 25,
                    }
                ],
            }
        ],
        "inbounds": [
            {
                "type": "socks",
                "tag": "in",
                "listen": "127.0.0.1",
                "listen_port": int(state["socks_port"]),
            }
        ],
        "route": {
            "rules": [{"inbound": "in", "outbound": "wg-ep"}],
            "final": "wg-ep",
            "auto_detect_interface": True,
        },
    }


def write_config(state: dict) -> str:
    path = config_path(state["country"])
    atomic_write(path, json.dumps(build_config(state), indent=2) + "\n", mode=0o600)
    return path


def check_config(path: str):
    """Run `sing-box check` if the binary is available. Returns (ok, message)."""
    if not os.path.isfile(consts.SINGBOX_BIN):
        return True, "sing-box binary not found; skipped check"
    import subprocess

    try:
        p = subprocess.run(
            [consts.SINGBOX_BIN, "check", "-c", path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if p.returncode == 0:
            return True, ""
        return False, (p.stderr or p.stdout or "").strip()
    except OSError as e:
        return False, f"failed to run sing-box: {e}"
