"""xProton command-line interface (xproton / xpt)."""

import os
import shutil
import sys
import tempfile
import time

from . import accounts, consts, ed25519, health, provision, proxies, service, verify
from .util import (
    XProtonError,
    fail,
    file_exists,
    http_json,
    ok,
    port_in_use,
    run,
    sym,
    version_tuple,
    warn,
)

PROG = "xproton"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _resolve_targets(arg):
    if not arg or arg.lower() == "all":
        return consts.LOCATION_COUNTRIES
    arg = arg.upper()
    if arg not in consts.LOCATION_COUNTRIES:
        raise XProtonError(
            f"unknown location '{arg}' — choose from: "
            f"{', '.join(consts.LOCATION_COUNTRIES)}"
        )
    return [arg]


def _require_root(action):
    service.require_root(action)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_status(_args):
    rows = []
    for country in consts.LOCATION_COUNTRIES:
        state = provision.read_state(country)
        active = service.is_active(country) if service.unit_installed() else False
        enabled = service.is_enabled(country) if service.unit_installed() else False
        prov = state is not None
        port = (state or {}).get("socks_port", consts.DEFAULT_SOCKS_PORTS[country])
        provider = (state or {}).get("provider", "-")
        server = ((state or {}).get("server") or {}).get("name", "-")
        rows.append(
            (country, str(port), provider, server,
             "running" if active else "stopped", "on" if enabled else "off",
             "yes" if prov else "no")
        )
    print(f"{'LOC':<4}{'SOCKS':<8}{'PROV':<8}{'SERVER':<22}{'STATE':<8}{'AUTO':<5}{'CONF'}")
    print("-" * 60)
    for r in rows:
        print(f"{r[0]:<4}{r[1]:<8}{r[2]:<8}{r[3]:<22}{r[4]:<8}{r[5]:<5}{r[6]}")


def cmd_locations(_args):
    print("Location  SOCKS5 (127.0.0.1)   Free-pool country")
    print("-" * 45)
    for country, port in consts.FREE_LOCATIONS:
        print(f"{country:<9} 127.0.0.1:{port:<6}    {country}")


def cmd_start(args):
    _require_root("start")
    targets = _resolve_targets(args[0] if args else "all")
    provider = "auto"
    delay = consts.PROVISION_DELAY
    proxy_file = None
    if "--provider" in args:
        provider = args[args.index("--provider") + 1]
    if "--proxy-file" in args:
        proxy_file = args[args.index("--proxy-file") + 1]
    proxy_pool = proxies.read_proxies(proxy_file) if proxy_file else None
    for i, country in enumerate(targets):
        if i > 0 and delay:
            print(f"  waiting {delay}s before the next account...")
            time.sleep(delay)
        if not provision.is_provisioned(country):
            print(f"[{country}] provisioning...")
            provision.provision_location(
                country, provider=provider, proxies=proxy_pool
            )
        else:
            print(f"[{country}] already provisioned")
        service.start(country)
        print(f"[{country}] started {sym('arrow')} socks5://127.0.0.1:{provision.read_state(country)['socks_port']}")


def cmd_stop(args):
    _require_root("stop")
    for country in _resolve_targets(args[0] if args else "all"):
        service.stop(country)
        print(f"[{country}] stopped")


def cmd_restart(args):
    _require_root("restart")
    for country in _resolve_targets(args[0] if args else "all"):
        service.restart(country)
        print(f"[{country}] restarted")


def cmd_logs(args):
    if not args:
        raise XProtonError(f"usage: {PROG} logs <COUNTRY|all> [-f] [-n N]")
    country = args[0].upper()
    if country != "ALL" and country not in consts.LOCATION_COUNTRIES:
        raise XProtonError(f"unknown location '{country}'")
    follow = "-f" in args
    lines = 50
    if "-n" in args:
        try:
            lines = int(args[args.index("-n") + 1])
        except (ValueError, IndexError):
            pass
    service.logs(country, follow=follow, lines=lines)


