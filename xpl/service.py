"""systemd integration: one xproton@<COUNTRY>.service instance per location."""

import os
import shutil

from . import consts
from .util import XProtonError, atomic_write, is_root, run

UNIT_CONTENT = """\
[Unit]
Description=xProton ProtonVPN location %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={singbox} run -c {instances}/%i/config.json
Restart=on-failure
RestartSec=5
LimitNOFILE=65535
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
""".format(singbox=consts.SINGBOX_BIN, instances=consts.INSTANCES_DIR)


def unit_installed() -> bool:
    return os.path.isfile(consts.UNIT_FILE)


def install_unit() -> None:
    atomic_write(consts.UNIT_FILE, UNIT_CONTENT, mode=0o644)
    run(["systemctl", "daemon-reload"], check=True, timeout=30)


def instance_name(country: str) -> str:
    return consts.UNIT_INSTANCE_PREFIX + country


def _systemctl(*args, check=False):
    return run(["systemctl", *args], check=check, timeout=60)


def start(country: str, check=True):
    return _systemctl("start", instance_name(country), check=check)


def stop(country: str, check=False):
    return _systemctl("stop", instance_name(country), check=check)


def restart(country: str, check=False):
    return _systemctl("restart", instance_name(country), check=check)


def is_active(country: str) -> bool:
    rc, out, _ = _systemctl("is-active", instance_name(country))
    return out.strip() == "active"


def is_enabled(country: str) -> bool:
    rc, out, _ = _systemctl("is-enabled", instance_name(country))
    return out.strip() == "enabled"


def autostart(on: bool) -> None:
    for country in consts.LOCATION_COUNTRIES:
        verb = "enable" if on else "disable"
        _systemctl(verb, instance_name(country))


def autostart_status() -> dict:
    return {c: is_enabled(c) for c in consts.LOCATION_COUNTRIES}


def logs(country: str, follow: bool = False, lines: int = 50):
    unit = instance_name(country) if country != "ALL" else "xproton@*"
    cmd = ["journalctl", "-u", unit, "-n", str(lines), "--no-pager"]
    if follow:
        cmd.append("-f")
    os.execvp(cmd[0], cmd)  # hand over to journalctl (handles ^C, colors)


def require_root(action: str) -> None:
    if not is_root():
        raise XProtonError(f"'{action}' needs root — run with sudo")


def install_binary_symlinks(source_dir: str) -> None:
    for name in ("xproton", "xpn", "xpt"):
        src = os.path.join(source_dir, name)
        dst = f"/usr/local/bin/{name}"
        if os.path.exists(dst):
            os.remove(dst)
        os.symlink(src, dst)
        os.chmod(src, 0o755)