def cmd_test(args):
    targets = _resolve_targets(args[0] if args and not args[0].startswith("-") else "all")
    speed = "--speed" in args
    timeout = 15.0
    if "--timeout" in args:
        try:
            timeout = float(args[args.index("--timeout") + 1])
        except (ValueError, IndexError):
            pass
    failed = False
    for country in targets:
        state = provision.read_state(country)
        if not state:
            fail(f"[{country}] not provisioned")
            failed = True
            continue
        port = state["socks_port"]
        if port_in_use(port):
            res = health.test_location(country, port, timeout)
            if res["ok"]:
                ip = res.get("exit_ip") or "?"
                ok(f"[{country}] 127.0.0.1:{port}  latency {res['latency_ms']}ms  exit {ip}")
            else:
                fail(f"[{country}] 127.0.0.1:{port}  {res.get('error')}")
                failed = True
            if speed and res["ok"]:
                sp = health.speed_location(country, port)
                if sp["ok"]:
                    ok(f"[{country}] download {sp['mbps']} Mbps ({sp['bytes']/1048576:.1f} MiB in {sp['seconds']}s)")
                else:
                    fail(f"[{country}] speed test failed: {sp.get('error')}")
        else:
            fail(f"[{country}] port {port} not listening (unit stopped?)")
            failed = True
    return 1 if failed else 0


def cmd_provision(args):
    _require_root("provision")
    targets = _resolve_targets(args[0] if args and not args[0].startswith("-") else "all")
    provider = "auto"
    if "--provider" in args:
        provider = args[args.index("--provider") + 1]
    force = "--force" in args
    delay = consts.PROVISION_DELAY
    if "--delay" in args:
        try:
            delay = float(args[args.index("--delay") + 1])
        except (ValueError, IndexError):
            pass
    for i, country in enumerate(targets):
        if i > 0 and delay:
            print(f"  waiting {delay}s...")
            time.sleep(delay)
        try:
            state = provision.provision_location(country, provider=provider, force=force)
            ok(f"[{country}] provisioned via {state['provider']} {sym('arrow')} "
               f"{state['server']['name']} ({state['server']['city']})")
        except XProtonError as e:
            fail(f"[{country}] {e}")


def cmd_port(args):
    _require_root("port")
    if len(args) < 1:
        raise XProtonError(f"usage: {PROG} port <COUNTRY> --socks <PORT>")
    country = args[0].upper()
    if country not in consts.LOCATION_COUNTRIES:
        raise XProtonError(f"unknown location '{country}'")
    if "--socks" not in args:
        raise XProtonError(f"usage: {PROG} port <COUNTRY> --socks <PORT>")
    try:
        new_port = int(args[args.index("--socks") + 1])
    except (ValueError, IndexError):
        raise XProtonError("invalid port")
    _validate_port(country, new_port)
    state = provision.read_state(country)
    if not state:
        raise XProtonError(f"[{country}] not provisioned yet — run '{PROG} start {country}' first")
    state["socks_port"] = new_port
    from .util import write_json
    write_json(provision.state_path(country), state, mode=0o600)
    from . import singbox
    singbox.write_config(state)
    if service.is_active(country):
        service.restart(country)
    ok(f"[{country}] SOCKS5 port {sym('arrow')} 127.0.0.1:{new_port}")


def _validate_port(country, new_port):
    if not (1024 <= new_port <= 65535):
        raise XProtonError("port must be between 1024 and 65535")
    for other, p in consts.DEFAULT_SOCKS_PORTS.items():
        if other != country and p == new_port:
            raise XProtonError(f"port {new_port} is the default for {other}")
    for other in consts.LOCATION_COUNTRIES:
        if other == country:
            continue
        st = provision.read_state(other)
        if st and st.get("socks_port") == new_port:
            raise XProtonError(f"port {new_port} is already used by {other}")


def cmd_accounts(_args):
    entries = accounts.parse_accounts()
    if not entries:
        print(f"no accounts yet in {consts.ACCOUNTS_FILE}")
        print(f"  create some:          {PROG} create")
        print(f"  or edit the file:     email:password[:totp][:COUNTRY] + session line")
        return
    mapping = accounts.assign_accounts(entries)
    spares = accounts.spare_pool(entries)
    print(f"accounts file: {consts.ACCOUNTS_FILE} "
          f"({len(entries)} entries, {len(spares)} spare)")
    print(f"{'TYPE':<7}{'ACCOUNT':<36}{'COUNTRY':<8}{'SESSION':<8}")
    print("-" * 64)
    for e in entries:
        print(f"{e['type'].upper():<7}{accounts.display_name(e):<36}"
              f"{(e.get('country') or '-'):<8}{'yes' if e.get('session') else 'no':<8}")
    print()
    print("assignment:")
    for country, entry in mapping.items():
        print(f"  {country:<4} {sym('arrow')} {accounts.display_name(entry)}")
    if spares:
        print("spares (not assigned to any location):")
        for e in spares:
            print(f"  {accounts.display_name(e)}")


def cmd_create(args):
    """Create temp accounts via the API provider (proxied when requested)."""
    count = None
    proxy_file = None
    countries = None
    yes = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--count":
            count = _int_arg(args, i, "count")
            i += 2
        elif a == "--proxy-file":
            proxy_file = args[i + 1]
            i += 2
        elif a == "--countries":
            countries = [c.strip().upper() for c in args[i + 1].split(",") if c.strip()]
            i += 2
        elif a in ("--yes", "-y"):
            yes = True
            i += 1
        else:
            raise XProtonError(f"unknown option '{a}' (try --count N [--proxy-file F] [--countries CA,US])")
    # ---- interactive part --------------------------------------------------
    if count is None:
        has_proxy = input("Do you have a proxy list? [y/N] ").strip().lower() in ("y", "yes")
        if has_proxy:
            proxy_file = input("proxy file path: ").strip()
        raw = input("how many accounts? ").strip()
        try:
            count = int(raw)
        except ValueError:
            raise XProtonError(f"invalid count '{raw}'")
    proxies_list = proxies.read_proxies(proxy_file) if proxy_file else None
    cap = consts.MAX_ACCOUNTS_WITH_PROXY if proxies_list else consts.MAX_ACCOUNTS_NO_PROXY
    if not (1 <= count <= cap):
        hint = ("max 50 with a proxy list" if proxies_list
                else "max 10 without a proxy list (protect your server IP)")
        raise XProtonError(f"count must be between 1 and {cap} ({hint})")
    if not yes:
        answer = input(f"create {count} account(s) via API? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return
    print(f"creating {count} account(s)...")
    created, failed = provision.create_accounts(count, proxies_list, countries)
    if created:
        ok(f"{len(created)} account(s) created and saved to {consts.ACCOUNTS_FILE}")
        for e in created:
            pin = f" for {e['country']}" if e.get("country") else " (spare)"
            print(f"    {accounts.display_name(e)}{pin}")
    if failed:
        fail(f"{len(failed)} account(s) failed")
        for f_ in failed:
            print(f"    #{f_['index']} {f_['country'] or 'spare'}: {f_['error']}")
    if created:
        print()
        warn("run 'xproton verify' to confirm the accounts are healthy")


def _int_arg(args, i, name):
    try:
        return int(args[i + 1])
    except (ValueError, IndexError):
        raise XProtonError(f"--{name} requires a number")


def cmd_verify(args):
    proxy_file = None
    if "--proxy-file" in args:
        proxy_file = args[args.index("--proxy-file") + 1]
    proxy_list = proxies.read_proxies(proxy_file) if proxy_file else None
    proxy = proxy_list[0] if proxy_list else None
    entries = accounts.parse_accounts()
    if not entries:
        warn(f"no accounts in {consts.ACCOUNTS_FILE} — create some with '{PROG} create'")
        return
    print(f"verifying {len(entries)} account(s) in {consts.ACCOUNTS_FILE} ...")
    results = verify.verify_entries(entries, proxy=proxy, progress=_verify_progress)
    good = sum(1 for r in results if r["ok"])
    bad = len(results) - good
    print()
    for r in results:
        label = accounts.display_name(r["entry"])
        if r["ok"]:
            ok(f"{label:<36} {r['detail']}")
        else:
            fail(f"{label:<36} {r['detail']}")
    print()
    ok(f"{good}/{len(results)} accounts healthy")
    if bad:
        warn(f"{bad} account(s) failed — delete them with '{PROG} accounts' (or edit accounts.txt)")
    print("checking provisioned certificates...")
    actions = verify.reissue_expiring(results)
    for country, msg in actions:
        print(f"  [{country}] {msg}")
    if not actions:
        ok("all certificates valid")


def _verify_progress(label, status):
    print(f"  {label} ... {status}")


def _github_latest_tag(repo):
    try:
        status, _h, data = http_json(
            "GET", f"https://api.github.com/repos/{repo}/releases/latest",
            timeout=15, retries=1,
        )
    except XProtonError:
        return None
    if status == 200 and isinstance(data, dict) and data.get("tag_name"):
        return data["tag_name"]
    return None


def cmd_update(args):
    check = "--check" in args or "-c" in args
    if not check:
        _require_root("update")
    repo = consts.GITHUB_REPO
    tag = _github_latest_tag(repo)
    if tag is None:
        warn(f"could not fetch the latest release for {repo} "
             f"(repo not published yet or GitHub rate-limited)")
        return
    ok(f"local version : {consts.VERSION}")
    ok(f"latest release: {tag}")
    if version_tuple(tag) <= version_tuple(consts.VERSION):
        ok("xProton is already up to date")
        return
    if check:
        return
    answer = input(f"update to {tag} now? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        print("aborted")
        return
    _perform_update(repo, tag)


def _perform_update(repo, tag):
    opt = consts.OPT_DIR
    if not os.path.isdir(opt):
        fail(f"install directory {opt} not found — re-run install.sh")
        return
    running = [c for c in consts.LOCATION_COUNTRIES if service.is_active(c)]
    if os.path.isdir(os.path.join(opt, ".git")):
        # git-based install: fast-forward to the tag
        run(["git", "-C", opt, "fetch", "--tags", "origin"], check=True, timeout=120)
        run(["git", "-C", opt, "checkout", "-f", tag], check=True, timeout=60)
        src = opt
        need_copy = False
    else:
        bak = opt + ".bak"
        if os.path.exists(bak):
            shutil.rmtree(bak, ignore_errors=True)
        shutil.copytree(opt, bak)
        tmp = tempfile.mkdtemp(prefix="xproton-update-")
        try:
            run(["curl", "-fsSL",
                 f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz",
                 "-o", os.path.join(tmp, "upd.tar.gz")], check=True, timeout=180)
            run(["tar", "-xzf", os.path.join(tmp, "upd.tar.gz"), "-C", tmp],
                check=True, timeout=60)
            src_dir = [d for d in os.listdir(tmp) if d.startswith("xProton-")]
            if not src_dir:
                raise XProtonError("could not locate extracted sources in the tarball")
            src = os.path.join(tmp, src_dir[0])
            for item in ("xproton", "xpl", "systemd", "accounts.example.txt"):
                s = os.path.join(src, item)
                if not os.path.exists(s):
                    continue
                dst = os.path.join(opt, item)
                if os.path.isdir(dst):
                    shutil.rmtree(dst, ignore_errors=True)
                elif os.path.exists(dst):
                    os.remove(dst)
                if os.path.isdir(s):
                    shutil.copytree(s, dst)
                else:
                    shutil.copy2(s, dst)
            need_copy = True
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if need_copy:
        os.chmod(os.path.join(opt, "xproton"), 0o755)
    # refresh systemd unit + symlinks
    unit_src = os.path.join(src, "systemd", "xproton@.service") if need_copy else os.path.join(opt, "systemd", "xproton@.service")
    if os.path.isfile(unit_src):
        shutil.copy2(unit_src, consts.UNIT_FILE)
        run(["systemctl", "daemon-reload"], check=True, timeout=30)
    service.install_binary_symlinks(opt)
    for c in running:
        service.restart(c)
    ok(f"updated to {tag}; restarted {len(running)} running location(s)")
    print("run 'xproton doctor' to confirm everything is healthy")


def cmd_autostart(args):
    _require_root("autostart")
    if not args:
        raise XProtonError(f"usage: {PROG} autostart on|off|status")
    act = args[0].lower()
    if act == "status":
        for c, e in service.autostart_status().items():
            print(f"  {c}: {'enabled' if e else 'disabled'}")
    elif act == "on":
        service.autostart(True)
        ok("autostart enabled for all locations")
    elif act == "off":
        service.autostart(False)
        ok("autostart disabled for all locations")
    else:
        raise XProtonError(f"usage: {PROG} autostart on|off|status")


def cmd_doctor(_args):
    import socket as _socket
    print("xProton doctor")
    print("=" * 60)
    import sys as _sys
    if _sys.version_info >= (3, 10):
        ok(f"python {_sys.version_info.major}.{_sys.version_info.minor}")
    else:
        fail("python >= 3.10 required")
    if file_exists(consts.SINGBOX_BIN):
        rc, out, _ = run([consts.SINGBOX_BIN, "version"])
        ok(f"sing-box: {out.strip().splitlines()[0] if out.strip() else 'present'}")
    else:
        fail(f"sing-box binary missing ({consts.SINGBOX_BIN})")
    if service.unit_installed():
        ok("systemd unit installed (xproton@.service)")
    else:
        fail("systemd unit missing — re-run install.sh")
    v = ed25519.ed25519_public_key(bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"))
    if v.hex() == "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a":
        ok("ed25519 key derivation (RFC 8032 vector)")
    else:
        fail("ed25519 self-test failed")
    if file_exists(consts.ACCOUNTS_FILE):
        try:
            entries = accounts.parse_accounts()
            ok(f"accounts.txt present ({len(entries)} entries)")
        except XProtonError as e:
            fail(f"accounts.txt parse error: {e}")
    else:
        warn("no accounts.txt yet — run 'xproton create' or add accounts")
    for country, port in consts.FREE_LOCATIONS:
        state = provision.read_state(country)
        used = port_in_use(port)
        if state:
            if used:
                ok(f"port {port} ({country}) listening")
            else:
                warn(f"port {port} ({country}) free (unit not running)")
        elif used:
            fail(f"port {port} ({country}) occupied by something else!")
    for host, port in (("proton-api.vercel.app", 443), ("vpn-api.proton.me", 443)):
        try:
            with _socket.create_connection((host, port), timeout=5):
                ok(f"reachable {host}:{port}")
        except OSError as e:
            fail(f"unreachable {host}:{port} ({e})")
    print()
    print("done. next: sudo xproton start US")


def cmd_version(_args):
    print(f"xProton {consts.VERSION}")


def cmd_uninstall(args):
    _require_root("uninstall")
    yes = "--yes" in args
    purge = "--purge" in args
    if not yes:
        answer = input(
            "This stops all locations, removes /opt/xproton and the "
            "'xproton'/'xpn'/'xpt' commands. Continue? [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return
    if not purge and not yes:
        answer = input("also delete accounts/config data in /etc/xproton? [y/N] ").strip().lower()
        purge = answer in ("y", "yes")
    for country in consts.LOCATION_COUNTRIES:
        service.stop(country)
        service._systemctl("disable", service.instance_name(country))
    if os.path.exists(consts.UNIT_FILE):
        os.remove(consts.UNIT_FILE)
    run(["systemctl", "daemon-reload"])
    for link in consts.BIN_SYMLINKS:
        if os.path.islink(link):
            os.remove(link)
    if os.path.isdir(consts.OPT_DIR):
        shutil.rmtree(consts.OPT_DIR, ignore_errors=True)
    if purge:
        if os.path.exists(consts.ETC_DIR):
            shutil.rmtree(consts.ETC_DIR, ignore_errors=True)
        print("xProton fully uninstalled (including /etc/xproton)")
    else:
        print("xProton uninstalled (configs/accounts kept in /etc/xproton)")


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------
def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        from . import panel
        return panel.run()
    cmd = argv[0].lower()
    rest = argv[1:]
    try:
        if cmd in ("status", "st"):
            cmd_status(rest)
        elif cmd in ("locations", "ls"):
            cmd_locations(rest)
        elif cmd in ("start",):
            cmd_start(rest)
        elif cmd in ("stop",):
            cmd_stop(rest)
        elif cmd in ("restart",):
            cmd_restart(rest)
        elif cmd in ("logs",):
            cmd_logs(rest)
        elif cmd in ("test", "speed"):
            return cmd_test(rest)
        elif cmd in ("provision", "prov"):
            cmd_provision(rest)
        elif cmd in ("port",):
            cmd_port(rest)
        elif cmd in ("accounts", "acc"):
            cmd_accounts(rest)
        elif cmd in ("create", "new"):
            cmd_create(rest)
        elif cmd in ("verify", "check"):
            cmd_verify(rest)
        elif cmd in ("update", "upgrade"):
            cmd_update(rest)
        elif cmd in ("autostart",):
            cmd_autostart(rest)
        elif cmd in ("doctor",):
            cmd_doctor(rest)
        elif cmd in ("version", "-v", "--version"):
            cmd_version(rest)
        elif cmd in ("uninstall",):
            cmd_uninstall(rest)
        elif cmd in ("panel", "menu", "tui"):
            from . import panel
            return panel.run()
        else:
            raise XProtonError(
                f"unknown command '{cmd}'. Try: {PROG} doctor | {PROG} start US | {PROG} test"
            )
    except XProtonError as e:
        fail(str(e))
        return 1
    return 0
